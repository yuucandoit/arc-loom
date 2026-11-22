from __future__ import annotations

import math
import re
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator


@dataclass(frozen=True)
class LLMCallResult:
    text: str
    input_tokens: int
    output_tokens: int
    model_name: str
    latency_ms: float = 0.0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


LLMHandler = Callable[[str, float, str], LLMCallResult]
TelemetryHook = Callable[[str, LLMCallResult], None]


def text_to_sparse_vector(text: str) -> dict[str, float]:
    normalized = re.sub(r"[^a-z0-9_]+", " ", text.lower()).strip()
    if not normalized:
        return {}
    tokens = normalized.split()
    counts: dict[str, float] = {}
    for tok in tokens:
        counts[f"w:{tok}"] = counts.get(f"w:{tok}", 0.0) + 2.0
        padded = f"#{tok}#"
        for i in range(len(padded) - 2):
            trigram = padded[i : i + 3]
            counts[f"c:{trigram}"] = counts.get(f"c:{trigram}", 0.0) + 1.0
    norm = math.sqrt(sum(v * v for v in counts.values()))
    if norm == 0.0:
        return counts
    return {k: v / norm for k, v in counts.items()}


def cosine_similarity_sparse(vec_a: dict[str, float], vec_b: dict[str, float]) -> float:
    if not vec_a or not vec_b:
        return 0.0
    if len(vec_a) > len(vec_b):
        vec_a, vec_b = vec_b, vec_a
    return sum(val * vec_b.get(key, 0.0) for key, val in vec_a.items())


@dataclass
class _LayerSlot:
    value: Any
    description: str
    vector: dict[str, float]


class Context:
    """Layered semantic memory stack for CPU-ARC semantic function execution."""

    def __init__(
        self,
        initial: dict[str, Any] | None = None,
        descriptions: dict[str, str] | None = None,
        *,
        project_slug: str = "default",
        request_id: str = "req-local",
        target_model: str = "local/llama-3.2-1b",
        llm_handler: LLMHandler | None = None,
        telemetry_hook: TelemetryHook | None = None,
        tools: dict[str, Callable[..., Any]] | None = None,
        tool_callback_url: str | None = None,
        tool_callback_secret: str | None = None,
        registry: Any | None = None,
    ) -> None:
        self.project_slug = project_slug
        self.request_id = request_id
        self.target_model = target_model
        self.llm_handler = llm_handler
        self.telemetry_hook = telemetry_hook
        self.tools: dict[str, Callable[..., Any]] = dict(tools or {})
        self.tool_callback_url = tool_callback_url
        self.tool_callback_secret = tool_callback_secret
        self.registry = registry
        self.narration_trace: list[str] = []
        self._layers: list[dict[str, _LayerSlot]] = []
        self.push(initial=initial, descriptions=descriptions)

    def push(
        self,
        initial: dict[str, Any] | None = None,
        descriptions: dict[str, str] | None = None,
    ) -> None:
        layer: dict[str, _LayerSlot] = {}
        desc_map = descriptions or {}
        if initial:
            for key, value in initial.items():
                desc = desc_map.get(key, "")
                semantic_text = f"{key.replace('_', ' ')} {desc}".strip()
                layer[key] = _LayerSlot(
                    value=value,
                    description=desc,
                    vector=text_to_sparse_vector(semantic_text),
                )
        self._layers.append(layer)

    def pop(self) -> dict[str, Any]:
        if len(self._layers) <= 1:
            raise RuntimeError("Cannot pop the root Context layer.")
        popped = self._layers.pop()
        return {k: slot.value for k, slot in popped.items()}

    @contextmanager
    def scope(
        self,
        initial: dict[str, Any] | None = None,
        descriptions: dict[str, str] | None = None,
    ) -> Iterator[Context]:
        self.push(initial=initial, descriptions=descriptions)
        try:
            yield self
        finally:
            self.pop()

    @property
    def depth(self) -> int:
        return len(self._layers)

    def set(self, key: str, value: Any, description: str = "") -> None:
        semantic_text = f"{key.replace('_', ' ')} {description}".strip()
        self._layers[-1][key] = _LayerSlot(
            value=value,
            description=description,
            vector=text_to_sparse_vector(semantic_text),
        )

    def get_exact(self, key: str) -> Any:
        for layer in reversed(self._layers):
            if key in layer:
                return layer[key].value
        raise KeyError(f"Key '{key}' not found in any Context layer.")

    def resolve_semantic(self, query: str, min_score: float = 0.15) -> tuple[str, Any]:
        # Prefer exact key lookup from top layer down before scoring descriptions.
        for layer in reversed(self._layers):
            if query in layer:
                return query, layer[query].value

        query_vec = text_to_sparse_vector(query)
        best_key: str | None = None
        best_value: Any = None
        best_score = -1.0

        # Upper layers shadow lower layers when similarity scores tie.
        for layer in reversed(self._layers):
            for key, slot in layer.items():
                score = cosine_similarity_sparse(query_vec, slot.vector)
                if score > best_score:
                    best_score = score
                    best_key = key
                    best_value = slot.value

        if best_key is None or best_score < min_score:
            raise KeyError(
                f"No parameter matching semantic query '{query}' (best_score={best_score:.3f})."
            )
        return best_key, best_value

    def call_target_lm(self, prompt: str, *, temperature: float, role: str) -> str:
        if self.llm_handler is None:
            raise RuntimeError(
                f"No target LLM handler configured on Context for role '{role}'."
            )
        result = self.llm_handler(prompt, temperature, role)
        if self.telemetry_hook is not None:
            self.telemetry_hook(role, result)
        return result.text
