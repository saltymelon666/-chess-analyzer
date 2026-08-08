from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import chess
import pytest

from app.config import load_settings
from app.engine import StockfishService
from app.game_review import analyze_pgn
from app.professional_analysis import (
    build_professional_payload,
    build_safe_professional_analysis,
    compute_professional_complexity,
    professional_user_prompt,
)
from app.professional_validation import build_validation_context, validate_professional_analysis


ROOT = Path(__file__).resolve().parent.parent
FIXTURE_PATH = ROOT / "tests" / "fixtures" / "professional_validation_positions.json"


def load_positions() -> list[dict]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def test_validation_set_has_required_categories_and_metadata() -> None:
    positions = load_positions()
    assert len(positions) == 15
    assert Counter(item["category"] for item in positions) == {
        "simple_opening": 3,
        "direct_tactics": 3,
        "king_attack": 3,
        "center_counter": 2,
        "closed_center_wing_attack": 2,
        "simplification_endgame": 2,
    }
    for item in positions:
        assert item["source"]["description"]
        assert item["expected"]["mainDanger"]
        assert item["expected"]["strategy"]["white"]
        assert item["expected"]["strategy"]["black"]
        assert item["expected"]["forbiddenConclusions"]


@pytest.mark.parametrize("position", load_positions(), ids=lambda item: item["id"])
def test_validation_set_routes_are_legal_and_from_the_saved_position(position: dict) -> None:
    board = chess.Board(position["fen"])
    assert board.is_valid()
    assert position["sideToMove"] == ("white" if board.turn else "black")

    played = chess.Move.from_uci(position["playedMove"]["uci"])
    assert played in board.legal_moves
    assert board.san(played) == position["playedMove"]["san"]

    lines = position["stockfishLines"]
    assert [line["rank"] for line in lines] == [1, 2, 3]
    for line in lines:
        route_board = board.copy(stack=False)
        assert 1 <= len(line["plies"]) <= 10
        for index, ply in enumerate(line["plies"], 1):
            move = chess.Move.from_uci(ply["uci"])
            assert move in route_board.legal_moves
            assert route_board.san(move) == ply["san"]
            assert ply["id"] == f"line:{line['rank']}:ply:{index}"
            route_board.push(move)


@pytest.mark.asyncio
async def test_all_validation_positions_build_compact_strict_fact_packages() -> None:
    settings = load_settings()
    engine = StockfishService(
        settings.stockfish_path,
        depth=10,
        threads=1,
        hash_mb=32,
        multipv=3,
        timeout_seconds=60,
    )
    for position in load_positions():
        review = await analyze_pgn(
            pgn=position["pgn"],
            stockfish=engine,
            analysis_id=f"test-{position['id']}",
            depth=10,
            timeout_seconds=90,
            max_plies=2,
        )
        move = review.moves[0]
        complexity = compute_professional_complexity(move)
        context = build_validation_context(move, complexity.level)
        prompt = professional_user_prompt(
            build_professional_payload(move, complexity, context.allowed_evidence_ids),
            complexity.level,
        )
        safe = build_safe_professional_analysis(move, complexity)

        assert len(move.candidate_lines) == 3
        assert all(len(line.moves) <= 10 for line in move.candidate_lines)
        assert len(prompt) < 60_000
        assert validate_professional_analysis(safe, context) == []
