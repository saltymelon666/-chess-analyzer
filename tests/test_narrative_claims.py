import chess

from app.models import CandidateLine, EvidenceFact, MoveFacts, VariationMove
from app.narrative_claims import (
    LEGACY_NARRATIVE_MARKERS,
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
from tests.test_professional_analysis import _valid_reference_draft, professional_review


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


def _line_from_uci(board: chess.Board, ucis: list[str]) -> CandidateLine:
    current = board.copy(stack=False)
    route: list[VariationMove] = []
    first_fact: MoveFacts | None = None
    for ply, uci in enumerate(ucis, start=1):
        chess_move = chess.Move.from_uci(uci)
        assert chess_move in current.legal_moves
        piece = current.piece_at(chess_move.from_square)
        captured = current.piece_at(chess_move.to_square)
        san = current.san(chess_move)
        values = dict(
            san=san,
            uci=uci,
            from_square=chess.square_name(chess_move.from_square),
            to_square=chess.square_name(chess_move.to_square),
            piece=chess.piece_name(piece.piece_type),
            capture=current.is_capture(chess_move),
            captured_piece=(
                f"{'white' if captured.color else 'black'}_{chess.piece_name(captured.piece_type)}"
                if captured is not None
                else None
            ),
            check=current.gives_check(chess_move),
            checkmate=False,
            castling=current.is_castling(chess_move),
        )
        if first_fact is None:
            first_fact = MoveFacts(id="line:played:first", **values)
        route.append(VariationMove(
            id=f"line:played:ply:{ply}",
            plyIndex=ply,
            fullMoveNumber=current.fullmove_number,
            side="white" if current.turn else "black",
            **values,
        ))
        current.push(chess_move)
    assert first_fact is not None
    return CandidateLine(
        id="line:played",
        rank=1,
        depth=18,
        evaluation=-424,
        firstMove=first_fact,
        pv=route,
        resultingFen=current.fen(),
        verified=True,
    )


def _rhg1_exchange_review():
    move = professional_review().model_copy(deep=True)
    before = chess.Board("4k2r/8/8/7p/6P1/4K3/7P/R6R w - - 0 1")
    played_move = chess.Move.from_uci("h1g1")
    san = before.san(played_move)
    after = before.copy(stack=False)
    after.push(played_move)
    move.side = "white"
    move.san = san
    move.uci = played_move.uci()
    move.from_square = "h1"
    move.to_square = "g1"
    move.before_fen = before.fen()
    move.after_fen = after.fen()
    move.played_move = MoveFacts(
        id="move:played:1",
        san=san,
        uci=played_move.uci(),
        from_square="h1",
        to_square="g1",
        piece="rook",
        capture=False,
        check=False,
        checkmate=False,
        castling=False,
    )
    move.best_move_uci = "e3f3"
    move.best_move_san = "Kf3"
    move.centipawn_loss = 160
    move.actual_move_line = _line_from_uci(after, ["h5g4", "g1g4", "h8h2"])
    move.allowed_squares = [chess.square_name(square) for square in chess.SQUARES]
    move.allowed_moves = [
        san,
        played_move.uci(),
        "Kf3",
        "e3f3",
        *[item.san for item in move.actual_move_line.moves],
        *[item.uci for item in move.actual_move_line.moves],
    ]
    return move


def _closed_center_flank_choice_review():
    move = professional_review().model_copy(deep=True)
    before = chess.Board("r2qk3/7n/4pp2/1N1pP3/3P4/8/Q4P2/3R2K1 w - - 0 1")
    played_move = chess.Move.from_uci("f2f4")
    san = before.san(played_move)
    after = before.copy(stack=False)
    after.push(played_move)
    best_line = _line_from_uci(
        before,
        ["a2b3", "f6e5", "d4e5", "d8c7", "f2f4"],
    )
    best_line.id = "line:best"
    actual_line = _line_from_uci(
        after,
        [
            "f6e5", "d4e5", "d8e7", "a2a5", "h7f8",
            "d1d3", "a8b8", "d3c3", "e8f7", "b5d4",
        ],
    )
    actual_line.id = "line:actual"
    move.side = "white"
    move.san = san
    move.uci = played_move.uci()
    move.from_square = "f2"
    move.to_square = "f4"
    move.before_fen = before.fen()
    move.after_fen = after.fen()
    move.before.centipawn = 124
    move.before.evaluation = "+1.24"
    move.after.centipawn = 105
    move.after.evaluation = "+1.05"
    move.played_move = MoveFacts(
        id="move:played:1",
        san=san,
        uci=played_move.uci(),
        from_square="f2",
        to_square="f4",
        piece="pawn",
        capture=False,
        check=False,
        checkmate=False,
        castling=False,
    )
    move.best_move = best_line.first_move.model_copy(deep=True)
    move.best_move_uci = "a2b3"
    move.best_move_san = "Qb3"
    move.centipawn_loss = 19
    move.quality_label = "好棋"
    move.candidate_lines = [best_line]
    move.actual_move_line = actual_line
    move.allowed_squares = [chess.square_name(square) for square in chess.SQUARES]
    move.allowed_moves = [
        san,
        played_move.uci(),
        "Qb3",
        "a2b3",
        *[item.san for item in best_line.moves],
        *[item.uci for item in best_line.moves],
        *[item.san for item in actual_line.moves],
        *[item.uci for item in actual_line.moves],
    ]
    return move


def _quiet_reply_pressure_review():
    move = professional_review().model_copy(deep=True)
    before = chess.Board("7k/6b1/3q4/4P3/8/2N5/8/7K b - - 0 1")
    played_move = chess.Move.from_uci("g7e5")
    san = before.san(played_move)
    after = before.copy(stack=False)
    after.push(played_move)
    best_line = _line_from_uci(before, ["d6d8", "c3e4"])
    best_line.id = "line:best"
    actual_line = _line_from_uci(after, ["c3e4", "d6e7"])
    actual_line.id = "line:actual"
    move.side = "black"
    move.san = san
    move.uci = played_move.uci()
    move.from_square = "g7"
    move.to_square = "e5"
    move.before_fen = before.fen()
    move.after_fen = after.fen()
    move.before.centipawn = -2
    move.before.evaluation = "-0.02"
    move.after.centipawn = 93
    move.after.evaluation = "+0.93"
    move.played_move = MoveFacts(
        id="move:played:1",
        san=san,
        uci=played_move.uci(),
        from_square="g7",
        to_square="e5",
        piece="bishop",
        capture=True,
        captured_piece="white_pawn",
        check=False,
        checkmate=False,
        castling=False,
    )
    move.best_move = best_line.first_move.model_copy(deep=True)
    move.best_move_uci = best_line.first_move.uci
    move.best_move_san = best_line.first_move.san
    move.centipawn_loss = 95
    move.quality_key = "mistake"
    move.quality_symbol = "?"
    move.quality_label = "错误"
    move.candidate_lines = [best_line]
    move.actual_move_line = actual_line
    move.allowed_squares = [chess.square_name(square) for square in chess.SQUARES]
    move.allowed_moves = [
        san,
        played_move.uci(),
        *[item.san for item in best_line.moves],
        *[item.uci for item in best_line.moves],
        *[item.san for item in actual_line.moves],
        *[item.uci for item in actual_line.moves],
    ]
    return move


def _multi_ply_reply_maneuver_review():
    move = professional_review().model_copy(deep=True)
    before = chess.Board("r1b3kr/6b1/q2N4/2pPB3/8/8/1P6/2B2RK1 b - - 0 1")
    played_move = chess.Move.from_uci("g7e5")
    san = before.san(played_move)
    after = before.copy(stack=False)
    after.push(played_move)
    best_line = _line_from_uci(before, ["a6d6", "g1g2"])
    best_line.id = "line:best"
    actual_line = _line_from_uci(
        after,
        [
            "d6e4",
            "c8f5",
            "f1e1",
            "e5g7",
            "e4c5",
            "a6c4",
            "c1e3",
            "a8d8",
            "d5d6",
            "g7b2",
        ],
    )
    actual_line.id = "line:actual"
    move.side = "black"
    move.san = san
    move.uci = played_move.uci()
    move.from_square = "g7"
    move.to_square = "e5"
    move.before_fen = before.fen()
    move.after_fen = after.fen()
    move.before.centipawn = -2
    move.before.evaluation = "-0.02"
    move.after.centipawn = 93
    move.after.evaluation = "+0.93"
    move.played_move = MoveFacts(
        id="move:played:1",
        san=san,
        uci=played_move.uci(),
        from_square="g7",
        to_square="e5",
        piece="bishop",
        capture=True,
        captured_piece="white_bishop",
        check=False,
        checkmate=False,
        castling=False,
    )
    move.best_move = best_line.first_move.model_copy(deep=True)
    move.best_move_uci = "a6d6"
    move.best_move_san = "Qxd6"
    move.centipawn_loss = 95
    move.quality_key = "mistake"
    move.quality_symbol = "?"
    move.quality_label = "错误"
    move.candidate_lines = [best_line]
    move.actual_move_line = actual_line
    move.allowed_squares = [chess.square_name(square) for square in chess.SQUARES]
    move.allowed_moves = [
        san,
        played_move.uci(),
        *[item.san for item in actual_line.moves],
        *[item.uci for item in actual_line.moves],
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
    assert all(item.kind != "teaching_rule" for item in resolved)
    assert "下一次遇到" not in paragraph


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
    assert paragraph.startswith(position.statement)
    assert paragraph.index(position.statement) < paragraph.index(comparison.statement)
    assert "从e2来到e4" not in paragraph
    assert "直接控制的格子" not in paragraph
    assert not any(marker in paragraph for marker in LEGACY_NARRATIVE_MARKERS)
    assert "检查顺序" not in paragraph
    assert "评价差距不足以支持" not in paragraph


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
    assert all(priority.id not in item.evidence_refs for item in selected)
    assert paragraph.startswith(selected[0].statement)
    assert "更具体地说" not in paragraph


def test_small_gap_wording_matches_the_existing_position_posture() -> None:
    move = professional_review().model_copy(deep=True)
    move.best_move_uci = "d2d4"
    move.best_move_san = "d4"
    move.centipawn_loss = 20

    move.before.centipawn = -424
    losing_text = compose_verified_core_paragraph(build_narrative_claim_package(move))
    assert "白方的困难在落子前已经存在" in losing_text

    move.before.centipawn = 180
    winning_text = compose_verified_core_paragraph(build_narrative_claim_package(move))
    assert "白方原有的优势在落子前已经形成" in winning_text
    assert "白方的困难" not in winning_text

    move.before.centipawn = 0
    balanced_text = compose_verified_core_paragraph(build_narrative_claim_package(move))
    assert "没有显著打破原有的平衡" in balanced_text
    assert "困难" not in balanced_text


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
    assert "首选回应" in reply.statement
    assert move.actual_move_line is not None
    assert move.actual_move_line.moves[0].san in reply.statement
    assert "验证路线" not in reply.statement
    assert "不能倒推" not in reply.statement
    assert paragraph.index(selected[0].statement) < paragraph.index(reply.statement)


def test_quiet_reply_explains_verified_attack_and_answering_tempo() -> None:
    move = _quiet_reply_pressure_review()
    package = build_narrative_claim_package(move)
    cause = next(item for item in package.claims if item.kind == "position_cause")
    paragraph = compose_verified_core_paragraph(package)

    assert "Bxe5的问题在于给了对手一个带攻击的主动节奏" in cause.statement
    assert "Ne4让白马从c3来到e4" in cause.statement
    assert "新增攻击d6的黑后" in cause.statement
    assert "黑方随后以Qe7把黑后移出这枚马的攻击范围" in cause.statement
    assert "白方在调动白马的同时，让黑方先回应对重要子力的攻击" in cause.statement
    assert cause.evidence_refs == [
        "line:actual",
        "line:played:ply:1",
        "line:played:ply:2",
    ]
    assert cause.statement in paragraph
    assert "首选回应" not in paragraph


def test_quiet_reply_does_not_claim_tempo_without_an_immediate_answer() -> None:
    move = _quiet_reply_pressure_review()
    after = chess.Board(move.after_fen)
    move.actual_move_line = _line_from_uci(after, ["c3e4", "h8g8"])
    move.actual_move_line.id = "line:actual"

    package = build_narrative_claim_package(move)
    paragraph = compose_verified_core_paragraph(package)

    assert all(item.kind != "position_cause" for item in package.claims)
    assert "带攻击的主动节奏" not in paragraph
    assert "首选回应是Ne4" in paragraph


def test_quiet_reply_explains_same_piece_multi_ply_capture() -> None:
    move = _multi_ply_reply_maneuver_review()
    package = build_narrative_claim_package(move)
    cause = next(item for item in package.claims if item.kind == "position_cause")
    paragraph = compose_verified_core_paragraph(package)

    assert "Ne4把白马从d6转到e4" in cause.statement
    assert "瞄住c5的黑兵" in cause.statement
    assert "经过Bf5、Re1、Bg7后，还是这枚马以Nxc5吃掉该子" in cause.statement
    assert "Ne4不是单纯调子，而是在为Nxc5改善落点" in cause.statement
    assert "不能把全部95 cp评价变化只归因于这一处" in cause.statement
    assert cause.evidence_refs == [
        "line:actual",
        "line:played:ply:1",
        "line:played:ply:2",
        "line:played:ply:3",
        "line:played:ply:4",
        "line:played:ply:5",
    ]
    assert cause.statement in paragraph
    assert "首选回应" not in paragraph


def test_quiet_reply_rejects_capture_by_another_same_type_piece() -> None:
    move = _multi_ply_reply_maneuver_review()
    after = chess.Board(move.after_fen)
    after.set_piece_at(chess.D3, chess.Piece(chess.KNIGHT, chess.WHITE))
    move.after_fen = after.fen()
    move.actual_move_line = _line_from_uci(
        after,
        ["d6e4", "c8f5", "f1e1", "e5g7", "d3c5"],
    )
    move.actual_move_line.id = "line:actual"

    package = build_narrative_claim_package(move)
    assert all(item.kind != "position_cause" for item in package.claims)


def test_quiet_reply_rejects_when_tracked_piece_is_captured_first() -> None:
    move = _multi_ply_reply_maneuver_review()
    after = chess.Board(move.after_fen)
    after.set_piece_at(chess.F5, chess.Piece(chess.BISHOP, chess.BLACK))
    move.after_fen = after.fen()
    move.actual_move_line = _line_from_uci(after, ["d6e4", "f5e4"])
    move.actual_move_line.id = "line:actual"

    package = build_narrative_claim_package(move)
    assert all(item.kind != "position_cause" for item in package.claims)


def test_quiet_reply_rejects_when_original_target_moves_before_capture() -> None:
    move = _multi_ply_reply_maneuver_review()
    after = chess.Board(move.after_fen)
    move.actual_move_line = _line_from_uci(
        after,
        ["d6e4", "c5c4", "f1e1", "c4c3", "e4c5"],
    )
    move.actual_move_line.id = "line:actual"

    package = build_narrative_claim_package(move)
    assert all(item.kind != "position_cause" for item in package.claims)


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
    assert consequence.statement in paragraph
    assert "首选回应" not in paragraph

    analysis = build_safe_professional_analysis(
        move,
        compute_professional_complexity(move),
    )
    guarded = apply_hard_fact_guard(analysis, move, narrative_claims=package)
    assert consequence.statement in guarded.played_move_analysis.intention


def test_small_gap_flank_push_explains_plan_timing_and_central_reply() -> None:
    move = _closed_center_flank_choice_review()
    package = build_narrative_claim_package(move)
    choice = next(item for item in package.claims if item.kind == "verified_choice")
    comparison = next(
        item for item in package.claims if item.kind == "evaluation_comparison"
    )
    paragraph = compose_verified_core_paragraph(package)

    assert "f4的棋理价值，是利用封闭中心先在王翼争取空间" in choice.statement
    assert "黑方立即以fxe5换掉e5兵" in choice.statement
    assert "d4兵被带到e5，中心兵型先发生了转换" in choice.statement
    assert "白方还要用Qa5、Rd3、Rc3重新组织子力" in choice.statement
    assert "f4没有立即形成翼侧突破" in choice.statement
    assert "首选路线并没有放弃f4" in choice.statement
    assert "先走Qb3" in choice.statement
    assert "与实战的真正区别是次序，不是进攻方向" in choice.statement
    assert "它不是失误，真正的差别在计划执行次序" in comparison.statement
    assert "Stockfish评价从+1.24变为+1.05" in comparison.statement
    assert "评价损失19 cp" in comparison.statement
    assert "走法等级为“好棋”" in comparison.statement
    assert paragraph.index(comparison.statement) < paragraph.index(choice.statement)
    assert "只是选择的侧重点不同" not in paragraph


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

    assert "白方选择h4" not in paragraph
    assert "这一步吃掉黑兵" not in paragraph


def test_actual_line_first_ply_is_used_as_opponent_reply() -> None:
    move = professional_review()
    move.best_move_uci = "d2d4"
    move.best_move_san = "d4"
    move.centipawn_loss = 80
    package = build_narrative_claim_package(move)
    reply = next(item for item in package.claims if item.kind == "opponent_resource")

    assert move.actual_move_line is not None
    assert move.actual_move_line.moves[0].san in reply.statement
    if len(move.actual_move_line.moves) > 1:
        assert move.actual_move_line.moves[1].san not in reply.statement


def test_non_capture_check_does_not_add_a_generic_teaching_checklist() -> None:
    move = _h4_review()
    move.played_move.check = True
    package = build_narrative_claim_package(move)
    paragraph = compose_verified_core_paragraph(package)

    assert "形成将军" not in paragraph
    assert "先检查" not in paragraph
    assert all(item.kind != "teaching_rule" for item in package.claims)


def test_pin_then_capture_is_explained_as_the_concrete_advantage() -> None:
    move = professional_review().model_copy(deep=True)
    before = chess.Board("r6k/8/8/8/8/8/P3N3/5K2 w - - 0 1")
    played = chess.Move.from_uci("f1e1")
    after = before.copy(stack=False)
    san = before.san(played)
    after.push(played)
    move.side = "white"
    move.before_fen = before.fen()
    move.after_fen = after.fen()
    move.before.centipawn = -424
    move.before.mate_in = None
    move.played_move = MoveFacts(
        id="move:played:1",
        san=san,
        uci=played.uci(),
        from_square="f1",
        to_square="e1",
        piece="king",
        capture=False,
        check=False,
        checkmate=False,
        castling=False,
    )
    move.best_move = move.played_move.model_copy(deep=True)
    move.best_move_uci = played.uci()
    move.best_move_san = san
    move.centipawn_loss = 0
    move.actual_move_line = _line_from_uci(after, ["a8e8", "a2a3", "e8e2"])

    package = build_narrative_claim_package(move)
    cause = next(item for item in package.claims if item.kind == "position_cause")
    paragraph = compose_verified_core_paragraph(package)

    assert "Re8把白马钉在王前" in cause.statement
    assert "这匹马因此一步也不能走" in cause.statement
    assert "白方走出a3后，Rxe2+随即吃掉这枚马" in cause.statement
    assert "黑方优势的具体落点" in cause.statement
    assert cause.statement in paragraph
    assert cause.evidence_refs == [
        "line:played",
        "line:played:ply:1",
        "line:played:ply:2",
        "line:played:ply:3",
    ]


def test_pin_without_a_later_capture_is_not_promoted_to_a_cause() -> None:
    move = professional_review().model_copy(deep=True)
    after = chess.Board("r6k/8/8/8/8/8/P3N3/4K3 b - - 1 1")
    move.after_fen = after.fen()
    move.actual_move_line = _line_from_uci(after, ["a8e8", "a2a3"])

    package = build_narrative_claim_package(move)

    assert all(item.kind != "position_cause" for item in package.claims)
    assert "捉死" not in compose_verified_core_paragraph(package)


def test_existing_absolute_pin_can_explain_an_immediate_piece_loss() -> None:
    move = professional_review().model_copy(deep=True)
    after = chess.Board("4r2k/8/8/8/8/8/P3N3/4K3 b - - 1 1")
    move.after_fen = after.fen()
    move.before.centipawn = -424
    move.actual_move_line = _line_from_uci(after, ["e8e2"])

    package = build_narrative_claim_package(move)
    cause = next(item for item in package.claims if item.kind == "position_cause")

    assert "白马已经被钉在王前" in cause.statement
    assert "Rxe2+随即吃掉这枚马" in cause.statement
    assert cause.evidence_refs == ["line:played", "line:played:ply:1"]


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
