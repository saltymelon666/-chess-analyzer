from __future__ import annotations

import hashlib
from dataclasses import dataclass
from types import SimpleNamespace
from typing import TYPE_CHECKING, Literal, Protocol

import chess
from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from .chess_facts import ChessFactPackage, FactCandidateRoute
    from .models import EngineResult


THREAT_PACKAGE_VERSION = "1.1"
PIECE_VALUES = {
    chess.PAWN: 1,
    chess.KNIGHT: 3,
    chess.BISHOP: 3,
    chess.ROOK: 5,
    chess.QUEEN: 9,
    chess.KING: 0,
}
ThreatType = Literal[
    "mate_threat",
    "tactical_capture",
    "material_win",
    "promotion_threat",
    "center_break",
    "prepared_tactic",
]
ThreatScope = Literal[
    "current_direct_threat",
    "prepared_threat",
    "route_event",
]


class ThreatIgnoreTest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    performed: bool = False
    ignored_move: str | None = None
    evaluation_before: float | None = None
    evaluation_after: float | None = None
    evaluation_loss: float | None = None


class ThreatFact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    threat_id: str
    type: ThreatType
    scope: ThreatScope = "current_direct_threat"
    side: Literal["white", "black"]
    target: str | None = None
    supporting_moves: list[str] = Field(default_factory=list, min_length=1)
    preparation_moves: list[str] = Field(default_factory=list)
    evidence_route_ids: list[str] = Field(default_factory=list, min_length=1)
    evidence: list[str] = Field(default_factory=list, min_length=1)
    ignore_test: ThreatIgnoreTest = Field(default_factory=ThreatIgnoreTest)
    confidence: Literal["medium", "high"]
    source: Literal[
        "python-chess+stockfish",
        "python-chess+stockfish+ignore-test",
        "python-chess+stockfish+depth-escalation",
    ] = "python-chess+stockfish"


class ThreatPackage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal["1.1"] = THREAT_PACKAGE_VERSION
    position_id: str
    threats: list[ThreatFact] = Field(default_factory=list)
    prepared_threats: list[ThreatFact] = Field(default_factory=list)
    route_events: list[ThreatFact] = Field(default_factory=list)


class InitiativeAssessment(BaseModel):
    """A conservative, program-owned initiative decision."""

    model_config = ConfigDict(extra="forbid")

    side: Literal["white", "black", "unknown"] = "unknown"
    direct_threat_ids: list[str] = Field(default_factory=list)
    forcing_route_ids: list[str] = Field(default_factory=list)
    dynamic_source: bool = False
    reason: str


class ThreatEngine(Protocol):
    async def analyze_many(
        self,
        fens: list[str],
        *,
        depth: int,
        timeout_seconds: float,
    ) -> list["EngineResult"]: ...


@dataclass(frozen=True)
class _RouteStep:
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
    captured_value: int
    captured_piece: str | None


@dataclass
class _Candidate:
    type: ThreatType
    side: Literal["white", "black"]
    target: str | None
    supporting_moves: set[str]
    route_ids: set[str]
    evidence: set[str]
    route_ranks: set[int]
    scope: ThreatScope
    plies: set[int]
    preparation_moves: set[str]
    important_material: bool = False
    repeated: bool = False


class ThreatAnalyzer:
    """Find only route-proven threats and optionally run a bounded ignore test."""

    def __init__(
        self,
        *,
        evaluation_loss_threshold_pawns: float = 1.5,
        stable_route_threshold_cp: int = 75,
        ignore_depth: int = 8,
        ignore_timeout_seconds: float = 12.0,
        max_ignore_moves: int = 3,
        max_ignore_tests: int = 1,
        mate_probe_depth: int = 13,
        mate_probe_timeout_seconds: float = 20.0,
        mate_probe_min_checks: int = 2,
        mate_probe_min_abs_cp: int = 150,
    ) -> None:
        self.evaluation_loss_threshold_pawns = evaluation_loss_threshold_pawns
        self.stable_route_threshold_cp = stable_route_threshold_cp
        self.ignore_depth = ignore_depth
        self.ignore_timeout_seconds = ignore_timeout_seconds
        self.max_ignore_moves = max(1, min(max_ignore_moves, 5))
        self.max_ignore_tests = max(0, max_ignore_tests)
        self.mate_probe_depth = max(1, mate_probe_depth)
        self.mate_probe_timeout_seconds = mate_probe_timeout_seconds
        self.mate_probe_min_checks = max(1, mate_probe_min_checks)
        self.mate_probe_min_abs_cp = max(0, mate_probe_min_abs_cp)

    async def analyze(
        self,
        package: "ChessFactPackage",
        *,
        stockfish: ThreatEngine | None = None,
    ) -> ThreatPackage:
        classified = self.classify(package)
        threats = list(classified.threats)
        prepared_threats: list[ThreatFact] = []
        if stockfish is not None and callable(getattr(stockfish, "analyze_many", None)):
            if not any(item.type == "mate_threat" for item in threats):
                mate_threat = await self._probe_deeper_mate(package, stockfish)
                if mate_threat is not None:
                    threats.insert(0, mate_threat)
            tested = 0
            retained: list[ThreatFact] = []
            for threat in threats:
                if tested < self.max_ignore_tests:
                    ignore = await self._perform_ignore_test(package, threat, stockfish)
                    if ignore.performed:
                        tested += 1
                        threat.ignore_test = ignore
                        threat.source = "python-chess+stockfish+ignore-test"
                if self._confirmed(threat):
                    retained.append(threat)
            threats = retained
            for event in classified.route_events:
                if tested >= self.max_ignore_tests or not event.preparation_moves:
                    continue
                ignore = await self._perform_prepared_ignore_test(package, event, stockfish)
                if not ignore.performed:
                    continue
                tested += 1
                if (
                    ignore.evaluation_loss is None
                    or ignore.evaluation_loss < self.evaluation_loss_threshold_pawns
                ):
                    continue
                prepared = event.model_copy(deep=True)
                prepared.scope = "prepared_threat"
                prepared.threat_id = f"prepared_threat_{len(prepared_threats) + 1}"
                prepared.ignore_test = ignore
                prepared.source = "python-chess+stockfish+ignore-test"
                prepared_threats.append(prepared)
            if tested < self.max_ignore_tests:
                latent = await self._discover_latent_prepared_tactic(package, stockfish)
                if latent is not None:
                    tested += 1
                    prepared_threats.append(latent)
            threats.extend(prepared_threats)
        return ThreatPackage(
            position_id=position_id(package.position.fen),
            threats=threats,
            prepared_threats=prepared_threats,
            route_events=classified.route_events,
        )

    async def _probe_deeper_mate(
        self,
        package: "ChessFactPackage",
        stockfish: ThreatEngine,
    ) -> ThreatFact | None:
        baseline_cp = package.evaluation.evaluation_cp
        if baseline_cp is None or abs(baseline_cp) < self.mate_probe_min_abs_cp:
            return None
        board = chess.Board(package.position.fen)
        legal_checks = [move for move in board.legal_moves if board.gives_check(move)]
        if len(legal_checks) < self.mate_probe_min_checks:
            return None
        try:
            results = await stockfish.analyze_many(
                [board.fen()],
                depth=self.mate_probe_depth,
                timeout_seconds=self.mate_probe_timeout_seconds,
            )
        except (TimeoutError, RuntimeError, ValueError):
            return None
        if len(results) != 1:
            return None
        result = results[0]
        if result.mate_in is None or not result.top_moves:
            return None
        candidate = result.top_moves[0]
        if candidate.mate_in is None:
            return None
        try:
            first = chess.Move.from_uci(candidate.move)
        except ValueError:
            return None
        if first not in board.legal_moves or not board.gives_check(first):
            return None
        line_moves = candidate.line or [SimpleNamespace(uci=candidate.move)]
        from .chess_facts import verify_route

        deep_route = verify_route(
            route_id="depth_mate_probe",
            start_fen=board.fen(),
            moves=line_moves,
            evaluation=result.centipawn,
            mate=result.mate_in,
        )
        if not deep_route.verified or not deep_route.moves_san:
            return None
        package.candidate_routes = [
            *[route for route in package.candidate_routes if route.route_id != deep_route.route_id],
            deep_route,
        ]
        side = "white" if result.mate_in > 0 else "black"
        target_board = board.copy(stack=False)
        target_board.push(first)
        return ThreatFact(
            threat_id="threat_depth_mate_1",
            type="mate_threat",
            scope="current_direct_threat",
            side=side,
            target=_king_square_name(target_board, _opposite(side)),
            supporting_moves=[board.san(first)],
            evidence_route_ids=[deep_route.route_id],
            evidence=[
                f"浅层存在{len(legal_checks)}个合法将军且评价幅度达到条件，"
                f"Stockfish条件化升级到深度{self.mate_probe_depth}后确认M{abs(result.mate_in)}",
                f"python-chess验证深度升级走法{candidate.move} / {board.san(first)}",
            ],
            confidence="high",
            source="python-chess+stockfish+depth-escalation",
        )

    def detect(self, package: "ChessFactPackage") -> list[ThreatFact]:
        """Return only threats that are valid at the current root position."""
        return self.classify(package).threats

    def classify(self, package: "ChessFactPackage") -> ThreatPackage:
        """Separate root threats from events that only occur inside a PV."""
        steps_by_route = self._verified_route_steps(package)
        candidates: dict[
            tuple[ThreatType, str, str | None, str, ThreatScope],
            _Candidate,
        ] = {}
        base_cp = package.evaluation.evaluation_cp

        for route_rank, route in enumerate(package.candidate_routes, start=1):
            steps = steps_by_route.get(route.route_id, [])
            if not steps:
                continue
            route_side = steps[0].side
            mate_for_route_side = _mate_favors(route.mate, route_side)
            mating_step = next((step for step in steps if step.after.is_checkmate()), None)
            if mate_for_route_side or mating_step is not None:
                support = steps[0]
                self._merge_candidate(
                    candidates,
                    type_="mate_threat",
                    side=route_side,
                    target=_king_square_name(support.after, _opposite(route_side)),
                    supporting_move=support.san,
                    route=route,
                    route_rank=route_rank,
                    ply=1,
                    preparation_move=None,
                    evidence=(
                        f"{route.route_id}为verified路线，Stockfish给出将杀评价"
                        if route.mate is not None
                        else f"{route.route_id}逐步重放后形成将杀"
                    ),
                )

            for index, step in enumerate(steps):
                if step.captured_value < 3:
                    continue
                net_gain = _short_material_gain(steps, index, step.side)
                evaluation_swing = (
                    abs(route.evaluation - base_cp)
                    if route.evaluation is not None and base_cp is not None
                    else 0
                )
                evaluation_supported = (
                    evaluation_swing >= 150
                    or _evaluation_favors(
                        route.evaluation,
                        route.mate,
                        step.side,
                        minimum_cp=150,
                    )
                )
                if net_gain < 2 or not evaluation_supported:
                    continue
                target = chess.square_name(step.move.to_square)
                candidate = self._merge_candidate(
                    candidates,
                    type_="tactical_capture",
                    side=step.side,
                    target=target,
                    supporting_move=step.san,
                    route=route,
                    route_rank=route_rank,
                    ply=step.ply,
                    preparation_move=_preparation_move(steps, step),
                    evidence=(
                        f"{route.route_id}第{step.ply}步合法吃掉"
                        f"{step.captured_piece or '重要棋子'}，短路线净收益{net_gain}分，"
                        "Stockfish评价支持该结果"
                    ),
                )
                candidate.important_material = True

            route_gain = _route_material_gain(steps, route_side)
            first_winning_capture = next(
                (
                    step for step in steps
                    if step.side == route_side and step.captured_value >= 3
                ),
                None,
            )
            if (
                route_rank <= 2
                and
                route_gain >= 3
                and first_winning_capture is not None
                and _evaluation_favors(route.evaluation, route.mate, route_side, minimum_cp=150)
            ):
                candidate = self._merge_candidate(
                    candidates,
                    type_="material_win",
                    side=route_side,
                    target=chess.square_name(first_winning_capture.move.to_square),
                    supporting_move=first_winning_capture.san,
                    route=route,
                    route_rank=route_rank,
                    ply=first_winning_capture.ply,
                    preparation_move=_preparation_move(steps, first_winning_capture),
                    evidence=(
                        f"{route.route_id}完整verified路线为{route_side}带来"
                        f"{route_gain}分净子力收益，且高排名Stockfish评价支持"
                    ),
                )
                candidate.important_material = True

            for step in steps:
                if step.move.promotion is None:
                    continue
                if not _evaluation_favors(
                    route.evaluation,
                    route.mate,
                    step.side,
                    minimum_cp=200,
                ):
                    continue
                self._merge_candidate(
                    candidates,
                    type_="promotion_threat",
                    side=step.side,
                    target=chess.square_name(step.move.to_square),
                    supporting_move=step.san,
                    route=route,
                    route_rank=route_rank,
                    ply=step.ply,
                    preparation_move=_preparation_move(steps, step),
                    evidence=f"{route.route_id}的verified路线包含合法升变",
                )

        self._add_center_breaks(package, steps_by_route, candidates)
        merged = list(candidates.values())
        for candidate in merged:
            candidate.repeated = len(candidate.route_ids) >= 2

        confirmed = [
            candidate
            for candidate in merged
            if self._candidate_has_confirmation(candidate)
        ]
        direct_candidates = sorted(
            (
                candidate
                for candidate in confirmed
                if candidate.scope == "current_direct_threat"
            ),
            key=_candidate_sort_key,
        )
        route_candidates = sorted(
            (
                candidate
                for candidate in confirmed
                if candidate.scope == "route_event"
            ),
            key=lambda candidate: (min(candidate.plies), *_candidate_sort_key(candidate)),
        )
        direct = [
            self._to_threat(index, candidate)
            for index, candidate in enumerate(direct_candidates, start=1)
        ]
        route_events = [
            self._to_threat(index, candidate)
            for index, candidate in enumerate(route_candidates, start=1)
        ]
        for index, threat in enumerate(direct, start=1):
            threat.threat_id = f"threat_{index}"
        for index, event in enumerate(route_events, start=1):
            event.threat_id = f"route_event_{index}"
        return ThreatPackage(
            position_id=position_id(package.position.fen),
            threats=direct,
            route_events=route_events,
        )

    def _verified_route_steps(
        self,
        package: "ChessFactPackage",
    ) -> dict[str, list[_RouteStep]]:
        routes: dict[str, list[_RouteStep]] = {}
        for route_rank, route in enumerate(package.candidate_routes, start=1):
            if not route.verified or not route.moves_uci:
                continue
            board = chess.Board(package.position.fen)
            steps: list[_RouteStep] = []
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
                before = board.copy(stack=False)
                san = before.san(move)
                captured = _captured_piece(before, move)
                after = before.copy(stack=False)
                after.push(move)
                steps.append(_RouteStep(
                    route_id=route.route_id,
                    route_rank=route_rank,
                    route_evaluation=route.evaluation,
                    route_mate=route.mate,
                    ply=ply,
                    side="white" if before.turn == chess.WHITE else "black",
                    san=san,
                    uci=uci,
                    move=move,
                    before=before,
                    after=after,
                    captured_value=PIECE_VALUES.get(captured.piece_type, 0) if captured else 0,
                    captured_piece=chess.piece_name(captured.piece_type) if captured else None,
                ))
                board = after
            if valid and len(steps) == len(route.moves_uci):
                routes[route.route_id] = steps
        return routes

    def _add_center_breaks(
        self,
        package: "ChessFactPackage",
        steps_by_route: dict[str, list[_RouteStep]],
        candidates: dict[
            tuple[ThreatType, str, str | None, str, ThreatScope],
            _Candidate,
        ],
    ) -> None:
        occurrences: dict[tuple[str, str], list[tuple[_RouteStep, "FactCandidateRoute"]]] = {}
        routes_by_id = {route.route_id: route for route in package.candidate_routes}
        for route_id, steps in steps_by_route.items():
            seen: set[tuple[str, str]] = set()
            for step in steps:
                piece = step.before.piece_at(step.move.from_square)
                if (
                    piece is None
                    or piece.piece_type != chess.PAWN
                    or step.ply > 2
                    or step.before.is_capture(step.move)
                    or chess.square_file(step.move.from_square) not in (3, 4)
                    or chess.square_name(step.move.to_square) not in {"d4", "e4", "d5", "e5"}
                    or not _challenges_enemy_pawn(step)
                ):
                    continue
                key = (step.side, step.uci)
                if key in seen:
                    continue
                seen.add(key)
                occurrences.setdefault(key, []).append((step, routes_by_id[route_id]))

        for (_, _), items in occurrences.items():
            route_ids = {route.route_id for _, route in items}
            evaluations = [route.evaluation for _, route in items if route.evaluation is not None]
            if len(route_ids) < 2 or not evaluations:
                continue
            if max(evaluations) - min(evaluations) > self.stable_route_threshold_cp:
                continue
            for step, route in items:
                self._merge_candidate(
                    candidates,
                    type_="center_break",
                    side=step.side,
                    target=chess.square_name(step.move.to_square),
                    supporting_move=step.san,
                    route=route,
                    route_rank=step.route_rank,
                    ply=step.ply,
                    preparation_move=_preparation_move(
                        steps_by_route.get(route.route_id, []),
                        step,
                    ),
                    evidence=(
                        f"{route.route_id}包含同一中心兵推进，"
                        f"多路线评价差不超过{self.stable_route_threshold_cp}厘兵"
                    ),
                )

    @staticmethod
    def _merge_candidate(
        candidates: dict[
            tuple[ThreatType, str, str | None, str, ThreatScope],
            _Candidate,
        ],
        *,
        type_: ThreatType,
        side: Literal["white", "black"],
        target: str | None,
        supporting_move: str,
        route: "FactCandidateRoute",
        route_rank: int,
        ply: int,
        preparation_move: str | None,
        evidence: str,
    ) -> _Candidate:
        scope: ThreatScope = (
            "current_direct_threat"
            if ply == 1
            else "route_event"
        )
        key = (type_, side, target, supporting_move, scope)
        candidate = candidates.get(key)
        if candidate is None:
            candidate = _Candidate(
                type=type_,
                side=side,
                target=target,
                supporting_moves=set(),
                route_ids=set(),
                evidence=set(),
                route_ranks=set(),
                scope=scope,
                plies=set(),
                preparation_moves=set(),
            )
            candidates[key] = candidate
        candidate.supporting_moves.add(supporting_move)
        candidate.route_ids.add(route.route_id)
        candidate.evidence.add(evidence)
        candidate.route_ranks.add(route_rank)
        candidate.plies.add(ply)
        if preparation_move:
            candidate.preparation_moves.add(preparation_move)
        return candidate

    @staticmethod
    def _candidate_has_confirmation(candidate: _Candidate) -> bool:
        if candidate.type == "mate_threat":
            return True
        if candidate.type == "center_break":
            return candidate.repeated
        if candidate.type == "promotion_threat":
            return True
        return candidate.important_material or candidate.repeated

    @staticmethod
    def _to_threat(index: int, candidate: _Candidate) -> ThreatFact:
        confidence = (
            "high"
            if candidate.type == "mate_threat"
            or candidate.repeated
            or candidate.type == "material_win"
            else "medium"
        )
        return ThreatFact(
            threat_id=f"threat_{index}",
            type=candidate.type,
            scope=candidate.scope,
            side=candidate.side,
            target=candidate.target,
            supporting_moves=sorted(candidate.supporting_moves),
            preparation_moves=sorted(candidate.preparation_moves),
            evidence_route_ids=sorted(candidate.route_ids),
            evidence=sorted(candidate.evidence),
            confidence=confidence,
        )

    async def _perform_ignore_test(
        self,
        package: "ChessFactPackage",
        threat: ThreatFact,
        stockfish: ThreatEngine,
    ) -> ThreatIgnoreTest:
        board = self._board_after_supporting_move(package, threat)
        if board is None or board.is_check() or board.is_game_over(claim_draw=True):
            return ThreatIgnoreTest()
        ignored = self._ignore_moves(board, threat)
        if not ignored:
            return ThreatIgnoreTest()

        fens: list[str] = []
        sans: list[str] = []
        for move in ignored:
            sans.append(board.san(move))
            after = board.copy(stack=False)
            after.push(move)
            fens.append(after.fen())
        try:
            results = await stockfish.analyze_many(
                fens,
                depth=self.ignore_depth,
                timeout_seconds=self.ignore_timeout_seconds,
            )
        except (TimeoutError, RuntimeError, ValueError):
            return ThreatIgnoreTest()
        if len(results) != len(fens):
            return ThreatIgnoreTest()

        baseline_cp = package.evaluation.evaluation_cp
        if baseline_cp is None:
            route = next(
                (
                    route for route in package.candidate_routes
                    if route.route_id in threat.evidence_route_ids
                ),
                None,
            )
            baseline_cp = route.evaluation if route is not None else None
        if baseline_cp is None:
            return ThreatIgnoreTest()

        victim = _opposite(threat.side)
        scored: list[tuple[float, str, float]] = []
        for san, result in zip(sans, results):
            after_cp = _score_as_cp(result.centipawn, result.mate_in)
            loss_cp = (
                baseline_cp - after_cp
                if victim == "white"
                else after_cp - baseline_cp
            )
            scored.append((loss_cp / 100, san, after_cp / 100))
        loss, ignored_san, after_pawns = max(scored, key=lambda item: item[0])
        return ThreatIgnoreTest(
            performed=True,
            ignored_move=ignored_san,
            evaluation_before=round(baseline_cp / 100, 2),
            evaluation_after=round(after_pawns, 2),
            evaluation_loss=round(max(0.0, loss), 2),
        )

    async def _discover_latent_prepared_tactic(
        self,
        package: "ChessFactPackage",
        stockfish: ThreatEngine,
    ) -> ThreatFact | None:
        baseline_cp = package.evaluation.evaluation_cp
        board = chess.Board(package.position.fen)
        if baseline_cp is None or board.is_check() or board.is_game_over(claim_draw=True):
            return None
        latent = _latent_prepared_double_attacks(board)
        if not latent:
            return None

        ignored = _quiet_ignore_moves(board, target_square=None, limit=self.max_ignore_moves)
        if len(ignored) < 2:
            return None
        fens: list[str] = []
        ignored_sans: list[str] = []
        ignored_boards: list[chess.Board] = []
        for move in ignored:
            ignored_sans.append(board.san(move))
            after = board.copy(stack=False)
            after.push(move)
            ignored_boards.append(after)
            fens.append(after.fen())
        try:
            results = await stockfish.analyze_many(
                fens,
                depth=self.ignore_depth,
                timeout_seconds=self.ignore_timeout_seconds,
            )
        except (TimeoutError, RuntimeError, ValueError):
            return None
        if len(results) != len(fens):
            return None

        victim = "white" if board.turn == chess.WHITE else "black"
        losses: list[float] = []
        after_scores: list[float] = []
        for result in results:
            after_cp = _score_as_cp(result.centipawn, result.mate_in)
            loss_cp = (
                baseline_cp - after_cp
                if victim == "white"
                else after_cp - baseline_cp
            )
            losses.append(max(0.0, loss_cp / 100))
            after_scores.append(after_cp / 100)
        minimum_loss = min(losses)
        if minimum_loss < self.evaluation_loss_threshold_pawns:
            return None

        required_occurrences = max(2, len(results) - 1)
        occurrences: dict[str, int] = {uci: 0 for uci in latent}
        sans: dict[str, str] = {}
        targets: dict[str, list[str]] = {}
        for after, result in zip(ignored_boards, results):
            top_uci = {item.move for item in result.top_moves[:2]}
            for uci in latent:
                if uci not in top_uci:
                    continue
                try:
                    move = chess.Move.from_uci(uci)
                except ValueError:
                    continue
                if move not in after.legal_moves:
                    continue
                attacked = _newly_attacked_targets(after, move)
                if not _is_meaningful_double_attack(attacked):
                    continue
                occurrences[uci] += 1
                sans.setdefault(uci, after.san(move))
                targets.setdefault(uci, [square for square, _, _ in attacked])
        supported = [
            uci for uci, count in occurrences.items()
            if count >= required_occurrences
        ]
        if not supported:
            return None
        chosen = max(supported, key=lambda uci: (occurrences[uci], uci))
        least_loss_index = losses.index(minimum_loss)
        route_ids = [
            route.route_id
            for route in package.candidate_routes
            if route.verified
        ][:2]
        if not route_ids:
            return None
        san = sans[chosen]
        return ThreatFact(
            threat_id="prepared_threat_latent_1",
            type="prepared_tactic",
            scope="prepared_threat",
            side=_opposite(victim),
            target="、".join(targets.get(chosen, [])[:3]) or None,
            supporting_moves=[san],
            preparation_moves=[san],
            evidence_route_ids=route_ids,
            evidence=[
                f"python-chess验证准备走法{chosen} / {san}会同时形成至少两个新攻击目标",
                f"Stockfish对{len(results)}个中性忽略着进行有界Ignore Test，"
                f"最小评价损失为{minimum_loss:.2f}兵",
            ],
            ignore_test=ThreatIgnoreTest(
                performed=True,
                ignored_move="、".join(ignored_sans),
                evaluation_before=round(baseline_cp / 100, 2),
                evaluation_after=round(after_scores[least_loss_index], 2),
                evaluation_loss=round(minimum_loss, 2),
            ),
            confidence="high" if len(results) >= 3 else "medium",
            source="python-chess+stockfish+ignore-test",
        )

    async def _perform_prepared_ignore_test(
        self,
        package: "ChessFactPackage",
        event: ThreatFact,
        stockfish: ThreatEngine,
    ) -> ThreatIgnoreTest:
        board = self._board_after_preparation(package, event)
        if board is None or board.is_check() or board.is_game_over(claim_draw=True):
            return ThreatIgnoreTest()
        ignored = [
            move
            for move in self._ignore_moves(board, event)
            if self._supporting_move_survives(board, move, event)
        ]
        if not ignored:
            return ThreatIgnoreTest()

        fens: list[str] = []
        sans: list[str] = []
        for move in ignored:
            sans.append(board.san(move))
            after = board.copy(stack=False)
            after.push(move)
            fens.append(after.fen())
        try:
            results = await stockfish.analyze_many(
                fens,
                depth=self.ignore_depth,
                timeout_seconds=self.ignore_timeout_seconds,
            )
        except (TimeoutError, RuntimeError, ValueError):
            return ThreatIgnoreTest()
        if len(results) != len(fens):
            return ThreatIgnoreTest()

        route = next(
            (
                route
                for route in package.candidate_routes
                if route.route_id in event.evidence_route_ids
            ),
            None,
        )
        baseline_cp = route.evaluation if route is not None else None
        if baseline_cp is None:
            baseline_cp = package.evaluation.evaluation_cp
        if baseline_cp is None:
            return ThreatIgnoreTest()

        victim = _opposite(event.side)
        scored: list[tuple[float, str, float]] = []
        for san, result in zip(sans, results):
            after_cp = _score_as_cp(result.centipawn, result.mate_in)
            loss_cp = (
                baseline_cp - after_cp
                if victim == "white"
                else after_cp - baseline_cp
            )
            scored.append((loss_cp / 100, san, after_cp / 100))
        loss, ignored_san, after_pawns = max(scored, key=lambda item: item[0])
        return ThreatIgnoreTest(
            performed=True,
            ignored_move=ignored_san,
            evaluation_before=round(baseline_cp / 100, 2),
            evaluation_after=round(after_pawns, 2),
            evaluation_loss=round(max(0.0, loss), 2),
        )

    @staticmethod
    def _board_after_preparation(
        package: "ChessFactPackage",
        event: ThreatFact,
    ) -> chess.Board | None:
        preparations = set(event.preparation_moves)
        if not preparations:
            return None
        for route in package.candidate_routes:
            if not route.verified or route.route_id not in event.evidence_route_ids:
                continue
            if not route.moves_uci:
                continue
            board = chess.Board(package.position.fen)
            move = chess.Move.from_uci(route.moves_uci[0])
            if move not in board.legal_moves:
                continue
            san = board.san(move)
            if san not in preparations:
                continue
            board.push(move)
            return board
        return None

    @staticmethod
    def _supporting_move_survives(
        board: chess.Board,
        ignored_move: chess.Move,
        event: ThreatFact,
    ) -> bool:
        after = board.copy(stack=False)
        after.push(ignored_move)
        supported = {_normal_san(item) for item in event.supporting_moves}
        for move in after.legal_moves:
            if _normal_san(after.san(move)) not in supported:
                continue
            if event.type in {"tactical_capture", "material_win"}:
                return after.is_capture(move)
            if event.type == "promotion_threat":
                return move.promotion is not None
            if event.type == "mate_threat":
                return after.gives_check(move)
            return True
        return False

    @staticmethod
    def _board_after_supporting_move(
        package: "ChessFactPackage",
        threat: ThreatFact,
    ) -> chess.Board | None:
        supported = set(threat.supporting_moves)
        for route in package.candidate_routes:
            if not route.verified or route.route_id not in threat.evidence_route_ids:
                continue
            board = chess.Board(package.position.fen)
            for san, uci in zip(route.moves_san, route.moves_uci):
                try:
                    move = chess.Move.from_uci(uci)
                except ValueError:
                    return None
                if move not in board.legal_moves or board.san(move) != san:
                    return None
                board.push(move)
                if san in supported:
                    return board
        return None

    def _ignore_moves(
        self,
        board: chess.Board,
        threat: ThreatFact,
    ) -> list[chess.Move]:
        target_square = (
            chess.parse_square(threat.target)
            if threat.target and threat.target in chess.SQUARE_NAMES
            else None
        )
        return _quiet_ignore_moves(
            board,
            target_square=target_square,
            limit=self.max_ignore_moves,
        )

    def _confirmed(self, threat: ThreatFact) -> bool:
        if threat.type == "mate_threat":
            return True
        if threat.confidence == "high":
            return True
        if any(
            "重要棋子" in evidence
            or "净收益" in evidence
            or "评价支持" in evidence
            for evidence in threat.evidence
        ):
            return True
        return bool(
            threat.ignore_test.performed
            and threat.ignore_test.evaluation_loss is not None
            and threat.ignore_test.evaluation_loss >= self.evaluation_loss_threshold_pawns
        )


def _quiet_ignore_moves(
    board: chess.Board,
    *,
    target_square: chess.Square | None,
    limit: int,
) -> list[chess.Move]:
    values: list[tuple[tuple[int, int, str], chess.Move]] = []
    for move in board.legal_moves:
        if (
            board.is_capture(move)
            or board.gives_check(move)
            or move.promotion is not None
            or board.is_castling(move)
            or move.to_square == target_square
            or move.from_square == target_square
        ):
            continue
        piece = board.piece_at(move.from_square)
        file_distance = min(
            chess.square_file(move.from_square),
            7 - chess.square_file(move.from_square),
        )
        priority = (
            0 if piece and piece.piece_type == chess.PAWN else 1,
            file_distance,
            move.uci(),
        )
        values.append((priority, move))
    values.sort(key=lambda item: item[0])
    return [move for _, move in values[:limit]]


def _latent_prepared_double_attacks(board: chess.Board) -> dict[str, list[str]]:
    probe = board.copy(stack=False)
    probe.push(chess.Move.null())
    result: dict[str, list[str]] = {}
    for move in probe.legal_moves:
        if probe.is_capture(move) or probe.gives_check(move) or move.promotion is not None:
            continue
        attacked = _newly_attacked_targets(probe, move)
        if _is_meaningful_double_attack(attacked):
            result[move.uci()] = [square for square, _, _ in attacked]
    return result


def _newly_attacked_targets(
    board: chess.Board,
    move: chess.Move,
) -> list[tuple[str, int, str]]:
    attacker = board.turn
    enemy = not attacker
    after = board.copy(stack=False)
    after.push(move)
    result: list[tuple[str, int, str]] = []
    for square, piece in board.piece_map().items():
        if piece.color != enemy or square == move.to_square:
            continue
        before_attackers = set(board.attackers(attacker, square))
        after_attackers = set(after.attackers(attacker, square))
        if not after_attackers - before_attackers:
            continue
        value = 10 if piece.piece_type == chess.KING else PIECE_VALUES.get(piece.piece_type, 0)
        result.append((
            chess.square_name(square),
            value,
            chess.piece_name(piece.piece_type),
        ))
    return sorted(result, key=lambda item: (-item[1], item[0]))


def _is_meaningful_double_attack(targets: list[tuple[str, int, str]]) -> bool:
    return len(targets) >= 2 and any(value >= 3 for _, value, _ in targets)


def assess_initiative(
    package: "ChessFactPackage",
    threat_package: ThreatPackage,
) -> InitiativeAssessment:
    """Require direct pressure, repeated forced replies, and a dynamic source."""
    current = [
        threat
        for threat in threat_package.threats
        if threat.scope in {"current_direct_threat", "prepared_threat"}
    ]
    if not current:
        return InitiativeAssessment(reason="no_current_direct_or_prepared_threat")

    sides = {threat.side for threat in current}
    if len(sides) != 1:
        return InitiativeAssessment(
            direct_threat_ids=[threat.threat_id for threat in current],
            reason="threat_side_is_not_unique",
        )
    side = next(iter(sides))
    dynamic = any(
        threat.type in {
            "mate_threat",
            "tactical_capture",
            "promotion_threat",
            "prepared_tactic",
        }
        for threat in current
    )
    if not dynamic:
        return InitiativeAssessment(
            direct_threat_ids=[threat.threat_id for threat in current],
            reason="advantage_source_is_static_or_material_only",
        )

    relevant_routes = {
        route_id
        for threat in current
        for route_id in threat.evidence_route_ids
    }
    forcing_routes: set[str] = set()
    for threat in current:
        if (
            threat.scope == "prepared_threat"
            and threat.ignore_test.performed
            and threat.ignore_test.evaluation_loss is not None
            and threat.ignore_test.evaluation_loss > 0
        ):
            forcing_routes.update(threat.evidence_route_ids)
    for route in package.candidate_routes:
        if (
            not route.verified
            or route.route_id not in relevant_routes
            or not route.moves_uci
        ):
            continue
        board = chess.Board(package.position.fen)
        move = chess.Move.from_uci(route.moves_uci[0])
        if move in board.legal_moves and board.gives_check(move):
            forcing_routes.add(route.route_id)

    if len(forcing_routes) < 2:
        return InitiativeAssessment(
            direct_threat_ids=[threat.threat_id for threat in current],
            forcing_route_ids=sorted(forcing_routes),
            dynamic_source=True,
            reason="opponent_not_forced_in_multiple_verified_routes",
        )
    return InitiativeAssessment(
        side=side,
        direct_threat_ids=[threat.threat_id for threat in current],
        forcing_route_ids=sorted(forcing_routes),
        dynamic_source=True,
        reason="direct_dynamic_pressure_with_multiple_forced_routes",
    )


def position_id(fen: str) -> str:
    normalized = chess.Board(fen).fen()
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]
    return f"position:{digest}"


def _captured_piece(board: chess.Board, move: chess.Move) -> chess.Piece | None:
    if not board.is_capture(move):
        return None
    square = move.to_square
    if board.is_en_passant(move):
        square += -8 if board.turn == chess.WHITE else 8
    return board.piece_at(square)


def _challenges_enemy_pawn(step: _RouteStep) -> bool:
    moved = step.after.piece_at(step.move.to_square)
    if moved is None or moved.piece_type != chess.PAWN:
        return False
    for square in step.after.attacks(step.move.to_square):
        target = step.after.piece_at(square)
        if (
            target is not None
            and target.piece_type == chess.PAWN
            and target.color != moved.color
        ):
            return True
    return False


def _preparation_move(
    steps: list[_RouteStep],
    event: _RouteStep,
) -> str | None:
    if event.ply < 3 or not steps or steps[0].side != event.side:
        return None
    return steps[0].san


def _normal_san(value: str) -> str:
    return value.rstrip("+#")


def _candidate_sort_key(candidate: _Candidate) -> tuple[int, int, str, str]:
    confidence_rank = (
        0
        if candidate.type == "mate_threat"
        or candidate.repeated
        or candidate.type == "material_win"
        else 1
    )
    return (
        _type_priority(candidate.type),
        confidence_rank,
        candidate.side,
        candidate.target or "",
    )


def _route_material_gain(steps: list[_RouteStep], side: str) -> int:
    gain = 0
    for step in steps:
        if not step.captured_value:
            continue
        gain += step.captured_value if step.side == side else -step.captured_value
    return gain


def _short_material_gain(
    steps: list[_RouteStep],
    capture_index: int,
    side: str,
) -> int:
    window = steps[capture_index:capture_index + 2]
    return _route_material_gain(window, side)


def _mate_favors(mate: int | None, side: str) -> bool:
    return bool(mate is not None and ((mate > 0) == (side == "white")))


def _evaluation_favors(
    centipawn: int | None,
    mate: int | None,
    side: str,
    *,
    minimum_cp: int,
) -> bool:
    if _mate_favors(mate, side):
        return True
    if centipawn is None:
        return False
    return centipawn >= minimum_cp if side == "white" else centipawn <= -minimum_cp


def _score_as_cp(centipawn: int | None, mate: int | None) -> int:
    if mate is not None:
        return 100_000 if mate > 0 else -100_000
    return centipawn or 0


def _opposite(side: str) -> Literal["white", "black"]:
    return "black" if side == "white" else "white"


def _king_square_name(board: chess.Board, side: str) -> str | None:
    square = board.king(chess.WHITE if side == "white" else chess.BLACK)
    return chess.square_name(square) if square is not None else None


def _type_priority(type_: ThreatType) -> int:
    return {
        "mate_threat": 0,
        "material_win": 1,
        "tactical_capture": 2,
        "promotion_threat": 3,
        "center_break": 4,
    }[type_]
