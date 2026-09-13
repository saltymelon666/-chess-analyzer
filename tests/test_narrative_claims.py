import chess

from app.game_review import _move_facts
from app.models import EvidenceFact, MoveFacts
from app.narrative_claims import (
    build_narrative_claim_package,
    compose_verified_core_paragraph,
    evaluate_narrative_claim_grounding,
    resolve_narrative_claims,
)
from app.professional_analysis import (
    apply_hard_fact_guard,
    build_safe_professional_analysis,
    compute_professional_complexity,
)
from app.professional_refs import resolve_professional_draft, validate_professional_draft
from app.professional_validation import (
    build_validation_context,
    validate_professional_analysis,
)
from tests.test_professional_analysis import _line, _valid_reference_draft, professional_review


def _h4_review():
    move = professional_review().model_copy(deep=True)
    before = chess.Board("r5k1/1r3pb1/2N1b2p/1p2P1p1/p1p5/2P2P2/1P3BPP/R3R1K1 w - - 0 36")
    chess_move = chess.Move.from_uci("h2h4")
    san = before.san(chess_move)
    after = before.copy(stack=False)
    after.push(chess_move)
    move.index = 71
    move.move_number = 36
    move.side = "white"
    move.san = san
    move.uci = chess_move.uci()
    move.from_square = "h2"
    move.to_square = "h4"
    move.before_fen = before.fen()
    move.after_fen = after.fen()
    move.played_move = MoveFacts(
        id="move:played:71",
        san=san,
        uci=chess_move.uci(),
        from_square="h2",
        to_square="h4",
        piece="pawn",
        capture=False,
        check=False,
        checkmate=False,
        castling=False,
    )
    move.best_move = move.played_move.model_copy(deep=True)
    move.best_move_uci = chess_move.uci()
    move.best_move_san = san
    move.centipawn_loss = 0
    move.allowed_squares = sorted(set(move.allowed_squares) | {"h2", "h4", "g3", "g5", "d4", "e7"})
    move.candidate_lines = []
    move.actual_move_line = None
    return move


def _rhg1_exchange_review():
    move = professional_review().model_copy(deep=True)
    before = chess.Board("4k2r/8/8/7p/6P1/4K3/7P/R6R w - - 0 1")
    played_move = chess.Move.from_uci("h1g1")
    assert before.san(played_move) == "Rhg1"
    played = _move_facts(before, played_move)
    played.id = "move:played:1"
    after = before.copy(stack=False)
    after.push(played_move)
    actual = _line(
        after,
        1,
        ["h5g4", "g1g4", "h8h2"],
        "line:rhg1-punishment",
    )
    move.side = "white"
    move.san = played.san
    move.uci = played.uci
    move.from_square = played.from_square
    move.to_square = played.to_square
    move.before_fen = before.fen()
    move.after_fen = after.fen()
    move.played_move = played
    move.best_move_uci = "e2f3"
    move.best_move_san = "Kf3"
    move.centipawn_loss = 160
    move.actual_move_line = actual
    move.allowed_squares = [chess.square_name(square) for square in chess.SQUARES]
    move.allowed_moves = [
        played.san,
        played.uci,
        move.best_move_san,
        move.best_move_uci,
        *[item.san for item in actual.moves],
        *[item.uci for item in actual.moves],
    ]
    return move


def _direct_queen_loss_review():
    move = professional_review().model_copy(deep=True)
    before = chess.Board("3rk3/8/8/8/8/8/7P/3Q2K1 w - - 0 1")
    played_move = chess.Move.from_uci("h2h3")
    played = _move_facts(before, played_move)
    played.id = "move:played:1"
    after = before.copy(stack=False)
    after.push(played_move)
    actual = _line(
        after,
        1,
        ["d8d1"],
        "line:direct-queen-loss",
    )
    move.side = "white"
    move.san = played.san
    move.uci = played.uci
    move.from_square = played.from_square
    move.to_square = played.to_square
    move.before_fen = before.fen()
    move.after_fen = after.fen()
    move.played_move = played
    move.best_move_uci = "d1d8"
    move.best_move_san = "Qxd8+"
    move.centipawn_loss = 900
    move.actual_move_line = actual
    move.allowed_squares = [chess.square_name(square) for square in chess.SQUARES]
    move.allowed_moves = [
        played.san,
        played.uci,
        move.best_move_san,
        move.best_move_uci,
        *[item.san for item in actual.moves],
        *[item.uci for item in actual.moves],
    ]
    return move


def _best_move_wins_queen_review():
    move = professional_review().model_copy(deep=True)
    before = chess.Board("q3k3/8/8/8/8/8/8/R5K1 w - - 0 1")
    played_move = chess.Move.from_uci("a1a8")
    played = _move_facts(before, played_move)
    played.id = "move:played:1"
    after = before.copy(stack=False)
    after.push(played_move)
    actual = _line(after, 1, ["e8e7"], "line:best-wins-queen")
    move.side = "white"
    move.san = played.san
    move.uci = played.uci
    move.from_square = played.from_square
    move.to_square = played.to_square
    move.before_fen = before.fen()
    move.after_fen = after.fen()
    move.played_move = played
    move.best_move_uci = played.uci
    move.best_move_san = played.san
    move.centipawn_loss = 0
    move.actual_move_line = actual
    move.allowed_squares = [chess.square_name(square) for square in chess.SQUARES]
    move.allowed_moves = [
        played.san,
        played.uci,
        *[item.san for item in actual.moves],
        *[item.uci for item in actual.moves],
    ]
    return move


def test_verified_claims_do_not_turn_unrelated_route_squares_into_causality() -> None:
    move = _h4_review()
    package = build_narrative_claim_package(move)
    text = "".join(item.statement for item in package.claims)

    assert "新增控制g5" in text
    assert "d4" not in text
    assert "e7" not in text
    assert "支撑" not in text

    analysis = build_safe_professional_analysis(move, compute_professional_complexity(move))
    guarded = apply_hard_fact_guard(analysis, move, narrative_claims=package)
    assert "h4" in guarded.played_move_analysis.intention
    assert "d4" not in guarded.played_move_analysis.intention
    assert "e7" not in guarded.played_move_analysis.intention


def test_unknown_claim_selection_falls_back_to_verified_recommendations() -> None:
    package = build_narrative_claim_package(professional_review())
    resolved = resolve_narrative_claims(package, ["claim:not-real"])
    paragraph = compose_verified_core_paragraph(package, ["claim:not-real"])

    assert resolved
    assert all(item.claim_id in package.claim_ids for item in resolved)
    assert "not-real" not in paragraph
    assert any(item.kind == "evaluation_comparison" for item in resolved)
    assert all(item.kind not in {"move_event", "move_effect", "teaching_rule"} for item in resolved)


def test_global_posture_and_engine_comparison_are_always_in_the_core_path() -> None:
    move = professional_review()
    package = build_narrative_claim_package(move)
    selected = resolve_narrative_claims(package)
    position = next(item for item in package.claims if item.kind == "position_fact")
    comparison = next(
        item for item in package.claims if item.kind == "evaluation_comparison"
    )
    paragraph = compose_verified_core_paragraph(package)

    assert position.claim_id == "claim:1:position:evaluation"
    assert position.source == "stockfish"
    assert not any(character.isdigit() for character in position.statement)
    assert selected[0].kind == "position_fact"
    assert comparison in selected
    assert paragraph.startswith("走棋前")
    assert paragraph.index(position.statement) < paragraph.index(comparison.statement)
    assert "从e2走到e4" not in paragraph
    assert "直接控制的格子" not in paragraph
    assert "类似局面中" not in paragraph


def test_priority_position_fact_cannot_replace_stockfish_global_posture() -> None:
    move = professional_review()
    priority = move.position_facts.piece_activity[0]
    package = build_narrative_claim_package(
        move,
        priority_evidence_ids=[priority.id],
    )
    selected = resolve_narrative_claims(package)
    paragraph = compose_verified_core_paragraph(package)

    assert selected[0].claim_id == "claim:1:position:evaluation"
    assert selected[0].source == "stockfish"
    assert any(priority.id in item.evidence_refs for item in selected)
    assert paragraph.startswith(selected[0].statement)


def test_inferior_move_path_names_reply_without_inventing_a_single_cause() -> None:
    move = professional_review()
    move.best_move_uci = "d2d4"
    move.best_move_san = "d4"
    move.centipawn_loss = 80
    package = build_narrative_claim_package(move)
    selected = resolve_narrative_claims(package)
    kinds = [item.kind for item in selected]
    reply = next(item for item in selected if item.kind == "opponent_resource")
    paragraph = compose_verified_core_paragraph(package)

    assert "move_event" not in kinds
    assert "move_effect" not in kinds
    assert kinds.index("position_fact") < kinds.index("evaluation_comparison")
    assert kinds.index("evaluation_comparison") < kinds.index("opponent_resource")
    assert "teaching_rule" not in kinds
    assert "关键转折在于" in reply.statement
    assert move.actual_move_line is not None
    assert move.actual_move_line.moves[0].san in reply.statement
    assert "不能把后段事件提前说成" in reply.statement
    assert paragraph.index(selected[0].statement) < paragraph.index("关键转折在于")


def test_inferior_move_explains_verified_multi_ply_material_consequence() -> None:
    move = _rhg1_exchange_review()
    package = build_narrative_claim_package(move)
    consequence = next(
        item for item in package.claims if item.kind == "verified_consequence"
    )
    paragraph = compose_verified_core_paragraph(package)

    assert "Rhg1的问题" in consequence.statement
    assert "黑兵先以hxg4吃掉白兵" in consequence.statement
    assert "白车随即以Rxg4回吃这枚黑兵" in consequence.statement
    assert "黑车再以Rxh2吃掉白兵" in consequence.statement
    assert "hxg4让黑兵离开h线" in consequence.statement
    assert "清出了黑车从h8通往h2的线路" in consequence.statement
    assert "黑方净得一兵" in consequence.statement
    assert "直到验证路线结束，这项收益也没有被追回" in consequence.statement
    assert all(item.id in consequence.evidence_refs for item in move.actual_move_line.moves)
    assert consequence.statement in paragraph
    assert "关键转折" not in paragraph

    analysis = build_safe_professional_analysis(
        move,
        compute_professional_complexity(move),
    )
    guarded = apply_hard_fact_guard(
        analysis,
        move,
        narrative_claims=package,
    )
    assert consequence.statement in guarded.played_move_analysis.intention


def test_inferior_move_explains_direct_material_loss_without_generic_square_counts() -> None:
    move = _direct_queen_loss_review()
    package = build_narrative_claim_package(move)
    consequence = next(
        item for item in package.claims if item.kind == "verified_consequence"
    )
    paragraph = compose_verified_core_paragraph(package)

    assert "h3的问题在于对手可以立即兑现子力收益" in consequence.statement
    assert "黑车先以Rxd1+吃掉白后" in consequence.statement
    assert "黑方净得约9分子力" in consequence.statement
    assert "直到验证路线结束，这项收益也没有被追回" in consequence.statement
    assert consequence.statement in paragraph
    assert "关键转折" not in paragraph
    assert "从h2走到h3" not in paragraph
    assert "直接控制的格子" not in paragraph


def test_best_move_explains_favorable_result_after_opponents_reply() -> None:
    move = _best_move_wins_queen_review()
    package = build_narrative_claim_package(move)
    consequence = next(
        item for item in package.claims if item.kind == "verified_consequence"
    )
    paragraph = compose_verified_core_paragraph(package)

    assert "Rxa8+的价值体现在紧接着的强制交换中" in consequence.statement
    assert "白车先以Rxa8+吃掉黑后" in consequence.statement
    assert "这串交换结束后，白方净得约9分子力" in consequence.statement
    assert consequence.statement in paragraph


def test_claim_grounding_metric_requires_the_verified_statement_in_core_text() -> None:
    move = professional_review()
    package = build_narrative_claim_package(move)
    analysis = build_safe_professional_analysis(move, compute_professional_complexity(move))
    selected = resolve_narrative_claims(package)
    analysis.played_move_analysis.claim_refs = [item.claim_id for item in selected]
    analysis.played_move_analysis.intention = compose_verified_core_paragraph(package)

    valid = evaluate_narrative_claim_grounding(analysis, package)
    assert valid.grounding_precision == 1.0
    assert valid.declared_statement_coverage == 1.0
    assert valid.exact_render_match is True
    assert valid.entailed_count == valid.declared_count

    analysis.played_move_analysis.intention = "这是一句没有命题支持的流畅评价。"
    invalid = evaluate_narrative_claim_grounding(analysis, package)
    assert invalid.grounding_precision == 0.0
    assert invalid.declared_statement_coverage == 0.0
    assert invalid.exact_render_match is False
    assert invalid.missing_statements

    analysis.played_move_analysis.intention = compose_verified_core_paragraph(package) + "这步还迫使对手崩溃。"
    extra = evaluate_narrative_claim_grounding(analysis, package)
    assert extra.declared_statement_coverage == 1.0
    assert extra.grounding_precision == 0.0
    assert extra.exact_render_match is False
    assert extra.unexpected_text
    assert extra.unexpected_text == "这步还迫使对手崩溃。"


def test_surface_guard_removes_unverified_route_and_causal_prose() -> None:
    move = professional_review()
    package = build_narrative_claim_package(move)
    analysis = build_safe_professional_analysis(move, compute_professional_complexity(move))
    analysis.played_move_analysis.intention = "这步棋迫使对手崩溃，并为未来攻王创造决定性条件。"
    analysis.played_move_analysis.positive_effects = ["白方因此获得无法阻挡的攻势。"]
    analysis.played_move_analysis.error_type = "both"
    analysis.played_move_analysis.evidence_refs = [move.candidate_lines[-1].id]
    for line in analysis.candidate_lines:
        line.strategy_tags = ["king_attack"]
        line.evidence_refs = [move.candidate_lines[-1].id]
        line.advantages = ["这条路线永久限制对方全部棋子。"]
        line.risks = ["对手也许存在数据中没有出现的反击。"]
        for phase in line.continuation_phases:
            phase.explanation = "第一步造成了后面路线中的所有事件。"

    guarded = apply_hard_fact_guard(analysis, move, narrative_claims=package)
    visible = str(guarded.model_dump(by_alias=True))

    assert "迫使对手崩溃" not in visible
    assert "永久限制" not in visible
    assert "也许存在" not in visible
    assert "造成了后面" not in visible
    assert guarded.played_move_analysis.intention == compose_verified_core_paragraph(
        package,
        guarded.played_move_analysis.claim_refs,
    )
    assert all(not line.risks for line in guarded.candidate_lines)
    assert guarded.played_move_analysis.error_type == "none"
    assert all(not line.strategy_tags for line in guarded.candidate_lines)
    assert [line.evidence_refs for line in guarded.candidate_lines] == [
        [source.id] for source in move.candidate_lines
    ]
    assert all("Stockfish" in line.why_this_rank for line in guarded.candidate_lines)


def test_after_move_tactic_is_not_labeled_as_before_move_position_fact() -> None:
    move = _h4_review()
    move.position_facts.threats = [EvidenceFact(
        id="fact:after-tactic",
        type="fork",
        description="h4后，白兵攻击黑方g5兵。",
        evidence=["h2h4"],
        side="white",
        squares=["h4", "g5"],
    )]

    package = build_narrative_claim_package(
        move,
        priority_evidence_ids=["fact:after-tactic"],
    )
    paragraph = compose_verified_core_paragraph(package)

    assert "走棋前，h4后" not in paragraph
    assert all(item.claim_id != "claim:71:position:1" for item in package.claims)


def test_move_event_detail_is_not_used_as_core_evaluation() -> None:
    move = _h4_review()
    move.played_move.capture = True
    move.played_move.captured_piece = "black_pawn"
    package = build_narrative_claim_package(move)
    paragraph = compose_verified_core_paragraph(package)

    assert "白方走h4" not in paragraph
    assert "这一步吃掉黑兵" not in paragraph


def test_actual_line_first_ply_is_used_as_opponent_reply() -> None:
    move = professional_review()
    package = build_narrative_claim_package(move)
    reply = next(item for item in package.claims if item.kind == "opponent_resource")

    assert move.actual_move_line is not None
    assert move.actual_move_line.moves[0].san in reply.statement
    if len(move.actual_move_line.moves) > 1:
        assert move.actual_move_line.moves[1].san not in reply.statement


def test_non_capture_check_teaching_does_not_claim_a_capture() -> None:
    move = _h4_review()
    move.played_move.check = True
    package = build_narrative_claim_package(move)
    teaching = next(item for item in package.claims if item.kind == "teaching_rule")

    assert "将军" in teaching.statement
    assert "吃子" not in teaching.statement


def test_promotion_effects_name_the_promoted_piece() -> None:
    move = _h4_review()
    before = chess.Board("7k/P7/8/8/8/8/8/7K w - - 0 1")
    chess_move = chess.Move.from_uci("a7a8q")
    after = before.copy(stack=False)
    san = before.san(chess_move)
    after.push(chess_move)
    move.before_fen = before.fen()
    move.after_fen = after.fen()
    move.san = san
    move.uci = chess_move.uci()
    move.from_square = "a7"
    move.to_square = "a8"
    move.played_move = MoveFacts(
        id="move:played:71",
        san=san,
        uci=chess_move.uci(),
        from_square="a7",
        to_square="a8",
        piece="pawn",
        capture=False,
        check=True,
        checkmate=False,
        castling=False,
        promotion="queen",
    )
    move.best_move = move.played_move.model_copy(deep=True)
    move.best_move_uci = chess_move.uci()
    move.best_move_san = san
    move.allowed_squares = [chess.square_name(square) for square in chess.SQUARES]

    package = build_narrative_claim_package(move)
    effects = "".join(
        item.statement for item in package.claims if item.kind == "move_effect"
    )

    assert "这枚后" in effects
    assert "这枚兵新增" not in effects


def test_main_danger_evidence_covers_every_square_and_attacking_side() -> None:
    move = professional_review()
    context = build_validation_context(move, "normal")
    analysis = build_safe_professional_analysis(move, compute_professional_complexity(move))
    analysis.main_danger.side_in_danger = "black"
    analysis.main_danger.level = "immediate"
    analysis.main_danger.description = "白兵从e2走到h7，直接威胁黑兵。"
    analysis.main_danger.consequence = "若不处理，黑方下一回合会丢兵。"
    analysis.main_danger.evidence_refs = ["line:1:ply:1"]

    square_errors = validate_professional_analysis(
        analysis,
        context,
        enforce_length=False,
    )
    assert "mainDanger.evidenceRefs: 每个来源格和目标格都必须有对应证据" in square_errors

    analysis.main_danger.description = "白兵从e2走到e4，直接威胁黑兵。"
    analysis.main_danger.side_in_danger = "white"
    side_errors = validate_professional_analysis(
        analysis,
        context,
        enforce_length=False,
    )
    assert "mainDanger.sideInDanger与威胁证据的行棋方不一致" in side_errors


def test_draft_without_actual_continuation_resolves_without_reply_refs() -> None:
    move = professional_review()
    draft = _valid_reference_draft(move)
    move.actual_move_line = None
    draft.played_move_analysis.strongest_reply_ref = None
    draft.played_move_analysis.ply_refs = []
    draft.comparison.evidence_refs = [draft.candidate_lines[0].line_ref]
    context = build_validation_context(move, "normal")

    assert validate_professional_draft(draft, move, context) == []

    resolved = resolve_professional_draft(draft, move, context)
    assert resolved.played_move_analysis.strongest_response == "没有可用的对手续算路线，无法确认最强回应。"
    assert resolved.played_move_analysis.continuation_phases == []
    assert validate_professional_analysis(resolved, context, enforce_length=False) == []


def test_terminal_position_without_continuation_is_described_as_game_over() -> None:
    move = professional_review()
    draft = _valid_reference_draft(move)
    move.actual_move_line = None
    move.after_fen = "7k/6Q1/6K1/8/8/8/8/8 b - - 0 1"
    assert chess.Board(move.after_fen).is_checkmate()
    draft.played_move_analysis.strongest_reply_ref = None
    draft.played_move_analysis.ply_refs = []

    resolved = resolve_professional_draft(
        draft,
        move,
        build_validation_context(move, "normal"),
    )

    assert resolved.played_move_analysis.strongest_response == "棋局已经结束，没有对手回应。"
    assert resolved.played_move_analysis.continuation_phases == []
