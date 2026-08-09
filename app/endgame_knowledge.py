from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

import chess
from pydantic import BaseModel, ConfigDict, Field


DEFAULT_ENDGAME_DATASET = Path(__file__).resolve().parent / "data" / "endgame-knowledge.json"


class EndgameLookupRequest(BaseModel):
    fen: str = Field(min_length=15, max_length=120)


class EndgameKeyMove(BaseModel):
    uci: str
    san: str


class EndgameTablebaseResult(BaseModel):
    category: Literal["win", "loss", "draw", "cursed-win", "blessed-loss"]
    dtz: int | None = None
    dtm: int | None = None
    computed_white_outcome: Literal["white_win", "black_win", "draw"] = Field(
        alias="computedWhiteOutcome"
    )


class EndgameMatch(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    position_id: str = Field(alias="positionId")
    source_title: str = Field(alias="sourceTitle")
    source_author: str | None = Field(default=None, alias="sourceAuthor")
    source_year: int | None = Field(default=None, alias="sourceYear")
    source_page: int | None = Field(default=None, alias="sourcePage")
    source_locator: str | None = Field(default=None, alias="sourceLocator")
    source_url: str | None = Field(default=None, alias="sourceUrl")
    fen: str
    outcome: Literal["white_win", "black_win", "draw"]
    source_phrase: str = Field(alias="sourcePhrase")
    tablebase: EndgameTablebaseResult
    key_move: EndgameKeyMove = Field(alias="keyMove")
    book_move: EndgameKeyMove | None = Field(default=None, alias="bookMove")


class EndgameLookupResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    matched: bool
    match_type: Literal["exact_verified", "none"] = Field(alias="matchType")
    current_fen: str = Field(alias="currentFen")
    endgame: EndgameMatch | None = None
    authority_boundary: str = Field(
        alias="authorityBoundary",
        default=(
            "残局条目只匹配棋子摆放和行棋方完全相同的七子以内源局面；"
            "胜和结果与关键着来自Syzygy审计，未验证、冲突和相似局面不会返回。"
        ),
    )


def _position_key(board: chess.Board) -> str:
    ep = chess.square_name(board.ep_square) if board.ep_square is not None else "-"
    return " ".join((
        board.board_fen(),
        "w" if board.turn == chess.WHITE else "b",
        board.castling_xfen() or "-",
        ep,
    ))


class EndgameKnowledgeRepository:
    """Exact, read-only lookup over tablebase-verified book positions."""

    def __init__(self, dataset_path: Path | str = DEFAULT_ENDGAME_DATASET) -> None:
        self.dataset_path = Path(dataset_path)
        self._index: dict[str, dict[str, Any]] | None = None
        self._metadata: dict[str, Any] = {}

    def lookup(self, fen: str) -> EndgameLookupResponse:
        try:
            board = chess.Board(fen)
        except ValueError as exc:
            raise ValueError(f"无效FEN：{exc}") from exc
        self._ensure_loaded()
        entry = self._index.get(_position_key(board)) if self._index is not None else None
        if entry is None:
            return EndgameLookupResponse(
                matched=False, matchType="none", currentFen=board.fen()
            )
        source_fen = f"{entry['placement']} {entry['sideToMove']} - - 0 1"
        source = entry.get("source", {})
        return EndgameLookupResponse(
            matched=True,
            matchType="exact_verified",
            currentFen=board.fen(),
            endgame=EndgameMatch(
                positionId=entry["id"],
                sourceTitle=source.get("title", self._metadata.get("sourceTitle", "")),
                sourceAuthor=source.get("author"),
                sourceYear=source.get("year", self._metadata.get("sourceYear")),
                sourcePage=entry.get("sourcePage"),
                sourceLocator=source.get("locator"),
                sourceUrl=source.get("url"),
                fen=source_fen,
                outcome=entry["outcome"],
                sourcePhrase=entry["sourcePhrase"],
                tablebase=entry["tablebase"],
                keyMove=entry["keyMove"],
                bookMove=entry.get("bookMove"),
            ),
        )

    def _ensure_loaded(self) -> None:
        if self._index is not None:
            return
        if not self.dataset_path.exists():
            raise RuntimeError(f"残局知识数据不存在：{self.dataset_path}")
        payload = json.loads(self.dataset_path.read_text(encoding="utf-8"))
        self._metadata = {
            "sourceTitle": payload.get("sourceTitle", ""),
            "sourceYear": payload.get("sourceYear"),
        }
        index = {}
        for entry in payload.get("positions", []):
            board = chess.Board(f"{entry['placement']} {entry['sideToMove']} - - 0 1")
            key_move = chess.Move.from_uci(entry["keyMove"]["uci"])
            if not board.is_valid() or key_move not in board.legal_moves:
                raise RuntimeError(f"残局条目未通过合法性校验：{entry['id']}")
            if board.san(key_move) != entry["keyMove"]["san"]:
                raise RuntimeError(f"残局条目SAN不一致：{entry['id']}")
            index[_position_key(board)] = entry
        self._index = index
