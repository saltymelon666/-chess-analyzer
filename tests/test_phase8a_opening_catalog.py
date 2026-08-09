from __future__ import annotations

from scripts.build_phase8a_opening_catalog import build_catalog


def test_build_catalog_validates_paths_and_builds_parent_links() -> None:
    payload = build_catalog([
        {"eco": "C20", "name": "King's Pawn Game", "pgn": "1. e4 e5"},
        {"eco": "C40", "name": "King's Knight Opening", "pgn": "1. e4 e5 2. Nf3"},
        {"eco": "C50", "name": "Italian Game: Classical Variation", "pgn": "1. e4 e5 2. Nf3 Nc6 3. Bc4 Bc5"},
    ], revision="test")

    assert payload["summary"]["acceptedOpenings"] == 3
    assert payload["summary"]["rejectedRows"] == 0
    knight = next(item for item in payload["openings"] if item["eco"] == "C40")
    pawn = next(item for item in payload["openings"] if item["eco"] == "C20")
    italian = next(item for item in payload["openings"] if item["eco"] == "C50")
    assert knight["parentOpeningIds"] == [pawn["openingId"]]
    assert italian["parentOpeningIds"] == [knight["openingId"]]
    assert italian["familyName"] == "Italian Game"
    assert italian["variationPath"] == ["Classical Variation"]
    assert italian["sanMoves"][-1] == "Bc5"
    assert italian["uciMoves"][-1] == "f8c5"


def test_build_catalog_groups_transposed_terminal_positions() -> None:
    payload = build_catalog([
        {"eco": "A00", "name": "Line One", "pgn": "1. Nf3 d5 2. g3"},
        {"eco": "A00", "name": "Line Two", "pgn": "1. g3 d5 2. Nf3"},
    ], revision="test")

    assert payload["summary"]["transpositionGroups"] == 1
    assert all(len(item["transpositionOpeningIds"]) == 1 for item in payload["openings"])


def test_build_catalog_rejects_invalid_move_text() -> None:
    payload = build_catalog([
        {"eco": "A00", "name": "Broken", "pgn": "1. e5"},
    ], revision="test")

    assert payload["summary"]["acceptedOpenings"] == 0
    assert payload["summary"]["rejectedRows"] == 1
