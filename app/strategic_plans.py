from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

import chess
from pydantic import BaseModel, ConfigDict, Field

from .threat_analysis import ThreatPackage, position_id

if TYPE_CHECKING:
    from .chess_facts import ChessFactPackage, FactCandidateRoute
    from .models import PositionFacts


STRATEGIC_PLAN_PACKAGE_VERSION = "1.0"
PlanType = Literal[
    "improve_worst_piece",
    "prepare_center_break",
    "occupy_open_file",
    "activate_rook",
    "improve_king_safety",
    "attack_weak_pawn",
    "create_passed_pawn",
    "simplify_endgame",
]
PLAN_TYPES = {
    "improve_worst_piece",
    "prepare_center_break",
    "occupy_open_file",
    "activate_rook",
    "improve_king_safety",
    "attack_weak_pawn",
    "create_passed_pawn",
    "simplify_endgame",
}
PIECE_NAMES = {
    chess.PAWN: "兵",
    chess.KNIGHT: "马",
    chess.BISHOP: "象",
    chess.ROOK: "车",
    chess.QUEEN: "后",
    chess.KING: "王",
}
PIECE_VALUES = {
    chess.PAWN: 1,
    chess.KNIGHT: 3,
    chess.BISHOP: 3,
    chess.ROOK: 5,
    chess.QUEEN: 9,
    chess.KING: 0,
}
SLOW_PLAN_TYPES = {
    "improve_worst_piece",
    "prepare_center_break",
    "occupy_open_file",
    "activate_rook",
    "attack_weak_pawn",
    "create_passed_pawn",
}


class StrategicPlanFact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_id: str
    side: Literal["white", "black"]
    type: PlanType
    goal: str = Field(min_length=1, max_length=300)
    supporting_moves: list[str] = Field(default_factory=list, min_length=1)
    evidence_route_ids: list[str] = Field(default_factory=list, min_length=2)
    structural_evidence: list[str] = Field(default_factory=list, min_length=1)
    confidence: Literal["medium", "high"]
    source: Literal[
        "python-chess+stockfish-multipv+position-facts",
    ] = "python-chess+stockfish-multipv+position-facts"


class StrategicPlanPackage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal["1.0"] = STRATEGIC_PLAN_PACKAGE_VERSION
    position_id: str
    plans: list[StrategicPlanFact] = Field(default_factory=list)


@dataclass(frozen=True)
class _PlanStep:
    route_id: str
    route_rank: int
    route_evaluation: int | None
    route_mate: int | None
    ply: int
    side: Literal["white", "black"]
    san: str
    uci: str
    move: chess.Move
    before: chess.Board
    after: chess.Board
    piece: chess.Piece
    piece_origin: int
    captured_piece: chess.Piece | None


@dataclass
class _Candidate:
    type: PlanType
    side: Literal["white", "black"]
    key: str
    goal: str
    supporting_moves: set[str]
    route_ids: set[str]
    structural_evidence: set[str]
    evaluations: dict[str, int]
    strong_structure: bool = False


class StrategicPlanAnalyzer:
    """Derive conservative plans from existing verified MultiPV and board facts."""

    def __init__(
        self,
        *,
        stable_route_threshold_cp: int = 100,
        early_plan_plies: int = 5,
    ) -> None:
        self.stable_route_threshold_cp = stable_route_threshold_cp
        self.early_plan_plies = max(2, min(early_plan_plies, 8))

    def analyze(
        self,
        package: "ChessFactPackage",
        *,
        position_facts: "PositionFacts | None" = None,
        threat_package: ThreatPackage | None = None,
    ) -> StrategicPlanPackage:
        board = chess.Board(package.position.fen)
        steps_by_route = self._verified_route_steps(package)
        candidates: dict[tuple[PlanType, str, str], _Candidate] = {}
        open_files = _open_files(board, position_facts)
        weak_pawns = _weak_pawns(board, position_facts)

        self._detect_worst_piece(board, steps_by_route, candidates)
        self._detect_center_breaks(board, steps_by_route, candidates)
        self._detect_open_files(open_files, steps_by_route, candidates)
        self._detect_rook_activation(board, open_files, steps_by_route, candidates)
        self._detect_king_safety(board, steps_by_route, candidates)
        self._detect_weak_pawn_attacks(weak_pawns, steps_by_route, candidates)
        self._detect_passed_pawn_creation(board, steps_by_route, candidates)
        self._detect_simplification(board, package, steps_by_route, candidates)

        plans = [
            self._to_plan(candidate)
            for candidate in candidates.values()
            if self._candidate_valid(candidate)
        ]
        threats = (
            threat_package.threats
            if threat_package is not None
            else package.verified_threats
        )
        plans = [
            plan for plan in plans
            if _allowed_under_threat(plan, threats)
        ]
        plans.sort(key=lambda item: (
            0 if item.confidence == "high" else 1,
            _plan_priority(item.type),
            item.side,
            item.goal,
        ))
        for index, plan in enumerate(plans, start=1):
            plan.plan_id = f"plan_{index}"
        return StrategicPlanPackage(
            position_id=position_id(package.position.fen),
            plans=plans,
        )

    def _verified_route_steps(
        self,
        package: "ChessFactPackage",
    ) -> dict[str, list[_PlanStep]]:
        result: dict[str, list[_PlanStep]] = {}
        for rank, route in enumerate(package.candidate_routes, start=1):
            if (
                not route.verified
                or not route.moves_uci
                or route.mate is not None
                or route.evaluation is None
            ):
                continue
            board = chess.Board(package.position.fen)
            identities = {
                square: square
                for square in board.piece_map()
            }
            steps: list[_PlanStep] = []
            valid = True
            for ply, uci in enumerate(route.moves_uci, start=1):
                try:
                    move = chess.Move.from_uci(uci)
                except ValueError:
                    valid = False
                    break
                if move not in board.legal_moves:
                    valid = False
                    break
                piece = board.piece_at(move.from_square)
                if piece is None:
                    valid = False
                    break
                before = board.copy(stack=False)
                captured_square = _captured_square(before, move)
                captured = before.piece_at(captured_square) if captured_square is not None else None
                origin = identities.pop(move.from_square, move.from_square)
                if captured_square is not None:
                    identities.pop(captured_square, None)
                identities[move.to_square] = origin
                if before.is_castling(move):
                    rook_from, rook_to = _castling_rook_squares(move)
                    rook_origin = identities.pop(rook_from, rook_from)
                    identities[rook_to] = rook_origin
                after = before.copy(stack=False)
                san = before.san(move)
                after.push(move)
                steps.append(_PlanStep(
                    route_id=route.route_id,
                    route_rank=rank,
                    route_evaluation=route.evaluation,
                    route_mate=route.mate,
                    ply=ply,
                    side="white" if before.turn == chess.WHITE else "black",
                    san=san,
                    uci=uci,
                    move=move,
                    before=before,
                    after=after,
                    piece=piece,
                    piece_origin=origin,
                    captured_piece=captured,
                ))
                board = after
            if valid and len(steps) == len(route.moves_uci):
                result[route.route_id] = steps
        return result

    def _detect_worst_piece(
        self,
        board: chess.Board,
        steps_by_route: dict[str, list[_PlanStep]],
        candidates: dict[tuple[PlanType, str, str], _Candidate],
    ) -> None:
        low_activity: dict[tuple[str, int], tuple[chess.Piece, int]] = {}
        for square, piece in board.piece_map().items():
            if piece.piece_type not in {chess.KNIGHT, chess.BISHOP}:
                continue
            side = _side(piece.color)
            mobility = _piece_mobility(board, square)
            undeveloped = square in (
                {chess.B1, chess.C1, chess.F1, chess.G1}
                if piece.color == chess.WHITE
                else {chess.B8, chess.C8, chess.F8, chess.G8}
            )
            if mobility <= 2 or undeveloped:
                low_activity[(side, square)] = (piece, mobility)

        for route_id, steps in steps_by_route.items():
            seen: set[tuple[str, int]] = set()
            for step in steps:
                key = (step.side, step.piece_origin)
                if (
                    step.ply > self.early_plan_plies
                    or key not in low_activity
                    or key in seen
                    or step.before.is_capture(step.move)
                    or step.before.gives_check(step.move)
                ):
                    continue
                piece, root_mobility = low_activity[key]
                after_mobility = _piece_mobility(step.after, step.move.to_square)
                if after_mobility <= root_mobility:
                    continue
                seen.add(key)
                square = chess.square_name(step.piece_origin)
                label = PIECE_NAMES[piece.piece_type]
                candidate = self._candidate(
                    candidates,
                    "improve_worst_piece",
                    step.side,
                    square,
                    f"改善{_side_name(step.side)}{square}{label}的活动",
                )
                self._support(
                    candidate,
                    route_id,
                    step.route_evaluation,
                    [step.san],
                    [f"{square}{label}初始活动格只有{root_mobility}个，路线调动后增至{after_mobility}个"],
                    strong=after_mobility >= root_mobility + 2,
                )

    def _detect_center_breaks(
        self,
        board: chess.Board,
        steps_by_route: dict[str, list[_PlanStep]],
        candidates: dict[tuple[PlanType, str, str], _Candidate],
    ) -> None:
        del board
        for route_id, steps in steps_by_route.items():
            for index, step in enumerate(steps):
                if (
                    step.ply > self.early_plan_plies
                    or step.piece.piece_type != chess.PAWN
                    or step.before.is_capture(step.move)
                    or chess.square_file(step.move.from_square) not in {2, 3, 4, 5}
                    or not _pawn_challenges_enemy_pawn(step.after, step.move.to_square)
                ):
                    continue
                target = chess.square_name(step.move.to_square)
                preparatory = [
                    item.san
                    for item in steps[:index]
                    if item.side == step.side
                ][-1:]
                candidate = self._candidate(
                    candidates,
                    "prepare_center_break",
                    step.side,
                    target,
                    f"准备并实施{target}方向的中心兵突破",
                )
                origin = chess.square_name(step.piece_origin)
                self._support(
                    candidate,
                    route_id,
                    step.route_evaluation,
                    [*preparatory, step.san],
                    [
                        f"{origin}兵沿中心方向合法推进到{target}",
                        f"推进后直接挑战对方兵结构",
                    ],
                    strong=True,
                )

    def _detect_open_files(
        self,
        open_files: set[str],
        steps_by_route: dict[str, list[_PlanStep]],
        candidates: dict[tuple[PlanType, str, str], _Candidate],
    ) -> None:
        for route_id, steps in steps_by_route.items():
            seen: set[tuple[str, str]] = set()
            for step in steps:
                target_file = chess.FILE_NAMES[chess.square_file(step.move.to_square)]
                key = (step.side, target_file)
                if (
                    step.ply > self.early_plan_plies
                    or step.piece.piece_type != chess.ROOK
                    or target_file not in open_files
                    or chess.square_file(step.move.from_square) == chess.square_file(step.move.to_square)
                    or key in seen
                ):
                    continue
                seen.add(key)
                candidate = self._candidate(
                    candidates,
                    "occupy_open_file",
                    step.side,
                    target_file,
                    f"用车占领{target_file}开放线",
                )
                self._support(
                    candidate,
                    route_id,
                    step.route_evaluation,
                    [step.san],
                    [f"{target_file}线没有双方兵，路线中的车合法进入该线"],
                    strong=True,
                )

    def _detect_rook_activation(
        self,
        board: chess.Board,
        open_files: set[str],
        steps_by_route: dict[str, list[_PlanStep]],
        candidates: dict[tuple[PlanType, str, str], _Candidate],
    ) -> None:
        rook_mobility = {
            square: _piece_mobility(board, square)
            for square, piece in board.piece_map().items()
            if piece.piece_type == chess.ROOK
        }
        for route_id, steps in steps_by_route.items():
            seen: set[tuple[str, int]] = set()
            for step in steps:
                origin = step.piece_origin
                key = (step.side, origin)
                target_file = chess.FILE_NAMES[chess.square_file(step.move.to_square)]
                if (
                    step.ply > self.early_plan_plies
                    or step.piece.piece_type != chess.ROOK
                    or origin not in rook_mobility
                    or rook_mobility[origin] > 3
                    or key in seen
                    or target_file in open_files
                    or step.before.is_capture(step.move)
                ):
                    continue
                after_mobility = _piece_mobility(step.after, step.move.to_square)
                if after_mobility < rook_mobility[origin] + 2:
                    continue
                seen.add(key)
                square = chess.square_name(origin)
                candidate = self._candidate(
                    candidates,
                    "activate_rook",
                    step.side,
                    square,
                    f"改善{_side_name(step.side)}{square}车的活动",
                )
                self._support(
                    candidate,
                    route_id,
                    step.route_evaluation,
                    [step.san],
                    [f"{square}车活动格由{rook_mobility[origin]}个增加到{after_mobility}个"],
                    strong=after_mobility >= rook_mobility[origin] + 3,
                )

    def _detect_king_safety(
        self,
        board: chess.Board,
        steps_by_route: dict[str, list[_PlanStep]],
        candidates: dict[tuple[PlanType, str, str], _Candidate],
    ) -> None:
        del board
        for route_id, steps in steps_by_route.items():
            seen: set[tuple[str, str]] = set()
            for step in steps:
                if step.ply > self.early_plan_plies:
                    continue
                subtype = ""
                evidence = ""
                strong = False
                if step.before.is_castling(step.move):
                    subtype = "castling"
                    evidence = "verified路线包含合法王车易位"
                    strong = True
                elif step.piece.piece_type == chess.PAWN:
                    before_shield = _pawn_shield_count(step.before, step.side)
                    after_shield = _pawn_shield_count(step.after, step.side)
                    if after_shield > before_shield:
                        subtype = "pawn_shield"
                        evidence = f"兵推进后王前兵盾由{before_shield}枚增至{after_shield}枚"
                elif (
                    step.captured_piece is not None
                    and step.captured_piece.piece_type in {
                        chess.KNIGHT, chess.BISHOP, chess.ROOK, chess.QUEEN,
                    }
                    and _captured_piece_attacked_king_zone(step)
                ):
                    subtype = f"exchange_attacker:{chess.square_name(step.move.to_square)}"
                    evidence = "路线通过合法交换移除正在作用于王区的攻击子"
                    strong = True
                if not subtype or (step.side, subtype) in seen:
                    continue
                seen.add((step.side, subtype))
                candidate = self._candidate(
                    candidates,
                    "improve_king_safety",
                    step.side,
                    subtype,
                    f"通过{_king_safety_goal(subtype)}改善{_side_name(step.side)}王安全",
                )
                self._support(
                    candidate,
                    route_id,
                    step.route_evaluation,
                    [step.san],
                    [evidence],
                    strong=strong,
                )

    def _detect_weak_pawn_attacks(
        self,
        weak_pawns: dict[tuple[str, int], list[str]],
        steps_by_route: dict[str, list[_PlanStep]],
        candidates: dict[tuple[PlanType, str, str], _Candidate],
    ) -> None:
        for route_id, steps in steps_by_route.items():
            seen: set[tuple[str, int]] = set()
            for step in steps:
                if step.ply > self.early_plan_plies:
                    continue
                for (pawn_side, target), evidence in weak_pawns.items():
                    attacker_side = _opposite(pawn_side)
                    key = (attacker_side, target)
                    if step.side != attacker_side or key in seen:
                        continue
                    captured_target = (
                        step.captured_piece is not None
                        and step.captured_piece.piece_type == chess.PAWN
                        and step.move.to_square == target
                    )
                    attacks_target = target in step.after.attacks(step.move.to_square)
                    if not captured_target and not attacks_target:
                        continue
                    seen.add(key)
                    target_name = chess.square_name(target)
                    candidate = self._candidate(
                        candidates,
                        "attack_weak_pawn",
                        attacker_side,
                        target_name,
                        f"集中攻击{target_name}弱兵",
                    )
                    self._support(
                        candidate,
                        route_id,
                        step.route_evaluation,
                        [step.san],
                        [*evidence, f"路线中的棋子已合法攻击或取得{target_name}兵"],
                        strong=captured_target,
                    )

    def _detect_passed_pawn_creation(
        self,
        board: chess.Board,
        steps_by_route: dict[str, list[_PlanStep]],
        candidates: dict[tuple[PlanType, str, str], _Candidate],
    ) -> None:
        root_passed_files = {
            (_side(color), chess.square_file(square))
            for color in (chess.WHITE, chess.BLACK)
            for square in board.pieces(chess.PAWN, color)
            if _is_passed_pawn(board, square, color)
        }
        for route_id, steps in steps_by_route.items():
            seen: set[tuple[str, int]] = set()
            for step in steps:
                if step.ply > min(6, self.early_plan_plies + 1):
                    continue
                color = chess.WHITE if step.side == "white" else chess.BLACK
                for square in step.after.pieces(chess.PAWN, color):
                    file_index = chess.square_file(square)
                    key = (step.side, file_index)
                    if (
                        key in root_passed_files
                        or key in seen
                        or not _is_passed_pawn(step.after, square, color)
                    ):
                        continue
                    seen.add(key)
                    file_name = chess.FILE_NAMES[file_index]
                    candidate = self._candidate(
                        candidates,
                        "create_passed_pawn",
                        step.side,
                        file_name,
                        f"通过兵结构转换制造{file_name}线通路兵",
                    )
                    self._support(
                        candidate,
                        route_id,
                        step.route_evaluation,
                        [step.san],
                        [f"路线转换后{chess.square_name(square)}兵前方及相邻线路已无敌兵"],
                        strong=step.before.is_capture(step.move),
                    )

    def _detect_simplification(
        self,
        board: chess.Board,
        package: "ChessFactPackage",
        steps_by_route: dict[str, list[_PlanStep]],
        candidates: dict[tuple[PlanType, str, str], _Candidate],
    ) -> None:
        advantage_side = _advantage_side(package.evaluation.evaluation_cp)
        if advantage_side is None:
            return
        root_non_pawns = _non_pawn_material_count(board)
        for route_id, steps in steps_by_route.items():
            for index, step in enumerate(steps[:-1]):
                reply = steps[index + 1]
                if (
                    step.side != advantage_side
                    or step.ply > self.early_plan_plies
                    or step.captured_piece is None
                    or reply.captured_piece is None
                    or PIECE_VALUES[step.captured_piece.piece_type] < 3
                    or PIECE_VALUES[reply.captured_piece.piece_type] < 3
                    or abs(
                        PIECE_VALUES[step.captured_piece.piece_type]
                        - PIECE_VALUES[reply.captured_piece.piece_type]
                    ) > 2
                    or _non_pawn_material_count(reply.after) >= root_non_pawns
                    or not _evaluation_favors(
                        step.route_evaluation,
                        advantage_side,
                        minimum_cp=100,
                    )
                ):
                    continue
                candidate = self._candidate(
                    candidates,
                    "simplify_endgame",
                    advantage_side,
                    "favorable_exchange",
                    f"通过主动交换进入对{_side_name(advantage_side)}有利的简化局面",
                )
                self._support(
                    candidate,
                    route_id,
                    step.route_evaluation,
                    [step.san, reply.san],
                    ["优势方主动发起重要子力交换，交换后非兵子力数量下降且路线评价保持优势"],
                    strong=True,
                )
                break

    @staticmethod
    def _candidate(
        candidates: dict[tuple[PlanType, str, str], _Candidate],
        type_: PlanType,
        side: Literal["white", "black"],
        key: str,
        goal: str,
    ) -> _Candidate:
        identity = (type_, side, key)
        if identity not in candidates:
            candidates[identity] = _Candidate(
                type=type_,
                side=side,
                key=key,
                goal=goal,
                supporting_moves=set(),
                route_ids=set(),
                structural_evidence=set(),
                evaluations={},
            )
        return candidates[identity]

    @staticmethod
    def _support(
        candidate: _Candidate,
        route_id: str,
        evaluation: int | None,
        moves: list[str],
        evidence: list[str],
        *,
        strong: bool,
    ) -> None:
        if evaluation is None:
            return
        candidate.route_ids.add(route_id)
        candidate.evaluations[route_id] = evaluation
        candidate.supporting_moves.update(move for move in moves if move)
        candidate.structural_evidence.update(item for item in evidence if item)
        candidate.strong_structure = candidate.strong_structure or strong

    def _candidate_valid(self, candidate: _Candidate) -> bool:
        if len(candidate.route_ids) < 2:
            return False
        values = [
            candidate.evaluations[route_id]
            for route_id in candidate.route_ids
            if route_id in candidate.evaluations
        ]
        if len(values) != len(candidate.route_ids):
            return False
        if max(values) - min(values) > self.stable_route_threshold_cp:
            return False
        if min(values) < -75 and max(values) > 75:
            return False
        return bool(candidate.supporting_moves and candidate.structural_evidence)

    @staticmethod
    def _to_plan(candidate: _Candidate) -> StrategicPlanFact:
        confidence = (
            "high"
            if len(candidate.route_ids) >= 3 or candidate.strong_structure
            else "medium"
        )
        return StrategicPlanFact(
            plan_id="pending",
            side=candidate.side,
            type=candidate.type,
            goal=candidate.goal,
            supporting_moves=sorted(candidate.supporting_moves),
            evidence_route_ids=sorted(candidate.route_ids),
            structural_evidence=sorted(candidate.structural_evidence),
            confidence=confidence,
        )


def _open_files(board: chess.Board, facts: "PositionFacts | None") -> set[str]:
    if facts is not None and facts.open_files:
        return set(facts.open_files)
    return {
        chess.FILE_NAMES[file_index]
        for file_index in range(8)
        if not board.pieces(chess.PAWN, chess.WHITE).intersection(
            chess.SquareSet(chess.BB_FILES[file_index])
        )
        and not board.pieces(chess.PAWN, chess.BLACK).intersection(
            chess.SquareSet(chess.BB_FILES[file_index])
        )
    }


def _weak_pawns(
    board: chess.Board,
    facts: "PositionFacts | None",
) -> dict[tuple[str, int], list[str]]:
    result: dict[tuple[str, int], list[str]] = {}
    allowed = {"isolated_pawn", "doubled_pawns", "vulnerable_pawn"}
    if facts is not None:
        for fact in facts.pawn_structure:
            if fact.category not in allowed:
                continue
            for square_name in fact.squares:
                if square_name not in chess.SQUARE_NAMES:
                    continue
                square = chess.parse_square(square_name)
                piece = board.piece_at(square)
                if piece is None or piece.piece_type != chess.PAWN:
                    continue
                key = (_side(piece.color), square)
                result.setdefault(key, []).append(fact.description)
    if result:
        return result
    for color in (chess.WHITE, chess.BLACK):
        pawns = board.pieces(chess.PAWN, color)
        file_counts = {
            file_index: sum(
                1 for square in pawns
                if chess.square_file(square) == file_index
            )
            for file_index in range(8)
        }
        for square in pawns:
            file_index = chess.square_file(square)
            adjacent = any(
                file_counts.get(index, 0)
                for index in (file_index - 1, file_index + 1)
                if 0 <= index < 8
            )
            reasons = []
            if not adjacent:
                reasons.append(f"{chess.square_name(square)}兵是孤兵")
            if file_counts[file_index] >= 2:
                reasons.append(f"{chess.FILE_NAMES[file_index]}线存在叠兵")
            if reasons:
                result[(_side(color), square)] = reasons
    return result


def _piece_mobility(board: chess.Board, square: int) -> int:
    piece = board.piece_at(square)
    if piece is None:
        return 0
    return sum(
        1
        for target in board.attacks(square)
        if board.color_at(target) != piece.color
    )


def _pawn_challenges_enemy_pawn(board: chess.Board, square: int) -> bool:
    pawn = board.piece_at(square)
    if pawn is None or pawn.piece_type != chess.PAWN:
        return False
    return any(
        (target := board.piece_at(attacked)) is not None
        and target.piece_type == chess.PAWN
        and target.color != pawn.color
        for attacked in board.attacks(square)
    )


def _captured_square(board: chess.Board, move: chess.Move) -> int | None:
    if not board.is_capture(move):
        return None
    if board.is_en_passant(move):
        return move.to_square + (-8 if board.turn == chess.WHITE else 8)
    return move.to_square


def _castling_rook_squares(move: chess.Move) -> tuple[int, int]:
    rank = chess.square_rank(move.from_square)
    if chess.square_file(move.to_square) > chess.square_file(move.from_square):
        return chess.square(7, rank), chess.square(5, rank)
    return chess.square(0, rank), chess.square(3, rank)


def _pawn_shield_count(board: chess.Board, side: str) -> int:
    color = chess.WHITE if side == "white" else chess.BLACK
    king = board.king(color)
    if king is None:
        return 0
    direction = 1 if color == chess.WHITE else -1
    count = 0
    for rank_delta in (1, 2):
        rank = chess.square_rank(king) + direction * rank_delta
        if not 0 <= rank < 8:
            continue
        for file_delta in (-1, 0, 1):
            file_index = chess.square_file(king) + file_delta
            if not 0 <= file_index < 8:
                continue
            if board.piece_at(chess.square(file_index, rank)) == chess.Piece(chess.PAWN, color):
                count += 1
    return count


def _captured_piece_attacked_king_zone(step: _PlanStep) -> bool:
    enemy_color = not step.piece.color
    king = step.before.king(step.piece.color)
    if king is None:
        return False
    attackers = step.before.attackers(enemy_color, king)
    if step.move.to_square in attackers:
        return True
    king_zone = set(step.before.attacks(king))
    captured_square = step.move.to_square
    return any(
        captured_square in step.before.attackers(enemy_color, square)
        for square in king_zone
    )


def _king_safety_goal(subtype: str) -> str:
    if subtype == "castling":
        return "王车易位"
    if subtype == "pawn_shield":
        return "建立兵盾"
    return "交换王区攻击子"


def _is_passed_pawn(board: chess.Board, square: int, color: chess.Color) -> bool:
    file_index = chess.square_file(square)
    rank_index = chess.square_rank(square)
    enemy = not color
    for enemy_square in board.pieces(chess.PAWN, enemy):
        enemy_file = chess.square_file(enemy_square)
        enemy_rank = chess.square_rank(enemy_square)
        if abs(enemy_file - file_index) > 1:
            continue
        if color == chess.WHITE and enemy_rank > rank_index:
            return False
        if color == chess.BLACK and enemy_rank < rank_index:
            return False
    return True


def _non_pawn_material_count(board: chess.Board) -> int:
    return sum(
        len(board.pieces(piece_type, color))
        for color in (chess.WHITE, chess.BLACK)
        for piece_type in (chess.KNIGHT, chess.BISHOP, chess.ROOK, chess.QUEEN)
    )


def _advantage_side(centipawn: int | None) -> Literal["white", "black"] | None:
    if centipawn is None or abs(centipawn) < 100:
        return None
    return "white" if centipawn > 0 else "black"


def _evaluation_favors(
    centipawn: int | None,
    side: str,
    *,
    minimum_cp: int,
) -> bool:
    if centipawn is None:
        return False
    return centipawn >= minimum_cp if side == "white" else centipawn <= -minimum_cp


def _allowed_under_threat(plan: StrategicPlanFact, threats: list[object]) -> bool:
    for threat in threats:
        confidence = getattr(threat, "confidence", None)
        threat_side = getattr(threat, "side", None)
        threat_type = getattr(threat, "type", None)
        if (
            confidence == "high"
            and threat_side in {"white", "black"}
            and _opposite(threat_side) == plan.side
            and threat_type in {"mate_threat", "material_win"}
            and plan.type in SLOW_PLAN_TYPES
            and plan.confidence != "high"
        ):
            return False
    return True


def _side(color: chess.Color) -> Literal["white", "black"]:
    return "white" if color == chess.WHITE else "black"


def _opposite(side: str) -> Literal["white", "black"]:
    return "black" if side == "white" else "white"


def _side_name(side: str) -> str:
    return "白方" if side == "white" else "黑方"


def _plan_priority(type_: PlanType) -> int:
    return {
        "improve_king_safety": 0,
        "prepare_center_break": 1,
        "occupy_open_file": 2,
        "activate_rook": 3,
        "improve_worst_piece": 4,
        "attack_weak_pawn": 5,
        "create_passed_pawn": 6,
        "simplify_endgame": 7,
    }[type_]
