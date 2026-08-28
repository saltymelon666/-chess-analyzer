from datetime import datetime, timedelta, timezone
from pathlib import Path

import app.analytics as analytics_module
from app.analytics import AnalyticsEventRequest, AnalyticsStore
from app.request_protection import RequestProtector


def test_analytics_records_anonymous_events_and_analysis_usage(tmp_path: Path) -> None:
    store = AnalyticsStore(
        tmp_path / "analytics.sqlite3",
        input_price_per_million=1,
        output_price_per_million=2,
    )
    visitor_id = "visitor_test_1234"
    store.record_event(
        AnalyticsEventRequest(
            visitor_id=visitor_id,
            event="page_view",
            page="/",
            device_info="test browser",
            source_info="https://example.com/path?private=value",
        )
    )
    store.record_event(
        AnalyticsEventRequest(
            visitor_id=visitor_id,
            event="upload_pgn",
            pgn_length=42,
            success=True,
        )
    )
    store.start_analysis("analysis_test_1234", visitor_id, "1. e4 e5")
    store.finish_analysis(
        "analysis_test_1234",
        success=True,
        stockfish_ms=1200,
        total_ms=1200,
        move_count=2,
    )
    store.add_deepseek_usage(
        "analysis_test_1234",
        elapsed_ms=300,
        prompt_tokens=100,
        completion_tokens=50,
        total_tokens=150,
    )

    statistics = store.daily_statistics()
    assert statistics.visitors == 1
    assert statistics.page_views == 1
    assert statistics.uploads == 1
    assert statistics.upload_successes == 1
    assert statistics.upload_failures == 0
    assert statistics.analyses == 1
    assert statistics.successes == 1
    assert statistics.failures == 0
    assert statistics.average_analysis_ms == 1500
    assert statistics.deepseek_total_tokens == 150
    assert statistics.estimated_ai_cost == 0.0002
    assert statistics.upload_to_analysis_rate == 1
    recent = store.recent_analyses()
    assert len(recent) == 1
    assert recent[0].analysis_id == "analysis_test_1234"
    assert recent[0].total_tokens == 150
    with store._connect() as connection:
        source = connection.execute(
            "SELECT source_info FROM visitors WHERE visitor_id = ?", (visitor_id,)
        ).fetchone()[0]
    assert source == "https://example.com"


def test_feedback_event_records_rating_and_suggestion(tmp_path: Path) -> None:
    store = AnalyticsStore(tmp_path / "feedback.sqlite3")
    store.record_event(
        AnalyticsEventRequest(
            visitor_id="visitor_feedback_1234",
            event="feedback",
            page="/",
            analysis_id="analysis_feedback_1234",
            rating=5,
            suggestion="希望增加更多残局示例",
            analysis_result="第 12 回合 · 白方 · Nf3\n\n双方子力接近。",
        )
    )

    with store._connect() as connection:
        feedback = connection.execute(
            "SELECT event_name, rating, suggestion, analysis_id, analysis_result FROM events"
        ).fetchone()

    assert tuple(feedback) == (
        "feedback",
        5,
        "希望增加更多残局示例",
        "analysis_feedback_1234",
        "第 12 回合 · 白方 · Nf3\n\n双方子力接近。",
    )
    recent = store.recent_feedback()
    assert recent[0].rating == 5
    assert recent[0].suggestion == "希望增加更多残局示例"
    assert recent[0].analysis_result.startswith("第 12 回合")


def test_all_time_history_and_feedback_summary_include_older_records(
    tmp_path: Path,
) -> None:
    store = AnalyticsStore(tmp_path / "all-time.sqlite3")
    store.record_event(
        AnalyticsEventRequest(
            visitor_id="visitor_history_1234",
            event="page_view",
            page="/",
        )
    )
    store.start_analysis(
        "analysis_history_1234",
        "visitor_history_1234",
        "1. e4 e5",
    )
    store.record_event(
        AnalyticsEventRequest(
            visitor_id="visitor_history_1234",
            event="feedback",
            rating=3,
            suggestion="希望增加残局内容",
        )
    )
    yesterday = datetime.now(store.timezone) - timedelta(days=1)
    yesterday_start = datetime.combine(
        yesterday.date(), datetime.min.time(), tzinfo=store.timezone
    ).astimezone(timezone.utc)
    old_timestamp = yesterday_start.isoformat(timespec="milliseconds")
    with store._connect() as connection:
        connection.execute("UPDATE events SET created_at = ?", (old_timestamp,))
        connection.execute("UPDATE analysis_logs SET created_at = ?", (old_timestamp,))

    store.record_event(
        AnalyticsEventRequest(
            visitor_id="visitor_today_1234",
            event="page_view",
            page="/",
        )
    )
    store.record_event(
        AnalyticsEventRequest(
            visitor_id="visitor_today_1234",
            event="feedback",
            rating=5,
        )
    )

    daily = store.daily_statistics()
    historical = store.all_time_statistics()
    feedback = store.feedback_summary()

    assert daily.page_views == 1
    assert daily.all_time is not None
    assert daily.all_time.page_views == 2
    assert historical.page_views == 2
    assert [item.analysis_id for item in store.analysis_history()] == [
        "analysis_history_1234"
    ]
    assert len(store.recent_feedback()) == 1
    assert len(store.feedback_history()) == 2
    assert feedback.total_feedback == 2
    assert feedback.rating_count == 2
    assert feedback.average_rating == 4
    assert feedback.suggestion_count == 1


def test_request_protector_rejects_only_after_threshold() -> None:
    protector = RequestProtector()
    assert protector.allow("analysis", "client", per_minute=2, per_day=10).allowed
    assert protector.allow("analysis", "client", per_minute=2, per_day=10).allowed
    assert not protector.allow("analysis", "client", per_minute=2, per_day=10).allowed


def test_deepseek_failure_updates_the_existing_analysis_status(tmp_path: Path) -> None:
    store = AnalyticsStore(tmp_path / "failed-analysis.sqlite3")
    store.start_analysis("analysis_failed_1234", "visitor_failed_1234", "1. e4 e5")
    store.finish_analysis(
        "analysis_failed_1234",
        success=True,
        stockfish_ms=100,
        total_ms=100,
    )
    store.mark_analysis_failed("analysis_failed_1234")

    statistics = store.daily_statistics()
    assert statistics.successes == 0
    assert statistics.failures == 1


def test_event_payload_rejects_unexpected_personal_fields() -> None:
    try:
        AnalyticsEventRequest.model_validate(
            {
                "visitor_id": "visitor_test_1234",
                "event": "page_view",
                "email": "person@example.com",
            }
        )
    except ValueError:
        pass
    else:
        raise AssertionError("analytics payload must reject arbitrary personal fields")


def test_core_events_require_their_measurement_fields() -> None:
    try:
        AnalyticsEventRequest(
            visitor_id="visitor_test_1234",
            event="analysis_complete",
            analysis_id="analysis_test_1234",
        )
    except ValueError:
        pass
    else:
        raise AssertionError("analysis_complete requires duration and success")


def test_postgres_backend_uses_portable_schema_and_placeholders(monkeypatch) -> None:
    executed: list[tuple[str, tuple]] = []

    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, statement, parameters=()):
            executed.append((statement, parameters))
            return self

    class FakePsycopg:
        @staticmethod
        def connect(_url, row_factory=None):
            assert row_factory is not None
            return FakeConnection()

    monkeypatch.setattr(analytics_module, "psycopg", FakePsycopg())
    monkeypatch.setattr(analytics_module, "dict_row", object())
    store = AnalyticsStore("postgresql://example.invalid/pawnlab")
    connection = FakeConnection()
    store._execute(connection, "SELECT ?", (1,))

    statements = "\n".join(statement for statement, _ in executed)
    assert "BIGSERIAL PRIMARY KEY" in statements
    assert "AUTOINCREMENT" not in statements
    assert "ALTER TABLE events ADD COLUMN IF NOT EXISTS rating INTEGER" in statements
    assert "ALTER TABLE events ADD COLUMN IF NOT EXISTS suggestion TEXT" in statements
    assert (
        "ALTER TABLE events ADD COLUMN IF NOT EXISTS analysis_result TEXT" in statements
    )
    assert executed[-1] == ("SELECT %s", (1,))
