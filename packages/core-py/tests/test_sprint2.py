from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from arc_loom import (
    ArcGatewayServer,
    ArcLoom,
    LLMCallResult,
)


def _mock_target_1b(prompt: str, temperature: float, role: str) -> LLMCallResult:
    assert temperature == 0.0
    low = prompt.lower()
    if role == "extractor":
        if "task: extract expense category" in low:
            return LLMCallResult("makan", 40, 4, "local/llama-3.2-1b", 10.5)
        if "task: extract month" in low:
            if "desember" in low:
                return LLMCallResult("2026-12", 42, 6, "local/llama-3.2-1b", 11.0)
            return LLMCallResult("2026-11", 42, 6, "local/llama-3.2-1b", 11.0)
    return LLMCallResult("OK", 30, 3, "local/llama-3.2-1b", 8.0)


def _mock_source_model(prompt: str, temperature: float, role: str) -> LLMCallResult:
    return LLMCallResult(
        text="Total pengeluaran kategori makan pada 2026-11 adalah Rp 2450000",
        input_tokens=950,
        output_tokens=320,
        model_name="anthropic/claude-opus-4.5",
        latency_ms=1380.0,
    )


class TestSprint2BuilderAndGateway(unittest.TestCase):
    def test_5_phase_builder_and_1b_validation_loop(self) -> None:
        builder_calls = 0

        def _mock_builder_model(prompt: str, temperature: float, role: str) -> LLMCallResult:
            nonlocal builder_calls
            builder_calls += 1
            if "PHASE 1:" in prompt:
                spec_json = json.dumps(
                    {
                        "entry_key": "query_monthly_category_expense",
                        "intent_description": "Berapa total pengeluaran kategori makan bulan November 2026?",
                        "param_schema": {"month": "str", "category": "str"},
                        "extraction_hints": {
                            "month": "YYYY-MM format month",
                            "category": "expense category name",
                        },
                    }
                )
                return LLMCallResult(
                    spec_json, 400, 120, "anthropic/claude-opus-4.5", 650.0
                )

            if "PREVIOUS VALIDATION ERROR TO FIX" not in prompt:
                unsafe_code = """```python
import os
def run(ctx):
    return os.getcwd()
```"""
                return LLMCallResult(
                    unsafe_code, 550, 90, "anthropic/claude-opus-4.5", 500.0
                )

            valid_code = """```python
def run(ctx):
    raw_q = load(ctx, "raw user prompt query text", str)
    narrate(ctx, "Mengekstrak bulan dan kategori pengeluaran dengan model 1B")
    month = extract(ctx, raw_q, "Extract month in YYYY-MM format", str)
    category = extract(ctx, raw_q, "Extract expense category", str)
    total = invoke_tool(ctx, "getMonthlyExpense", {"month": month, "category": category})
    narrate(ctx, f"Hasil query database: Rp {total}")
    return f"Total pengeluaran kategori {category} pada {month} adalah Rp {total}"
```"""
            return LLMCallResult(
                valid_code, 620, 180, "anthropic/claude-opus-4.5", 780.0
            )

        with TemporaryDirectory() as tmp_dir:
            arc = ArcLoom(
                project="finansia",
                mode="active",
                source_model="anthropic/claude-opus-4.5",
                builder_model="anthropic/claude-opus-4.5",
                target_model="local/llama-3.2-1b",
                cache_dir=Path(tmp_dir),
                source_handler=_mock_source_model,
                builder_handler=_mock_builder_model,
                target_handler=_mock_target_1b,
            )

            @arc.tool("Ambil total pengeluaran bulanan berdasarkan bulan dan kategori")
            def getMonthlyExpense(month: str, category: str = "makan") -> int:
                if month == "2026-12":
                    return 3100000
                return 2450000

            miss_res = arc.run(
                "Berapa total pengeluaran kategori makan bulan November 2026?"
            )
            self.assertEqual(miss_res.execution_path, "arc_miss_build")
            self.assertEqual(len(arc.builder_queue), 1)

            artifacts = arc.process_builder_queue(max_retries=2)
            self.assertEqual(len(artifacts), 1)
            art = artifacts[0]
            self.assertTrue(art.passed)
            self.assertEqual(art.attempts, 2)
            self.assertEqual(art.entry_key, "query_monthly_category_expense")
            self.assertTrue(
                (Path(tmp_dir) / "finansia" / "query_monthly_category_expense.py").exists()
            )

            hit_res = arc.run(
                "Berapa total pengeluaran kategori makan bulan Desember 2026?"
            )
            self.assertEqual(hit_res.execution_path, "arc_hit")
            self.assertEqual(
                hit_res.output,
                "Total pengeluaran kategori makan pada 2026-12 adalah Rp 3100000",
            )
            self.assertEqual(len(hit_res.narration_trace), 2)
            self.assertGreater(hit_res.saved_usd, 0.03)

    def test_openai_compatible_gateway_and_telemetry_endpoints(self) -> None:
        def _simple_builder(prompt: str, temperature: float, role: str) -> LLMCallResult:
            if "PHASE 1:" in prompt:
                return LLMCallResult(
                    json.dumps(
                        {
                            "entry_key": "calc_food_budget",
                            "intent_description": "Berapa total pengeluaran kategori makan bulan November 2026?",
                            "param_schema": {"month": "str"},
                        }
                    ),
                    300,
                    90,
                    "anthropic/claude-opus-4.5",
                    400.0,
                )
            code = """```python
def run(ctx):
    raw_q = load(ctx, "raw user prompt query text", str)
    month = extract(ctx, raw_q, "Extract month in YYYY-MM", str)
    return f"Total pengeluaran kategori makan pada {month} adalah Rp 2450000"
```"""
            return LLMCallResult(code, 450, 140, "anthropic/claude-opus-4.5", 520.0)

        gw = ArcGatewayServer(
            default_source_model="anthropic/claude-opus-4.5",
            default_builder_model="anthropic/claude-opus-4.5",
            default_target_model="local/llama-3.2-1b",
            source_handler=_mock_source_model,
            builder_handler=_simple_builder,
            target_handler=_mock_target_1b,
        )

        reg_resp = gw.handle_register_tools(
            {
                "project": "finansia-laravel",
                "toolCallbackUrl": "http://localhost:8000/api/internal/arc-tools",
                "tools": {"getMonthlyExpense": "Query monthly expense from database"},
            }
        )
        self.assertIn("getMonthlyExpense", reg_resp["registeredTools"])

        miss_completion = gw.handle_chat_completions(
            payload={
                "model": "anthropic/claude-opus-4.5",
                "messages": [
                    {
                        "role": "user",
                        "content": "Berapa total pengeluaran kategori makan bulan November 2026?",
                    }
                ],
            },
            headers={
                "X-ARC-Project": "finansia-laravel",
                "X-ARC-Mode": "active",
            },
        )
        self.assertEqual(miss_completion["arc_telemetry"]["path"], "arc_miss_build")

        build_status = gw.handle_build_queue("finansia-laravel")
        self.assertEqual(build_status["processed"], 1)
        self.assertTrue(build_status["artifacts"][0]["passed"])

        hit_completion = gw.handle_chat_completions(
            payload={
                "model": "anthropic/claude-opus-4.5",
                "messages": [
                    {
                        "role": "user",
                        "content": "Berapa total pengeluaran kategori makan bulan November 2026?",
                    }
                ],
            },
            headers={
                "X-ARC-Project": "finansia-laravel",
                "X-ARC-Mode": "active",
            },
        )
        self.assertEqual(hit_completion["arc_telemetry"]["path"], "arc_hit")
        self.assertEqual(
            hit_completion["arc_telemetry"]["matchedEntryKey"], "calc_food_budget"
        )
        self.assertGreater(hit_completion["arc_telemetry"]["savedUsd"], 0.03)

        telem = gw.handle_project_telemetry("finansia-laravel")
        self.assertEqual(telem["totalRequests"], 2)
        self.assertEqual(telem["arcHits"], 1)
        self.assertEqual(len(telem["functions"]), 1)
        self.assertEqual(telem["functions"][0]["entryKey"], "calc_food_budget")
        self.assertIsNotNone(telem["functions"][0]["breakEvenCalls"])


if __name__ == "__main__":
    unittest.main()
