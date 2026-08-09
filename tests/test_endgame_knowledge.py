from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app import api
from app.endgame_knowledge import EndgameKnowledgeRepository
from scripts.export_phase8c_endgame_knowledge import build_runtime_dataset


def _repository(tmp_path) -> EndgameKnowledgeRepository:
    research = {
        "sourceTitle": "Test Endings",
        "sourceYear": 1851,
        "cases": [
            {
                "id": "OK", "placement": "3k4/3p4/3K4/8/8/8/3P4/8",
                "sideToMove": "w", "bookClaim": "draw", "sourcePage": 1,
                "sourcePhrase": "White draws.", "admissionStatus": "exact_verified",
                "tablebase": {"category": "draw", "dtz": 0, "dtm": 0,
                              "computedWhiteOutcome": "draw"},
                "verifiedRoute": [{"uci": "d2d3", "san": "d3"}],
            },
            {
                "id": "CONFLICT", "placement": "6k1/8/4KP2/4N3/8/8/1b6/8",
                "sideToMove": "w", "bookClaim": "white_win", "sourcePage": 2,
                "sourcePhrase": "White wins.", "admissionStatus": "book_tablebase_conflict",
                "tablebase": {"category": "draw", "dtz": 0, "dtm": 0,
                              "computedWhiteOutcome": "draw"},
                "verifiedRoute": [{"uci": "f6f7", "san": "f7+"}],
            },
        ],
    }
    path = tmp_path / "endgames.json"
    path.write_text(json.dumps(build_runtime_dataset(research)), encoding="utf-8")
    return EndgameKnowledgeRepository(path)


def test_exact_lookup_requires_matching_side_to_move(tmp_path) -> None:
    repository = _repository(tmp_path)

    matched = repository.lookup("3k4/3p4/3K4/8/8/8/3P4/8 w - - 8 20")
    wrong_turn = repository.lookup("3k4/3p4/3K4/8/8/8/3P4/8 b - - 0 1")

    assert matched.matched is True
    assert matched.endgame is not None
    assert matched.endgame.key_move.san == "d3"
    assert wrong_turn.matched is False


def test_conflicting_book_result_is_not_exported(tmp_path) -> None:
    repository = _repository(tmp_path)

    result = repository.lookup("6k1/8/4KP2/4N3/8/8/1b6/8 w - - 0 1")

    assert result.matched is False


def test_endgame_lookup_api_does_not_call_engine_or_deepseek(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(api, "endgame_knowledge", _repository(tmp_path))
    response = TestClient(api.app).post("/api/endgame-lookup", json={
        "fen": "3k4/3p4/3K4/8/8/8/3P4/8 w - - 0 1",
    })

    assert response.status_code == 200
    body = response.json()
    assert body["matched"] is True
    assert body["matchType"] == "exact_verified"
    assert body["endgame"]["tablebase"]["computedWhiteOutcome"] == "draw"
    assert body["authorityBoundary"].startswith("残局条目只匹配")


def test_entry_level_source_metadata_overrides_dataset_metadata(tmp_path) -> None:
    payload = build_runtime_dataset({
        "sourceTitle": "Fallback",
        "sourceYear": 1851,
        "cases": [{
            "id": "MIXED", "placement": "3k4/3p4/3K4/8/8/8/3P4/8",
            "sideToMove": "w", "bookClaim": "draw", "sourcePage": 9,
            "sourcePhrase": "White draws.", "admissionStatus": "exact_verified",
            "tablebase": {"category": "draw", "dtz": 0, "dtm": 0,
                          "computedWhiteOutcome": "draw"},
            "verifiedRoute": [{"uci": "d2d3", "san": "d3"}],
        }],
    })
    payload["positions"][0]["source"] = {
        "title": "Another Book", "author": "A. Author", "year": 1921,
        "locator": "Example 9", "url": "https://example.test/book",
    }
    path = tmp_path / "mixed.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    result = EndgameKnowledgeRepository(path).lookup(
        "3k4/3p4/3K4/8/8/8/3P4/8 w - - 0 1"
    )

    assert result.endgame is not None
    assert result.endgame.source_title == "Another Book"
    assert result.endgame.source_author == "A. Author"
    assert result.endgame.source_locator == "Example 9"
