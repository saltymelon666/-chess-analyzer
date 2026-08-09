from __future__ import annotations

import json
from types import SimpleNamespace

import chess
import pytest
from fastapi.testclient import TestClient

from app import api
from app.opening_knowledge import OpeningKnowledgeRepository
from scripts.build_phase8a_opening_catalog import build_catalog


@pytest.fixture
def repository(tmp_path) -> OpeningKnowledgeRepository:
    payload = build_catalog([
        {"eco": "C20", "name": "King's Pawn Game", "pgn": "1. e4 e5"},
        {"eco": "C40", "name": "King's Knight Opening", "pgn": "1. e4 e5 2. Nf3"},
        {"eco": "C50", "name": "Italian Game: Classical Variation", "pgn": "1. e4 e5 2. Nf3 Nc6 3. Bc4 Bc5"},
        {"eco": "A00", "name": "Transposed Development", "pgn": "1. Nf3 d5 2. g3"},
    ], revision="test")
    path = tmp_path / "openings.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    explanation_path = tmp_path / "explanations.json"
    explanation_path.write_text(json.dumps({"explanations": [{
        "pageTitle": "Chess Opening Theory/1. e4/1...e5",
        "pageUrl": "https://en.wikibooks.org/wiki/test",
        "revisionId": 123,
        "license": "CC BY-SA 4.0 / GFDL",
        "attribution": "Wikibooks contributors",
        "uciMoves": ["e2e4", "e7e5"],
        "text": "Both sides contest the centre while developing their pieces.",
    }]}), encoding="utf-8")
    return OpeningKnowledgeRepository(path, explanation_path)


def test_lookup_returns_longest_named_prefix(repository: OpeningKnowledgeRepository) -> None:
    result = repository.lookup(pgn="1. e4 e5 2. Nf3 Nc6 3. Bb5 a6")

    assert result.matched is True
    assert result.match_type == "path_prefix"
    assert result.opening is not None
    assert result.opening.name == "King's Knight Opening"
    assert result.opening.matched_ply == 3


def test_lookup_returns_exact_path_and_next_branches(repository: OpeningKnowledgeRepository) -> None:
    result = repository.lookup(pgn="1. e4 e5 2. Nf3")

    assert result.match_type == "exact_path"
    assert result.opening is not None
    assert result.opening.eco == "C40"
    assert any(branch.uci == "b8c6" for branch in result.next_branches)
    assert result.human_explanation is not None
    assert result.human_explanation.matched_ply == 2
    assert result.human_explanation.license.startswith("CC BY-SA")


def test_lookup_recognizes_a_transposed_position(repository: OpeningKnowledgeRepository) -> None:
    result = repository.lookup(pgn="1. g3 d5 2. Nf3")

    assert result.match_type == "position_transposition"
    assert result.opening is not None
    assert result.opening.name == "Transposed Development"


def test_lookup_by_exact_fen(repository: OpeningKnowledgeRepository) -> None:
    board = chess.Board()
    for move in ("g1f3", "d7d5", "g2g3"):
        board.push_uci(move)

    result = repository.lookup(fen=board.fen())

    assert result.match_type == "exact_fen"
    assert result.opening is not None
    assert result.opening.eco == "A00"


def test_lookup_rejects_mismatched_pgn_and_fen(repository: OpeningKnowledgeRepository) -> None:
    with pytest.raises(ValueError, match="不一致"):
        repository.lookup(pgn="1. e4 e5", fen=chess.STARTING_FEN)


def test_presentation_recognizes_high_confidence_italian_path() -> None:
    repository = OpeningKnowledgeRepository()

    result = repository.presentation_for_moves([
        "e2e4", "e7e5", "g1f3", "b8c6", "f1c4",
        "f8c5", "c2c3", "g8f6", "d2d4", "e5d4",
    ])

    assert result is not None
    assert result.family_name_zh == "意大利开局"
    assert result.variation_name_zh == "古典变化 · 中心进攻变化"
    assert result.confidence == "high"
    assert "d4" in result.white_plan
    assert "d5" in result.black_plan


def test_presentation_hides_shallow_random_prefix() -> None:
    repository = OpeningKnowledgeRepository()

    result = repository.presentation_for_moves([
        "e2e4", "a7a6", "d2d4", "h7h6", "b1c3",
    ])

    assert result is None


def test_nonstandard_start_does_not_gain_path_identity_from_same_uci_moves() -> None:
    repository = OpeningKnowledgeRepository()
    board = chess.Board()
    board.remove_piece_at(chess.A2)

    result = repository.lookup_moves(["e2e4", "e7e5"], initial_fen=board.fen())

    assert result.match_type == "none"
    assert result.opening is None


def test_professional_context_uses_moves_through_selected_node(monkeypatch) -> None:
    monkeypatch.setattr(api, "opening_knowledge", OpeningKnowledgeRepository())
    uci_moves = ["e2e4", "e7e5", "g1f3", "b8c6", "f1c4", "f8c5"]
    moves = [
        SimpleNamespace(
            before_fen=chess.STARTING_FEN if index == 0 else "unused",
            played_move=SimpleNamespace(uci=uci),
        )
        for index, uci in enumerate(uci_moves)
    ]

    opening = api._professional_opening_context(moves, len(moves))

    assert opening is not None
    assert opening.display_name == "意大利开局 · 吉奥科钢琴变化"


def test_opening_lookup_api_uses_no_engine_or_deepseek(monkeypatch, repository) -> None:
    monkeypatch.setattr(api, "opening_knowledge", repository)
    client = TestClient(api.app)

    response = client.post("/api/opening-lookup", json={"pgn": "1. e4 e5 2. Nf3"})

    assert response.status_code == 200
    body = response.json()
    assert body["matched"] is True
    assert body["matchType"] == "exact_path"
    assert body["opening"]["name"] == "King's Knight Opening"
    assert body["authorityBoundary"].startswith("开局目录只提供")


def test_opening_lookup_api_rejects_empty_request() -> None:
    response = TestClient(api.app).post("/api/opening-lookup", json={})

    assert response.status_code == 422
