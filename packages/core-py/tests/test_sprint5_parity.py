from __future__ import annotations

import json
import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from arc_loom import (
    ArcGatewayServer,
    AsyncBuilderWorker,
    LLMCallResult,
    ProjectConfig,
    SemanticRegistry,
    TelemetryLedger,
    export_sft_jsonl,
)


class _PostgresCompatCursor:
    def __init__(self, sqlite_cur: sqlite3.Cursor) -> None:
        self._cur = sqlite_cur

    def execute(self, sql: str, params: tuple[object, ...] = ()) -> _PostgresCompatCursor:
        # Verify TelemetryLedger translates '?' placeholders to '%s' for psycopg.
        assert "?" not in sql, f"Unconverted SQLite placeholder in Postgres SQL: {sql}"
        pg_translated = sql.replace("%s", "?")
        pg_translated = pg_translated.replace("BIGSERIAL PRIMARY KEY", "INTEGER PRIMARY KEY AUTOINCREMENT")
        pg_translated = pg_translated.replace("SERIAL PRIMARY KEY", "INTEGER PRIMARY KEY AUTOINCREMENT")
        pg_translated = pg_translated.replace("JSONB", "TEXT")
        pg_translated = pg_translated.replace("NUMERIC(12, 8)", "REAL")
        pg_translated = pg_translated.replace("NOW()", "CURRENT_TIMESTAMP")
        self._cur.execute(pg_translated, params)
        return self

    def fetchone(self) -> object:
        return self._cur.fetchone()

    def fetchall(self) -> list[object]:
        return self._cur.fetchall()


class _FakePostgresConnection:
    def __init__(self) -> None:
        self._sqlite = sqlite3.connect(":memory:")
        self._sqlite.row_factory = sqlite3.Row

    def cursor(self) -> _PostgresCompatCursor:
        return _PostgresCompatCursor(self._sqlite.cursor())

    def commit(self) -> None:
        self._sqlite.commit()


class TestSprint5PaperParity(unittest.TestCase):
    def test_dense_embedding_provider_matches_semantic_synonyms(self) -> None:
        def fake_embed(text: str) -> list[float]:
            low = text.lower()
            if "pengeluaran" in low or "biaya makan" in low or "expense" in low:
                return [0.98, 0.12, 0.05]
            return [0.05, 0.99, 0.02]

        reg = SemanticRegistry(default_threshold=0.85, embedding_fn=fake_embed)
        reg.register(
            project_slug="finansia",
            entry_key="monthly_expense_fn",
            intent_description="Hitung total pengeluaran kategori makanan bulanan",
            callable_fn=lambda c: "OK",
        )

        # Query shares zero exact keywords ("pengeluaran"/"makanan") yet matches via dense embedding.
        match = reg.find_best(
            "Berapa rekap biaya makan dan expense saya?",
            project_slug="finansia",
        )
        self.assertIsNotNone(match)
        entry, score = match  # type: ignore[misc]
        self.assertEqual(entry.entry_key, "monthly_expense_fn")
        self.assertGreater(score, 0.99)

    def test_perturbation_check_and_continual_sft_jsonl_export(self) -> None:
        ledger = TelemetryLedger()
        ledger.upsert_project(
            ProjectConfig(
                slug="finansia",
                source_model="fable-5.1",
                builder_model="fable-5.1",
                target_model="local/llama-3.2-1b",
            )
        )
        registry = SemanticRegistry()

        def mock_builder(prompt: str, temperature: float, role: str) -> LLMCallResult:
            if "PHASE 1:" in prompt:
                return LLMCallResult(
                    json.dumps(
                        {
                            "entry_key": "extract_invoice_total",
                            "intent_description": "Hitung total tagihan faktur",
                            "param_schema": {"query": "str"},
                        }
                    ),
                    200,
                    50,
                    "fable-5.1",
                    200.0,
                )
            if "PHASE 4B:" in prompt:
                return LLMCallResult(
                    json.dumps(
                        [
                            "Tolong cek nominal tagihan faktur nomor 88",
                            "Berapa jumlah tagihan pada faktur 99?",
                        ]
                    ),
                    150,
                    40,
                    "fable-5.1",
                    180.0,
                )
            code = """```python
def run(ctx):
    q = load(ctx, "raw user prompt query text", str)
    inv_no = extract(ctx, q, "Extract invoice number", str)
    return f"Faktur #{inv_no}: Rp 500000"
```"""
            return LLMCallResult(code, 300, 90, "fable-5.1", 300.0)

        def mock_target(prompt: str, temperature: float, role: str) -> LLMCallResult:
            if "99" in prompt:
                return LLMCallResult("99", 30, 2, "local/llama-3.2-1b", 8.0)
            if "88" in prompt:
                return LLMCallResult("88", 30, 2, "local/llama-3.2-1b", 8.0)
            return LLMCallResult("42", 30, 2, "local/llama-3.2-1b", 8.0)

        worker = AsyncBuilderWorker(
            registry=registry,
            ledger=ledger,
            builder_handler=mock_builder,
            target_handler=mock_target,
            enable_perturbation_check=True,
        )
        artifact = worker.build_from_miss(
            {
                "request_id": "req-p4b-1",
                "project_slug": "finansia",
                "prompt": "Berapa tagihan faktur 42?",
                "source_output": "Faktur #42: Rp 500000",
                "source_cost_usd": 0.04,
            }
        )
        self.assertTrue(artifact.passed)
        self.assertEqual(artifact.perturbation_passed, 2)
        self.assertEqual(len(artifact.sft_examples), 3)

        with TemporaryDirectory() as tmp_dir:
            out_file = Path(tmp_dir) / "sft_1b.jsonl"
            jsonl_text = export_sft_jsonl(worker.collected_sft_examples, out_file)
            lines = [json.loads(line) for line in jsonl_text.strip().splitlines()]
            self.assertEqual(len(lines), 3)
            self.assertEqual(lines[0]["role"], "extractor")
            self.assertEqual(lines[0]["messages"][1]["content"], "42")
            self.assertTrue(out_file.exists())

    def test_postgres_adapter_and_global_gateway_telemetry(self) -> None:
        pg_conn = _FakePostgresConnection()
        ledger = TelemetryLedger(pg_connection=pg_conn)
        gw = ArcGatewayServer(
            ledger=ledger,
            source_handler=lambda p, t, r: LLMCallResult("OK", 500, 120, "fable-5.1", 800.0),
            builder_handler=lambda p, t, r: LLMCallResult(
                '{"entry_key":"ping_fn","intent_description":"ping test"}'
                if "PHASE 1:" in p
                else "def run(ctx):\n    return 'OK'",
                200,
                50,
                "fable-5.1",
                250.0,
            ),
            target_handler=lambda p, t, r: LLMCallResult("OK", 20, 2, "local/llama-3.2-1b", 5.0),
        )
        gw.handle_chat_completions(
            payload={"model": "fable-5.1", "messages": [{"role": "user", "content": "ping test"}]},
            headers={"X-ARC-Project": "pg-proj", "X-ARC-Mode": "active"},
        )
        gw.handle_build_queue("pg-proj")

        global_telem = gw.handle_global_telemetry()
        self.assertIn("pg-proj", global_telem["projects"])
        self.assertEqual(len(global_telem["functions"]), 1)
        self.assertGreaterEqual(len(global_telem["ledgerRows"]), 2)

        compiled = gw.handle_compile_entry({"entryKey": "ping_fn"})
        self.assertTrue(compiled["isCompiled"])

    def test_persistent_sqlite_wal_mode_and_reopen(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            db_file = Path(tmp_dir) / "nested" / "arc_loom.db"
            gw1 = ArcGatewayServer(
                sqlite_path=str(db_file),
                source_handler=lambda p, t, r: LLMCallResult("OK", 400, 100, "fable-5.1", 500.0),
            )
            gw1.handle_chat_completions(
                payload={"model": "fable-5.1", "messages": [{"role": "user", "content": "cek saldo"}]},
                headers={"X-ARC-Project": "sqlite-local", "X-ARC-Mode": "active"},
            )
            mode_row = gw1.ledger._conn.execute("PRAGMA journal_mode;").fetchone()
            self.assertEqual(str(mode_row[0]).lower(), "wal")
            self.assertTrue(db_file.exists())

            # Reopen ledger from the same SQLite file to verify persistence.
            reopened = TelemetryLedger(sqlite_path=str(db_file))
            self.assertIn("sqlite-local", reopened.list_projects())
            self.assertEqual(len(reopened.list_records("sqlite-local")), 1)


if __name__ == "__main__":
    unittest.main()
