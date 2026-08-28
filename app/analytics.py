from __future__ import annotations

import hashlib
import sqlite3
import threading
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

try:
    import psycopg
    from psycopg.rows import dict_row
except ImportError:  # Local SQLite development does not require the Postgres driver.
    psycopg = None
    dict_row = None


EventName = Literal[
    "page_view",
    "upload_pgn",
    "analysis_start",
    "analysis_complete",
    "report_export",
    "feedback",
]


class AnalyticsEventRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    visitor_id: str = Field(min_length=8, max_length=80)
    event: EventName
    page: str | None = Field(default=None, max_length=500)
    device_info: str | None = Field(default=None, max_length=500)
    source_info: str | None = Field(default=None, max_length=1000)
    pgn_length: int | None = Field(default=None, ge=0, le=100_000)
    success: bool | None = None
    analysis_id: str | None = Field(default=None, min_length=8, max_length=80)
    duration_ms: int | None = Field(default=None, ge=0, le=86_400_000)
    report_id: str | None = Field(default=None, min_length=1, max_length=120)
    rating: int | None = Field(default=None, ge=1, le=5)
    suggestion: str | None = Field(default=None, max_length=2000)
    analysis_result: str | None = Field(default=None, max_length=20_000)

    @field_validator("visitor_id", "analysis_id", "report_id")
    @classmethod
    def validate_identifier(cls, value: str | None) -> str | None:
        if value is None:
            return value
        if not all(character.isalnum() or character in "_-" for character in value):
            raise ValueError("identifier contains unsupported characters")
        return value

    @model_validator(mode="after")
    def validate_event_fields(self) -> "AnalyticsEventRequest":
        if self.event == "page_view" and self.page is None:
            raise ValueError("page_view requires page")
        if self.event == "upload_pgn" and (
            self.pgn_length is None or self.success is None
        ):
            raise ValueError("upload_pgn requires pgn_length and success")
        if self.event in {"analysis_start", "analysis_complete"} and self.analysis_id is None:
            raise ValueError(f"{self.event} requires analysis_id")
        if self.event == "analysis_complete" and (
            self.duration_ms is None or self.success is None
        ):
            raise ValueError("analysis_complete requires duration_ms and success")
        if self.event == "report_export" and self.report_id is None:
            raise ValueError("report_export requires report_id")
        if self.event == "feedback" and self.rating is None and not (self.suggestion or "").strip():
            raise ValueError("feedback requires rating or suggestion")
        if self.event != "feedback" and (
            self.rating is not None
            or self.suggestion is not None
            or self.analysis_result is not None
        ):
            raise ValueError("feedback fields are only allowed for feedback")
        return self


class AnalyticsEventResponse(BaseModel):
    accepted: bool = True


class DailyStatistics(BaseModel):
    date: str
    visitors: int
    page_views: int
    uploads: int
    upload_successes: int
    upload_failures: int
    analyses: int
    successes: int
    failures: int
    success_rate: float | None
    average_analysis_ms: int | None
    stockfish_ms: int
    deepseek_ms: int
    deepseek_prompt_tokens: int
    deepseek_completion_tokens: int
    deepseek_total_tokens: int
    estimated_ai_cost: float | None = None
    upload_to_analysis_rate: float | None = None


class RecentAnalysis(BaseModel):
    analysis_id: str
    visitor_id: str
    created_at: str
    completed_at: str | None
    pgn_length: int
    move_count: int | None
    stockfish_ms: int
    deepseek_ms: int
    total_ms: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    status: Literal["success", "failed"]


class RecentFeedback(BaseModel):
    created_at: str
    visitor_id: str
    analysis_id: str | None
    rating: int | None
    suggestion: str | None
    analysis_result: str | None


class AnalyticsStore:
    """Small privacy-preserving SQLite store for the public beta."""

    def __init__(
        self,
        database_location: Path | str,
        *,
        timezone_name: str = "Asia/Shanghai",
        input_price_per_million: float = 0,
        output_price_per_million: float = 0,
    ) -> None:
        location = str(database_location)
        self._postgres = location.startswith(("postgresql://", "postgres://"))
        self.database_url = location if self._postgres else None
        self.database_path = None if self._postgres else Path(database_location)
        if self._postgres and psycopg is None:
            raise RuntimeError("Postgres analytics requires psycopg")
        if timezone_name != "Asia/Shanghai":
            raise ValueError("public beta analytics currently supports Asia/Shanghai only")
        self.timezone = timezone(timedelta(hours=8), name="Asia/Shanghai")
        self.input_price_per_million = max(0, input_price_per_million)
        self.output_price_per_million = max(0, output_price_per_million)
        self._lock = threading.RLock()
        if self.database_path is not None:
            self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self):
        if self._postgres:
            return psycopg.connect(self.database_url, row_factory=dict_row)
        assert self.database_path is not None
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def _execute(self, connection, statement: str, parameters: tuple = ()):
        sql = statement.replace("?", "%s") if self._postgres else statement
        return connection.execute(sql, parameters)

    def _initialize(self) -> None:
        with self._lock, self._connect() as connection:
            schema = """
                CREATE TABLE IF NOT EXISTS visitors (
                    visitor_id TEXT PRIMARY KEY,
                    first_visit_at TEXT NOT NULL,
                    last_visit_at TEXT NOT NULL,
                    device_info TEXT,
                    source_info TEXT
                );
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    visitor_id TEXT NOT NULL,
                    event_name TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    page TEXT,
                    pgn_length INTEGER,
                    success INTEGER,
                    analysis_id TEXT,
                    duration_ms INTEGER,
                    report_id TEXT,
                    rating INTEGER,
                    suggestion TEXT,
                    analysis_result TEXT,
                    FOREIGN KEY(visitor_id) REFERENCES visitors(visitor_id)
                );
                CREATE INDEX IF NOT EXISTS idx_events_created_at ON events(created_at);
                CREATE INDEX IF NOT EXISTS idx_events_name_created ON events(event_name, created_at);
                CREATE TABLE IF NOT EXISTS analysis_logs (
                    analysis_id TEXT PRIMARY KEY,
                    visitor_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    completed_at TEXT,
                    pgn_length INTEGER NOT NULL,
                    pgn_sha256 TEXT NOT NULL,
                    move_count INTEGER,
                    stockfish_ms INTEGER NOT NULL DEFAULT 0,
                    deepseek_ms INTEGER NOT NULL DEFAULT 0,
                    total_ms INTEGER NOT NULL DEFAULT 0,
                    prompt_tokens INTEGER NOT NULL DEFAULT 0,
                    completion_tokens INTEGER NOT NULL DEFAULT 0,
                    total_tokens INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL CHECK(status IN ('success', 'failed')),
                    FOREIGN KEY(visitor_id) REFERENCES visitors(visitor_id)
                );
                CREATE INDEX IF NOT EXISTS idx_analysis_created_at ON analysis_logs(created_at);
                CREATE INDEX IF NOT EXISTS idx_analysis_visitor ON analysis_logs(visitor_id);
                """
            if self._postgres:
                schema = schema.replace(
                    "id INTEGER PRIMARY KEY AUTOINCREMENT",
                    "id BIGSERIAL PRIMARY KEY",
                )
                for statement in schema.split(";"):
                    if statement.strip():
                        connection.execute(statement)
            else:
                connection.executescript(schema)
            feedback_columns = (
                ("rating", "INTEGER"),
                ("suggestion", "TEXT"),
                ("analysis_result", "TEXT"),
            )
            if self._postgres:
                for column_name, column_type in feedback_columns:
                    connection.execute(
                        f"ALTER TABLE events ADD COLUMN IF NOT EXISTS {column_name} {column_type}"
                    )
            else:
                for column_name, column_type in feedback_columns:
                    try:
                        self._execute(
                            connection,
                            f"ALTER TABLE events ADD COLUMN {column_name} {column_type}",
                        )
                    except Exception as error:
                        if not any(
                            marker in str(error).lower()
                            for marker in ("duplicate column", "already exists")
                        ):
                            raise

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="milliseconds")

    @staticmethod
    def _source_summary(source_info: str | None) -> str | None:
        if not source_info or source_info == "direct":
            return source_info
        parsed = urlsplit(source_info)
        if parsed.scheme in {"http", "https"} and parsed.netloc:
            return f"{parsed.scheme}://{parsed.netloc}"
        return "other"

    def ensure_visitor(
        self,
        visitor_id: str,
        *,
        device_info: str | None = None,
        source_info: str | None = None,
    ) -> None:
        now = self._now()
        with self._lock, self._connect() as connection:
            self._execute(
                connection,
                """
                INSERT INTO visitors(visitor_id, first_visit_at, last_visit_at, device_info, source_info)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(visitor_id) DO UPDATE SET
                    last_visit_at = excluded.last_visit_at,
                    device_info = COALESCE(excluded.device_info, visitors.device_info),
                    source_info = COALESCE(visitors.source_info, excluded.source_info)
                """,
                (visitor_id, now, now, device_info, self._source_summary(source_info)),
            )

    def record_event(self, event: AnalyticsEventRequest) -> None:
        self.ensure_visitor(
            event.visitor_id,
            device_info=event.device_info,
            source_info=event.source_info,
        )
        with self._lock, self._connect() as connection:
            self._execute(
                connection,
                """
                INSERT INTO events(
                    visitor_id, event_name, created_at, page, pgn_length,
                    success, analysis_id, duration_ms, report_id, rating, suggestion,
                    analysis_result
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.visitor_id,
                    event.event,
                    self._now(),
                    event.page,
                    event.pgn_length,
                    None if event.success is None else int(event.success),
                    event.analysis_id,
                    event.duration_ms,
                    event.report_id,
                    event.rating,
                    event.suggestion.strip() if event.suggestion else None,
                    event.analysis_result.strip() if event.analysis_result else None,
                ),
            )

    def start_analysis(self, analysis_id: str, visitor_id: str, pgn: str) -> None:
        self.ensure_visitor(visitor_id)
        with self._lock, self._connect() as connection:
            self._execute(
                connection,
                """
                INSERT INTO analysis_logs(
                    analysis_id, visitor_id, created_at, pgn_length, pgn_sha256, status
                ) VALUES (?, ?, ?, ?, ?, 'failed')
                """,
                (
                    analysis_id,
                    visitor_id,
                    self._now(),
                    len(pgn),
                    hashlib.sha256(pgn.encode("utf-8")).hexdigest(),
                ),
            )

    def finish_analysis(
        self,
        analysis_id: str,
        *,
        success: bool,
        stockfish_ms: int,
        total_ms: int,
        move_count: int | None = None,
    ) -> None:
        with self._lock, self._connect() as connection:
            self._execute(
                connection,
                """
                UPDATE analysis_logs SET
                    completed_at = ?, move_count = ?, stockfish_ms = ?, total_ms = ?, status = ?
                WHERE analysis_id = ?
                """,
                (
                    self._now(),
                    move_count,
                    max(0, stockfish_ms),
                    max(0, total_ms),
                    "success" if success else "failed",
                    analysis_id,
                ),
            )

    def add_deepseek_usage(
        self,
        analysis_id: str,
        *,
        elapsed_ms: int,
        prompt_tokens: int | None,
        completion_tokens: int | None,
        total_tokens: int | None,
    ) -> None:
        with self._lock, self._connect() as connection:
            self._execute(
                connection,
                """
                UPDATE analysis_logs SET
                    deepseek_ms = deepseek_ms + ?,
                    total_ms = total_ms + ?,
                    prompt_tokens = prompt_tokens + ?,
                    completion_tokens = completion_tokens + ?,
                    total_tokens = total_tokens + ?
                WHERE analysis_id = ?
                """,
                (
                    max(0, elapsed_ms),
                    max(0, elapsed_ms),
                    max(0, prompt_tokens or 0),
                    max(0, completion_tokens or 0),
                    max(0, total_tokens or 0),
                    analysis_id,
                ),
            )

    def mark_analysis_failed(self, analysis_id: str) -> None:
        with self._lock, self._connect() as connection:
            self._execute(
                connection,
                """
                UPDATE analysis_logs SET status = 'failed', completed_at = ?
                WHERE analysis_id = ?
                """,
                (self._now(), analysis_id),
            )

    def daily_statistics(self, day: datetime | None = None) -> DailyStatistics:
        local_now = day.astimezone(self.timezone) if day else datetime.now(self.timezone)
        local_start = datetime.combine(local_now.date(), time.min, tzinfo=self.timezone)
        start = local_start.astimezone(timezone.utc).isoformat(timespec="milliseconds")
        end = (local_start + timedelta(days=1)).astimezone(timezone.utc).isoformat(timespec="milliseconds")
        with self._lock, self._connect() as connection:
            visitors_row = self._execute(
                connection,
                "SELECT COUNT(DISTINCT visitor_id) AS visitors FROM events WHERE created_at >= ? AND created_at < ?",
                (start, end),
            ).fetchone()
            visitors = visitors_row["visitors"]
            events = self._execute(
                connection,
                """
                SELECT
                    SUM(CASE WHEN event_name = 'page_view' THEN 1 ELSE 0 END) AS page_views,
                    SUM(CASE WHEN event_name = 'upload_pgn' THEN 1 ELSE 0 END) AS uploads,
                    SUM(CASE WHEN event_name = 'upload_pgn' AND success = 1 THEN 1 ELSE 0 END) AS upload_successes,
                    SUM(CASE WHEN event_name = 'upload_pgn' AND success = 0 THEN 1 ELSE 0 END) AS upload_failures
                FROM events WHERE created_at >= ? AND created_at < ?
                """,
                (start, end),
            ).fetchone()
            row = self._execute(
                connection,
                """
                SELECT COUNT(*) AS analyses,
                       SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) AS successes,
                       SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failures,
                       AVG(CASE WHEN status = 'success' THEN total_ms END) AS average_ms,
                       COALESCE(SUM(stockfish_ms), 0) AS stockfish_ms,
                       COALESCE(SUM(deepseek_ms), 0) AS deepseek_ms,
                       COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                       COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
                       COALESCE(SUM(total_tokens), 0) AS total_tokens
                FROM analysis_logs WHERE created_at >= ? AND created_at < ?
                """,
                (start, end),
            ).fetchone()
        analyses = int(row["analyses"] or 0)
        uploads = int(events["uploads"] or 0)
        successes = int(row["successes"] or 0)
        failures = int(row["failures"] or 0)
        prompt_tokens = int(row["prompt_tokens"] or 0)
        completion_tokens = int(row["completion_tokens"] or 0)
        has_prices = self.input_price_per_million > 0 or self.output_price_per_million > 0
        estimated_cost = None
        if has_prices:
            estimated_cost = round(
                prompt_tokens * self.input_price_per_million / 1_000_000
                + completion_tokens * self.output_price_per_million / 1_000_000,
                6,
            )
        return DailyStatistics(
            date=local_now.date().isoformat(),
            visitors=int(visitors or 0),
            page_views=int(events["page_views"] or 0),
            uploads=uploads,
            upload_successes=int(events["upload_successes"] or 0),
            upload_failures=int(events["upload_failures"] or 0),
            analyses=analyses,
            successes=successes,
            failures=failures,
            success_rate=round(successes / analyses, 4) if analyses else None,
            average_analysis_ms=round(row["average_ms"]) if row["average_ms"] is not None else None,
            stockfish_ms=int(row["stockfish_ms"] or 0),
            deepseek_ms=int(row["deepseek_ms"] or 0),
            deepseek_prompt_tokens=prompt_tokens,
            deepseek_completion_tokens=completion_tokens,
            deepseek_total_tokens=int(row["total_tokens"] or 0),
            estimated_ai_cost=estimated_cost,
            upload_to_analysis_rate=round(analyses / uploads, 4) if uploads else None,
        )

    def recent_analyses(
        self,
        day: datetime | None = None,
        *,
        limit: int = 50,
    ) -> list[RecentAnalysis]:
        local_now = day.astimezone(self.timezone) if day else datetime.now(self.timezone)
        local_start = datetime.combine(local_now.date(), time.min, tzinfo=self.timezone)
        start = local_start.astimezone(timezone.utc).isoformat(timespec="milliseconds")
        end = (local_start + timedelta(days=1)).astimezone(timezone.utc).isoformat(timespec="milliseconds")
        with self._lock, self._connect() as connection:
            rows = self._execute(
                connection,
                """
                SELECT analysis_id, visitor_id, created_at, completed_at, pgn_length,
                       move_count, stockfish_ms, deepseek_ms, total_ms,
                       prompt_tokens, completion_tokens, total_tokens, status
                FROM analysis_logs
                WHERE created_at >= ? AND created_at < ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (start, end, min(100, max(1, limit))),
            ).fetchall()
        return [RecentAnalysis.model_validate(dict(row)) for row in rows]

    def recent_feedback(
        self,
        day: datetime | None = None,
        *,
        limit: int = 50,
    ) -> list[RecentFeedback]:
        local_now = day.astimezone(self.timezone) if day else datetime.now(self.timezone)
        local_start = datetime.combine(local_now.date(), time.min, tzinfo=self.timezone)
        start = local_start.astimezone(timezone.utc).isoformat(timespec="milliseconds")
        end = (local_start + timedelta(days=1)).astimezone(timezone.utc).isoformat(timespec="milliseconds")
        with self._lock, self._connect() as connection:
            rows = self._execute(
                connection,
                """
                SELECT created_at, visitor_id, analysis_id, rating, suggestion, analysis_result
                FROM events
                WHERE event_name = 'feedback' AND created_at >= ? AND created_at < ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (start, end, min(100, max(1, limit))),
            ).fetchall()
        return [RecentFeedback.model_validate(dict(row)) for row in rows]
