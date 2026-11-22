from __future__ import annotations

import ast
import hashlib
import hmac
import json
import re
import urllib.request
from typing import Any

from arc_loom.runtime.context import Context


_TYPE_ALIASES: dict[str, type] = {
    "str": str,
    "string": str,
    "int": int,
    "integer": int,
    "float": float,
    "number": float,
    "bool": bool,
    "boolean": bool,
    "list": list,
    "dict": dict,
}


def _resolve_target_type(d_type: type | str) -> type:
    if isinstance(d_type, type):
        return d_type
    key = str(d_type).strip().lower()
    if key in _TYPE_ALIASES:
        return _TYPE_ALIASES[key]
    raise ValueError(f"Unsupported target type specification: {d_type}")


def _direct_cast(value: Any, target_cls: type) -> Any:
    if isinstance(value, target_cls) and target_cls is not int:
        return value
    if target_cls is int and isinstance(value, int) and not isinstance(value, bool):
        return value

    if target_cls is str:
        return str(value)

    text = str(value).strip()

    if target_cls is bool:
        lowered = text.lower()
        if lowered in {"true", "1", "yes", "ya", "benar"}:
            return True
        if lowered in {"false", "0", "no", "tidak", "salah"}:
            return False
        raise ValueError(f"Cannot cast '{value}' to bool directly.")

    if target_cls is int:
        if isinstance(value, float) and value.is_integer():
            return int(value)
        cleaned = re.sub(r"[,\s_]", "", text)
        if re.fullmatch(r"[-+]?\d+", cleaned):
            return int(cleaned)
        raise ValueError(f"Cannot cast '{value}' to int directly.")

    if target_cls is float:
        cleaned = re.sub(r"[,\s_]", "", text)
        return float(cleaned)

    if target_cls in (list, dict):
        try:
            parsed = json.loads(text)
        except Exception:
            parsed = ast.literal_eval(text)
        if isinstance(parsed, target_cls):
            return parsed
        raise ValueError(f"Parsed literal is not of type {target_cls.__name__}.")

    return target_cls(value)


def _strip_code_fences(raw: str) -> str:
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if len(lines) >= 2:
            if lines[-1].strip() == "```":
                lines = lines[1:-1]
            else:
                lines = lines[1:]
            cleaned = "\n".join(lines).strip()
    return cleaned


def smart_convert(ctx: Context, text: Any, target_type: type | str) -> Any:
    """Tiered type conversion with 1B literal rewrite fallback validated by ast.literal_eval."""
    target_cls = _resolve_target_type(target_type)
    try:
        return _direct_cast(text, target_cls)
    except Exception:
        pass

    prompt = (
        f"Convert the following input into a valid Python {target_cls.__name__} literal.\n"
        f"Output ONLY the Python literal with no explanation.\n"
        f"Input: {text!r}"
    )
    rewritten = ctx.call_target_lm(prompt, temperature=0.0, role="converter")
    literal_text = _strip_code_fences(rewritten)
    evaluated = ast.literal_eval(literal_text)
    return _direct_cast(evaluated, target_cls)


def load(ctx: Context, query: str, d_type: type | str = str) -> Any:
    """Retrieve a parameter from layered Context by semantic description and coerce its type."""
    _, raw_val = ctx.resolve_semantic(query)
    return smart_convert(ctx, raw_val, d_type)


def extract(ctx: Context, text: str, prompt: str, d_type: type | str = str) -> Any:
    """Extract a specific typed value from raw text using the local 1B model at temperature=0.0."""
    target_cls = _resolve_target_type(d_type)
    full_prompt = (
        f"Task: {prompt}\n"
        f"Target type: {target_cls.__name__}\n"
        f"Source text:\n{text}\n"
        f"Return only the extracted value."
    )
    raw_output = ctx.call_target_lm(full_prompt, temperature=0.0, role="extractor")
    return smart_convert(ctx, _strip_code_fences(raw_output), target_cls)


def lm(ctx: Context, prompt: str) -> str:
    """Invoke the local 1B target model for lightweight classification or formatting."""
    return ctx.call_target_lm(prompt, temperature=0.0, role="target").strip()


def do(
    ctx: Context,
    intent_description: str,
    payloads: dict[str, Any] | None = None,
) -> Any:
    """Resolve and execute a semantic sub-function inside an isolated Context layer."""
    if ctx.registry is None:
        raise RuntimeError("Context has no attached SemanticRegistry for 'do' dispatch.")
    match = ctx.registry.find_best(intent_description, project_slug=ctx.project_slug)
    if match is None:
        raise LookupError(
            f"No semantic sub-function matched intent '{intent_description}'."
        )
    entry, _ = match
    with ctx.scope(initial=payloads or {}):
        return ctx.registry.execute_entry(entry, ctx)


def invoke_tool(
    ctx: Context,
    tool_name: str,
    args: dict[str, Any] | None = None,
) -> Any:
    """Execute a registered local tool or send an HMAC-SHA256 signed webhook to the client app."""
    payload_args = args or {}
    if tool_name in ctx.tools:
        tool_fn = ctx.tools[tool_name]
        return tool_fn(**payload_args)

    if not ctx.tool_callback_url:
        raise KeyError(
            f"Tool '{tool_name}' is not registered locally and no tool_callback_url is set."
        )

    body_dict = {
        "project": ctx.project_slug,
        "request_id": ctx.request_id,
        "tool": tool_name,
        "args": payload_args,
    }
    body_bytes = json.dumps(body_dict, separators=(",", ":"), sort_keys=True).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if ctx.tool_callback_secret:
        digest = hmac.new(
            ctx.tool_callback_secret.encode("utf-8"),
            body_bytes,
            hashlib.sha256,
        ).hexdigest()
        headers["X-ARC-Signature"] = f"sha256={digest}"

    req = urllib.request.Request(
        ctx.tool_callback_url,
        data=body_bytes,
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15.0) as resp:
        resp_data = json.loads(resp.read().decode("utf-8"))
        if isinstance(resp_data, dict) and "result" in resp_data:
            return resp_data["result"]
        return resp_data


def narrate(ctx: Context, message: str) -> None:
    """Record a human-readable reasoning step for audit and debugging."""
    ctx.narration_trace.append(str(message))
