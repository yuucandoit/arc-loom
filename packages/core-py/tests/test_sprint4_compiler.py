from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from arc_loom import ArcLoom, LLMCallResult


def _mock_1b(prompt: str, temperature: float, role: str) -> LLMCallResult:
    return LLMCallResult("POSITIF", 35, 3, "local/llama-3.2-1b", 8.0)


class TestSprint4StaticCompiler(unittest.TestCase):
    def test_static_o1_compiler_eliminates_runtime_vector_search(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            arc = ArcLoom(
                project="ima-isa",
                mode="active",
                source_model="fable-5.1",
                builder_model="fable-5.1",
                target_model="local/llama-3.2-1b",
                cache_dir=Path(tmp_dir),
                target_handler=_mock_1b,
            )

            sub_script = """
def run(ctx):
    items = load(ctx, "daftar artikel berita", list)
    return lm(ctx, f"Sentimen untuk {len(items)} artikel")
"""
            arc.register_semantic_function(
                entry_key="classify_news_batch",
                intent_description="klasifikasi sentimen kumpulan artikel berita",
                param_schema={"daftar_artikel": "daftar artikel berita"},
                source_code=sub_script,
            )

            main_script = """
def run(ctx):
    topic = load(ctx, "topik utama berita ekonomi", str)
    days = load(ctx, "jumlah rentang hari pencarian", int)
    verdict = do(
        ctx,
        "klasifikasi sentimen kumpulan artikel berita",
        {"daftar_artikel": [f"{topic}-{days}"]},
    )
    narrate(ctx, f"Compiled O(1) execution for {topic} ({days}d)")
    return f"{topic} ({days}d): {verdict}"
"""
            entry = arc.register_semantic_function(
                entry_key="summarize_economic_topic",
                intent_description="Ringkas sentimen berita ekonomi berdasarkan topik dan hari",
                param_schema={
                    "topik_berita": "topik utama berita ekonomi",
                    "rentang_hari": "jumlah rentang hari pencarian",
                },
                source_code=main_script,
            )

            comp_res = arc.compile_function("summarize_economic_topic")
            self.assertTrue(entry.is_compiled)
            self.assertIn("def _compiled_classify_news_batch(ctx):", comp_res.compiled_code)
            self.assertIn("ctx.get_exact('topik_berita')", comp_res.compiled_code)
            self.assertIn("ctx.get_exact('rentang_hari')", comp_res.compiled_code)
            self.assertEqual(
                comp_res.resolved_sub_calls,
                {"klasifikasi sentimen kumpulan artikel berita": "classify_news_batch"},
            )
            self.assertTrue(Path(comp_res.source_path).exists())

            # Patch find_best after initial entry match to prove sub-function `do` and `load`
            # no longer invoke vector search at runtime.
            with patch.object(
                arc.registry,
                "find_best",
                side_effect=AssertionError("Runtime vector search should not be called inside compiled O(1) function!"),
            ):
                ctx = arc._build_context(
                    request_id="req-compiled-1",
                    prompt="Ringkas sentimen berita ekonomi berdasarkan topik dan hari",
                    params={"topik_berita": "Suku Bunga BI", "rentang_hari": "14"},
                    param_descriptions=None,
                    execution_path="arc_hit",
                    cache_entry_id=entry.db_id,
                    records=[],
                )
                output = arc.registry.execute_entry(entry, ctx)

            self.assertEqual(output, "Suku Bunga BI (14d): POSITIF")
            self.assertEqual(
                ctx.narration_trace,
                ["Compiled O(1) execution for Suku Bunga BI (14d)"],
            )


if __name__ == "__main__":
    unittest.main()
