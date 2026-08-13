from __future__ import annotations

import json
from types import SimpleNamespace

import chess
import pytest
from fastapi.testclient import TestClient

from app import api
from app.opening_knowledge import (
    DEFAULT_CLASSIC_OPENING_EXTENSIONS,
    DEFAULT_OPENING_CATALOG,
    OpeningKnowledgeRepository,
    _OPENING_FAMILY_PROFILES,
    _concise_opening_explanation,
    _normalize_opening_explanation_zh,
    _opening_explanation_language_is_natural,
)
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


def test_lookup_prefers_chinese_opening_explanation(repository: OpeningKnowledgeRepository, tmp_path) -> None:
    explanation_path = tmp_path / "translated.json"
    explanation_path.write_text(json.dumps({"explanations": [{
        "pageTitle": "Chess Opening Theory/1. e4/1...e5",
        "pageUrl": "https://en.wikibooks.org/wiki/test",
        "revisionId": 123,
        "license": "CC BY-SA 4.0 / GFDL",
        "attribution": "Wikibooks contributors",
        "uciMoves": ["e2e4", "e7e5"],
        "text": "Both sides contest the centre.",
        "textEn": "Both sides contest the centre.",
        "textZh": "双方争夺中心。",
    }]}), encoding="utf-8")
    translated = OpeningKnowledgeRepository(repository.catalog_path, explanation_path)

    result = translated.lookup(pgn="1. e4 e5 2. Nf3")

    assert result.human_explanation is not None
    assert result.human_explanation.text == "双方争夺中心。"
    assert result.human_explanation.text_en == "Both sides contest the centre."


def test_chinese_opening_explanation_normalizes_colors_and_move_markers() -> None:
    result = _normalize_opening_explanation_zh(
        "White 选择 /3. e3/，Black 回应 /3...c5/。怀特保持中心，布莱克准备反击。"
    )

    assert result == "白方选择3.e3，黑方回应3...c5。白方保持中心，黑方准备反击。"


def test_chinese_opening_explanation_rejects_mojibake() -> None:
    assert _normalize_opening_explanation_zh("ÕâÊÇ»³ÌØ×îÊÜ»¶Ó­") is None


def test_chinese_opening_explanation_preserves_only_source_paragraphs() -> None:
    result = _normalize_opening_explanation_zh(
        "4...Nf6（施密特变体）最常见。主要延续是5.Nc3 Bb4 6.Nxc6 bxc6 "
        "7.Bd3，而不是5.Nxc6 bxc6 6.e5。\n\n"
        "4...Qh4（斯坦尼茨变体）更具侵略性。"
    )

    assert result is not None
    assert result.count("\n\n") == 1
    assert "6.Nxc6 bxc6 7.Bd3，而不是5.Nxc6 bxc6 6.e5。" in result


def test_chinese_opening_explanation_uses_standard_piece_names() -> None:
    result = _normalize_opening_explanation_zh(
        "白方发展主教和骑士，fianchettoed主教控制长对角线。"
        "白方准备城堡王侧。城堡从a1走到e1。"
    )

    assert result == (
        "白方发展象和马， fianchettoed象控制长对角线。"
        "白方准备王翼易位。车从a1走到e1。"
    )


def test_concise_opening_explanation_never_cuts_at_a_move_number() -> None:
    result = _concise_opening_explanation(
        "第一段完整说明。\n\n4...Nf6之后是" + "较长变化" * 80,
        limit=30,
    )

    assert result == "第一段完整说明。"


@pytest.mark.parametrize(
    "text",
    [
        "这是意大利足球的两个主要分支之一。",
        "黑方威胁白方的电子兵。",
        "大多数黑人玩家选择拒绝这一策略。",
        "柏林号以坚固著称。",
        "白方的小子控制中心。",
        "黑方在b7上用剑来对抗长对角线。",
        "黑方准备播放 e5。",
        "白方可以选择更安静的选择。",
        "第一段完整。\n\n3",
        "白方对后形成马节奏。",
        "该脚可能给白方带来问题。",
        "这是英语开场。",
    ],
)
def test_opening_explanation_language_gate_rejects_machine_translation(text: str) -> None:
    assert not _opening_explanation_language_is_natural(text)


def test_presentation_falls_back_when_book_chinese_is_machine_translated(tmp_path) -> None:
    explanation_path = tmp_path / "translated.json"
    explanation_path.write_text(json.dumps({"explanations": [{
        "pageTitle": "Chess Opening Theory/1. e4/1...e5/2. Nf3/2...Nc6/3. Bc4/3...Bc5",
        "pageUrl": "https://en.wikibooks.org/wiki/test",
        "revisionId": 123,
        "license": "CC BY-SA 4.0 / GFDL",
        "attribution": "Wikibooks contributors",
        "uciMoves": ["e2e4", "e7e5", "g1f3", "b8c6", "f1c4", "f8c5"],
        "textZh": "这是意大利足球的两个主要分支之一。",
    }]}), encoding="utf-8")
    repository = OpeningKnowledgeRepository(DEFAULT_OPENING_CATALOG, explanation_path)

    result = repository.presentation_for_moves([
        "e2e4", "e7e5", "g1f3", "b8c6", "f1c4", "f8c5",
    ])

    assert result is not None
    assert result.description == _OPENING_FAMILY_PROFILES["Italian Game"]["description"]


def test_lookup_recognizes_a_transposed_position(repository: OpeningKnowledgeRepository) -> None:
    result = repository.lookup(pgn="1. g3 d5 2. Nf3")

    assert result.match_type == "position_transposition"
    assert result.opening is not None
    assert result.opening.name == "Transposed Development"


def test_presentation_recognizes_an_intermediate_transposed_position(tmp_path) -> None:
    payload = build_catalog([{
        "eco": "A01",
        "name": "Zukertort Opening: Test Development",
        "pgn": "1. Nf3 d5 2. g3 Nf6 3. Bg2",
    }], revision="intermediate-transposition-test")
    path = tmp_path / "intermediate-openings.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    repository = OpeningKnowledgeRepository(path, explanation_path=None)

    result = repository.presentation_for_moves([
        "g2g3", "d7d5", "g1f3", "g8f6",
    ])

    assert result is not None
    assert result.match_type == "position_transposition"
    assert result.query_ply == 4
    assert result.matched_ply == 4


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


@pytest.mark.parametrize(
    ("moves", "expected_name"),
    [
        (["e2e4"], "王兵开局"),
        (["e2e4", "e7e5"], "王兵开局"),
        (["e2e4", "e7e5", "g1f3"], "王翼马开局"),
    ],
)
def test_presentation_keeps_verified_early_opening_nodes_visible(
    moves: list[str], expected_name: str
) -> None:
    repository = OpeningKnowledgeRepository()

    result = repository.presentation_for_moves(moves)

    assert result is not None
    assert result.family_name_zh == expected_name


def test_presentation_translates_italian_greco_branch() -> None:
    repository = OpeningKnowledgeRepository()

    result = repository.presentation_for_moves([
        "e2e4", "e7e5", "g1f3", "b8c6", "f1c4", "f8c5", "c2c3",
        "g8f6", "d2d4", "e5d4", "c3d4", "c5b4", "b1c3", "f6e4",
        "e1g1",
    ])

    assert result is not None
    assert result.variation_name_zh == "吉奥科钢琴变化 · 格列柯进攻"


@pytest.mark.parametrize(
    ("moves", "family_name", "expected_zh"),
    [
        (["e2e4", "c7c5", "g1f3", "d7d6"], "Sicilian Defense", "西西里防御"),
        (["d2d4", "g8f6", "c2c4", "e7e6", "b1c3", "f8b4"], "Nimzo-Indian Defense", "尼姆佐印度防御"),
        (["d2d4", "g8f6", "g1f3", "g7g6", "c1f4"], "London System", "伦敦体系"),
        (["e2e4", "d7d5", "e4d5", "d8d5"], "Scandinavian Defense", "斯堪的纳维亚防御"),
    ],
)
def test_presentation_covers_opening_families_beyond_curated_profiles(
    moves: list[str], family_name: str, expected_zh: str
) -> None:
    repository = OpeningKnowledgeRepository()

    result = repository.presentation_for_moves(moves)

    assert result is not None
    assert result.family_name == family_name
    assert result.family_name_zh == expected_zh
    assert result.description
    assert result.white_plan
    assert result.black_plan


def test_every_catalog_family_with_a_named_four_ply_path_has_presentation() -> None:
    repository = OpeningKnowledgeRepository()
    catalog = json.loads(DEFAULT_OPENING_CATALOG.read_text(encoding="utf-8"))
    representatives: dict[str, list[str]] = {}
    for entry in catalog["openings"]:
        if entry["plyCount"] >= 4:
            representatives.setdefault(entry["familyName"], entry["uciMoves"])

    missing = [
        family
        for family, moves in representatives.items()
        if repository.presentation_for_moves(moves) is None
    ]

    assert len(representatives) == 133
    assert missing == []


def test_all_classic_extension_lines_are_legal_and_presented_at_full_depth() -> None:
    repository = OpeningKnowledgeRepository()
    payload = json.loads(DEFAULT_CLASSIC_OPENING_EXTENSIONS.read_text(encoding="utf-8"))

    results = [
        repository.presentation_for_moves(entry["uciMoves"])
        for entry in payload["openings"]
    ]

    assert len(results) == 44
    assert all(result is not None for result in results)
    assert min(result.query_ply for result in results if result is not None) >= 12
    assert max(result.query_ply for result in results if result is not None) >= 20
    assert all("数据库分支" not in result.display_name for result in results if result is not None)
    assert all("python-chess" in result.source for result in results if result is not None)



def test_presentation_hides_sequence_once_it_is_no_longer_a_catalog_path() -> None:
    repository = OpeningKnowledgeRepository()

    result = repository.presentation_for_moves([
        "e2e4", "e7e5", "g1f3", "b8c6", "f1c4",
        "f8c5", "c2c3", "g8f6", "d2d4", "e5d4", "h2h3",
    ])

    assert result is None


def test_french_advance_euwe_line_remains_in_book_before_be7() -> None:
    repository = OpeningKnowledgeRepository()

    result = repository.presentation_for_moves([
        "e2e4", "e7e6", "d2d4", "d7d5", "e4e5", "c7c5",
        "c2c3", "b8c6", "g1f3", "c8d7", "f1e2", "g8e7",
        "e1g1", "e7g6", "g2g3",
    ])

    assert result is not None
    assert result.family_name_zh == "法兰西防御"
    assert result.variation_name_zh == "推进变化 · 欧威变化"
    assert result.query_ply == 15
    assert "尤威变例" in result.description
    assert result.resolver_version == "opening-context-2"
    assert result.database_revision != "unknown"


def test_every_catalog_prefix_has_a_presentation() -> None:
    repository = OpeningKnowledgeRepository()
    catalog = json.loads(DEFAULT_OPENING_CATALOG.read_text(encoding="utf-8"))
    extensions = json.loads(DEFAULT_CLASSIC_OPENING_EXTENSIONS.read_text(encoding="utf-8"))
    prefixes = {
        tuple(entry["uciMoves"][:length])
        for entry in catalog["openings"] + extensions["openings"]
        for length in range(1, len(entry["uciMoves"]) + 1)
    }

    missing = [prefix for prefix in prefixes if repository.presentation_for_moves(prefix) is None]

    assert len(prefixes) == 8832
    assert missing == []


def test_presentation_has_no_round_or_depth_limit_for_catalog_paths() -> None:
    repository = OpeningKnowledgeRepository()
    catalog = json.loads(DEFAULT_OPENING_CATALOG.read_text(encoding="utf-8"))
    deepest = max(catalog["openings"], key=lambda entry: entry["plyCount"])

    result = repository.presentation_for_moves(deepest["uciMoves"])

    assert result is not None
    assert result.query_ply == deepest["plyCount"]
    assert result.query_ply > 23


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


def test_professional_context_uses_only_moves_before_selected_node(monkeypatch) -> None:
    monkeypatch.setattr(api, "opening_knowledge", OpeningKnowledgeRepository())
    uci_moves = ["e2e4", "e7e5", "g1f3", "b8c6", "f1c4", "f8c5", "c2c3"]
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


def test_professional_context_shows_early_opening_before_nc6(monkeypatch) -> None:
    monkeypatch.setattr(api, "opening_knowledge", OpeningKnowledgeRepository())
    uci_moves = ["e2e4", "e7e5", "g1f3", "b8c6"]
    moves = [
        SimpleNamespace(
            before_fen=chess.STARTING_FEN if index == 0 else "unused",
            played_move=SimpleNamespace(uci=uci),
        )
        for index, uci in enumerate(uci_moves)
    ]

    opening = api._professional_opening_context(moves, 4)

    assert opening is not None
    assert opening.display_name == "王翼马开局"
    assert opening.query_ply == 3


def test_move_review_serializes_opening_context_alias() -> None:
    from app.models import MoveReview

    assert MoveReview.model_fields["opening_context"].alias == "openingContext"


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
