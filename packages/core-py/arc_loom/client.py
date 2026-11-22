from __future__ import annotations

import json
import time
import urllib.request
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from arc_loom.builder.worker import AsyncBuilderWorker, BuildArtifact
from arc_loom.runtime.compiler import CompilationResult, StaticCompiler
from arc_loom.runtime.context import Context, LLMCallResult, LLMHandler
from arc_loom.runtime.registry import CacheEntry, EmbeddingProvider, SemanticRegistry
from arc_loom.telemetry.ledger import ProjectConfig, TelemetryLedger, TelemetryRecord
from arc_loom.telemetry.pricing import PricingCatalog


def tool(description: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Standalone decorator attaching semantic tool metadata to a Python function."""

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        setattr(fn, "_arc_tool_description", description)
        return fn

    return decorator


@dataclass
class ArcResponse:
    output: Any
    execution_path: str
    request_id: str
    narration_trace: list[str] = field(default_factory=list)
    actual_cost_usd: float = 0.0
    counterfactual_cost_usd: float = 0.0
    saved_usd: float = 0.0
    latency_ms: float = 0.0
    matched_entry_key: str | None = None
    similarity_score: float = 0.0
    telemetry_records: list[TelemetryRecord] = field(default_factory=list)


def _openai_compatible_call(
    endpoint: str,
    model_name: str,
    prompt: str,
    temperature: float,
    api_key: str | None = None,
) -> LLMCallResult:
    url = endpoint.rstrip("/") + "/chat/completions"
    payload = {
        "model": model_name,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature,
    }
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    t0 = time.perf_counter()
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30.0) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    latency = (time.perf_counter() - t0) * 1000.0

    choice = data["choices"][0]["message"]["content"]
    usage = data.get("usage", {})
    in_tok = int(usage.get("prompt_tokens", max(1, len(prompt.split()))))
    out_tok = int(usage.get("completion_tokens", max(1, len(str(choice).split()))))
    return LLMCallResult(
        text=str(choice),
        input_tokens=in_tok,
        output_tokens=out_tok,
        model_name=model_name,
        latency_ms=latency,
    )


class ArcLoom:
    """Embedded and gateway-compatible CPU-ARC Orchestrator client for Python."""

    def __init__(
        self,
        *,
        project: str,
        mode: str = "active",
        source_model: str = "fable-5.1",
        builder_model: str = "fable-5.1",
        target_endpoint: str = "http://localhost:11434/v1",
        target_model: str = "llama-3.2-1b",
        source_endpoint: str | None = None,
        source_api_key: str | None = None,
        similarity_threshold: float = 0.82,
        tool_callback_url: str | None = None,
        tool_callback_secret: str | None = None,
        cache_dir: str | Path | None = None,
        sqlite_path: str = ":memory:",
        database_url: str | None = None,
        embedding_fn: EmbeddingProvider | None = None,
        registry: SemanticRegistry | None = None,
        ledger: TelemetryLedger | None = None,
        pricing: PricingCatalog | None = None,
        source_handler: LLMHandler | None = None,
        target_handler: LLMHandler | None = None,
        builder_handler: LLMHandler | None = None,
    ) -> None:
        if mode not in {"bypass", "shadow", "active"}:
            raise ValueError(f"Unsupported routing mode '{mode}'.")
        self.project = project
        self.mode = mode
        self.source_model = source_model
        self.builder_model = builder_model
        self.target_endpoint = target_endpoint
        self.target_model = target_model
        self.source_endpoint = source_endpoint
        self.source_api_key = source_api_key
        self.similarity_threshold = similarity_threshold
        self.tool_callback_url = tool_callback_url
        self.tool_callback_secret = tool_callback_secret

        self.pricing = pricing or PricingCatalog()
        self.ledger = ledger or TelemetryLedger(
            sqlite_path=sqlite_path,
            pricing=self.pricing,
            database_url=database_url,
        )
        self.registry = registry or SemanticRegistry(
            cache_dir=cache_dir,
            default_threshold=similarity_threshold,
            embedding_fn=embedding_fn,
        )
        self.tools: dict[str, Callable[..., Any]] = {}
        self.tool_descriptions: dict[str, str] = {}
        self.builder_queue: list[dict[str, Any]] = []

        self._source_handler = source_handler
        self._target_handler = target_handler
        self._builder_handler = builder_handler or source_handler

        self.ledger.upsert_project(
            ProjectConfig(
                slug=self.project,
                source_model=self.source_model,
                builder_model=self.builder_model,
                target_model=self.target_model,
                routing_mode=self.mode,
                similarity_threshold=self.similarity_threshold,
                tool_callback_url=self.tool_callback_url,
                tool_callback_secret=self.tool_callback_secret,
            )
        )

    def tool(
        self,
        description: str = "",
        *,
        name: str | None = None,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
            tool_name = name or fn.__name__
            resolved_desc = description or getattr(fn, "_arc_tool_description", "")
            self.tools[tool_name] = fn
            self.tool_descriptions[tool_name] = resolved_desc
            return fn

        return decorator

    def register_semantic_function(
        self,
        *,
        entry_key: str,
        intent_description: str,
        param_schema: dict[str, Any] | None = None,
        source_code: str = "",
        callable_fn: Callable[[Context], Any] | None = None,
        avg_source_cost_usd: float = 0.035,
        builder_cost_usd: float = 0.08,
        persist_to_disk: bool = False,
    ) -> CacheEntry:
        entry = self.registry.register(
            project_slug=self.project,
            entry_key=entry_key,
            intent_description=intent_description,
            param_schema=param_schema,
            source_code=source_code,
            callable_fn=callable_fn,
            avg_source_cost_usd=avg_source_cost_usd,
            builder_cost_usd=builder_cost_usd,
            persist_to_disk=persist_to_disk,
        )
        db_id = self.ledger.upsert_cache_entry(
            project_slug=self.project,
            entry_key=entry_key,
            intent_description=intent_description,
            param_schema=entry.param_schema,
            source_path=entry.source_path or f"memory://{entry_key}.py",
            is_compiled=entry.is_compiled,
            hit_count=entry.hit_count,
            avg_source_cost_usd=avg_source_cost_usd,
            builder_cost_usd=builder_cost_usd,
        )
        entry.db_id = db_id
        return entry

    def process_builder_queue(self, max_retries: int = 2) -> list[BuildArtifact]:
        worker = AsyncBuilderWorker(
            registry=self.registry,
            ledger=self.ledger,
            pricing=self.pricing,
            builder_model=self.builder_model,
            target_model=self.target_model,
            builder_handler=self._builder_handler,
            target_handler=self._call_target,
            tools_provider=lambda _: self.tools,
            tool_descriptions_provider=lambda _: self.tool_descriptions,
            max_retries=max_retries,
        )
        return worker.drain_queue(self.builder_queue)

    def compile_function(
        self,
        entry_key: str,
        *,
        param_descriptions: dict[str, str] | None = None,
        persist_to_disk: bool = True,
    ) -> CompilationResult:
        compiler = StaticCompiler(self.registry)
        res = compiler.compile_entry(
            entry_key,
            param_descriptions=param_descriptions,
            persist_to_disk=persist_to_disk,
        )
        entry = self.registry.get(entry_key)
        if entry is not None:
            self.ledger.upsert_cache_entry(
                project_slug=self.project,
                entry_key=entry.entry_key,
                intent_description=entry.intent_description,
                param_schema=entry.param_schema,
                source_path=res.source_path,
                is_compiled=True,
                hit_count=entry.hit_count,
                avg_source_cost_usd=entry.avg_source_cost_usd,
                builder_cost_usd=entry.builder_cost_usd,
            )
        return res

    def _call_source(self, prompt: str) -> LLMCallResult:
        if self._source_handler is not None:
            return self._source_handler(prompt, 0.0, "source")
        if self.source_endpoint:
            return _openai_compatible_call(
                self.source_endpoint,
                self.source_model,
                prompt,
                0.0,
                api_key=self.source_api_key,
            )
        raise RuntimeError(
            "No source_handler or source_endpoint configured for Source Model call."
        )

    def _call_target(self, prompt: str, temperature: float, role: str) -> LLMCallResult:
        if self._target_handler is not None:
            return self._target_handler(prompt, temperature, role)
        return _openai_compatible_call(
            self.target_endpoint,
            self.target_model,
            prompt,
            temperature,
        )

    def run(
        self,
        prompt: str,
        *,
        params: dict[str, Any] | None = None,
        param_descriptions: dict[str, str] | None = None,
        mode_override: str | None = None,
    ) -> ArcResponse:
        active_mode = mode_override or self.mode
        request_id = f"req-{uuid.uuid4().hex[:12]}"
        t0 = time.perf_counter()
        records: list[TelemetryRecord] = []

        if active_mode == "bypass":
            src_res = self._call_source(prompt)
            cost = self.pricing.calculate_cost_usd(
                src_res.model_name,
                input_tokens=src_res.input_tokens,
                output_tokens=src_res.output_tokens,
                cache_read_tokens=src_res.cache_read_tokens,
                cache_write_tokens=src_res.cache_write_tokens,
            )
            rec = self.ledger.record(
                request_id=request_id,
                project_slug=self.project,
                execution_path="bypass",
                role="source",
                model_name=src_res.model_name,
                input_tokens=src_res.input_tokens,
                output_tokens=src_res.output_tokens,
                cache_read_tokens=src_res.cache_read_tokens,
                cache_write_tokens=src_res.cache_write_tokens,
                cost_usd=cost,
                counterfactual_cost_usd=cost,
                latency_ms=(time.perf_counter() - t0) * 1000.0,
            )
            records.append(rec)
            return ArcResponse(
                output=src_res.text,
                execution_path="bypass",
                request_id=request_id,
                actual_cost_usd=cost,
                counterfactual_cost_usd=cost,
                saved_usd=0.0,
                latency_ms=rec.latency_ms,
                telemetry_records=records,
            )

        match = self.registry.find_best(
            prompt,
            project_slug=self.project,
            threshold=self.similarity_threshold,
        )

        if active_mode == "shadow":
            src_res = self._call_source(prompt)
            src_cost = self.pricing.calculate_cost_usd(
                src_res.model_name,
                input_tokens=src_res.input_tokens,
                output_tokens=src_res.output_tokens,
                cache_read_tokens=src_res.cache_read_tokens,
                cache_write_tokens=src_res.cache_write_tokens,
            )
            matched_key: str | None = None
            sim_score = 0.0
            narration: list[str] = []

            if match is not None:
                entry, sim_score = match
                matched_key = entry.entry_key
                ctx = self._build_context(
                    request_id=request_id,
                    prompt=prompt,
                    params=params,
                    param_descriptions=param_descriptions,
                    execution_path="shadow",
                    cache_entry_id=entry.db_id,
                    records=records,
                )
                self.registry.execute_entry(entry, ctx)
                self.ledger.increment_cache_hit(entry.entry_key)
                narration = list(ctx.narration_trace)

            src_rec = self.ledger.record(
                request_id=request_id,
                project_slug=self.project,
                execution_path="shadow",
                role="source",
                model_name=src_res.model_name,
                input_tokens=src_res.input_tokens,
                output_tokens=src_res.output_tokens,
                cache_read_tokens=src_res.cache_read_tokens,
                cache_write_tokens=src_res.cache_write_tokens,
                cost_usd=src_cost,
                counterfactual_cost_usd=src_cost,
                latency_ms=(time.perf_counter() - t0) * 1000.0,
                cache_entry_id=match[0].db_id if match else None,
            )
            records.append(src_rec)
            total_actual = round(sum(r.cost_usd for r in records), 8)
            return ArcResponse(
                output=src_res.text,
                execution_path="shadow",
                request_id=request_id,
                narration_trace=narration,
                actual_cost_usd=total_actual,
                counterfactual_cost_usd=src_cost,
                saved_usd=round(src_cost - total_actual, 8),
                latency_ms=(time.perf_counter() - t0) * 1000.0,
                matched_entry_key=matched_key,
                similarity_score=sim_score,
                telemetry_records=records,
            )

        # Active mode: execute semantic cache on hit, fallback to Source + Builder queue on miss.
        if match is not None:
            entry, sim_score = match
            ctx = self._build_context(
                request_id=request_id,
                prompt=prompt,
                params=params,
                param_descriptions=param_descriptions,
                execution_path="arc_hit",
                cache_entry_id=entry.db_id,
                records=records,
            )
            output = self.registry.execute_entry(entry, ctx)
            self.ledger.increment_cache_hit(entry.entry_key)
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            cf_cost = round(entry.avg_source_cost_usd, 8)

            if records:
                # Attach the counterfactual cost to the primary cache execution record.
                first = records[0]
                if first.id is not None:
                    self.ledger.update_counterfactual_cost(first.id, cf_cost)
                records[0] = TelemetryRecord(
                    id=first.id,
                    request_id=first.request_id,
                    project_slug=first.project_slug,
                    cache_entry_id=first.cache_entry_id,
                    execution_path=first.execution_path,
                    role=first.role,
                    model_name=first.model_name,
                    input_tokens=first.input_tokens,
                    output_tokens=first.output_tokens,
                    cache_read_tokens=first.cache_read_tokens,
                    cache_write_tokens=first.cache_write_tokens,
                    cost_usd=first.cost_usd,
                    counterfactual_cost_usd=cf_cost,
                    latency_ms=first.latency_ms,
                )
            else:
                zero_rec = self.ledger.record(
                    request_id=request_id,
                    project_slug=self.project,
                    execution_path="arc_hit",
                    role="target",
                    model_name=self.target_model,
                    input_tokens=0,
                    output_tokens=0,
                    cost_usd=0.0,
                    counterfactual_cost_usd=cf_cost,
                    latency_ms=elapsed_ms,
                    cache_entry_id=entry.db_id,
                )
                records.append(zero_rec)

            actual_cost = round(sum(r.cost_usd for r in records), 8)
            return ArcResponse(
                output=output,
                execution_path="arc_hit",
                request_id=request_id,
                narration_trace=list(ctx.narration_trace),
                actual_cost_usd=actual_cost,
                counterfactual_cost_usd=cf_cost,
                saved_usd=round(cf_cost - actual_cost, 8),
                latency_ms=elapsed_ms,
                matched_entry_key=entry.entry_key,
                similarity_score=sim_score,
                telemetry_records=records,
            )

        src_res = self._call_source(prompt)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        src_cost = self.pricing.calculate_cost_usd(
            src_res.model_name,
            input_tokens=src_res.input_tokens,
            output_tokens=src_res.output_tokens,
            cache_read_tokens=src_res.cache_read_tokens,
            cache_write_tokens=src_res.cache_write_tokens,
        )
        miss_rec = self.ledger.record(
            request_id=request_id,
            project_slug=self.project,
            execution_path="arc_miss_build",
            role="source",
            model_name=src_res.model_name,
            input_tokens=src_res.input_tokens,
            output_tokens=src_res.output_tokens,
            cache_read_tokens=src_res.cache_read_tokens,
            cache_write_tokens=src_res.cache_write_tokens,
            cost_usd=src_cost,
            counterfactual_cost_usd=src_cost,
            latency_ms=elapsed_ms,
        )
        records.append(miss_rec)
        self.builder_queue.append(
            {
                "request_id": request_id,
                "project_slug": self.project,
                "prompt": prompt,
                "params": params or {},
                "source_output": src_res.text,
                "source_cost_usd": src_cost,
            }
        )
        return ArcResponse(
            output=src_res.text,
            execution_path="arc_miss_build",
            request_id=request_id,
            actual_cost_usd=src_cost,
            counterfactual_cost_usd=src_cost,
            saved_usd=0.0,
            latency_ms=elapsed_ms,
            telemetry_records=records,
        )

    def _build_context(
        self,
        *,
        request_id: str,
        prompt: str,
        params: dict[str, Any] | None,
        param_descriptions: dict[str, str] | None,
        execution_path: str,
        cache_entry_id: int | None,
        records: list[TelemetryRecord],
    ) -> Context:
        initial_data = {"query": prompt, "prompt": prompt}
        if params:
            initial_data.update(params)
        descriptions = {
            "query": "raw user prompt query text",
            "prompt": "raw user prompt query text",
        }
        if param_descriptions:
            descriptions.update(param_descriptions)

        def on_telemetry(role: str, res: LLMCallResult) -> None:
            rec = self.ledger.record(
                request_id=request_id,
                project_slug=self.project,
                execution_path=execution_path,
                role=role,
                model_name=res.model_name,
                input_tokens=res.input_tokens,
                output_tokens=res.output_tokens,
                cache_read_tokens=res.cache_read_tokens,
                cache_write_tokens=res.cache_write_tokens,
                latency_ms=res.latency_ms,
                cache_entry_id=cache_entry_id,
            )
            records.append(rec)

        return Context(
            initial=initial_data,
            descriptions=descriptions,
            project_slug=self.project,
            request_id=request_id,
            target_model=self.target_model,
            llm_handler=self._call_target,
            telemetry_hook=on_telemetry,
            tools=self.tools,
            tool_callback_url=self.tool_callback_url,
            tool_callback_secret=self.tool_callback_secret,
            registry=self.registry,
        )
