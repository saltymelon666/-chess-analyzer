import chess
import pytest

from app.book_mechanisms import build_book_mechanism
from app.analysis_focus import select_analysis_focus
from app.game_review import _move_facts
from app.narrative_claims import (build_narrative_claim_package, compose_verified_core_paragraph,
                                  resolve_narrative_claims, VerifiedNarrativeClaim)
from app.position_facts import extract_position_facts
from app.professional_analysis import apply_hard_fact_guard, build_safe_professional_analysis, compute_professional_complexity
from app.professional_validation import build_validation_context, validate_professional_analysis
from tests.test_professional_analysis import _line, professional_review


def _san_line(board, sans, line_id):
    current = board.copy(stack=False)
    ucis = []
    for san in sans.split():
        move = current.parse_san(san)
        ucis.append(move.uci())
        current.push(move)
    return _line(board, 1, ucis, line_id)


def _review(fen, san, actual, best=None):
    move = professional_review().model_copy(deep=True)
    before = chess.Board(fen)
    played = before.parse_san(san)
    after = before.copy(stack=False)
    after.push(played)
    move.before_fen, move.after_fen = before.fen(), after.fen()
    move.played_move = _move_facts(before, played)
    move.played_move.id = "move:played:1"
    move.san, move.uci = san, played.uci()
    move.side = "white" if before.turn else "black"
    move.from_square, move.to_square = move.played_move.from_square, move.played_move.to_square
    move.actual_move_line = _san_line(after, actual, "line:played")
    primary = _san_line(before, best or f"{san} {actual}", "line:1")
    move.candidate_lines = [primary]
    move.best_move = primary.first_move
    move.best_move_san, move.best_move_uci = primary.first_move.san, primary.first_move.uci
    move.position_facts = extract_position_facts(before.fen(), candidate_lines=[primary], actual_move_line=move.actual_move_line, tactics=[], namespace="move-1-before")
    move.position_facts_after = extract_position_facts(after.fen(), candidate_lines=[], actual_move_line=None, tactics=[], namespace="move-1-after")
    move.allowed_squares = [chess.square_name(sq) for sq in chess.SQUARES]
    move.allowed_moves = [fact.san for line in [primary, move.actual_move_line] for fact in line.moves]
    return move


CASES = [
    ("6k1/1p3pp1/p2p3p/8/4nP2/P5PP/1PP1N3/2K5 b - - 3 25", "Kf8",
     "b4 b5 c3 Ke7 Kc2 Ke6 Nd4+ Kf6 Kd3 Nf2+", None, "g8—f8—e7—e6", "靠近f4兵"),
    ("3K3q/1P3k2/3Q4/8/7p/8/8/8 w - - 4 79", "Kd7",
     "Qe8+ Kc7 Qh8 b8=Q Qxb8+ Kxb8 h3 Kc7 Ke8 Qf4", None, "升变兵由此换掉了对手的后", "d6后仍在"),
    ("3r2k1/p1rn1p1p/1p2pp1q/8/3PQN1P/5PP1/P1P2R2/R5K1 w - - 1 22", "g4",
     "Rc3 Raf1 Kh8 Rg2 Rdc8 g5 Qg7 Kh1 f5 Qe1", None, "g2支援g4兵", "控制f5"),
    ("8/1p4k1/pNp1b3/2P5/PP1P1pp1/2K5/5PPr/6R1 w - - 0 33", "d5",
     "cxd5 Kd4 Kf7 a5 f3 gxf3 gxf3 b5 Rxf2 c6", None, "让开了c5兵前方的格子", "c6便利用"),
    ("2b1r3/p3q1kp/1p2Prp1/2p1Rp2/2P5/2QB4/PP3PPP/4R1K1 w - - 1 25", "Be2",
     "Qd6 Rd1 Qf8 Bf3 Rexe6 Red5 Re8 R5d3 f4 Bc6",
     "Bc2 Kg8 Ba4 Rd8 Bc6 Rd6 Bd5 Bxe6 Qe3 Kf7", "d3—c2—a4—c6—d5", "保护e6通路兵"),
]


@pytest.mark.asyncio
async def test_kf8_with_losing_kh8_candidate_returns_valid_professional_prose():
    from app.chess_facts import build_move_fact_package
    from app.professional_analysis import ProfessionalAnalysisService
    from app.threat_analysis import ThreatAnalyzer

    move = _review("6k1/pp4pp/4B3/3R4/8/3P2P1/Prn2PKP/8 b - - 2 27",
                   "Kf8", "Rd7 h5 Kf3 a5 d4 Ne1+ Ke3 Ng2+ Ke4 Re2+")
    losing = _san_line(chess.Board(move.before_fen), "Kh8 Rd8#", "line:2")
    losing.rank = 2
    losing.mate_in = 1
    move.candidate_lines.append(losing)
    move.allowed_moves.extend(fact.san for fact in losing.moves)
    move.position_facts = extract_position_facts(
        move.before_fen, candidate_lines=move.candidate_lines,
        actual_move_line=move.actual_move_line, tactics=[], namespace="move-1-before")
    threats = ThreatAnalyzer().classify(build_move_fact_package(move))
    assert not any(t.type == "mate_threat" and t.side == "black" for t in threats.threats)
    service = ProfessionalAnalysisService(api_key="", base_url="https://example.invalid",
                                          model="test", timeout_seconds=1)
    result = await service.analyze(move, threat_package=threats)
    assert result.analysis is not None
    context = build_validation_context(move, compute_professional_complexity(move).level)
    assert validate_professional_analysis(result.analysis, context) == []
    core = result.analysis.played_move_analysis.intention
    assert core and all(term not in core for term in ("后评价", " cp", "定级", "首选是"))


@pytest.mark.parametrize("fen,san,actual,best,first,second", CASES)
def test_book_explanations_include_verified_mechanism_and_continuation(fen, san, actual, best, first, second):
    move = _review(fen, san, actual, best)
    mechanism = build_book_mechanism(move)
    assert mechanism is not None
    assert first in mechanism.statement and second in mechanism.statement
    context = build_validation_context(move, "normal")
    assert set(mechanism.evidence_refs) <= context.allowed_evidence_ids
    core = compose_verified_core_paragraph(build_narrative_claim_package(move))
    assert first in core and second in core


@pytest.mark.parametrize("field,value", [("uci", "a1a8"), ("san", "Qh8"), ("side", "black"),
                                       ("piece", "queen"), ("piece", "black_pawn"),
                                       ("checkmate", True), ("castling", True), ("promotion", "queen")])
def test_route_metadata_or_legality_tampering_blocks_mechanism(field, value):
    move = _review(*CASES[0][:4])
    setattr(move.actual_move_line.moves[0], field, value)
    assert build_book_mechanism(move) is None


def test_changed_endpoint_and_unverified_route_do_not_produce_book_claims():
    move = _review(*CASES[1][:4])
    move.actual_move_line.resulting_fen = move.before_fen
    assert build_book_mechanism(move) is None
    move = _review(*CASES[1][:4])
    move.actual_move_line.verified = False
    assert build_book_mechanism(move) is None


def test_immediately_recaptured_pawn_is_not_a_durable_passed_pawn():
    move = _review("r2qk2r/1b1nbppp/p3pn2/1pp5/3PP3/2NB1N2/PP2QPPP/R1BR2K1 b kq - 1 12",
                   "c4", "Bc2 b4 Na4 Qc7 Bg5 O-O Rac1 h6 Bh4 Rfc8",
                   "cxd4 Nxd4 Qb8 Be3 O-O f3 Ne5 Rac1 Nfd7 Bb1")
    mechanism = build_book_mechanism(move)
    assert mechanism is not None
    assert "d4的黑兵成为通路兵" not in mechanism.statement
    assert "Nxd4随即回吃" in mechanism.statement
    assert "中心兵仍然保留" in mechanism.statement


def test_same_capture_choice_explains_which_piece_occupies_the_square():
    move = _review("r2qk2r/1ppbbppp/p2p2n1/8/3nP3/2N1B3/PPPQBPPP/2KR3R w kq - 0 11",
                   "Qxd4", "O-O h4 Bxh4 e5 Re8 exd6 cxd6 Bf3 Be7 Bxb7",
                   "Bxd4 Nf4 Bf1 Ne6 Be3 Bc6 f4 O-O Qf2 b5")
    mechanism = build_book_mechanism(move)
    assert mechanism is not None
    assert "Qxd4与Bxd4都能吃掉d4的黑马" in mechanism.statement
    assert "白后留在d2支援它" in mechanism.statement


def test_explanations_never_add_unlisted_squares():
    move = _review(*CASES[0][:4])
    move.allowed_squares.remove("f4")
    mechanism = build_book_mechanism(move)
    assert mechanism is None or "f4" not in mechanism.statement


def test_mate_forecast_does_not_authorize_terminal_or_wrong_side_claims():
    move = _review("3r2k1/p4ppp/1nP5/2q4n/7P/1P3P2/PKP1R3/R1BQ4 b - - 7 25",
                   "Na4+", "Kb1 Nc3+ Kb2 Nxd1+ Kb1 Nc3+ Kb2 Nxe2 c7 Qxc7")
    move.before.mate_in = -20
    move.before.centipawn = None
    move.before.evaluation = "黑方 M20"
    level = compute_professional_complexity(move)
    context = build_validation_context(move, level.level)
    assert not context.allows_checkmate
    safe = apply_hard_fact_guard(build_safe_professional_analysis(move, level), move)
    errors = validate_professional_analysis(safe, context, enforce_length=False)
    assert not any("将杀" in error or "内部变量名" in error for error in errors)
    for invalid in ("白方已有强制将杀。", "黑方已经形成将杀。"):
        bad = safe.model_copy(deep=True)
        bad.played_move_analysis.intention = invalid
        assert any("将杀" in error for error in validate_professional_analysis(bad, context, enforce_length=False))
    bad = safe.model_copy(deep=True)
    bad.main_danger.description = "黑方已有强制将杀。"
    assert any("将杀" in error for error in validate_professional_analysis(bad, context, enforce_length=False))


QUIET_CASES = [
    ("r1bqr1k1/pppnbppp/4pn2/6B1/2BP4/2N1PN2/PP3PPP/2RQK2R b K - 0 9",
     "a6", "a4 h6 Bh4 b6 O-O Bb7 Qe2 c5 Rfd1 Qc7", "b5", "c3的敌马", "Bb7"),
    ("r2q1r2/2p2pkp/ppbp2p1/2n5/2P1PP2/2N5/PPB3PP/R2Q1RK1 b - - 1 18",
     "a5", "Qd2 Qf6 Rae1 Rae8 Re3 Kh8 h3 Qg7 b3 Re7", "b4", "赶走c5马", "a5兵便可以吃掉它"),
]


@pytest.mark.parametrize("fen,san,line,square,meaning,plan", QUIET_CASES)
def test_quiet_pawn_control_requires_specific_recomputed_evidence(fen, san, line, square, meaning, plan):
    move = _review(fen, san, line)
    move.allowed_squares.remove(square)
    mechanism = build_book_mechanism(move)
    assert mechanism and meaning in mechanism.statement and plan in mechanism.statement
    assert any("pawn_control" in ref for ref in mechanism.evidence_refs)
    context = build_validation_context(move, "normal")
    assert set(mechanism.evidence_refs) <= context.allowed_evidence_ids
    assert square in context.allowed_squares
    assert not any(fact.category == "pawn_control" for fact in select_analysis_focus(move).selected_facts)
    # Empty squares must not be admitted by absent, stale, or wrong-side facts.
    fact = next(f for f in move.position_facts_after.pawn_structure
                if f.category == "pawn_control" and f.squares[0] == move.to_square)
    for corruption in ("side", "squares", "missing"):
        bad = move.model_copy(deep=True)
        target = next(f for f in bad.position_facts_after.pawn_structure if f.id == fact.id)
        if corruption == "side":
            target.side = "white"
        elif corruption == "squares":
            target.squares.append("h8")
        else:
            bad.position_facts_after.pawn_structure.remove(target)
        rejected = build_book_mechanism(bad)
        assert rejected is None or meaning not in rejected.statement


def test_central_plan_and_king_support_are_taken_from_actual_routes():
    queen = _review("r1bq1rk1/pp4p1/4pp2/2pp3Q/8/2P1P3/PP1N1PPP/R3K2R b KQ - 1 15",
                    "Qb6", "b3 Qa5 Nb1 e5 O-O Be6 Rc1 d4 Nd2 Rad8")
    mechanism = build_book_mechanism(queen)
    assert mechanism and "e5兵支援d4兵" in mechanism.statement
    king = _review("3k1n2/r1r4p/2p1pqb1/1pPp1p2/pN1P1P2/P2KQBP1/1P3P1R/2R5 b - - 33 44",
                   "Kc8", "Rch1 Kb7 Ke2 Ra8 Bh5 Re8 Bxg6 Qxg6 Rh6 Qg8")
    mechanism = build_book_mechanism(king)
    assert mechanism and "亲自保护c6兵" in mechanism.statement


def test_same_knight_compares_coordination_and_common_reply_without_repeating_it():
    move = _review("1r1qr1k1/p1pb1pb1/3p1npp/2p5/2PNP2B/2N5/PP3PPP/1R1QR1K1 w - - 0 15",
                   "Nb3", "g5 Bg3 Ng4 Qd2 h5 h3 h4 Bxd6 cxd6 hxg4",
                   "Nf3 g5 Bg3 Nh5 Qc2 Bg4 Nd5 Qd7 Nd2 Be6")
    mechanism = build_book_mechanism(move)
    assert mechanism and "保护h4象，同时控制g5" in mechanism.statement
    assert mechanism.statement.count("Bg3") == 1
    assert "保护g1" not in mechanism.statement
    assert set(mechanism.evidence_refs) <= build_validation_context(move, "normal").allowed_evidence_ids
    move.candidate_lines[0].moves[2].san = "Bh2"
    changed = build_book_mechanism(move)
    assert changed and "两种走法后都可出现" not in changed.statement


def test_passed_pawn_plan_records_the_lost_supporting_pawn_and_actual_advance():
    move = _review("8/p5pp/2k5/2P1p1N1/2N5/1P5P/r5P1/6K1 w - - 2 37",
                   "Ne4", "Ra1+ Kf2 Kd5 Kf3 a5 Nb6+ Kc6 Na4 Rf1+ Ke2",
                   "b4 Kb5 Nxe5 Re2 Ngf3 Kxb4 c6 a5 Kf1 Rc2")
    mechanism = build_book_mechanism(move)
    assert mechanism and "Kxb4吃掉这枚支援兵后，c6继续推进通路兵" in mechanism.statement
    assert "b4，与推进后的c6兵拉开了距离" in mechanism.statement
    assert set(mechanism.evidence_refs) <= build_validation_context(move, "normal").allowed_evidence_ids


def test_bishop_clearance_explains_rook_capture_instead_of_unrelated_new_passer():
    move = _review("8/p5pk/7p/5p1P/2p2NP1/1PPp1b2/P4R1K/4r3 b - - 0 43",
                   "Be2", "Nd5 d2 Ne3 Bxg4 Rxd2 Rxe3 bxc4 Rxc3 Rd7 a6")
    mechanism = build_book_mechanism(move)
    assert mechanism and "Bxg4让开e2，打开e1的黑车通向e3马的线路" in mechanism.statement
    assert "Rxd2" in mechanism.statement and "Rxe3沿这条线路吃掉该子" in mechanism.statement
    assert "f5的黑兵成为通路兵" not in mechanism.statement


@pytest.mark.parametrize("use_mechanism", [False, True])
def test_legacy_numeric_claims_cannot_reenter_prose_or_safe_fallback(use_mechanism):
    move = _review(*CASES[0][:4]) if use_mechanism else professional_review()
    original = move.model_dump()
    package = build_narrative_claim_package(move)
    assert any(c.claim_id.endswith(":book-mechanism") for c in package.claims) == use_mechanism
    old = VerifiedNarrativeClaim(
        claimId="claim:1:comparison", kind="evaluation_comparison", scope="after_played_move",
        statement='Rb6后评价+1.26→+2.20，损失94 cp，定级“错误”；首选是Kf7。',
        evidenceRefs=["evaluation:before:1", "evaluation:after:1"], source="stockfish",
    )
    package.version = "1.8"
    package.claims.append(old)
    package.recommended_claim_refs.insert(0, old.claim_id)
    selected = resolve_narrative_claims(package, [old.claim_id])
    assert selected and old not in selected
    core = compose_verified_core_paragraph(package, [old.claim_id])
    safe = build_safe_professional_analysis(move, compute_professional_complexity(move))
    safe.played_move_analysis.intention = old.statement
    safe.played_move_analysis.claim_refs = [old.claim_id]
    guarded = apply_hard_fact_guard(safe, move, narrative_claims=package)
    for text in (core, guarded.played_move_analysis.intention):
        assert not any(marker in text for marker in ("后评价", " cp", "定级", "首选是"))
    assert old.claim_id not in guarded.played_move_analysis.claim_refs
    assert move.model_dump() == original
