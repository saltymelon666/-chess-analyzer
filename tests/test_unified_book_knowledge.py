from pathlib import Path
import sqlite3

import chess

from app.unified_book_knowledge import (
    UnifiedBookKnowledgeRepository,
    _board_profile,
    _profile_similarity,
)


def _database(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript("""
            CREATE TABLE sources (
                source_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                author TEXT NOT NULL,
                source_url TEXT NOT NULL
            );
            CREATE TABLE knowledge_records (
                record_id TEXT PRIMARY KEY,
                source_id TEXT NOT NULL,
                record_type TEXT NOT NULL,
                phase TEXT NOT NULL,
                title TEXT NOT NULL,
                locator TEXT NOT NULL,
                fen TEXT,
                position_key TEXT,
                move_san TEXT,
                move_uci TEXT,
                path_uci_json TEXT NOT NULL DEFAULT '[]',
                text_en TEXT NOT NULL,
                text_zh TEXT,
                authority_level TEXT NOT NULL,
                authority_boundary TEXT NOT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{}'
            );
            CREATE VIRTUAL TABLE knowledge_fts USING fts5(
                record_id UNINDEXED, title, text_en, text_zh,
                content='knowledge_records', content_rowid='rowid'
            );
        """)
        connection.execute(
            "INSERT INTO sources VALUES (?, ?, ?, ?)",
            ("source", "Human Chess Book", "Human Author", "https://example.test/book"),
        )
        rows = [
            (
                "exact", "source", "exact_position", "opening", "Exact position", "p. 1",
                "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
                "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq -",
                "", "e2e4", "[]",
                "The central decision is to develop with purpose, connect the pieces, and preserve flexibility before committing the pawn structure.",
                None, "exact_source_position", "Only the exact legal state is authoritative.", "{}",
            ),
            (
                "principle", "source", "chapter_principle", "opening", "Development", "chapter 2",
                None, None, "", "", "[]",
                "Rapid development is valuable when every developing move also improves coordination and makes the next central decision easier.",
                None, "principle_only", "This is a general principle, not a current-position fact.", "{}",
            ),
        ]
        connection.executemany(
            "INSERT INTO knowledge_records VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        connection.execute(
            "INSERT INTO knowledge_fts(rowid, record_id, title, text_en, text_zh) "
            "SELECT rowid, record_id, title, text_en, text_zh FROM knowledge_records"
        )


def test_analysis_context_prefers_exact_position_and_omits_source_fen(tmp_path: Path) -> None:
    database = tmp_path / "books.sqlite3"
    _database(database)
    repository = UnifiedBookKnowledgeRepository(database)

    context = repository.analysis_context(
        "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
        theme_hints=["piece_activity_and_coordination"],
    )
    payload = context.prompt_payload()

    assert context.excerpts[0].relation == "exact_current_position"
    assert context.excerpts[-1].relation == "principle_only"
    assert payload["role"] == "human_chess_book_reasoning_reference"
    assert payload["excerpts"] and context.version == "1.2"
    assert "fen" not in str(payload).lower()
    assert "current-position fact" in context.excerpts[1].authority_boundary


def test_analysis_context_degrades_to_empty_when_database_is_missing(tmp_path: Path) -> None:
    repository = UnifiedBookKnowledgeRepository(tmp_path / "missing.sqlite3")

    context = repository.analysis_context(
        "8/8/8/8/8/8/4K3/7k w - - 0 1",
        theme_hints=["conversion_and_compensation"],
    )

    assert context.excerpts == []
    assert context.requested_themes == ["conversion_and_compensation"]


def test_analysis_context_only_uses_exact_commentary_for_current_decision(
    tmp_path: Path,
) -> None:
    database = tmp_path / "books.sqlite3"
    _database(database)
    repository = UnifiedBookKnowledgeRepository(database)

    matching = repository.analysis_context(
        chess.STARTING_FEN,
        played_move_uci="e2e4",
        best_move_uci="e2e4",
    )
    unrelated = repository.analysis_context(
        chess.STARTING_FEN,
        played_move_uci="d2d4",
        best_move_uci="d2d4",
    )

    assert matching.excerpts[0].relation == "exact_current_position"
    assert all(item.relation != "exact_current_position" for item in unrelated.excerpts)


def test_position_similarity_distinguishes_pawn_advancement_from_shared_files() -> None:
    starting = _board_profile(chess.Board())
    advanced = _board_profile(
        chess.Board("8/8/8/pppppppp/PPPPPPPP/8/8/4K2k w - - 0 1")
    )

    assert _profile_similarity(starting, advanced) < 0.62
