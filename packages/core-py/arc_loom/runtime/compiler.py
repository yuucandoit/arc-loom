from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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
from arc_loom.runtime.registry import CacheEntry, SemanticRegistry


@dataclass(frozen=True)
class CompilationResult:
    entry_key: str
    compiled_code: str
    resolved_sub_calls: dict[str, str]
    resolved_loads: dict[str, str]
    source_path: str


def _safe_identifier(name: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_]+", "_", name).strip("_")
    if not cleaned or cleaned[0].isdigit():
        cleaned = f"fn_{cleaned}"
    return cleaned


def _resolve_static_param_key(
    query: str,
    param_schema: dict[str, Any],
    param_descriptions: dict[str, str] | None = None,
) -> str | None:
    all_keys: dict[str, str] = {
        "query": "raw user prompt query text",
        "prompt": "raw user prompt query text",
    }
    for k, v in param_schema.items():
        all_keys[k] = f"{k.replace('_', ' ')} {v}"
    if param_descriptions:
        for k, desc in param_descriptions.items():
            all_keys[k] = f"{k.replace('_', ' ')} {desc}"

    if query in all_keys:
        return query

    q_vec = text_to_sparse_vector(query)
    best_key: str | None = None
    best_score = -1.0
    for key, semantic_desc in all_keys.items():
        slot_vec = text_to_sparse_vector(f"{key.replace('_', ' ')} {semantic_desc}")
        score = cosine_similarity_sparse(q_vec, slot_vec)
        if score > best_score:
            best_score = score
            best_key = key

    if best_key is not None and best_score >= 0.15:
        return best_key
    return None


class _StaticO1Transformer(ast.NodeTransformer):
    """Rewrites semantic `load` and `do` calls into O(1) direct lookups and calls."""

    def __init__(
        self,
        *,
        project_slug: str,
        registry: SemanticRegistry,
        param_schema: dict[str, Any],
        param_descriptions: dict[str, str] | None = None,
    ) -> None:
        self.project_slug = project_slug
        self.registry = registry
        self.param_schema = param_schema
        self.param_descriptions = param_descriptions
        self.resolved_sub_calls: dict[str, str] = {}
        self.resolved_loads: dict[str, str] = {}
        self.helper_entries: dict[str, CacheEntry] = {}

    def visit_Call(self, node: ast.Call) -> ast.AST:
        self.generic_visit(node)
        if not isinstance(node.func, ast.Name):
            return node

        fn_name = node.func.id

        # Rewrite load(ctx, "query", d_type) -> smart_convert(ctx, ctx.get_exact("key"), d_type)
        if fn_name == "load" and len(node.args) >= 2:
            ctx_arg = node.args[0]
            query_arg = node.args[1]
            dtype_arg = (
                node.args[2]
                if len(node.args) >= 3
                else ast.Name(id="str", ctx=ast.Load())
            )
            if isinstance(query_arg, ast.Constant) and isinstance(query_arg.value, str):
                resolved_key = _resolve_static_param_key(
                    query_arg.value,
                    self.param_schema,
                    self.param_descriptions,
                )
                if resolved_key is not None:
                    self.resolved_loads[query_arg.value] = resolved_key
                    exact_call = ast.Call(
                        func=ast.Attribute(
                            value=ctx_arg,
                            attr="get_exact",
                            ctx=ast.Load(),
                        ),
                        args=[ast.Constant(value=resolved_key)],
                        keywords=[],
                    )
                    return ast.Call(
                        func=ast.Name(id="smart_convert", ctx=ast.Load()),
                        args=[ctx_arg, exact_call, dtype_arg],
                        keywords=[],
                    )

        # Rewrite do(ctx, "intent", payloads) -> _invoke_static(_compiled_<key>, ctx, payloads)
        if fn_name == "do" and len(node.args) >= 2:
            ctx_arg = node.args[0]
            intent_arg = node.args[1]
            payload_arg = (
                node.args[2]
                if len(node.args) >= 3
                else ast.Dict(keys=[], values=[])
            )
            if isinstance(intent_arg, ast.Constant) and isinstance(intent_arg.value, str):
                match = self.registry.find_best(
                    intent_arg.value,
                    project_slug=self.project_slug,
                    threshold=0.15,
                )
                if match is not None:
                    sub_entry, _ = match
                    helper_name = f"_compiled_{_safe_identifier(sub_entry.entry_key)}"
                    self.resolved_sub_calls[intent_arg.value] = sub_entry.entry_key
                    self.helper_entries[helper_name] = sub_entry
                    return ast.Call(
                        func=ast.Name(id="_invoke_static", ctx=ast.Load()),
                        args=[
                            ast.Name(id=helper_name, ctx=ast.Load()),
                            ctx_arg,
                            payload_arg,
                        ],
                        keywords=[],
                    )

        return node


def _invoke_static(
    fn: Any,
    ctx: Context,
    payloads: dict[str, Any] | None = None,
) -> Any:
    with ctx.scope(initial=payloads or {}):
        return fn(ctx)


class StaticCompiler:
    """Compiles a network of dynamic CPU-ARC semantic functions into an O(1) Python module."""

    def __init__(self, registry: SemanticRegistry) -> None:
        self.registry = registry

    def compile_entry(
        self,
        entry_key: str,
        *,
        param_descriptions: dict[str, str] | None = None,
        persist_to_disk: bool = True,
    ) -> CompilationResult:
        entry = self.registry.get(entry_key)
        if entry is None:
            raise KeyError(f"CacheEntry '{entry_key}' not found in SemanticRegistry.")

        if not entry.source_code and entry.source_path:
            entry.source_code = Path(entry.source_path).read_text(encoding="utf-8")

        if not entry.source_code:
            raise ValueError(
                f"CacheEntry '{entry_key}' has no Python source_code to compile."
            )

        tree = ast.parse(entry.source_code)
        transformer = _StaticO1Transformer(
            project_slug=entry.project_slug,
            registry=self.registry,
            param_schema=entry.param_schema,
            param_descriptions=param_descriptions,
        )
        transformed_tree = transformer.visit(tree)
        ast.fix_missing_locations(transformed_tree)

        helper_blocks: list[str] = []
        bound_callables: dict[str, Any] = {}

        for helper_name, sub_entry in transformer.helper_entries.items():
            if sub_entry.source_code:
                sub_tree = ast.parse(sub_entry.source_code)
                sub_tx = _StaticO1Transformer(
                    project_slug=sub_entry.project_slug,
                    registry=self.registry,
                    param_schema=sub_entry.param_schema,
                )
                sub_tree = sub_tx.visit(sub_tree)
                for node in sub_tree.body:
                    if isinstance(node, ast.FunctionDef) and node.name in {
                        "run",
                        "semantic_fn",
                    }:
                        node.name = helper_name
                ast.fix_missing_locations(sub_tree)
                helper_blocks.append(ast.unparse(sub_tree))
            elif sub_entry.callable_fn is not None:
                bound_callables[helper_name] = sub_entry.callable_fn

        main_code = ast.unparse(transformed_tree)
        header = "# ARC-Loom Static O(1) Compiled Module (zero runtime vector search)\n"
        full_compiled_code = "\n\n".join([header.strip(), *helper_blocks, main_code])

        namespace: dict[str, Any] = {
            "Context": Context,
            "_invoke_static": _invoke_static,
            "load": load,
            "extract": extract,
            "lm": lm,
            "smart_convert": smart_convert,
            "do": do,
            "invoke_tool": invoke_tool,
            "narrate": narrate,
            **bound_callables,
        }
        exec(full_compiled_code, namespace)
        compiled_fn = namespace.get("run") or namespace.get("semantic_fn")
        if not callable(compiled_fn):
            raise RuntimeError(
                f"Compiled module for '{entry_key}' did not produce a callable 'run(ctx)'."
            )

        entry.callable_fn = compiled_fn
        entry.is_compiled = True

        compiled_path = entry.source_path
        if persist_to_disk and self.registry.cache_dir is not None:
            proj_dir = self.registry.cache_dir / entry.project_slug
            proj_dir.mkdir(parents=True, exist_ok=True)
            out_file = proj_dir / f"{entry.entry_key}.compiled.py"
            out_file.write_text(full_compiled_code, encoding="utf-8")
            compiled_path = str(out_file)
            entry.source_path = compiled_path

        return CompilationResult(
            entry_key=entry.entry_key,
            compiled_code=full_compiled_code,
            resolved_sub_calls=transformer.resolved_sub_calls,
            resolved_loads=transformer.resolved_loads,
            source_path=compiled_path or f"memory://{entry.entry_key}.compiled.py",
        )
