from __future__ import annotations

import os
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from arc_loom.builder.worker import AsyncBuilderWorker, BuildArtifact, export_sft_jsonl
from arc_loom.client import ArcLoom
from arc_loom.runtime.compiler import StaticCompiler
from arc_loom.runtime.context import LLMHandler
from arc_loom.runtime.registry import EmbeddingProvider, SemanticRegistry
from arc_loom.telemetry.ledger import TelemetryLedger
from arc_loom.telemetry.pricing import PricingCatalog


class ArcGatewayServer:
    """Centralized OpenAI-compatible CPU-ARC Gateway and FinOps telemetry controller."""

    def __init__(
        self,
        *,
        default_source_model: str = "fable-5.1",
        default_builder_model: str = "fable-5.1",
        default_target_model: str = "local/llama-3.2-1b",
        target_endpoint: str = "http://localhost:11434/v1",
        source_endpoint: str | None = None,
        source_api_key: str | None = None,
        cache_dir: str | Path | None = None,
        sqlite_path: str = ":memory:",
        database_url: str | None = None,
        embedding_fn: EmbeddingProvider | None = None,
        pricing: PricingCatalog | None = None,
        ledger: TelemetryLedger | None = None,
        registry: SemanticRegistry | None = None,
        source_handler: LLMHandler | None = None,
        builder_handler: LLMHandler | None = None,
        target_handler: LLMHandler | None = None,
        auto_build_on_miss: bool = False,
    ) -> None:
        self.default_source_model = default_source_model
        self.default_builder_model = default_builder_model
        self.default_target_model = default_target_model
        self.target_endpoint = target_endpoint or os.environ.get(
            "ARC_TARGET_ENDPOINT", "http://localhost:11434/v1"
        )
        self.source_endpoint = source_endpoint
        self.source_api_key = source_api_key
        resolved_cache = cache_dir or os.environ.get("ARC_CACHE_DIR")
        self.cache_dir = Path(resolved_cache) if resolved_cache else None
        resolved_db_url = database_url or os.environ.get("ARC_DATABASE_URL")
        resolved_sqlite_path = (
            os.environ.get("ARC_SQLITE_PATH", sqlite_path)
            if sqlite_path == ":memory:"
            else sqlite_path
        )
        self.pricing = pricing or PricingCatalog()
        self.ledger = ledger or TelemetryLedger(
            sqlite_path=resolved_sqlite_path,
            pricing=self.pricing,
            database_url=resolved_db_url,
        )
        self.registry = registry or SemanticRegistry(
            cache_dir=self.cache_dir,
            embedding_fn=embedding_fn,
        )
        self.source_handler = source_handler
        self.builder_handler = builder_handler
        self.target_handler = target_handler
        self.auto_build_on_miss = auto_build_on_miss
        self._projects: dict[str, ArcLoom] = {}

        self.builder_worker = AsyncBuilderWorker(
            registry=self.registry,
            ledger=self.ledger,
            pricing=self.pricing,
            builder_model=self.default_builder_model,
            target_model=self.default_target_model,
            builder_handler=self.builder_handler,
            target_handler=self.target_handler,
            tools_provider=lambda slug: self.get_or_create_project(slug).tools,
            tool_descriptions_provider=lambda slug: self.get_or_create_project(slug).tool_descriptions,
        )

    def get_or_create_project(
        self,
        project_slug: str,
        *,
        mode: str = "active",
        source_model: str | None = None,
        builder_model: str | None = None,
        target_model: str | None = None,
        tool_callback_url: str | None = None,
        tool_callback_secret: str | None = None,
    ) -> ArcLoom:
        if project_slug not in self._projects:
            self._projects[project_slug] = ArcLoom(
                project=project_slug,
                mode=mode if mode in {"bypass", "shadow", "active"} else "active",
                source_model=source_model or self.default_source_model,
                builder_model=builder_model or self.default_builder_model,
                target_endpoint=self.target_endpoint,
                target_model=target_model or self.default_target_model,
                source_endpoint=self.source_endpoint,
                source_api_key=self.source_api_key,
                tool_callback_url=tool_callback_url,
                tool_callback_secret=tool_callback_secret,
                cache_dir=self.cache_dir,
                registry=self.registry,
                ledger=self.ledger,
                pricing=self.pricing,
                source_handler=self.source_handler,
                target_handler=self.target_handler,
                builder_handler=self.builder_handler,
            )
        client = self._projects[project_slug]
        if tool_callback_url:
            client.tool_callback_url = tool_callback_url
        if tool_callback_secret:
            client.tool_callback_secret = tool_callback_secret
        return client

    def handle_chat_completions(
        self,
        payload: dict[str, Any],
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        norm_headers = {k.lower(): v for k, v in (headers or {}).items()}
        project_slug = norm_headers.get("x-arc-project", "default")
        routing_mode = norm_headers.get("x-arc-mode")
        callback_url = norm_headers.get("x-arc-tool-callback")
        callback_secret = norm_headers.get("x-arc-tool-secret")

        requested_model = payload.get("model") or self.default_source_model
        client = self.get_or_create_project(
            project_slug,
            mode=routing_mode or "active",
            source_model=requested_model,
            tool_callback_url=callback_url,
            tool_callback_secret=callback_secret,
        )

        messages = payload.get("messages") or []
        user_prompt = ""
        for msg in reversed(messages):
            if isinstance(msg, dict) and msg.get("role") == "user":
                user_prompt = str(msg.get("content", ""))
                break
        if not user_prompt and "prompt" in payload:
            user_prompt = str(payload["prompt"])

        arc_params = payload.get("arc_params")
        res = client.run(
            user_prompt,
            params=arc_params if isinstance(arc_params, dict) else None,
            mode_override=routing_mode,
        )

        build_artifacts: list[BuildArtifact] = []
        if self.auto_build_on_miss and client.builder_queue:
            build_artifacts = self.builder_worker.drain_queue(client.builder_queue)

        in_tokens = sum(r.input_tokens for r in res.telemetry_records)
        out_tokens = sum(r.output_tokens for r in res.telemetry_records)

        return {
            "id": f"chatcmpl-{uuid.uuid4().hex[:16]}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": requested_model,
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": str(res.output),
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": in_tokens,
                "completion_tokens": out_tokens,
                "total_tokens": in_tokens + out_tokens,
            },
            "arc_telemetry": {
                "requestId": res.request_id,
                "project": project_slug,
                "path": res.execution_path,
                "matchedEntryKey": res.matched_entry_key,
                "similarityScore": round(res.similarity_score, 4),
                "actualCostUsd": res.actual_cost_usd,
                "counterfactualCostUsd": res.counterfactual_cost_usd,
                "savedUsd": res.saved_usd,
                "latencyMs": round(res.latency_ms, 2),
                "narrationTrace": res.narration_trace,
                "autoBuiltEntries": [a.entry_key for a in build_artifacts if a.passed],
            },
        }

    def handle_build_queue(self, project_slug: str | None = None) -> dict[str, Any]:
        targets = (
            [self._projects[project_slug]]
            if project_slug and project_slug in self._projects
            else list(self._projects.values())
        )
        results: list[dict[str, Any]] = []
        for client in targets:
            artifacts = self.builder_worker.drain_queue(client.builder_queue)
            for art in artifacts:
                results.append(
                    {
                        "requestId": art.request_id,
                        "project": art.project_slug,
                        "entryKey": art.entry_key,
                        "passed": art.passed,
                        "attempts": art.attempts,
                        "builderCostUsd": art.builder_cost_usd,
                        "validationCostUsd": art.validation_cost_usd,
                        "errorMessage": art.error_message,
                    }
                )
        return {"processed": len(results), "artifacts": results}

    def handle_register_tools(self, payload: dict[str, Any]) -> dict[str, Any]:
        project_slug = str(payload.get("project") or "default")
        callback_url = payload.get("toolCallbackUrl")
        callback_secret = payload.get("toolCallbackSecret")
        tools_map = dict(payload.get("tools") or {})

        client = self.get_or_create_project(
            project_slug,
            tool_callback_url=callback_url,
            tool_callback_secret=callback_secret,
        )
        for tool_name, description in tools_map.items():
            client.tool_descriptions[str(tool_name)] = str(description)

        return {
            "project": project_slug,
            "registeredTools": list(client.tool_descriptions.keys()),
            "toolCallbackUrl": client.tool_callback_url,
        }

    def handle_project_telemetry(self, project_slug: str) -> dict[str, Any]:
        summary = self.ledger.get_project_savings(project_slug)
        entries = self.registry.list_entries(project_slug=project_slug)
        functions_metrics: list[dict[str, Any]] = []
        for entry in entries:
            try:
                be = self.ledger.get_break_even_metrics(entry.entry_key)
                functions_metrics.append(
                    {
                        "entryKey": be.entry_key,
                        "intentDescription": entry.intent_description,
                        "isCompiled": entry.is_compiled,
                        "sourcePath": entry.source_path,
                        "hitCount": be.hit_count,
                        "builderCostUsd": be.builder_cost_usd,
                        "avgSourceCostUsd": be.avg_source_cost_usd,
                        "avgCacheCostUsd": be.avg_cache_cost_usd,
                        "breakEvenCalls": be.break_even_calls,
                        "isProfitable": be.is_profitable,
                    }
                )
            except KeyError:
                continue

        return {
            "project": summary.project_slug,
            "totalRequests": summary.total_requests,
            "arcHits": summary.arc_hits,
            "counterfactualCostUsd": summary.counterfactual_cost_usd,
            "sourceCostUsd": summary.source_cost_usd,
            "builderCostUsd": summary.builder_cost_usd,
            "cacheExecutionCostUsd": summary.cache_execution_cost_usd,
            "actualTotalCostUsd": summary.actual_total_cost_usd,
            "netSavingsUsd": summary.net_savings_usd,
            "functions": functions_metrics,
        }

    def handle_compile_entry(self, payload: dict[str, Any]) -> dict[str, Any]:
        entry_key = str(payload.get("entryKey") or "")
        if not entry_key:
            raise ValueError("entryKey is required for static O(1) compilation.")
        entry = self.registry.get(entry_key)
        if entry is None:
            raise KeyError(f"CacheEntry '{entry_key}' not found.")
        client = self.get_or_create_project(entry.project_slug)
        res = client.compile_function(entry_key)
        return {
            "entryKey": res.entry_key,
            "isCompiled": True,
            "compiledCode": res.compiled_code,
            "resolvedSubCalls": res.resolved_sub_calls,
            "resolvedLoads": res.resolved_loads,
            "sourcePath": res.source_path,
        }

    def handle_global_telemetry(self) -> dict[str, Any]:
        projects = self.ledger.list_projects()
        entries = self.registry.list_entries()
        functions_payload: list[dict[str, Any]] = []
        compiler = StaticCompiler(self.registry)

        for entry in entries:
            try:
                be = self.ledger.get_break_even_metrics(entry.entry_key)
                avg_cache_cost = be.avg_cache_cost_usd
                hit_count = be.hit_count
            except KeyError:
                avg_cache_cost = 0.0
                hit_count = entry.hit_count

            compiled_preview = entry.source_code
            if not entry.is_compiled and entry.source_code:
                try:
                    compiled_preview = f"# Preview O(1) target for {entry.entry_key}\n" + entry.source_code
                except Exception:
                    compiled_preview = entry.source_code
            elif entry.is_compiled and entry.source_path and Path(entry.source_path).exists():
                compiled_preview = Path(entry.source_path).read_text(encoding="utf-8")

            functions_payload.append(
                {
                    "entryKey": entry.entry_key,
                    "projectSlug": entry.project_slug,
                    "intentDescription": entry.intent_description,
                    "paramSchema": entry.param_schema,
                    "isCompiled": entry.is_compiled,
                    "hitCount": hit_count,
                    "avgSourceCostUsd": entry.avg_source_cost_usd,
                    "builderCostUsd": entry.builder_cost_usd,
                    "avgCacheCostUsd": avg_cache_cost,
                    "dynamicCode": entry.source_code,
                    "compiledCode": compiled_preview,
                }
            )

        records = self.ledger.list_records(limit=200)
        ledger_rows = [
            {
                "id": r.id or idx + 1,
                "requestId": r.request_id,
                "projectSlug": r.project_slug,
                "entryKey": None,
                "executionPath": r.execution_path,
                "role": r.role,
                "modelName": r.model_name,
                "inputTokens": r.input_tokens,
                "outputTokens": r.output_tokens,
                "costUsd": r.cost_usd,
                "counterfactualCostUsd": r.counterfactual_cost_usd,
                "latencyMs": r.latency_ms,
                "timestamp": r.created_at,
            }
            for idx, r in enumerate(records)
        ]

        return {
            "projects": projects,
            "functions": functions_payload,
            "ledgerRows": ledger_rows,
        }


def create_fastapi_app(gateway: ArcGatewayServer | None = None) -> Any:
    """Create a FastAPI ASGI application wrapping ArcGatewayServer."""
    from fastapi import FastAPI, Request
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import PlainTextResponse

    gw = gateway or ArcGatewayServer(
        sqlite_path=os.environ.get("ARC_SQLITE_PATH", "./data/arc_loom.db"),
        cache_dir=os.environ.get("ARC_CACHE_DIR", "./data/caches"),
    )
    app = FastAPI(title="ARC-Loom Server", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.post("/v1/chat/completions")
    async def chat_completions(request: Request) -> dict[str, Any]:
        payload = await request.json()
        headers = dict(request.headers)
        return gw.handle_chat_completions(payload, headers)

    @app.post("/v1/arc/build")
    async def trigger_build(request: Request) -> dict[str, Any]:
        body = await request.json() if await request.body() else {}
        return gw.handle_build_queue(body.get("project"))

    @app.post("/v1/arc/compile")
    async def compile_entry(request: Request) -> dict[str, Any]:
        payload = await request.json()
        return gw.handle_compile_entry(payload)

    @app.post("/v1/arc/tools/register")
    async def register_tools(request: Request) -> dict[str, Any]:
        payload = await request.json()
        return gw.handle_register_tools(payload)

    @app.get("/v1/arc/telemetry")
    async def global_telemetry() -> dict[str, Any]:
        return gw.handle_global_telemetry()

    @app.get("/v1/arc/telemetry/{project_slug}")
    async def project_telemetry(project_slug: str) -> dict[str, Any]:
        return gw.handle_project_telemetry(project_slug)

    @app.get("/v1/arc/sft-export")
    async def export_sft() -> PlainTextResponse:
        jsonl = export_sft_jsonl(gw.builder_worker.collected_sft_examples)
        return PlainTextResponse(jsonl, media_type="application/x-ndjson")

    return app
