from __future__ import annotations

import hashlib
import hmac
import io
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from arc_loom import (
    ArcLoom,
    Context,
    LLMCallResult,
    do,
    extract,
    invoke_tool,
    lm,
    load,
    narrate,
    smart_convert,
)


def _mock_target_llm(prompt: str, temperature: float, role: str) -> LLMCallResult:
    assert temperature == 0.0
    if role == "extractor":
        if "category" in prompt.lower() or "kategori" in prompt.lower():
            return LLMCallResult("makan", 45, 4, "local/llama-3.2-1b", 12.5)
        if "month" in prompt.lower() or "bulan" in prompt.lower():
            return LLMCallResult("2026-11", 45, 6, "local/llama-3.2-1b", 14.0)
        if "days" in prompt.lower() or "hari" in prompt.lower():
            return LLMCallResult("7", 38, 3, "local/llama-3.2-1b", 10.0)
    if role == "converter":
        if "lima belas ribu" in prompt.lower():
            return LLMCallResult("15000", 50, 5, "local/llama-3.2-1b", 11.0)
        if "['bi', 'inflasi']" in prompt.lower() or "bi dan inflasi" in prompt.lower():
            return LLMCallResult("['BI', 'inflasi']", 52, 8, "local/llama-3.2-1b", 15.0)
    if role == "target":
        return LLMCallResult("POSITIF", 60, 4, "local/llama-3.2-1b", 18.0)
    return LLMCallResult("0", 20, 2, "local/llama-3.2-1b", 5.0)


def _mock_source_llm(prompt: str, temperature: float, role: str) -> LLMCallResult:
    return LLMCallResult(
        text=f"Jawaban frontier untuk: {prompt}",
        input_tokens=1200,
        output_tokens=450,
        model_name="fable-5.1",
        latency_ms=1450.0,
    )


class TestSprint1RuntimeAndTelemetry(unittest.TestCase):
    def test_layered_context_and_load_primitive(self) -> None:
        ctx = Context(
            initial={"rentang_hari": "14", "topik_berita": "suku bunga BI"},
            descriptions={
                "rentang_hari": "jumlah rentang hari pencarian artikel",
                "topik_berita": "topik utama berita ekonomi",
            },
            llm_handler=_mock_target_llm,
        )
        days = load(ctx, "jumlah hari pencarian artikel", int)
        topic = load(ctx, "topik utama berita", str)
        self.assertEqual(days, 14)
        self.assertEqual(topic, "suku bunga BI")

        with ctx.scope(
            initial={"rentang_hari": "30"},
            descriptions={"rentang_hari": "jumlah rentang hari pencarian artikel"},
        ):
            self.assertEqual(ctx.depth, 2)
            self.assertEqual(load(ctx, "jumlah hari pencarian artikel", int), 30)

        self.assertEqual(ctx.depth, 1)
        self.assertEqual(load(ctx, "jumlah hari pencarian artikel", int), 14)

    def test_smart_convert_direct_and_1b_ast_fallback(self) -> None:
        roles_called: list[str] = []

        def track_telemetry(role: str, res: LLMCallResult) -> None:
            roles_called.append(role)

        ctx = Context(llm_handler=_mock_target_llm, telemetry_hook=track_telemetry)
        self.assertEqual(smart_convert(ctx, "42,500", int), 42500)
        self.assertTrue(smart_convert(ctx, "benar", bool))
        self.assertEqual(len(roles_called), 0)

        converted_int = smart_convert(ctx, "lima belas ribu", int)
        self.assertEqual(converted_int, 15000)
        converted_list = smart_convert(ctx, "topik: bi dan inflasi", list)
        self.assertEqual(converted_list, ["BI", "inflasi"])
        self.assertEqual(roles_called, ["converter", "converter"])

    def test_extract_lm_do_and_narrate_primitives(self) -> None:
        arc = ArcLoom(
            project="ima-isa",
            mode="active",
            source_model="fable-5.1",
            builder_model="fable-5.1",
            target_model="local/llama-3.2-1b",
            source_handler=_mock_source_llm,
            target_handler=_mock_target_llm,
        )

        @arc.tool("Ambil daftar artikel berdasarkan topik dan rentang hari")
        def fetch_articles(topic: str, days: int = 7) -> list[dict[str, str]]:
            return [{"title": f"Artikel {topic} ({days}d)", "sentiment": "optimis"}]

        arc.register_semantic_function(
            entry_key="classify_article_batch",
            intent_description="klasifikasi sentimen kumpulan artikel berita",
            callable_fn=lambda c: lm(
                c, f"Klasifikasikan sentimen: {load(c, 'daftar artikel', list)}"
            ),
        )

        semantic_script = """
def run(ctx):
    raw_query = load(ctx, "raw user prompt query text", str)
    narrate(ctx, "Mengekstrak rentang hari dari prompt pengguna")
    days = extract(ctx, raw_query, "Berapa rentang hari yang diminta?", int)
    narrate(ctx, f"Memanggil tool fetch_articles untuk {days} hari")
    articles = invoke_tool(ctx, "fetch_articles", {"topic": "suku bunga BI", "days": days})
    sentiment = do(
        ctx,
        "klasifikasi sentimen kumpulan artikel berita",
        {"daftar_artikel": articles},
    )
    narrate(ctx, f"Sentimen terdeteksi: {sentiment}")
    return f"Ringkasan {len(articles)} artikel ({days} hari): {sentiment}"
"""
        with TemporaryDirectory() as tmp_dir:
            arc.registry.cache_dir = Path(tmp_dir)
            arc.register_semantic_function(
                entry_key="summarize_bi_rate_news",
                intent_description="Ringkas sentimen berita tentang suku bunga BI 7 hari terakhir",
                param_schema={"days": "int"},
                source_code=semantic_script,
                avg_source_cost_usd=0.0414,
                builder_cost_usd=0.12,
                persist_to_disk=True,
            )

            res = arc.run("Ringkas sentimen berita tentang suku bunga BI 7 hari terakhir")
            self.assertEqual(res.execution_path, "arc_hit")
            self.assertEqual(res.output, "Ringkasan 1 artikel (7 hari): POSITIF")
            self.assertEqual(len(res.narration_trace), 3)
            self.assertGreater(res.saved_usd, 0.04)

            roles = [r.role for r in res.telemetry_records]
            self.assertEqual(roles, ["extractor", "target"])

            summary = arc.ledger.get_project_savings("ima-isa")
            self.assertEqual(summary.arc_hits, 1)
            self.assertAlmostEqual(summary.counterfactual_cost_usd, 0.0414, places=6)
            self.assertGreater(summary.net_savings_usd, 0.041)

            be = arc.ledger.get_break_even_metrics("summarize_bi_rate_news")
            self.assertEqual(be.break_even_calls, 3)
            self.assertEqual(be.hit_count, 1)
            self.assertFalse(be.is_profitable)

            arc.run("Ringkas sentimen berita tentang suku bunga BI 7 hari terakhir")
            arc.run("Ringkas sentimen berita tentang suku bunga BI 7 hari terakhir")
            be_after = arc.ledger.get_break_even_metrics("summarize_bi_rate_news")
            self.assertEqual(be_after.hit_count, 3)
            self.assertTrue(be_after.is_profitable)

    def test_routing_modes_bypass_shadow_and_miss(self) -> None:
        arc = ArcLoom(
            project="finansia",
            mode="active",
            source_model="fable-5.1",
            builder_model="fable-5.1",
            target_model="local/llama-3.2-1b",
            source_handler=_mock_source_llm,
            target_handler=_mock_target_llm,
        )

        miss_res = arc.run("Berapa proyeksi arus kas kuartal depan?")
        self.assertEqual(miss_res.execution_path, "arc_miss_build")
        self.assertEqual(len(arc.builder_queue), 1)
        self.assertEqual(miss_res.saved_usd, 0.0)

        arc.register_semantic_function(
            entry_key="monthly_food_expense",
            intent_description="Berapa total pengeluaran kategori makan bulan November 2026?",
            callable_fn=lambda c: "Rp 2.450.000 (cached)",
            avg_source_cost_usd=0.0414,
            builder_cost_usd=0.08,
        )

        bypass_res = arc.run(
            "Berapa total pengeluaran kategori makan bulan November 2026?",
            mode_override="bypass",
        )
        self.assertEqual(bypass_res.execution_path, "bypass")
        self.assertTrue(str(bypass_res.output).startswith("Jawaban frontier"))

        shadow_res = arc.run(
            "Berapa total pengeluaran kategori makan bulan November 2026?",
            mode_override="shadow",
        )
        self.assertEqual(shadow_res.execution_path, "shadow")
        self.assertEqual(shadow_res.matched_entry_key, "monthly_food_expense")
        self.assertTrue(str(shadow_res.output).startswith("Jawaban frontier"))

    def test_hmac_webhook_tool_invocation(self) -> None:
        secret = "arc-webhook-secret-key"
        captured_req = None

        class _FakeHTTPResponse:
            def __enter__(self) -> _FakeHTTPResponse:
                return self

            def __exit__(self, *args: object) -> None:
                return None

            def read(self) -> bytes:
                return json.dumps({"result": {"total_idr": 2450000}}).encode("utf-8")

        def fake_urlopen(req: object, timeout: float = 15.0) -> _FakeHTTPResponse:
            nonlocal captured_req
            captured_req = req
            return _FakeHTTPResponse()

        ctx = Context(
            project_slug="finansia-laravel",
            request_id="req-webhook-1",
            tool_callback_url="http://localhost:8000/api/internal/arc-tools",
            tool_callback_secret=secret,
        )
        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            result = invoke_tool(
                ctx,
                "getMonthlyExpense",
                {"month": "2026-11", "category": "makan"},
            )

        self.assertEqual(result, {"total_idr": 2450000})
        self.assertIsNotNone(captured_req)
        body_bytes = captured_req.data
        expected_sig = "sha256=" + hmac.new(
            secret.encode("utf-8"),
            body_bytes,
            hashlib.sha256,
        ).hexdigest()
        self.assertEqual(captured_req.get_header("X-arc-signature"), expected_sig)


if __name__ == "__main__":
    unittest.main()
