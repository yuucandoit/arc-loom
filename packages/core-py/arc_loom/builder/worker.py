from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from arc_loom.runtime.context import Context, LLMCallResult, LLMHandler
from arc_loom.runtime.registry import CacheEntry, SemanticRegistry
from arc_loom.telemetry.ledger import TelemetryLedger
from arc_loom.telemetry.pricing import PricingCatalog

_FORBIDDEN_AST_CALLS = {
    "eval",
    "exec",
    "compile",
    "open",
    "__import__",
    "globals",
    "locals",
    "getattr",
    "setattr",
    "delattr",
}

_FORBIDDEN_MODULES = {
    "os",
    "sys",
    "subprocess",
    "socket",
    "shutil",
    "pathlib",
    " ctypes",
    "importlib",
}


class AxisValidationError(Exception):
    """Raised when a synthesized semantic script fails AST safety or 1B runtime validation."""


@dataclass
class BuilderSpec:
    entry_key: str
    intent_description: str
    param_schema: dict[str, str]
    extraction_hints: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class SFTTrainingExample:
    project_slug: str
    entry_key: str
    role: str
    prompt: str
    completion: str


@dataclass
class BuildArtifact:
    request_id: str
    project_slug: str
    entry_key: str
    intent_description: str
    source_code: str
    builder_cost_usd: float
    validation_cost_usd: float
    attempts: int
    passed: bool
    cache_entry: CacheEntry | None = None
    error_message: str | None = None
    perturbation_passed: int = 0
    sft_examples: list[SFTTrainingExample] = field(default_factory=list)


def export_sft_jsonl(
    examples: list[SFTTrainingExample],
    output_path: str | Path | None = None,
) -> str:
    """Export validated 1B Target/Extractor/Converter traces as JSONL for continual SFT/LoRA training."""
    lines: list[str] = []
    for ex in examples:
        record = {
            "project": ex.project_slug,
            "entry_key": ex.entry_key,
            "role": ex.role,
            "messages": [
                {"role": "user", "content": ex.prompt},
                {"role": "assistant", "content": ex.completion},
            ],
        }
        lines.append(json.dumps(record, ensure_ascii=False))
    content = "\n".join(lines) + ("\n" if lines else "")
    if output_path is not None:
        Path(output_path).write_text(content, encoding="utf-8")
    return content


def validate_semantic_ast(source_code: str) -> None:
    try:
        tree = ast.parse(source_code)
    except SyntaxError as exc:
        raise AxisValidationError(f"Syntax error in generated code: {exc}") from exc

    has_run_fn = False
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            mod_names = [alias.name.split(".")[0] for alias in node.names]
            if isinstance(node, ast.ImportFrom) and node.module:
                mod_names.append(node.module.split(".")[0])
            for mod in mod_names:
                if mod in _FORBIDDEN_MODULES:
                    raise AxisValidationError(
                        f"Forbidden module import '{mod}' in semantic script."
                    )
        elif isinstance(node, ast.FunctionDef) and node.name in {"run", "semantic_fn"}:
            has_run_fn = True
            if len(node.args.args) != 1:
                raise AxisValidationError(
                    f"Function '{node.name}' must accept exactly 1 argument (ctx)."
                )
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id in _FORBIDDEN_AST_CALLS:
                raise AxisValidationError(
                    f"Forbidden builtin call '{node.func.id}' in semantic script."
                )
        elif isinstance(node, ast.Attribute):
            if node.attr.startswith("__") and node.attr.endswith("__"):
                raise AxisValidationError(
                    f"Forbidden dunder attribute access '{node.attr}'."
                )

    if not has_run_fn:
        raise AxisValidationError(
            "Generated code must define 'def run(ctx):' or 'def semantic_fn(ctx):'."
        )


def _extract_code_block(raw_text: str) -> str:
    text = raw_text.strip()
    fence_match = re.search(r"```(?:python)?\s*\n(.*?)```", text, re.DOTALL)
    if fence_match:
        return fence_match.group(1).strip()
    return text


def _slugify_intent(text: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    tokens = [t for t in cleaned.split("_") if t][:6]
    return "_".join(tokens) or "semantic_fn"


class AsyncBuilderWorker:
    """5-phase CPU-ARC background builder with AST safety check and 1B validation loop."""

    def __init__(
        self,
        *,
        registry: SemanticRegistry,
        ledger: TelemetryLedger,
        pricing: PricingCatalog | None = None,
        builder_model: str = "fable-5.1",
        target_model: str = "local/llama-3.2-1b",
        builder_handler: LLMHandler | None = None,
        target_handler: LLMHandler | None = None,
        tools_provider: Callable[[str], dict[str, Callable[..., Any]]] | None = None,
        tool_descriptions_provider: Callable[[str], dict[str, str]] | None = None,
        max_retries: int = 2,
        validator_fn: Callable[[Any, Any], bool] | None = None,
        enable_perturbation_check: bool = False,
    ) -> None:
        self.registry = registry
        self.ledger = ledger
        self.pricing = pricing or ledger.pricing
        self.builder_model = builder_model
        self.target_model = target_model
        self.builder_handler = builder_handler
        self.target_handler = target_handler
        self.tools_provider = tools_provider
        self.tool_descriptions_provider = tool_descriptions_provider
        self.max_retries = max_retries
        self.validator_fn = validator_fn or self._default_output_match
        self.enable_perturbation_check = enable_perturbation_check
        self.collected_sft_examples: list[SFTTrainingExample] = []

    @staticmethod
    def _default_output_match(expected: Any, candidate: Any) -> bool:
        if candidate is None:
            return False
        exp_str = str(expected).strip().lower()
        cand_str = str(candidate).strip().lower()
        if not cand_str:
            return False
        if exp_str == cand_str or exp_str in cand_str or cand_str in exp_str:
            return True
        exp_nums = re.findall(r"\d+(?:[.,]\d+)?", exp_str)
        cand_nums = re.findall(r"\d+(?:[.,]\d+)?", cand_str)
        if exp_nums and exp_nums == cand_nums:
            return True
        return False

    def _call_builder(
        self,
        *,
        request_id: str,
        project_slug: str,
        prompt: str,
    ) -> tuple[str, float]:
        if self.builder_handler is None:
            raise RuntimeError("No builder_handler configured on AsyncBuilderWorker.")
        res = self.builder_handler(prompt, 0.0, "builder")
        rec = self.ledger.record(
            request_id=request_id,
            project_slug=project_slug,
            execution_path="arc_miss_build",
            role="builder",
            model_name=res.model_name or self.builder_model,
            input_tokens=res.input_tokens,
            output_tokens=res.output_tokens,
            cache_read_tokens=res.cache_read_tokens,
            cache_write_tokens=res.cache_write_tokens,
            latency_ms=res.latency_ms,
        )
        return res.text, rec.cost_usd

    def _phase1_decompose(
        self,
        *,
        request_id: str,
        project_slug: str,
        user_prompt: str,
        source_output: str,
        tool_descriptions: dict[str, str],
    ) -> tuple[BuilderSpec, float]:
        decompose_prompt = (
            "PHASE 1: Decompose user query into a reusable CPU-ARC semantic specification.\n"
            "Return JSON with keys: entry_key, intent_description, param_schema, extraction_hints.\n"
            f"Available tools: {json.dumps(tool_descriptions)}\n"
            f"User query: {user_prompt}\n"
            f"Reference output: {source_output}"
        )
        raw, cost = self._call_builder(
            request_id=request_id,
            project_slug=project_slug,
            prompt=decompose_prompt,
        )
        cleaned = _extract_code_block(raw)
        try:
            parsed = json.loads(cleaned)
            spec = BuilderSpec(
                entry_key=str(parsed.get("entry_key") or _slugify_intent(user_prompt)),
                intent_description=str(parsed.get("intent_description") or user_prompt),
                param_schema=dict(parsed.get("param_schema") or {}),
                extraction_hints=dict(parsed.get("extraction_hints") or {}),
            )
        except Exception:
            spec = BuilderSpec(
                entry_key=_slugify_intent(user_prompt),
                intent_description=user_prompt,
                param_schema={"query": "str"},
            )
        return spec, cost

    def _phase2_synthesize_code(
        self,
        *,
        request_id: str,
        project_slug: str,
        spec: BuilderSpec,
        user_prompt: str,
        source_output: str,
        tool_descriptions: dict[str, str],
        previous_error: str | None = None,
    ) -> tuple[str, float]:
        feedback_section = (
            f"\nPREVIOUS VALIDATION ERROR TO FIX:\n{previous_error}\n"
            if previous_error
            else ""
        )
        synth_prompt = (
            "PHASE 2: Write a deterministic CPU-ARC Python semantic function `def run(ctx):`.\n"
            "Allowed primitives: load(ctx, query, d_type), extract(ctx, text, prompt, d_type), "
            "lm(ctx, prompt), smart_convert(ctx, text, target_type), do(ctx, intent, payloads), "
            "invoke_tool(ctx, tool_name, args), narrate(ctx, message).\n"
            "Do NOT import os, sys, subprocess, or call eval/exec/open.\n"
            f"Specification: {json.dumps(spec.__dict__)}\n"
            f"Available tools: {json.dumps(tool_descriptions)}\n"
            f"Sample query: {user_prompt}\n"
            f"Expected reference output: {source_output}"
            f"{feedback_section}"
        )
        raw, cost = self._call_builder(
            request_id=request_id,
            project_slug=project_slug,
            prompt=synth_prompt,
        )
        return _extract_code_block(raw), cost

    def build_from_miss(self, job: dict[str, Any]) -> BuildArtifact:
        request_id = str(job["request_id"])
        project_slug = str(job["project_slug"])
        user_prompt = str(job["prompt"])
        params = dict(job.get("params") or {})
        source_output = str(job["source_output"])
        source_cost_usd = float(job.get("source_cost_usd") or 0.035)

        tools = self.tools_provider(project_slug) if self.tools_provider else {}
        tool_descs = (
            self.tool_descriptions_provider(project_slug)
            if self.tool_descriptions_provider
            else {}
        )

        total_builder_cost = 0.0
        total_validation_cost = 0.0

        spec, p1_cost = self._phase1_decompose(
            request_id=request_id,
            project_slug=project_slug,
            user_prompt=user_prompt,
            source_output=source_output,
            tool_descriptions=tool_descs,
        )
        total_builder_cost += p1_cost

        last_error: str | None = None
        last_code = ""

        for attempt in range(1, self.max_retries + 2):
            code, p2_cost = self._phase2_synthesize_code(
                request_id=request_id,
                project_slug=project_slug,
                spec=spec,
                user_prompt=user_prompt,
                source_output=source_output,
                tool_descriptions=tool_descs,
                previous_error=last_error,
            )
            total_builder_cost += p2_cost
            last_code = code

            try:
                validate_semantic_ast(code)
                candidate_entry = CacheEntry(
                    entry_key=spec.entry_key,
                    project_slug=project_slug,
                    intent_description=spec.intent_description,
                    param_schema=spec.param_schema,
                    source_code=code,
                )

                attempt_val_cost = 0.0
                attempt_sft: list[SFTTrainingExample] = []

                def _make_target_recorder(
                    sft_sink: list[SFTTrainingExample],
                ) -> Callable[[str, float, str], LLMCallResult]:
                    def _wrapped(p: str, temp: float, r: str) -> LLMCallResult:
                        if self.target_handler is None:
                            raise RuntimeError("No target_handler configured on AsyncBuilderWorker.")
                        res = self.target_handler(p, temp, r)
                        sft_sink.append(
                            SFTTrainingExample(
                                project_slug=project_slug,
                                entry_key=spec.entry_key,
                                role=r,
                                prompt=p,
                                completion=res.text,
                            )
                        )
                        return res

                    return _wrapped

                def on_val_telemetry(role: str, res: LLMCallResult) -> None:
                    nonlocal attempt_val_cost
                    rec = self.ledger.record(
                        request_id=request_id,
                        project_slug=project_slug,
                        execution_path="arc_miss_build",
                        role=role,
                        model_name=res.model_name or self.target_model,
                        input_tokens=res.input_tokens,
                        output_tokens=res.output_tokens,
                        cache_read_tokens=res.cache_read_tokens,
                        cache_write_tokens=res.cache_write_tokens,
                        latency_ms=res.latency_ms,
                    )
                    attempt_val_cost += rec.cost_usd

                initial_ctx_data = {"query": user_prompt, "prompt": user_prompt}
                initial_ctx_data.update(params)
                test_ctx = Context(
                    initial=initial_ctx_data,
                    descriptions={
                        "query": "raw user prompt query text",
                        "prompt": "raw user prompt query text",
                    },
                    project_slug=project_slug,
                    request_id=request_id,
                    target_model=self.target_model,
                    llm_handler=_make_target_recorder(attempt_sft),
                    telemetry_hook=on_val_telemetry,
                    tools=tools,
                    registry=self.registry,
                )

                candidate_output = self.registry.execute_entry(candidate_entry, test_ctx)
                total_validation_cost += attempt_val_cost

                if not self.validator_fn(source_output, candidate_output):
                    raise AxisValidationError(
                        f"1B validation output '{candidate_output}' did not match reference '{source_output}'."
                    )

                perturbation_queries = list(job.get("perturbation_queries") or [])
                if not perturbation_queries and self.enable_perturbation_check:
                    p4b_raw, p4b_cost = self._call_builder(
                        request_id=request_id,
                        project_slug=project_slug,
                        prompt=(
                            "PHASE 4B: Generate a JSON list of 2 paraphrased/perturbed user queries "
                            f"for testing robustness of:\n{user_prompt}"
                        ),
                    )
                    total_builder_cost += p4b_cost
                    try:
                        parsed_q = json.loads(_extract_code_block(p4b_raw))
                        if isinstance(parsed_q, list):
                            perturbation_queries = [str(q) for q in parsed_q[:2]]
                    except Exception:
                        perturbation_queries = []

                perturbation_passed = 0
                for p_query in perturbation_queries:
                    p_ctx_data = {"query": p_query, "prompt": p_query}
                    p_ctx_data.update(params)
                    p_ctx = Context(
                        initial=p_ctx_data,
                        descriptions={
                            "query": "raw user prompt query text",
                            "prompt": "raw user prompt query text",
                        },
                        project_slug=project_slug,
                        request_id=request_id,
                        target_model=self.target_model,
                        llm_handler=_make_target_recorder(attempt_sft),
                        telemetry_hook=on_val_telemetry,
                        tools=tools,
                        registry=self.registry,
                    )
                    p_out = self.registry.execute_entry(candidate_entry, p_ctx)
                    if p_out is None or not str(p_out).strip():
                        raise AxisValidationError(
                            f"Perturbation query '{p_query}' produced empty output."
                        )
                    perturbation_passed += 1

                self.collected_sft_examples.extend(attempt_sft)

                total_build_investment = round(
                    total_builder_cost + total_validation_cost, 8
                )
                registered = self.registry.register(
                    project_slug=project_slug,
                    entry_key=spec.entry_key,
                    intent_description=spec.intent_description,
                    param_schema=spec.param_schema,
                    source_code=code,
                    avg_source_cost_usd=source_cost_usd,
                    builder_cost_usd=total_build_investment,
                    persist_to_disk=self.registry.cache_dir is not None,
                )
                db_id = self.ledger.upsert_cache_entry(
                    project_slug=project_slug,
                    entry_key=spec.entry_key,
                    intent_description=spec.intent_description,
                    param_schema=spec.param_schema,
                    source_path=registered.source_path or f"memory://{spec.entry_key}.py",
                    is_compiled=False,
                    hit_count=0,
                    avg_source_cost_usd=source_cost_usd,
                    builder_cost_usd=total_build_investment,
                )
                registered.db_id = db_id

                return BuildArtifact(
                    request_id=request_id,
                    project_slug=project_slug,
                    entry_key=spec.entry_key,
                    intent_description=spec.intent_description,
                    source_code=code,
                    builder_cost_usd=round(total_builder_cost, 8),
                    validation_cost_usd=round(total_validation_cost, 8),
                    attempts=attempt,
                    passed=True,
                    cache_entry=registered,
                    perturbation_passed=perturbation_passed,
                    sft_examples=attempt_sft,
                )
            except Exception as exc:
                last_error = str(exc)

        return BuildArtifact(
            request_id=request_id,
            project_slug=project_slug,
            entry_key=spec.entry_key,
            intent_description=spec.intent_description,
            source_code=last_code,
            builder_cost_usd=round(total_builder_cost, 8),
            validation_cost_usd=round(total_validation_cost, 8),
            attempts=self.max_retries + 1,
            passed=False,
            error_message=last_error,
        )

    def drain_queue(self, queue: list[dict[str, Any]]) -> list[BuildArtifact]:
        artifacts: list[BuildArtifact] = []
        while queue:
            job = queue.pop(0)
            artifacts.append(self.build_from_miss(job))
        return artifacts
