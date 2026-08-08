from __future__ import annotations

import re
from typing import Any, Iterable, Literal

import chess
from pydantic import BaseModel, ConfigDict, Field

from .models import CandidateLine, EngineResult, MoveResult, MoveReview, VariationMove
from .strategic_plans import StrategicPlanFact
from .threat_analysis import ThreatFact


CHESS_FACT_PACKAGE_VERSION = "1.0"


class FactPosition(BaseModel):
    fen: str
    move_number: int
    side_to_move: Literal["white", "black"]
    source: Literal["python-chess"] = "python-chess"


class FactEvaluation(BaseModel):
    perspective: Literal["white"] = "white"
    evaluation_cp: int | None = None
    evaluation_pawns: float | None = None
    mate: int | None = None
    source: Literal["stockfish"] = "stockfish"


class FactActualMove(BaseModel):
    san: str
    uci: str
    legal: bool
    evaluation_before: int | None = None
    evaluation_after: int | None = None
    loss: int | None = None
    classification: str
    source: Literal["python-chess+stockfish"] = "python-chess+stockfish"


class FactBestMove(BaseModel):
    san: str
    uci: str
    source: Literal["stockfish+python-chess"] = "stockfish+python-chess"


class FactCandidateRoute(BaseModel):
    route_id: str
    moves_san: list[str] = Field(default_factory=list)
    moves_uci: list[str] = Field(default_factory=list)
    evaluation: int | None = None
    mate: int | None = None
    verified: bool
    error: str | None = None
    resulting_fen: str | None = None
    source: Literal["stockfish+python-chess"] = "stockfish+python-chess"


class FactEvent(BaseModel):
    event_id: str
    type: str
    description: str
    evidence: list[str] = Field(default_factory=list)
    source: Literal["python-chess"] = "python-chess"


class ChessFactPackage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal["1.0"] = CHESS_FACT_PACKAGE_VERSION
    position: FactPosition
    evaluation: FactEvaluation
    actual_move: FactActualMove | None = None
    best_move: FactBestMove | None = None
    candidate_routes: list[FactCandidateRoute] = Field(default_factory=list)
    events: list[FactEvent] = Field(default_factory=list)
    threats: list[ThreatFact] = Field(default_factory=list)
    plans: list[StrategicPlanFact] = Field(default_factory=list)

    def prompt_payload(self) -> dict[str, Any]:
        """Return the only payload allowed to cross the DeepSeek boundary.

        The server retains FEN for verification, but the model does not receive it.
        Invalid routes remain auditable in the server package and are removed here.
        """
        payload = self.model_dump()
        payload["position"].pop("fen", None)
        verified_routes = []
        for route in payload["candidate_routes"]:
            if not route["verified"]:
                continue
            route.pop("resulting_fen", None)
            route.pop("error", None)
            verified_routes.append(route)
        payload["candidate_routes"] = verified_routes
        verified_route_ids = self.verified_route_ids
        payload["threats"] = [
            threat
            for threat in payload["threats"]
            if threat["scope"] in {"current_direct_threat", "prepared_threat"}
            and threat["evidence_route_ids"]
            and set(threat["evidence_route_ids"]) <= verified_route_ids
        ]
        payload["plans"] = [
            plan
            for plan in payload["plans"]
            if len(set(plan["evidence_route_ids"])) >= 2
            and set(plan["evidence_route_ids"]) <= verified_route_ids
        ]
        return payload

    def protocol_manifest(self) -> dict[str, Any]:
        """Compact proof that a richer prompt was built through this protocol.

        Professional analysis already has a stricter reference directory for
        pieces and plies, so repeating every SAN/UCI here would waste tokens.
        """
        return {
            "version": self.version,
            "position": {
                "move_number": self.position.move_number,
                "side_to_move": self.position.side_to_move,
                "source": self.position.source,
            },
            "evaluation": self.evaluation.model_dump(),
            "actual_move": (
                {
                    "legal": self.actual_move.legal,
                    "classification": self.actual_move.classification,
                    "source": self.actual_move.source,
                }
                if self.actual_move else None
            ),
            "best_move": (
                {"source": self.best_move.source}
                if self.best_move else None
            ),
            "candidate_routes": [
                {
                    "route_id": route.route_id,
                    "verified": route.verified,
                    "source": route.source,
                }
                for route in self.candidate_routes
                if route.verified
            ],
            "events": [
                {
                    "event_id": event.event_id,
                    "type": event.type,
                    "source": event.source,
                }
                for event in self.events
            ],
            "threats": [
                {
                    "threat_id": threat.threat_id,
                    "type": threat.type,
                    "scope": threat.scope,
                    "evidence_route_ids": threat.evidence_route_ids,
                    "confidence": threat.confidence,
                    "source": threat.source,
                }
                for threat in self.verified_threats
            ],
            "plans": [
                {
                    "plan_id": plan.plan_id,
                    "type": plan.type,
                    "side": plan.side,
                    "goal": plan.goal,
                    "supporting_moves": plan.supporting_moves,
                    "evidence_route_ids": plan.evidence_route_ids,
                    "structural_evidence": plan.structural_evidence,
                    "confidence": plan.confidence,
                    "source": plan.source,
                }
                for plan in self.verified_plans
            ],
        }

    @property
    def verified_route_ids(self) -> set[str]:
        return {route.route_id for route in self.candidate_routes if route.verified}

    @property
    def event_ids(self) -> set[str]:
        return {event.event_id for event in self.events}

    @property
    def threat_ids(self) -> set[str]:
        return {threat.threat_id for threat in self.verified_threats}

    @property
    def verified_threats(self) -> list[ThreatFact]:
        route_ids = self.verified_route_ids
        return [
            threat
            for threat in self.threats
            if threat.scope in {"current_direct_threat", "prepared_threat"}
            and threat.evidence_route_ids
            and set(threat.evidence_route_ids) <= route_ids
        ]

    @property
    def plan_ids(self) -> set[str]:
        return {plan.plan_id for plan in self.verified_plans}

    @property
    def verified_plans(self) -> list[StrategicPlanFact]:
        route_ids = self.verified_route_ids
        return [
            plan
            for plan in self.plans
            if len(set(plan.evidence_route_ids)) >= 2
            and set(plan.evidence_route_ids) <= route_ids
        ]

    @property
    def allowed_moves(self) -> set[str]:
        values: set[str] = set()
        if self.actual_move:
            values.update((self.actual_move.san, self.actual_move.uci))
        if self.best_move:
            values.update((self.best_move.san, self.best_move.uci))
        for route in self.candidate_routes:
            if route.verified:
                values.update(route.moves_san)
                values.update(route.moves_uci)
        return values


class RouteExplanation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    route_id: str
    explanation: str = Field(min_length=1, max_length=500)


class EventExplanation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str
    explanation: str = Field(min_length=1, max_length=500)


class ThreatExplanation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    threat_id: str
    explanation: str = Field(min_length=1, max_length=500)


class PlanExplanation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_id: str
    explanation: str = Field(min_length=1, max_length=500)


class FactExplanationDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    information_insufficient: bool = False
    summary: str = Field(default="", max_length=800)
    actual_move_explanation: str = Field(default="", max_length=500)
    best_move_explanation: str = Field(default="", max_length=500)
    route_explanations: list[RouteExplanation] = Field(default_factory=list, max_length=3)
    event_explanations: list[EventExplanation] = Field(default_factory=list, max_length=8)
    threat_explanations: list[ThreatExplanation] = Field(default_factory=list, max_length=5)
    plan_explanations: list[PlanExplanation] = Field(default_factory=list, max_length=8)


class RouteVerificationError(ValueError):
    pass


def normalized_evaluation(
    centipawn: int | None,
    mate: int | None,
) -> FactEvaluation:
    return FactEvaluation(
        evaluation_cp=centipawn,
        evaluation_pawns=None if centipawn is None else round(centipawn / 100, 2),
        mate=mate,
    )


def verify_route(
    *,
    route_id: str,
    start_fen: str,
    moves: Iterable[Any],
    evaluation: int | None,
    mate: int | None,
    expected_resulting_fen: str | None = None,
) -> FactCandidateRoute:
    board = chess.Board(start_fen)
    moves_san: list[str] = []
    moves_uci: list[str] = []
    try:
        route_moves = list(moves)
        if not route_moves:
            raise RouteVerificationError("empty_route")
        for ply, item in enumerate(route_moves, start=1):
            uci = str(getattr(item, "uci", "") or "")
            try:
                move = chess.Move.from_uci(uci)
            except ValueError as exc:
                raise RouteVerificationError(f"invalid_uci_at_ply_{ply}") from exc
            if move not in board.legal_moves:
                raise RouteVerificationError(f"illegal_move_at_ply_{ply}")
            san = board.san(move)
            supplied_san = str(getattr(item, "san", "") or "")
            if supplied_san and supplied_san.replace("0", "O") != san.replace("0", "O"):
                raise RouteVerificationError(f"san_mismatch_at_ply_{ply}")
            supplied_from = str(getattr(item, "from_square", "") or "")
            supplied_to = str(getattr(item, "to_square", "") or "")
            if supplied_from and supplied_from != chess.square_name(move.from_square):
                raise RouteVerificationError(f"from_square_mismatch_at_ply_{ply}")
            if supplied_to and supplied_to != chess.square_name(move.to_square):
                raise RouteVerificationError(f"to_square_mismatch_at_ply_{ply}")
            moves_san.append(san)
            moves_uci.append(uci)
            board.push(move)

        resulting_fen = board.fen()
        if expected_resulting_fen:
            expected = chess.Board(expected_resulting_fen).fen()
            if resulting_fen != expected:
                raise RouteVerificationError("resulting_fen_mismatch")
        return FactCandidateRoute(
            route_id=route_id,
            moves_san=moves_san,
            moves_uci=moves_uci,
            evaluation=evaluation,
            mate=mate,
            verified=True,
            resulting_fen=resulting_fen,
        )
    except (RouteVerificationError, ValueError) as exc:
        return FactCandidateRoute(
            route_id=route_id,
            evaluation=evaluation,
            mate=mate,
            verified=False,
            error=str(exc) or "route_validation_failed",
        )


def _move_result_items(board: chess.Board, result: MoveResult) -> list[VariationMove]:
    if result.line:
        return list(result.line)
    current = board.copy(stack=False)
    items: list[VariationMove] = []
    values = [result.move]
    try:
        first = chess.Move.from_uci(result.move)
    except ValueError as exc:
        raise RouteVerificationError("invalid_first_move_uci") from exc
    if first not in current.legal_moves:
        raise RouteVerificationError("illegal_first_move")
    current.push(first)
    for san in result.pv:
        try:
            parsed = current.parse_san(san)
        except ValueError as exc:
            raise RouteVerificationError(f"invalid_pv_san_at_ply_{len(values) + 1}") from exc
        values.append(parsed.uci())
        current.push(parsed)

    current = board.copy(stack=False)
    for ply, uci in enumerate(values, start=1):
        parsed = chess.Move.from_uci(uci)
        if parsed not in current.legal_moves:
            raise RouteVerificationError(f"illegal_move_at_ply_{ply}")
        san = current.san(parsed)
        items.append(VariationMove(
            ply=ply,
            move_number=current.fullmove_number,
            side="white" if current.turn == chess.WHITE else "black",
            san=san,
            uci=uci,
            from_square=chess.square_name(parsed.from_square),
            to_square=chess.square_name(parsed.to_square),
            piece=_piece_id(current.piece_at(parsed.from_square)),
            capture=current.is_capture(parsed),
            captured_piece=_captured_piece_id(current, parsed),
            check=current.gives_check(parsed),
            checkmate=_is_checkmate_after(current, parsed),
            castling=current.is_castling(parsed),
            promotion=chess.piece_name(parsed.promotion) if parsed.promotion else None,
        ))
        current.push(parsed)
    return items


def build_engine_fact_package(fen: str, result: EngineResult) -> ChessFactPackage:
    board = chess.Board(fen)
    routes: list[FactCandidateRoute] = []
    for index, candidate in enumerate(result.top_moves, start=1):
        rank = candidate.rank or index
        try:
            items = _move_result_items(board, candidate)
        except RouteVerificationError as exc:
            routes.append(FactCandidateRoute(
                route_id=f"pv_{rank}",
                evaluation=candidate.centipawn,
                mate=candidate.mate_in,
                verified=False,
                error=str(exc),
            ))
            continue
        routes.append(verify_route(
            route_id=f"pv_{rank}",
            start_fen=fen,
            moves=items,
            evaluation=candidate.centipawn,
            mate=candidate.mate_in,
            expected_resulting_fen=candidate.resulting_fen,
        ))
    verified = [route for route in routes if route.verified and route.moves_uci]
    best = verified[0] if verified else None
    events = _events_from_routes(routes)
    return ChessFactPackage(
        position=FactPosition(
            fen=board.fen(),
            move_number=board.fullmove_number,
            side_to_move="white" if board.turn == chess.WHITE else "black",
        ),
        evaluation=normalized_evaluation(result.centipawn, result.mate_in),
        best_move=(
            FactBestMove(san=best.moves_san[0], uci=best.moves_uci[0])
            if best else None
        ),
        candidate_routes=routes,
        events=events,
    )


def build_move_fact_package(move: MoveReview) -> ChessFactPackage:
    board = chess.Board(move.before_fen)
    played = chess.Move.from_uci(move.played_move.uci)
    legal = played in board.legal_moves and board.san(played) == move.played_move.san
    routes = [
        verify_route(
            route_id=line.id or f"pv_{line.rank}",
            start_fen=move.before_fen,
            moves=line.moves,
            evaluation=line.centipawn,
            mate=line.mate_in,
            expected_resulting_fen=line.resulting_fen,
        )
        for line in move.candidate_lines
    ]
    events = _events_from_played_move(move)
    events.extend(_events_from_routes(routes))
    best_route = next((route for route in routes if route.verified and route.moves_uci), None)
    best = move.best_move
    if best is None and best_route:
        best_fact = FactBestMove(san=best_route.moves_san[0], uci=best_route.moves_uci[0])
    elif best is not None:
        best_fact = FactBestMove(san=best.san, uci=best.uci)
    else:
        best_fact = None
    return ChessFactPackage(
        position=FactPosition(
            fen=board.fen(),
            move_number=board.fullmove_number,
            side_to_move="white" if board.turn == chess.WHITE else "black",
        ),
        evaluation=normalized_evaluation(move.before.centipawn, move.before.mate_in),
        actual_move=FactActualMove(
            san=move.played_move.san,
            uci=move.played_move.uci,
            legal=legal,
            evaluation_before=move.before.centipawn,
            evaluation_after=move.after.centipawn,
            loss=move.centipawn_loss,
            classification=move.quality_key,
        ),
        best_move=best_fact,
        candidate_routes=routes,
        events=_deduplicate_events(events),
    )


def validate_fact_explanation(
    draft: FactExplanationDraft,
    package: ChessFactPackage,
) -> list[str]:
    errors: list[str] = []
    route_ids = [item.route_id for item in draft.route_explanations]
    invalid_routes = sorted(set(route_ids) - package.verified_route_ids)
    if invalid_routes:
        errors.append("出现不存在或未验证的route_id：" + "、".join(invalid_routes))
    if len(route_ids) != len(set(route_ids)):
        errors.append("route_id重复")

    event_ids = [item.event_id for item in draft.event_explanations]
    invalid_events = sorted(set(event_ids) - package.event_ids)
    if invalid_events:
        errors.append("出现不存在的event_id：" + "、".join(invalid_events))
    if len(event_ids) != len(set(event_ids)):
        errors.append("event_id重复")

    threat_ids = [item.threat_id for item in draft.threat_explanations]
    invalid_threats = sorted(set(threat_ids) - package.threat_ids)
    if invalid_threats:
        errors.append("出现不存在的threat_id：" + "、".join(invalid_threats))
    if len(threat_ids) != len(set(threat_ids)):
        errors.append("threat_id重复")

    plan_ids = [item.plan_id for item in draft.plan_explanations]
    invalid_plans = sorted(set(plan_ids) - package.plan_ids)
    if invalid_plans:
        errors.append("出现不存在的plan_id：" + "、".join(invalid_plans))
    if len(plan_ids) != len(set(plan_ids)):
        errors.append("plan_id重复")

    prose = "\n".join([
        draft.summary,
        draft.actual_move_explanation,
        draft.best_move_explanation,
        *[item.explanation for item in draft.route_explanations],
        *[item.explanation for item in draft.event_explanations],
        *[item.explanation for item in draft.threat_explanations],
        *[item.explanation for item in draft.plan_explanations],
    ])
    uci = sorted(set(re.findall(
        r"(?<![A-Za-z0-9])(?:[a-h][1-8]){2}[qrbn]?(?![A-Za-z0-9])",
        prose,
        re.IGNORECASE,
    )))
    san = sorted(set(re.findall(
        r"(?<![A-Za-z0-9])(?:O-O(?:-O)?|[KQRBN][a-h1-8]?x?[a-h][1-8](?:=[QRBN])?|"
        r"[a-h]x[a-h][1-8](?:=[QRBN])?|[a-h][1-8](?:=[QRBN])?)[+#]?(?![A-Za-z0-9])",
        prose,
    )))
    if uci or san:
        errors.append("DeepSeek解释不得返回具体棋步；棋步必须由后端按route_id插入")

    malformed = sorted(set(re.findall(
        r"(?<![A-Za-z0-9])([A-Za-z][0-9])(?![A-Za-z0-9])",
        prose,
    )))
    malformed = [value for value in malformed if not re.fullmatch(r"[a-h][1-8]", value, re.I)]
    if malformed:
        errors.append("出现棋盘范围外的格子：" + "、".join(malformed))

    event_types = {event.type for event in package.events}
    threat_types = {threat.type for threat in package.threats}
    positive = _remove_negated_events(prose)
    capture_support = {"tactical_capture", "material_win"}.intersection(threat_types)
    if (
        re.search(r"吃子|吃掉|捕获|拿掉", positive)
        and "capture" not in event_types
        and not capture_support
    ):
        errors.append("描述了事实包中不存在的吃子")
    if (
        re.search(r"将杀|绝杀", positive)
        and "checkmate" not in event_types
        and "mate_threat" not in threat_types
    ):
        errors.append("描述了事实包中不存在的将杀")
    if "将军" in positive and not {"check", "checkmate"}.intersection(event_types):
        errors.append("描述了事实包中不存在的将军")
    if (
        re.search(r"升变|变后", positive)
        and "promotion" not in event_types
        and "promotion_threat" not in threat_types
    ):
        errors.append("描述了事实包中不存在的升变")
    return errors


def render_fact_explanation(draft: FactExplanationDraft) -> str:
    if draft.information_insufficient:
        return ""
    parts = [
        draft.summary,
        draft.actual_move_explanation,
        draft.best_move_explanation,
        *[item.explanation for item in draft.route_explanations],
        *[item.explanation for item in draft.event_explanations],
        *[item.explanation for item in draft.threat_explanations],
        *[item.explanation for item in draft.plan_explanations],
    ]
    return "\n\n".join(item.strip() for item in parts if item and item.strip())


def safe_fact_explanation(package: ChessFactPackage) -> str:
    evaluation = package.evaluation
    if evaluation.mate is not None:
        side = "白方" if evaluation.mate > 0 else "黑方"
        assessment = f"Stockfish确认{side}存在将杀结果。"
    elif evaluation.evaluation_pawns is None:
        assessment = "Stockfish已经完成计算，但当前没有可可靠展示的数值评价。"
    else:
        value = evaluation.evaluation_pawns
        if abs(value) < 0.3:
            assessment = "Stockfish评价显示局面接近均衡。"
        elif value > 0:
            assessment = "Stockfish评价显示白方目前稍占优势。"
        else:
            assessment = "Stockfish评价显示黑方目前稍占优势。"
    route_text = (
        "系统只展示已经由棋规逐步验证的候选路线。"
        if package.verified_route_ids
        else "当前没有通过完整棋规验证的候选路线。"
    )
    return f"{assessment}{route_text}"


def _events_from_played_move(move: MoveReview) -> list[FactEvent]:
    facts = move.played_move
    active = [
        ("capture", facts.capture),
        ("check", facts.check),
        ("checkmate", facts.checkmate),
        ("castling", facts.castling),
        ("promotion", bool(facts.promotion)),
    ]
    return [
        FactEvent(
            event_id=f"event:played:{move.index}:{event_type}",
            type=event_type,
            description=f"实战走法包含已验证的{event_type}事件",
            evidence=[facts.uci, facts.san],
        )
        for event_type, enabled in active
        if enabled
    ]


def _events_from_routes(routes: list[FactCandidateRoute]) -> list[FactEvent]:
    events: list[FactEvent] = []
    for route in routes:
        if not route.verified or not route.resulting_fen:
            continue
        board = None
        # Events are intentionally limited to notation-proven rule events in Phase 1.
        for ply, san in enumerate(route.moves_san, start=1):
            types = []
            if "x" in san:
                types.append("capture")
            if "#" in san:
                types.append("checkmate")
            elif "+" in san:
                types.append("check")
            if "=" in san:
                types.append("promotion")
            if san.startswith("O-O"):
                types.append("castling")
            for event_type in types:
                events.append(FactEvent(
                    event_id=f"event:{route.route_id}:{ply}:{event_type}",
                    type=event_type,
                    description=f"{route.route_id}第{ply}个半回合包含已验证的{event_type}事件",
                    evidence=[route.route_id, route.moves_uci[ply - 1], san],
                ))
    return _deduplicate_events(events)


def _deduplicate_events(events: list[FactEvent]) -> list[FactEvent]:
    result: list[FactEvent] = []
    seen: set[str] = set()
    for event in events:
        if event.event_id not in seen:
            seen.add(event.event_id)
            result.append(event)
    return result


def _piece_id(piece: chess.Piece | None) -> str:
    if piece is None:
        return "unknown_piece"
    return f"{'white' if piece.color == chess.WHITE else 'black'}_{chess.piece_name(piece.piece_type)}"


def _captured_piece_id(board: chess.Board, move: chess.Move) -> str | None:
    if not board.is_capture(move):
        return None
    square = move.to_square
    if board.is_en_passant(move):
        square += -8 if board.turn == chess.WHITE else 8
    return _piece_id(board.piece_at(square))


def _is_checkmate_after(board: chess.Board, move: chess.Move) -> bool:
    copy = board.copy(stack=False)
    copy.push(move)
    return copy.is_checkmate()


def _remove_negated_events(text: str) -> str:
    terms = r"(?:吃子|吃掉|捕获|拿掉|将军|将杀|绝杀|升变|变后)"
    return re.sub(
        rf"(?:不是|没有|并未|并没有|未)(?:形成|完成|发生|造成)?{terms}(?:、{terms})*",
        "",
        text,
    )
