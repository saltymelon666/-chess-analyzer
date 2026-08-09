from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any, Literal, Sequence

import chess
import chess.pgn
from pydantic import BaseModel, ConfigDict, Field, model_validator


DEFAULT_OPENING_CATALOG = Path(__file__).resolve().parent / "data" / "opening-path-catalog.json"
DEFAULT_OPENING_EXPLANATIONS = (
    Path(__file__).resolve().parent / "data" / "opening-explanations.json"
)


class OpeningLookupRequest(BaseModel):
    pgn: str | None = Field(default=None, min_length=1, max_length=100_000)
    fen: str | None = Field(default=None, min_length=15, max_length=120)

    @model_validator(mode="after")
    def require_position_source(self) -> "OpeningLookupRequest":
        if not self.pgn and not self.fen:
            raise ValueError("pgn或fen至少提供一个")
        return self


class OpeningContinuation(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    san: str
    uci: str
    opening_name: str = Field(alias="openingName")
    eco: str


class OpeningMatch(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    opening_id: str = Field(alias="openingId")
    eco: str
    name: str
    family_name: str = Field(alias="familyName")
    variation_path: list[str] = Field(alias="variationPath", default_factory=list)
    pgn: str
    san_moves: list[str] = Field(alias="sanMoves")
    uci_moves: list[str] = Field(alias="uciMoves")
    matched_ply: int = Field(alias="matchedPly", ge=1)


class OpeningHumanExplanation(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    text: str
    matched_ply: int = Field(alias="matchedPly", ge=1)
    page_title: str = Field(alias="pageTitle")
    page_url: str = Field(alias="pageUrl")
    revision_id: int = Field(alias="revisionId")
    license: str
    attribution: str


class OpeningLookupResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    matched: bool
    match_type: Literal["exact_path", "path_prefix", "position_transposition", "exact_fen", "none"] = Field(
        alias="matchType"
    )
    query_ply: int = Field(alias="queryPly", ge=0)
    current_fen: str = Field(alias="currentFen")
    opening: OpeningMatch | None = None
    human_explanation: OpeningHumanExplanation | None = Field(
        default=None, alias="humanExplanation"
    )
    next_branches: list[OpeningContinuation] = Field(alias="nextBranches", default_factory=list)
    source: str = "Lichess chess-openings (CC0-1.0)"
    authority_boundary: str = Field(
        alias="authorityBoundary",
        default=(
            "开局目录只提供经过验证的名称、ECO和走法路径；"
            "当前局面评价、走法优劣和战略结论仍由Stockfish与程序事实层决定。"
        ),
    )


class OpeningPresentation(BaseModel):
    """Conservative, user-facing opening context backed by a verified path."""

    model_config = ConfigDict(populate_by_name=True)

    opening_id: str = Field(alias="openingId")
    eco: str
    name: str
    family_name: str = Field(alias="familyName")
    family_name_zh: str = Field(alias="familyNameZh")
    variation_path: list[str] = Field(alias="variationPath", default_factory=list)
    variation_name_zh: str | None = Field(alias="variationNameZh", default=None)
    display_name: str = Field(alias="displayName")
    match_type: Literal["exact_path", "path_prefix", "position_transposition", "exact_fen"] = Field(
        alias="matchType"
    )
    matched_ply: int = Field(alias="matchedPly", ge=1)
    query_ply: int = Field(alias="queryPly", ge=1)
    confidence: Literal["exact", "high"]
    description: str
    white_plan: str = Field(alias="whitePlan")
    black_plan: str = Field(alias="blackPlan")
    tactical_themes: list[str] = Field(alias="tacticalThemes", default_factory=list)
    source: str = "Lichess chess-openings (CC0-1.0)"

    def prompt_payload(self) -> dict[str, Any]:
        return {
            "identityAuthority": "program_confirmed",
            "openingId": self.opening_id,
            "eco": self.eco,
            "name": self.name,
            "familyName": self.family_name,
            "variationPath": self.variation_path,
            "matchType": self.match_type,
            "matchedPly": self.matched_ply,
            "queryPly": self.query_ply,
            "background": {
                "description": self.description,
                "whitePlan": self.white_plan,
                "blackPlan": self.black_plan,
                "tacticalThemes": self.tactical_themes,
            },
            "policy": (
                "名称和变例由程序确定，不得重新命名。背景只说明该开局的常见思路；"
                "只有当前事实包另有支持时，才能把常见思路写成当前局面的事实或计划。"
            ),
        }


_OPENING_FAMILY_PROFILES: dict[str, dict[str, Any]] = {
    "Italian Game": {
        "zh": "意大利开局",
        "description": "这是一个经典开放型布局，双方通过快速发展子力争夺中心。",
        "white": "白方常通过准备并推进d4打开中心，让双象和王翼子力获得更活跃的空间。",
        "black": "黑方通常完成王翼发展，并寻找d5反击来直接挑战白方中心。",
        "themes": ["中心突破d4", "黑方反击d5", "e5兵与f7弱点", "王的安全"],
    },
    "Ruy Lopez": {
        "zh": "西班牙开局",
        "description": "这是一个经典开放型布局，双方围绕中心控制和子力协调展开长期较量。",
        "white": "白方通常保持对中心的压力，完成发展后再选择中心或王翼行动。",
        "black": "黑方通常巩固e5兵，并通过协调后翼子力和中心反击争取平衡。",
        "themes": ["e5兵压力", "中心张力", "子力重组", "王的安全"],
    },
    "Sicilian Defense": {
        "zh": "西西里防御",
        "description": "这是一个不对称的半开放型布局，双方往往在不同区域争取主动。",
        "white": "白方通常利用空间和发展速度在中心或王翼组织行动。",
        "black": "黑方通常利用c线和后翼兵形制造反击，并持续挑战中心。",
        "themes": ["不对称兵形", "开放c线", "中心突破", "异侧进攻"],
    },
    "French Defense": {
        "zh": "法兰西防御",
        "description": "这是一个结构鲜明的半开放型布局，中心兵链决定双方主要行动方向。",
        "white": "白方通常利用空间优势，在中心和王翼寻找突破。",
        "black": "黑方通常攻击白方兵链根部，并用c5或f6反击中心。",
        "themes": ["兵链攻防", "c5反击", "f6反击", "后翼空间"],
    },
    "Caro-Kann Defense": {
        "zh": "卡罗-康防御",
        "description": "这是一个以稳固兵形和顺利发展为目标的半开放型布局。",
        "white": "白方通常利用空间优势保持中心压力，并争取更主动的子力部署。",
        "black": "黑方通常先完成稳健发展，再用c5或e5挑战白方中心。",
        "themes": ["稳固兵形", "中心挑战", "轻子协调", "残局结构"],
    },
    "Queen's Gambit": {
        "zh": "后翼弃兵",
        "description": "这是一个经典封闭型布局，核心是用翼兵交换争取中心控制。",
        "white": "白方通常争取建立强中心，并利用开放线路发展后翼子力。",
        "black": "黑方通常选择稳固中心或及时归还兵，以完成发展并寻找反击。",
        "themes": ["中心控制", "c线活动", "少数兵进攻", "孤后兵结构"],
    },
    "English Opening": {
        "zh": "英国式开局",
        "description": "这是一个灵活的侧翼开局，常通过后翼控制间接影响中心。",
        "white": "白方通常保持兵形弹性，逐步加强对中心和后翼关键格的控制。",
        "black": "黑方可以直接占据中心，也可以采用对称结构后寻找及时突破。",
        "themes": ["后翼空间", "中心反击", "长对角线", "兵形转换"],
    },
    "King's Indian Defense": {
        "zh": "王印度防御",
        "description": "这是一个动态封闭型布局，黑方允许白方占据中心后再发动反击。",
        "white": "白方通常利用中心空间在后翼推进，并限制黑方反击速度。",
        "black": "黑方通常攻击白方中心，并在王翼寻找主动行动。",
        "themes": ["中心兵链", "王翼进攻", "后翼扩张", "中心反击"],
    },
}


_VARIATION_NAMES_ZH = {
    "Giuoco Piano": "吉奥科钢琴变化",
    "Giuoco Pianissimo": "吉奥科皮亚尼西莫变化",
    "Classical Variation": "古典变化",
    "Center Attack": "中心进攻变化",
    "Two Knights Defense": "双马防御",
    "Evans Gambit": "伊文斯弃兵",
    "Main Line": "主线",
}


def _position_key(board: chess.Board) -> str:
    ep = chess.square_name(board.ep_square) if board.ep_square is not None else "-"
    return " ".join((
        board.board_fen(),
        "w" if board.turn == chess.WHITE else "b",
        board.castling_xfen() or "-",
        ep,
    ))


def _parse_pgn(pgn: str) -> tuple[list[str], chess.Board, bool]:
    game = chess.pgn.read_game(io.StringIO(pgn))
    if game is None:
        raise ValueError("无法解析PGN")
    if game.errors:
        raise ValueError(f"PGN包含非法走法：{game.errors[0]}")
    board = game.board()
    starts_from_standard_position = board.fen() == chess.STARTING_FEN
    moves: list[str] = []
    for move in game.mainline_moves():
        if move not in board.legal_moves:
            raise ValueError(f"PGN包含非法走法：{move.uci()}")
        moves.append(move.uci())
        board.push(move)
    if not moves:
        raise ValueError("PGN中没有可识别的主线走法")
    return moves, board, starts_from_standard_position


class OpeningKnowledgeRepository:
    """Read-only deterministic opening lookup; never evaluates the position."""

    def __init__(
        self,
        catalog_path: Path | str = DEFAULT_OPENING_CATALOG,
        explanation_path: Path | str | None = DEFAULT_OPENING_EXPLANATIONS,
    ) -> None:
        self.catalog_path = Path(catalog_path)
        self.explanation_path = Path(explanation_path) if explanation_path else None
        self._entries: list[dict[str, Any]] | None = None
        self._path_index: dict[tuple[str, ...], list[dict[str, Any]]] = {}
        self._position_index: dict[str, list[dict[str, Any]]] = {}
        self._explanation_index: dict[tuple[str, ...], dict[str, Any]] = {}

    def lookup(self, *, pgn: str | None = None, fen: str | None = None) -> OpeningLookupResponse:
        self._ensure_loaded()
        query_moves: list[str] = []
        allow_path_match = False
        if pgn:
            query_moves, board, allow_path_match = _parse_pgn(pgn)
        elif fen:
            try:
                board = chess.Board(fen)
            except ValueError as exc:
                raise ValueError(f"无效FEN：{exc}") from exc
        else:
            raise ValueError("pgn或fen至少提供一个")

        if fen and pgn:
            try:
                supplied = chess.Board(fen)
            except ValueError as exc:
                raise ValueError(f"无效FEN：{exc}") from exc
            if _position_key(supplied) != _position_key(board):
                raise ValueError("PGN终点局面与提供的FEN不一致")

        return self._lookup_resolved(
            query_moves=query_moves,
            board=board,
            allow_path_match=allow_path_match,
        )

    def lookup_moves(
        self,
        uci_moves: Sequence[str],
        *,
        initial_fen: str = chess.STARTING_FEN,
    ) -> OpeningLookupResponse:
        """Look up one already parsed main-line prefix without involving an LLM."""
        self._ensure_loaded()
        try:
            board = chess.Board(initial_fen)
        except ValueError as exc:
            raise ValueError(f"无效初始FEN：{exc}") from exc
        starts_from_standard_position = board.fen() == chess.STARTING_FEN
        query_moves: list[str] = []
        for raw_uci in uci_moves:
            try:
                move = chess.Move.from_uci(raw_uci)
            except ValueError as exc:
                raise ValueError(f"走法序列包含无效UCI：{raw_uci}") from exc
            if move not in board.legal_moves:
                raise ValueError(f"走法序列包含非法走法：{raw_uci}")
            query_moves.append(move.uci())
            board.push(move)
        if not query_moves:
            raise ValueError("走法序列为空")
        return self._lookup_resolved(
            query_moves=query_moves,
            board=board,
            allow_path_match=starts_from_standard_position,
        )

    def presentation_for_moves(
        self,
        uci_moves: Sequence[str],
        *,
        initial_fen: str = chess.STARTING_FEN,
    ) -> OpeningPresentation | None:
        return self.presentation_from_lookup(
            self.lookup_moves(uci_moves, initial_fen=initial_fen)
        )

    @staticmethod
    def presentation_from_lookup(result: OpeningLookupResponse) -> OpeningPresentation | None:
        opening = result.opening
        if not result.matched or opening is None or result.match_type == "none":
            return None
        if opening.matched_ply < 4:
            return None
        stale_ply = result.query_ply - opening.matched_ply
        coverage = opening.matched_ply / max(result.query_ply, 1)
        if result.match_type == "path_prefix" and (stale_ply > 2 or coverage < 0.75):
            return None

        profile = _OPENING_FAMILY_PROFILES.get(opening.family_name)
        if profile is None:
            return None
        translated_variations = [
            _VARIATION_NAMES_ZH.get(name, name)
            for name in opening.variation_path
        ]
        variation_zh = " · ".join(translated_variations) or None
        display_name = str(profile["zh"])
        if variation_zh:
            display_name += f" · {variation_zh}"
        confidence = "exact" if result.match_type in {"exact_path", "position_transposition", "exact_fen"} else "high"
        return OpeningPresentation(
            openingId=opening.opening_id,
            eco=opening.eco,
            name=opening.name,
            familyName=opening.family_name,
            familyNameZh=profile["zh"],
            variationPath=opening.variation_path,
            variationNameZh=variation_zh,
            displayName=display_name,
            matchType=result.match_type,
            matchedPly=opening.matched_ply,
            queryPly=result.query_ply,
            confidence=confidence,
            description=profile["description"],
            whitePlan=profile["white"],
            blackPlan=profile["black"],
            tacticalThemes=list(profile["themes"]),
            source=result.source,
        )

    def _lookup_resolved(
        self,
        *,
        query_moves: list[str],
        board: chess.Board,
        allow_path_match: bool,
    ) -> OpeningLookupResponse:
        position_matches = self._position_index.get(_position_key(board), [])
        exact_path_matches = (
            self._path_index.get(tuple(query_moves), [])
            if query_moves and allow_path_match
            else []
        )
        entry: dict[str, Any] | None = None
        match_type: Literal["exact_path", "path_prefix", "position_transposition", "exact_fen", "none"] = "none"

        if exact_path_matches:
            entry = self._select(exact_path_matches)
            match_type = "exact_path"
        elif position_matches:
            entry = self._select(position_matches)
            match_type = "position_transposition" if query_moves else "exact_fen"
        elif query_moves and allow_path_match:
            for length in range(len(query_moves) - 1, 0, -1):
                matches = self._path_index.get(tuple(query_moves[:length]), [])
                if matches:
                    entry = self._select(matches)
                    match_type = "path_prefix"
                    break

        explanation_moves = query_moves or (entry["uciMoves"] if entry else [])
        return OpeningLookupResponse(
            matched=entry is not None,
            matchType=match_type,
            queryPly=len(query_moves),
            currentFen=board.fen(),
            opening=self._to_match(entry) if entry else None,
            humanExplanation=self._find_explanation(explanation_moves),
            nextBranches=self._next_branches(query_moves),
        )

    def _ensure_loaded(self) -> None:
        if self._entries is not None:
            return
        if not self.catalog_path.exists():
            raise RuntimeError(f"开局目录不存在：{self.catalog_path}")
        payload = json.loads(self.catalog_path.read_text(encoding="utf-8"))
        entries = list(payload.get("openings", []))
        path_index: dict[tuple[str, ...], list[dict[str, Any]]] = {}
        position_index: dict[str, list[dict[str, Any]]] = {}
        for entry in entries:
            path_index.setdefault(tuple(entry["uciMoves"]), []).append(entry)
            position_index.setdefault(entry["canonicalPositionKey"], []).append(entry)
        self._entries = entries
        self._path_index = path_index
        self._position_index = position_index
        if self.explanation_path and self.explanation_path.exists():
            explanation_payload = json.loads(
                self.explanation_path.read_text(encoding="utf-8")
            )
            self._explanation_index = {
                tuple(item["uciMoves"]): item
                for item in explanation_payload.get("explanations", [])
            }

    @staticmethod
    def _select(entries: list[dict[str, Any]]) -> dict[str, Any]:
        return sorted(entries, key=lambda item: (item["plyCount"], item["eco"], item["name"]))[-1]

    @staticmethod
    def _to_match(entry: dict[str, Any]) -> OpeningMatch:
        return OpeningMatch(
            openingId=entry["openingId"],
            eco=entry["eco"],
            name=entry["name"],
            familyName=entry["familyName"],
            variationPath=entry["variationPath"],
            pgn=entry["pgn"],
            sanMoves=entry["sanMoves"],
            uciMoves=entry["uciMoves"],
            matchedPly=entry["plyCount"],
        )

    def _next_branches(self, query_moves: list[str], *, limit: int = 12) -> list[OpeningContinuation]:
        if not query_moves or self._entries is None:
            return []
        prefix = tuple(query_moves)
        branches: dict[str, OpeningContinuation] = {}
        for entry in self._entries:
            moves = tuple(entry["uciMoves"])
            if len(moves) <= len(prefix) or moves[:len(prefix)] != prefix:
                continue
            next_uci = moves[len(prefix)]
            branches.setdefault(next_uci, OpeningContinuation(
                san=entry["sanMoves"][len(prefix)],
                uci=next_uci,
                openingName=entry["name"],
                eco=entry["eco"],
            ))
        return sorted(branches.values(), key=lambda item: (item.san, item.opening_name))[:limit]

    def _find_explanation(
        self, moves: list[str]
    ) -> OpeningHumanExplanation | None:
        for length in range(len(moves), 0, -1):
            item = self._explanation_index.get(tuple(moves[:length]))
            if item:
                return OpeningHumanExplanation(
                    text=item["text"],
                    matchedPly=length,
                    pageTitle=item["pageTitle"],
                    pageUrl=item["pageUrl"],
                    revisionId=item["revisionId"],
                    license=item["license"],
                    attribution=item["attribution"],
                )
        return None
