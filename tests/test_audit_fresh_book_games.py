import json
from pathlib import Path

from scripts.audit_fresh_book_games import _source_positions


ROOT = Path(__file__).resolve().parents[1]


def test_fresh_book_audit_uses_distinct_games_and_new_legal_positions() -> None:
    positions = _source_positions()
    previous = json.loads(
        (ROOT / "tests/fixtures/professional_validation_positions.json").read_text(encoding="utf-8")
    )
    previous_boards = {item["fen"].split(" ", 4)[0] for item in previous if item.get("fen")}

    assert len(positions) == 15
    assert len({(item["sourceId"], item["title"]) for item in positions}) == 15
    assert {phase: sum(item["phase"] == phase for item in positions)
            for phase in ("opening", "middlegame", "endgame")} == {
        "opening": 5, "middlegame": 5, "endgame": 5,
    }
    assert all(item["fen"].split(" ", 4)[0] not in previous_boards for item in positions)


def test_second_book_batch_excludes_all_first_batch_game_titles() -> None:
    first = _source_positions()
    excluded = {item["title"] for item in first}
    second = _source_positions(excluded)
    assert len(second) == 15
    assert not excluded.intersection(item["title"] for item in second)
    assert not {item["fen"] for item in first}.intersection(item["fen"] for item in second)
