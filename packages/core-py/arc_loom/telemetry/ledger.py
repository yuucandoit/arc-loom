from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from arc_loom.telemetry.pricing import PricingCatalog

VALID_EXECUTION_PATHS = {"arc_hit", "arc_miss_build", "bypass", "shadow"}
VALID_ROLES = {"source", "builder", "target", "extractor", "converter"}


@dataclass(frozen=True)
class ProjectConfig:
    slug: str
    source_model: str
    builder_model: str
    target_model: str
    routing_mode: str = "active"
    similarity_threshold: float = 0.82
    tool_callback_url: str | None = None
    tool_callback_secret: str | None = None


@dataclass(frozen=True)
class TelemetryRecord:
    request_id: str
    project_slug: str
    execution_path: str
    role: str
    model_name: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float = 0.0
    counterfactual_cost_usd: float = 0.0
    latency_ms: float = 0.0
    cache_entry_id: int | None = None
    id: int | None = None
    created_at: str = ""


@dataclass(frozen=True)
class ProjectSavingsSummary:
    project_slug: str
    total_requests: int
    arc_hits: int
    counterfactual_cost_usd: float
    source_cost_usd: float
    builder_cost_usd: float
    cache_execution_cost_usd: float
    actual_total_cost_usd: float
    net_savings_usd: float


@dataclass(frozen=True)
class BreakEvenMetrics:
    entry_key: str
    builder_cost_usd: float
    avg_source_cost_usd: float
    avg_cache_cost_usd: float
    break_even_calls: int | None
    hit_count: int
    is_profitable: bool


_SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT UNIQUE NOT NULL,
    routing_mode TEXT DEFAULT 'active',
    similarity_threshold REAL DEFAULT 0.82,
    source_model TEXT NOT NULL,
    builder_model TEXT NOT NULL,
    target_model TEXT NOT NULL,
    tool_callback_url TEXT,
    tool_callback_secret TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS cache_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_slug TEXT REFERENCES projects(slug),
    entry_key TEXT UNIQUE NOT NULL,
    intent_description TEXT NOT NULL,
    param_schema TEXT NOT NULL,
    source_path TEXT NOT NULL,
    is_compiled INTEGER DEFAULT 0,
    hit_count INTEGER DEFAULT 0,
    avg_source_cost_usd REAL DEFAULT 0,
    builder_cost_usd REAL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS telemetry_ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT NOT NULL,
    project_slug TEXT NOT NULL,
    cache_entry_id INTEGER REFERENCES cache_entries(id) ON DELETE SET NULL,
    execution_path TEXT NOT NULL,
    role TEXT NOT NULL,
    model_name TEXT NOT NULL,
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    cache_read_tokens INTEGER DEFAULT 0,
    cache_write_tokens INTEGER DEFAULT 0,
    cost_usd REAL DEFAULT 0,
    counterfactual_cost_usd REAL DEFAULT 0,
    latency_ms REAL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


class TelemetryLedger:
    """FinOps ledger supporting both embedded SQLite and PostgreSQL backends."""

    def __init__(
        self,
        sqlite_path: str = ":memory:",
        pricing: PricingCatalog | None = None,
        *,
        database_url: str | None = None,
        pg_connection: Any | None = None,
    ) -> None:
        self.sqlite_path = sqlite_path
        self.database_url = database_url
        self.pricing = pricing or PricingCatalog()
        self._is_postgres = False

        if pg_connection is not None:
            self._is_postgres = True
            self._conn = pg_connection
            self._init_postgres_schema()
        elif database_url and (
            database_url.startswith("postgresql://")
            or database_url.startswith("postgres://")
        ):
            try:
                import psycopg  # type: ignore
                from psycopg.rows import dict_row  # type: ignore
            except ImportError as exc:
                raise RuntimeError(
                    "PostgreSQL database_url requires 'psycopg[binary]' to be installed."
                ) from exc
            self._is_postgres = True
            self._conn = psycopg.connect(database_url, row_factory=dict_row)
            self._init_postgres_schema()
        else:
            resolved_path = sqlite_path
            if database_url and database_url.startswith("sqlite:///"):
                resolved_path = database_url[len("sqlite:///") :]
            if resolved_path != ":memory:":
                Path(resolved_path).parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(resolved_path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            if resolved_path != ":memory:":
                self._conn.execute("PRAGMA journal_mode=WAL;")
                self._conn.execute("PRAGMA synchronous=NORMAL;")
            self._conn.execute("PRAGMA foreign_keys=ON;")
            self._conn.executescript(_SQLITE_SCHEMA)
            self._conn.commit()

    def _init_postgres_schema(self) -> None:
        schema_file = Path(__file__).with_name("schema.sql")
        ddl = schema_file.read_text(encoding="utf-8")
        cur = self._conn.cursor()
        for stmt in [s.strip() for s in ddl.split(";") if s.strip()]:
            cur.execute(stmt)
        self._conn.commit()

    def _format_sql(self, sql: str) -> str:
        return sql.replace("?", "%s") if self._is_postgres else sql

    def _execute(
        self, sql: str, params: tuple[Any, ...] = (), *, commit: bool = False
    ) -> Any:
        cur = self._conn.cursor()
        cur.execute(self._format_sql(sql), params)
        if commit:
            self._conn.commit()
        return cur

    def _fetchone(self, sql: str, params: tuple[Any, ...] = ()) -> Any:
        return self._execute(sql, params).fetchone()

    def _fetchall(self, sql: str, params: tuple[Any, ...] = ()) -> list[Any]:
        return list(self._execute(sql, params).fetchall())

    def upsert_project(self, config: ProjectConfig) -> int:
        self._execute(
            """
            INSERT INTO projects (
                slug, routing_mode, similarity_threshold,
                source_model, builder_model, target_model,
                tool_callback_url, tool_callback_secret
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(slug) DO UPDATE SET
                routing_mode = excluded.routing_mode,
                similarity_threshold = excluded.similarity_threshold,
                source_model = excluded.source_model,
                builder_model = excluded.builder_model,
                target_model = excluded.target_model,
                tool_callback_url = excluded.tool_callback_url,
                tool_callback_secret = excluded.tool_callback_secret
            """,
            (
                config.slug,
                config.routing_mode,
                config.similarity_threshold,
                config.source_model,
                config.builder_model,
                config.target_model,
                config.tool_callback_url,
                config.tool_callback_secret,
            ),
            commit=True,
        )
        row = self._fetchone(
            "SELECT id FROM projects WHERE slug = ?", (config.slug,)
        )
        return int(row["id"])

    def upsert_cache_entry(
        self,
        *,
        project_slug: str,
        entry_key: str,
        intent_description: str,
        param_schema: dict[str, Any],
        source_path: str,
        is_compiled: bool = False,
        hit_count: int = 0,
        avg_source_cost_usd: float = 0.0,
        builder_cost_usd: float = 0.0,
    ) -> int:
        compiled_val: Any = bool(is_compiled) if self._is_postgres else (1 if is_compiled else 0)
        self._execute(
            """
            INSERT INTO cache_entries (
                project_slug, entry_key, intent_description, param_schema,
                source_path, is_compiled, hit_count, avg_source_cost_usd, builder_cost_usd
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(entry_key) DO UPDATE SET
                intent_description = excluded.intent_description,
                param_schema = excluded.param_schema,
                source_path = excluded.source_path,
                is_compiled = excluded.is_compiled,
                hit_count = excluded.hit_count,
                avg_source_cost_usd = excluded.avg_source_cost_usd,
                builder_cost_usd = excluded.builder_cost_usd
            """,
            (
                project_slug,
                entry_key,
                intent_description,
                json.dumps(param_schema),
                source_path,
                compiled_val,
                hit_count,
                round(avg_source_cost_usd, 8),
                round(builder_cost_usd, 8),
            ),
            commit=True,
        )
        row = self._fetchone(
            "SELECT id FROM cache_entries WHERE entry_key = ?", (entry_key,)
        )
        return int(row["id"])

    def increment_cache_hit(self, entry_key: str) -> None:
        self._execute(
            "UPDATE cache_entries SET hit_count = hit_count + 1 WHERE entry_key = ?",
            (entry_key,),
            commit=True,
        )

    def update_counterfactual_cost(
        self, record_id: int, counterfactual_cost_usd: float
    ) -> None:
        self._execute(
            "UPDATE telemetry_ledger SET counterfactual_cost_usd = ? WHERE id = ?",
            (round(counterfactual_cost_usd, 8), record_id),
            commit=True,
        )

    def record(
        self,
        *,
        request_id: str,
        project_slug: str,
        execution_path: str,
        role: str,
        model_name: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cache_read_tokens: int = 0,
        cache_write_tokens: int = 0,
        cost_usd: float | None = None,
        counterfactual_cost_usd: float = 0.0,
        latency_ms: float = 0.0,
        cache_entry_id: int | None = None,
    ) -> TelemetryRecord:
        if execution_path not in VALID_EXECUTION_PATHS:
            raise ValueError(f"Invalid execution_path: {execution_path}")
        if role not in VALID_ROLES:
            raise ValueError(f"Invalid role: {role}")

        actual_cost = (
            round(cost_usd, 8)
            if cost_usd is not None
            else self.pricing.calculate_cost_usd(
                model_name,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cache_read_tokens=cache_read_tokens,
                cache_write_tokens=cache_write_tokens,
            )
        )
        cf_cost = round(counterfactual_cost_usd, 8)

        insert_sql = """
            INSERT INTO telemetry_ledger (
                request_id, project_slug, cache_entry_id, execution_path,
                role, model_name, input_tokens, output_tokens,
                cache_read_tokens, cache_write_tokens,
                cost_usd, counterfactual_cost_usd, latency_ms
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        params = (
            request_id,
            project_slug,
            cache_entry_id,
            execution_path,
            role,
            model_name,
            input_tokens,
            output_tokens,
            cache_read_tokens,
            cache_write_tokens,
            actual_cost,
            cf_cost,
            latency_ms,
        )

        if self._is_postgres:
            cur = self._execute(insert_sql + " RETURNING id", params, commit=False)
            row = cur.fetchone()
            self._conn.commit()
            row_id = int(row["id"])
        else:
            cur = self._execute(insert_sql, params, commit=True)
            row_id = int(cur.lastrowid)

        return TelemetryRecord(
            id=row_id,
            request_id=request_id,
            project_slug=project_slug,
            cache_entry_id=cache_entry_id,
            execution_path=execution_path,
            role=role,
            model_name=model_name,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read_tokens,
            cache_write_tokens=cache_write_tokens,
            cost_usd=actual_cost,
            counterfactual_cost_usd=cf_cost,
            latency_ms=latency_ms,
        )

    def list_projects(self) -> list[str]:
        rows = self._fetchall("SELECT slug FROM projects ORDER BY id ASC")
        return [str(r["slug"]) for r in rows]

    def list_records(
        self, project_slug: str | None = None, limit: int = 200
    ) -> list[TelemetryRecord]:
        if project_slug:
            rows = self._fetchall(
                """
                SELECT id, request_id, project_slug, cache_entry_id, execution_path,
                       role, model_name, input_tokens, output_tokens,
                       cache_read_tokens, cache_write_tokens,
                       cost_usd, counterfactual_cost_usd, latency_ms, created_at
                FROM telemetry_ledger
                WHERE project_slug = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (project_slug, limit),
            )
        else:
            rows = self._fetchall(
                """
                SELECT id, request_id, project_slug, cache_entry_id, execution_path,
                       role, model_name, input_tokens, output_tokens,
                       cache_read_tokens, cache_write_tokens,
                       cost_usd, counterfactual_cost_usd, latency_ms, created_at
                FROM telemetry_ledger
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            )

        return [
            TelemetryRecord(
                id=int(r["id"]),
                request_id=str(r["request_id"]),
                project_slug=str(r["project_slug"]),
                cache_entry_id=r["cache_entry_id"],
                execution_path=str(r["execution_path"]),
                role=str(r["role"]),
                model_name=str(r["model_name"]),
                input_tokens=int(r["input_tokens"]),
                output_tokens=int(r["output_tokens"]),
                cache_read_tokens=int(r["cache_read_tokens"]),
                cache_write_tokens=int(r["cache_write_tokens"]),
                cost_usd=float(r["cost_usd"]),
                counterfactual_cost_usd=float(r["counterfactual_cost_usd"]),
                latency_ms=float(r["latency_ms"]),
                created_at=str(r["created_at"] or ""),
            )
            for r in reversed(rows)
        ]

    def get_project_savings(self, project_slug: str) -> ProjectSavingsSummary:
        rows = self._fetchall(
            """
            SELECT request_id, execution_path, role, cost_usd, counterfactual_cost_usd
            FROM telemetry_ledger
            WHERE project_slug = ?
            """,
            (project_slug,),
        )

        request_ids: set[str] = set()
        hit_requests: set[str] = set()
        counterfactual_total = 0.0
        source_cost = 0.0
        builder_cost = 0.0
        cache_exec_cost = 0.0

        for row in rows:
            req_id = str(row["request_id"])
            request_ids.add(req_id)
            if row["execution_path"] == "arc_hit":
                hit_requests.add(req_id)
            counterfactual_total += float(row["counterfactual_cost_usd"])
            cost = float(row["cost_usd"])
            role = str(row["role"])
            if role == "source":
                source_cost += cost
            elif role == "builder":
                builder_cost += cost
            else:
                cache_exec_cost += cost

        actual_total = source_cost + builder_cost + cache_exec_cost
        net_savings = counterfactual_total - actual_total
        return ProjectSavingsSummary(
            project_slug=project_slug,
            total_requests=len(request_ids),
            arc_hits=len(hit_requests),
            counterfactual_cost_usd=round(counterfactual_total, 8),
            source_cost_usd=round(source_cost, 8),
            builder_cost_usd=round(builder_cost, 8),
            cache_execution_cost_usd=round(cache_exec_cost, 8),
            actual_total_cost_usd=round(actual_total, 8),
            net_savings_usd=round(net_savings, 8),
        )

    def get_break_even_metrics(self, entry_key: str) -> BreakEvenMetrics:
        entry_row = self._fetchone(
            """
            SELECT id, hit_count, avg_source_cost_usd, builder_cost_usd
            FROM cache_entries
            WHERE entry_key = ?
            """,
            (entry_key,),
        )
        if entry_row is None:
            raise KeyError(f"Cache entry '{entry_key}' not found in ledger.")

        entry_id = int(entry_row["id"])
        hit_count = int(entry_row["hit_count"])
        avg_source_cost = float(entry_row["avg_source_cost_usd"])
        builder_cost = float(entry_row["builder_cost_usd"])

        req_rows = self._fetchall(
            """
            SELECT request_id, SUM(cost_usd) AS req_cache_cost
            FROM telemetry_ledger
            WHERE cache_entry_id = ?
              AND execution_path = 'arc_hit'
              AND role IN ('target', 'extractor', 'converter')
            GROUP BY request_id
            """,
            (entry_id,),
        )

        if req_rows:
            avg_cache_cost = sum(float(r["req_cache_cost"]) for r in req_rows) / len(
                req_rows
            )
        else:
            avg_cache_cost = 0.0

        margin = avg_source_cost - avg_cache_cost
        if margin <= 0.0:
            break_even_calls: int | None = None
            is_profitable = False
        else:
            break_even_calls = math.ceil(builder_cost / margin)
            is_profitable = hit_count >= break_even_calls

        return BreakEvenMetrics(
            entry_key=entry_key,
            builder_cost_usd=round(builder_cost, 8),
            avg_source_cost_usd=round(avg_source_cost, 8),
            avg_cache_cost_usd=round(avg_cache_cost, 8),
            break_even_calls=break_even_calls,
            hit_count=hit_count,
            is_profitable=is_profitable,
        )
