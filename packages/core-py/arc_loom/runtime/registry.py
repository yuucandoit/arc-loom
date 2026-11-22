from __future__ import annotations

import hashlib
import json
import math
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from arc_loom.runtime.context import (
    Context,
    cosine_similarity_sparse,
    text_to_sparse_vector,
)
from arc_loom.runtime.primitives import (
    do,
    extract,
    invoke_tool,
    lm,
    load,
    narrate,
    smart_convert,
)

try:
    import faiss  # type: ignore
    import numpy as np  # type: ignore

    _HAS_FAISS = True
except Exception:
    _HAS_FAISS = False


EmbeddingProvider = Callable[[str], list[float]]


def _normalize_dense(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in vec))
    if norm <= 0.0:
        return vec
    return [v / norm for v in vec]


def _cosine_dense(vec_a: list[float], vec_b: list[float]) -> float:
    if not vec_a or not vec_b or len(vec_a) != len(vec_b):
        return 0.0
    return sum(a * b for a, b in zip(vec_a, vec_b))


def openai_embedding_provider(
    endpoint: str,
    model: str = "nomic-embed-text",
    api_key: str | None = None,
) -> EmbeddingProvider:
    """Create a dense neural embedding provider targeting an OpenAI-compatible /v1/embeddings endpoint."""
    url = endpoint.rstrip("/") + "/embeddings"

    def embed(text: str) -> list[float]:
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        req = urllib.request.Request(
            url,
            data=json.dumps({"model": model, "input": text}).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return _normalize_dense([float(x) for x in data["data"][0]["embedding"]])

    return embed


def text_to_dense_vector(text: str, dim: int = 128) -> list[float]:
    sparse = text_to_sparse_vector(text)
    vec = [0.0] * dim
    for token, weight in sparse.items():
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
        idx = int.from_bytes(digest[:4], "little") % dim
        sign = 1.0 if (digest[4] & 1) == 0 else -1.0
        vec[idx] += sign * weight
    return _normalize_dense(vec)


@dataclass
class CacheEntry:
    entry_key: str
    project_slug: str
    intent_description: str
    param_schema: dict[str, Any]
    source_code: str = ""
    source_path: str = ""
    is_compiled: bool = False
    hit_count: int = 0
    avg_source_cost_usd: float = 0.0
    builder_cost_usd: float = 0.0
    callable_fn: Callable[[Context], Any] | None = None
    db_id: int | None = None
    _sparse_vec: dict[str, float] = field(default_factory=dict, repr=False)
    _dense_vec: list[float] = field(default_factory=list, repr=False)


class SemanticRegistry:
    """Vector-indexed registry of CPU-ARC semantic functions per project."""

    def __init__(
        self,
        cache_dir: str | Path | None = None,
        default_threshold: float = 0.82,
        vector_dim: int = 128,
        embedding_fn: EmbeddingProvider | None = None,
    ) -> None:
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.default_threshold = default_threshold
        self.vector_dim = vector_dim
        self.embedding_fn = embedding_fn
        self._entries: dict[str, CacheEntry] = {}

    def register(
        self,
        *,
        project_slug: str,
        entry_key: str,
        intent_description: str,
        param_schema: dict[str, Any] | None = None,
        source_code: str = "",
        source_path: str = "",
        callable_fn: Callable[[Context], Any] | None = None,
        is_compiled: bool = False,
        avg_source_cost_usd: float = 0.0,
        builder_cost_usd: float = 0.0,
        db_id: int | None = None,
        persist_to_disk: bool = False,
    ) -> CacheEntry:
        resolved_path = source_path
        if persist_to_disk and source_code and self.cache_dir is not None:
            proj_dir = self.cache_dir / project_slug
            proj_dir.mkdir(parents=True, exist_ok=True)
            file_path = proj_dir / f"{entry_key}.py"
            file_path.write_text(source_code, encoding="utf-8")
            resolved_path = str(file_path)

        dense_vec = (
            _normalize_dense(self.embedding_fn(intent_description))
            if self.embedding_fn is not None
            else text_to_dense_vector(intent_description, dim=self.vector_dim)
        )

        entry = CacheEntry(
            entry_key=entry_key,
            project_slug=project_slug,
            intent_description=intent_description,
            param_schema=param_schema or {},
            source_code=source_code,
            source_path=resolved_path,
            is_compiled=is_compiled,
            avg_source_cost_usd=avg_source_cost_usd,
            builder_cost_usd=builder_cost_usd,
            callable_fn=callable_fn,
            db_id=db_id,
            _sparse_vec=text_to_sparse_vector(intent_description),
            _dense_vec=dense_vec,
        )
        self._entries[entry_key] = entry
        return entry

    def get(self, entry_key: str) -> CacheEntry | None:
        return self._entries.get(entry_key)

    def list_entries(self, project_slug: str | None = None) -> list[CacheEntry]:
        if project_slug is None:
            return list(self._entries.values())
        return [e for e in self._entries.values() if e.project_slug == project_slug]

    def find_best(
        self,
        query: str,
        *,
        project_slug: str | None = None,
        threshold: float | None = None,
    ) -> tuple[CacheEntry, float] | None:
        min_score = self.default_threshold if threshold is None else threshold
        candidates = self.list_entries(project_slug=project_slug)
        if not candidates:
            return None

        best_entry: CacheEntry | None = None
        best_score = -1.0

        if self.embedding_fn is not None:
            q_dense = _normalize_dense(self.embedding_fn(query))
            if _HAS_FAISS and len(candidates) >= 16:
                dim = len(q_dense)
                index = faiss.IndexFlatIP(dim)
                index.add(np.array([c._dense_vec for c in candidates], dtype="float32"))
                _, indices = index.search(np.array([q_dense], dtype="float32"), min(8, len(candidates)))
                shortlist = [candidates[i] for i in indices[0] if i >= 0]
            else:
                shortlist = candidates

            for entry in shortlist:
                score = _cosine_dense(q_dense, entry._dense_vec)
                if score > best_score:
                    best_score = score
                    best_entry = entry
        else:
            q_sparse = text_to_sparse_vector(query)
            if _HAS_FAISS and len(candidates) >= 16:
                q_dense_h = np.array([text_to_dense_vector(query, self.vector_dim)], dtype="float32")
                mat = np.array([c._dense_vec for c in candidates], dtype="float32")
                index = faiss.IndexFlatIP(self.vector_dim)
                index.add(mat)
                _, indices = index.search(q_dense_h, min(8, len(candidates)))
                shortlist = [candidates[i] for i in indices[0] if i >= 0]
            else:
                shortlist = candidates

            for entry in shortlist:
                score = cosine_similarity_sparse(q_sparse, entry._sparse_vec)
                if score > best_score:
                    best_score = score
                    best_entry = entry

        if best_entry is None or best_score < min_score:
            return None
        return best_entry, best_score

    def execute_entry(self, entry: CacheEntry, ctx: Context) -> Any:
        entry.hit_count += 1
        if entry.callable_fn is not None:
            return entry.callable_fn(ctx)

        code = entry.source_code
        if not code and entry.source_path:
            code = Path(entry.source_path).read_text(encoding="utf-8")
            entry.source_code = code

        if not code:
            raise RuntimeError(
                f"CacheEntry '{entry.entry_key}' has neither callable_fn nor source_code."
            )

        namespace: dict[str, Any] = {
            "Context": Context,
            "ctx": ctx,
            "load": load,
            "extract": extract,
            "lm": lm,
            "smart_convert": smart_convert,
            "do": do,
            "invoke_tool": invoke_tool,
            "narrate": narrate,
        }
        exec(code, namespace)
        if "run" in namespace and callable(namespace["run"]):
            return namespace["run"](ctx)
        if "semantic_fn" in namespace and callable(namespace["semantic_fn"]):
            return namespace["semantic_fn"](ctx)
        if "result" in namespace:
            return namespace["result"]
        raise RuntimeError(
            f"Semantic script '{entry.entry_key}' must define 'def run(ctx):' or 'result'."
        )
