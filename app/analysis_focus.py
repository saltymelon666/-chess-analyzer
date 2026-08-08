from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Literal

import chess

from .models import EvidenceFact, MoveReview


FocusScope = Literal[
    "current_position",
    "played_move_line",
    "candidate_line_1",
    "candidate_line_2",
    "candidate_line_3",
]

PIECE_VALUES = {
    "pawn": 1,
    "knight": 3,
    "bishop": 3,
    "rook": 5,
    "queen": 9,
    "king": 100,
}
WEAKNESS_CATEGORIES = {
    "undefended_piece",
    "underprotected",
    "vulnerable_pawn",
    "isolated_pawn",
    "doubled_pawns",
}
KING_CONTEXT_CATEGORIES = {
    "king_square",
    "castling_history_unavailable",
    "castling_rights",
    "pawn_shield",
    "nearby_attackers",
    "king_near_open_file",
}


@dataclass(frozen=True)
class FocusFact:
    id: str
    scope: FocusScope
    category: str
    description: str
    importance_score: int
    decision_impact: str
    evidence_refs: tuple[str, ...]
    display: bool
    display_section: str | None
    rejection_reason: str | None
    side: str | None = None
    squares: tuple[str, ...] = ()

    def prompt_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "scope": self.scope,
            "category": self.category,
            "importanceScore": self.importance_score,
            "decisionImpact": self.decision_impact,
            "evidenceRefs": list(self.evidence_refs),
            "display": self.display,
            "displaySection": self.display_section,
            "side": self.side,
            "squares": list(self.squares),
            "text": self.description,
        }


@dataclass(frozen=True)
class AnalysisFocus:
    facts: tuple[FocusFact, ...]
    king_safety_relevant_sides: frozenset[str]
    weaknesses: dict[str, tuple[FocusFact, ...]]
    global_threats: tuple[FocusFact, ...]
    line_events: dict[int, tuple[FocusFact, ...]]
    display_sections: tuple[str, ...]
    counters: dict[str, int] = field(default_factory=dict)

    @property
    def selected_facts(self) -> tuple[FocusFact, ...]:
        return tuple(item for item in self.facts if item.display)

    @property
    def selected_ids(self) -> set[str]:
        return {item.id for item in self.selected_facts}


def select_analysis_focus(move: MoveReview) -> AnalysisFocus:
    """Score raw chess facts without changing or deleting the underlying fact package."""
    board = chess.Board(move.before_fen)
    raw_facts = [
        *move.position_facts.piece_activity,
        *move.position_facts.king_safety,
        *move.position_facts.pawn_structure,
        *move.position_facts.threats,
    ]
    facts: list[FocusFact] = []
    counters = {
        "rawWeaknesses": 0,
        "selectedWeaknesses": 0,
        "filteredUndefendedOnly": 0,
        "filteredKingSafety": 0,
        "movedToCandidateLine": 0,
        "filteredOrdinaryPvCaptures": 0,
    }

    route_usage = _route_usage(move)
    king_sides, king_evidence = _relevant_king_safety(move, board)

    weaknesses: dict[str, list[FocusFact]] = {"white": [], "black": []}
    line_events: dict[int, list[FocusFact]] = {1: [], 2: [], 3: []}
    global_threats: list[FocusFact] = []

    for raw in raw_facts:
        if raw.category in WEAKNESS_CATEGORIES:
            counters["rawWeaknesses"] += 1
            scored = _score_weakness(raw, move, board, route_usage)
            facts.append(scored)
            if scored.display and raw.side in weaknesses:
                weaknesses[raw.side].append(scored)
            elif raw.category == "undefended_piece" and scored.importance_score == 0:
                counters["filteredUndefendedOnly"] += 1
            continue

        if raw.category in KING_CONTEXT_CATEGORIES:
            scored = _score_king_fact(raw, king_sides, king_evidence)
            facts.append(scored)
            if not scored.display:
                counters["filteredKingSafety"] += 1
            continue

        if raw.category in {"route_event", "direct_piece_loss"}:
            scored = _score_route_event(raw, move)
            facts.append(scored)
            rank = _candidate_rank(raw.description)
            if rank is not None:
                if scored.display:
                    line_events[rank].append(scored)
                elif "吃子" in raw.description:
                    counters["filteredOrdinaryPvCaptures"] += 1
            continue

        if raw.category.startswith("immediate_") or raw.category not in {
            "active_piece", "central_piece", "verified_outpost",
            "important_defender", "closed_center", "passed_pawn", "central_pawn",
            "open_file", "half_open_file",
        }:
            scored = _score_current_threat(raw, move)
            facts.append(scored)
            if scored.display and scored.display_section == "threats":
                global_threats.append(scored)
            continue

        scored = _score_strategic_fact(raw, route_usage)
        facts.append(scored)

    # Legal move facts have stronger, current-position scope than narrative route facts.
    for item in move.position_facts.immediate_checks:
        score = 5 if item.checkmate else 4
        target_side = "black" if move.side == "white" else "white"
        fact = FocusFact(
            id=item.id,
            scope="current_position",
            category="immediate_checkmate" if item.checkmate else "immediate_check",
            description=f"{move.side}方当前可以走{item.san}",
            importance_score=score,
            decision_impact="当前即可将军，必须纳入本回合决策。",
            evidence_refs=(item.id,),
            display=True,
            display_section="threats",
            rejection_reason=None,
            side=move.side,
            squares=(item.from_square, item.to_square),
        )
        facts.append(fact)
        global_threats.append(fact)
        king_sides = frozenset({*king_sides, target_side})

    for item in move.position_facts.immediate_captures:
        value = _piece_value(item.captured_piece)
        display = value >= 3
        fact = FocusFact(
            id=item.id,
            scope="current_position",
            category="immediate_capture",
            description=f"{move.side}方当前可以走{item.san}",
            importance_score=4 if display else 2,
            decision_impact=(
                "当前即可吃掉重要棋子，直接影响走法选择。"
                if display else "只是当前合法的普通吃子，未达到全局威胁阈值。"
            ),
            evidence_refs=(item.id,),
            display=display,
            display_section="threats" if display else None,
            rejection_reason=None if display else "普通合法吃子没有显著子力或评价后果",
            side=move.side,
            squares=(item.from_square, item.to_square),
        )
        facts.append(fact)
        if display:
            global_threats.append(fact)

    for side in ("white", "black"):
        weaknesses[side] = _deduplicate_focus(weaknesses[side])[:2]
    counters["selectedWeaknesses"] = sum(len(items) for items in weaknesses.values())

    global_threats = _deduplicate_focus(global_threats)[:2]
    for rank in line_events:
        line_events[rank] = _deduplicate_focus(line_events[rank])[:2]
    counters["movedToCandidateLine"] = sum(len(items) for items in line_events.values())

    selected_ids = {
        item.id
        for values in [*weaknesses.values(), *line_events.values()]
        for item in values
    } | {item.id for item in global_threats}
    position_features = sorted(
        (
            item for item in facts
            if item.display and item.display_section == "positionAssessment"
        ),
        key=lambda item: (-item.importance_score, item.id),
    )[:2]
    selected_ids |= {item.id for item in position_features}
    selected_ids |= set(king_evidence)
    finalized = []
    for item in facts:
        should_display = item.display and item.id in selected_ids
        finalized.append(
            item if should_display == item.display else FocusFact(
                **{
                    **item.__dict__,
                    "display": should_display,
                    "display_section": item.display_section if should_display else None,
                    "rejection_reason": item.rejection_reason or "同栏目已有更直接、对当前决策影响更大的事实",
                }
            )
        )

    sections = ["positionAssessment"]
    if global_threats:
        sections.append("threats")
    if king_sides:
        sections.append("kingSafety")
    if any(weaknesses.values()):
        sections.append("weaknesses")
    sections.extend(("playedMoveAnalysis", "plans", "candidateLines", "comparison"))
    return AnalysisFocus(
        facts=tuple(finalized),
        king_safety_relevant_sides=frozenset(king_sides),
        weaknesses={side: tuple(items) for side, items in weaknesses.items()},
        global_threats=tuple(global_threats),
        line_events={rank: tuple(items) for rank, items in line_events.items()},
        display_sections=tuple(sections),
        counters=counters,
    )


def _score_weakness(
    fact: EvidenceFact,
    move: MoveReview,
    board: chess.Board,
    route_usage: dict[str, Any],
) -> FocusFact:
    side = fact.side
    square = fact.squares[0] if fact.squares else ""
    square_index = chess.parse_square(square) if square else None
    piece = board.piece_at(square_index) if square_index is not None else None
    owner = chess.WHITE if side == "white" else chess.BLACK
    attacker_squares = (
        sorted(chess.square_name(value) for value in board.attackers(not owner, square_index))
        if square_index is not None and side in {"white", "black"} else []
    )
    opponent_to_move = board.turn != owner if side in {"white", "black"} else False
    legal_captures = [
        item for item in move.position_facts.immediate_captures
        if opponent_to_move and item.to_square == square
    ]
    early_hits = [item for item in route_usage["early_hits"].get(square, []) if item[0] <= 2]
    repeated_hits = route_usage["hit_ranks"].get(square, set())
    important_loss = route_usage["important_losses"].get(square, [])
    has_concrete_method = bool(legal_captures or early_hits or important_loss)
    score = 0
    reasons: list[str] = []

    if important_loss and any(item[0] <= 2 for item in important_loss):
        score = 5
        reasons.append("第一轮应对内会直接丢失重要棋子")
    elif legal_captures:
        score = 4 if piece and piece.piece_type != chess.PAWN else 2
        reasons.append("对手当前有合法吃子，但目标只是普通兵" if score < 3 else "对手当前可合法吃掉重要棋子")
    elif early_hits:
        score = 3
        reasons.append("Stockfish路线在前两个半回合内直接利用该目标")
    elif len(repeated_hits) >= 2 and fact.category in {"underprotected", "vulnerable_pawn"}:
        score = 4
        reasons.append("两条以上候选路线反复利用同一目标")

    # Merely being undefended, having an isolated pawn, or being movable is not enough.
    display = score >= 3 and bool(square) and has_concrete_method
    if fact.category == "undefended_piece" and not display:
        rejection = "仅仅没有保护，没有受到可立即利用的攻击，也没有被Stockfish前段路线利用"
    elif not has_concrete_method:
        rejection = "没有具体攻击者、目标利用方式或Stockfish路线证据"
    elif score < 3:
        rejection = "对当前走法选择影响不足"
    else:
        rejection = None
    attacker = "、".join(attacker_squares) if attacker_squares else "Stockfish前段路线中的攻击棋子"
    impact = (
        f"{attacker}可以利用{square}目标；若不处理，"
        + ("会直接损失重要棋子。" if important_loss else "对手会取得具体节奏或子力收益。")
        if display else ""
    )
    evidence = [fact.id]
    evidence.extend(item.id for item in legal_captures[:2])
    evidence.extend(item[2] for item in early_hits[:2])
    evidence.extend(item[2] for item in important_loss[:2])
    return FocusFact(
        id=fact.id,
        scope="current_position",
        category="weakness",
        description=fact.description,
        importance_score=min(5, score),
        decision_impact=impact or "；".join(reasons),
        evidence_refs=tuple(dict.fromkeys(evidence)),
        display=display,
        display_section="weaknesses" if display else None,
        rejection_reason=rejection,
        side=side,
        squares=tuple(fact.squares),
    )


def _score_king_fact(
    fact: EvidenceFact,
    relevant_sides: frozenset[str],
    evidence_ids: frozenset[str],
) -> FocusFact:
    side_relevant = fact.side in relevant_sides
    supports_danger = fact.id in evidence_ids
    display = side_relevant and (supports_danger or fact.category == "king_square")
    if fact.category in {"castling_history_unavailable", "castling_rights", "pawn_shield"} and not supports_danger:
        display = False
    score = 4 if supports_danger else 3 if display else 0
    rejection = None
    if not display:
        if fact.category == "castling_rights":
            rejection = "没有易位权不能证明王不安全，也可能代表已经完成易位"
        elif fact.category == "pawn_shield":
            rejection = "兵盾数量本身没有被攻击棋子或Stockfish路线具体利用"
        else:
            rejection = "王安全不影响当前决策"
    return FocusFact(
        id=fact.id,
        scope="current_position",
        category="king_safety",
        description=fact.description,
        importance_score=score,
        decision_impact="该王区危险已被合法将军、连续将军或具体进攻证据验证。" if display else "",
        evidence_refs=(fact.id,),
        display=display,
        display_section="kingSafety" if display else None,
        rejection_reason=rejection,
        side=fact.side,
        squares=tuple(fact.squares),
    )


def _score_route_event(fact: EvidenceFact, move: MoveReview) -> FocusFact:
    rank = _candidate_rank(fact.description)
    scope: FocusScope = (
        f"candidate_line_{rank}" if rank in {1, 2, 3}
        else "played_move_line"
    )  # type: ignore[assignment]
    matched = _match_route_move(fact, move, rank)
    captured_value = _piece_value(matched.captured_piece) if matched else 0
    is_mate = bool(matched and matched.checkmate) or "将杀" in fact.description
    is_check = bool(matched and matched.check) or "将军" in fact.description
    significant_capture = fact.category == "direct_piece_loss" or captured_value >= 3
    score = 5 if is_mate else 4 if fact.category == "direct_piece_loss" else 3 if is_check or significant_capture else 0
    display = score >= 3
    if display:
        if is_mate:
            impact = "该路线包含强制将杀事件，必须在本路线内解释。"
        elif significant_capture:
            impact = "该路线会造成重要子力变化，只能作为本路线的具体后果说明。"
        else:
            impact = "该路线包含有决策意义的将军，只能在本路线内解释。"
        rejection = None
    else:
        impact = "普通交换或重新吃回只保留在走法序列中。"
        rejection = "只存在于单条PV中的普通吃子，不是当前局面的全局威胁"
    refs = [fact.id]
    if matched and matched.id:
        refs.append(matched.id)
    return FocusFact(
        id=fact.id,
        scope=scope,
        category="line_event",
        description=fact.description,
        importance_score=score,
        decision_impact=impact,
        evidence_refs=tuple(dict.fromkeys(refs)),
        display=display,
        display_section=scope if display else None,
        rejection_reason=rejection,
        side=fact.side,
        squares=tuple(fact.squares),
    )


def _score_current_threat(fact: EvidenceFact, move: MoveReview) -> FocusFact:
    category = fact.category
    is_current = category.startswith("immediate_") or category not in {"route_event", "direct_piece_loss"}
    score = 5 if "checkmate" in category else 4 if category in {"immediate_check", "immediate_promotion"} else 0
    if category == "immediate_capture":
        matching = next(
            (item for item in move.position_facts.immediate_captures if item.to_square in fact.squares),
            None,
        )
        score = 4 if matching and _piece_value(matching.captured_piece) >= 3 else 2
    # Immediate events are represented once by their canonical legal-move IDs below.
    duplicate_of_legal_move = category.startswith("immediate_")
    display = is_current and score >= 3 and not duplicate_of_legal_move
    return FocusFact(
        id=fact.id,
        scope="current_position",
        category="threat",
        description=fact.description,
        importance_score=score,
        decision_impact="当前即可执行，若不纳入计算会错过强制机会或遭受直接后果。" if display else "",
        evidence_refs=(fact.id,),
        display=display,
        display_section="threats" if display else None,
        rejection_reason=(
            None if display
            else "由对应合法走法ID统一展示，避免重复" if duplicate_of_legal_move
            else "没有具体攻击者、重要目标和显著后果"
        ),
        side=fact.side,
        squares=tuple(fact.squares),
    )


def _score_strategic_fact(fact: EvidenceFact, route_usage: dict[str, Any]) -> FocusFact:
    square = fact.squares[0] if fact.squares else ""
    appearances = route_usage["origin_counts"].get((fact.side, square), 0)
    if fact.category in {"closed_center", "verified_outpost", "important_defender"}:
        score = 3
        section = "positionAssessment"
    elif fact.category == "passed_pawn" and square and square[1] in {"2", "3", "6", "7"}:
        score = 3
        section = "positionAssessment"
    elif appearances >= 2:
        score = 3
        section = "positionAssessment"
    else:
        score = 1
        section = None
    display = score >= 3
    return FocusFact(
        id=fact.id,
        scope="current_position",
        category="position_feature",
        description=fact.description,
        importance_score=score,
        decision_impact=(
            "该棋子或结构在多条候选路线中反复出现，直接影响计划选择。"
            if display else ""
        ),
        evidence_refs=(fact.id,),
        display=display,
        display_section=section if display else None,
        rejection_reason=None if display else "与当前双方主要计划及候选路线关联不足",
        side=fact.side,
        squares=tuple(fact.squares),
    )


def _relevant_king_safety(
    move: MoveReview,
    board: chess.Board,
) -> tuple[frozenset[str], frozenset[str]]:
    relevant: set[str] = set()
    evidence: set[str] = set()
    if board.is_check():
        side = "white" if board.turn == chess.WHITE else "black"
        relevant.add(side)
    if move.position_facts.immediate_checks:
        relevant.add("black" if move.side == "white" else "white")
        evidence.update(item.id for item in move.position_facts.immediate_checks)

    checks_by_target: dict[str, set[int]] = {"white": set(), "black": set()}
    for line in move.candidate_lines:
        check_count = 0
        for item in line.moves[:6]:
            if item.check or item.checkmate:
                target = "black" if item.side == "white" else "white"
                checks_by_target[target].add(line.rank)
                check_count += 1
                evidence.add(item.id)
        if check_count >= 2:
            for item in line.moves[:6]:
                if item.check or item.checkmate:
                    relevant.add("black" if item.side == "white" else "white")
    for side, ranks in checks_by_target.items():
        if len(ranks) >= 2:
            relevant.add(side)

    for side in tuple(relevant):
        for fact in move.position_facts.king_safety:
            if fact.side == side and fact.category in {"king_square", "nearby_attackers", "king_near_open_file"}:
                evidence.add(fact.id)
    return frozenset(relevant), frozenset(evidence)


def _route_usage(move: MoveReview) -> dict[str, Any]:
    early_hits: dict[str, list[tuple[int, int, str]]] = {}
    hit_ranks: dict[str, set[int]] = {}
    important_losses: dict[str, list[tuple[int, int, str]]] = {}
    origin_counts: dict[tuple[str, str], int] = {}
    key_origins: set[str] = set()
    for line in move.candidate_lines:
        board = chess.Board(move.before_fen)
        seen_origins: set[tuple[str, str]] = set()
        for ply, item in enumerate(line.moves, start=1):
            key = (item.side, item.from_square)
            if key not in seen_origins:
                origin_counts[key] = origin_counts.get(key, 0) + 1
                seen_origins.add(key)
            if item.capture:
                early_hits.setdefault(item.to_square, []).append((ply, line.rank, item.id))
                hit_ranks.setdefault(item.to_square, set()).add(line.rank)
                if _piece_value(item.captured_piece) >= 3:
                    important_losses.setdefault(item.to_square, []).append((ply, line.rank, item.id))
            try:
                board.push_uci(item.uci)
            except ValueError:
                break
    for (side, square), count in origin_counts.items():
        if count >= 2:
            key_origins.add(square)
    return {
        "early_hits": early_hits,
        "hit_ranks": hit_ranks,
        "important_losses": important_losses,
        "origin_counts": origin_counts,
        "key_origins": key_origins,
    }


def _candidate_rank(description: str) -> int | None:
    match = re.search(r"候选路线([123])", description)
    return int(match.group(1)) if match else None


def _match_route_move(fact: EvidenceFact, move: MoveReview, rank: int | None) -> Any | None:
    lines = (
        [line for line in move.candidate_lines if line.rank == rank]
        if rank is not None else ([move.actual_move_line] if move.actual_move_line else [])
    )
    for line in lines:
        if line is None:
            continue
        for item in line.moves:
            if (
                item.from_square in fact.squares
                and item.to_square in fact.squares
                and item.san in fact.description
            ):
                return item
    return None


def _piece_value(piece_id: str | None) -> int:
    if not piece_id:
        return 0
    return PIECE_VALUES.get(piece_id.split("_", 1)[-1], 0)


def _deduplicate_focus(items: list[FocusFact]) -> list[FocusFact]:
    result: list[FocusFact] = []
    seen: set[tuple[str, tuple[str, ...], str | None]] = set()
    for item in sorted(items, key=lambda value: (-value.importance_score, value.id)):
        key = (item.category, item.squares, None if item.category == "line_event" else item.side)
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result
