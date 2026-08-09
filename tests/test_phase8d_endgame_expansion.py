from __future__ import annotations

from scripts.build_phase8d_endgame_expansion import build_expansion, merge_runtime


def _probe(_fen: str) -> dict:
    return {
        "category": "win", "dtz": 1, "dtm": 7,
        "moves": [{"uci": "e2e3"}],
    }


def test_expansion_requires_seven_or_fewer_pieces_and_uses_tablebase_move() -> None:
    payload = {"cases": [{
        "position_id": "TEST", "fen": "8/8/8/8/4k3/8/3KP3/8 w - - 0 1",
        "source_title": "Chess Fundamentals", "author": "Capablanca",
        "source_url": "https://example.test", "rights_boundary": "authorised",
        "locator": "Example", "reference_explanation": "White wins.",
        "annotated_move_uci": None,
    }]}

    result = build_expansion(payload, _probe)

    assert result["summary"]["exactVerifiedCount"] == 1
    assert result["cases"][0]["outcome"] == "white_win"
    assert result["cases"][0]["keyMove"] == {"uci": "e2e3", "san": "e3"}


def test_merge_keeps_only_exact_verified_cases() -> None:
    kling = {"sourceTitle": "Kling", "sourceYear": 1851, "cases": []}
    expansion = {"cases": [
        {
            "id": "OK", "placement": "8/8/8/8/4k3/8/3KP3/8",
            "sideToMove": "w", "outcome": "white_win", "sourcePhrase": "Win.",
            "source": {"title": "Book"},
            "tablebase": {"category": "win", "dtz": 1, "dtm": 7,
                          "computedWhiteOutcome": "white_win"},
            "keyMove": {"uci": "e2e3", "san": "e3"}, "bookMove": None,
            "pieceCount": 3, "admissionStatus": "exact_verified",
            "verificationMethod": "test",
        },
        {
            "id": "NO", "placement": "8/8/8/8/4k3/8/3KP3/8",
            "sideToMove": "b", "outcome": "draw", "sourcePhrase": "Conflict.",
            "source": {"title": "Book"}, "tablebase": {}, "keyMove": None,
            "bookMove": None, "pieceCount": 3,
            "admissionStatus": "book_tablebase_conflict", "verificationMethod": "test",
        },
    ]}

    runtime = merge_runtime(kling, expansion)

    assert [entry["id"] for entry in runtime["positions"]] == ["OK"]
