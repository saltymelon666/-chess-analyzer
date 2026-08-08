from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import sqlite3
from typing import Iterable

import chess
from pydantic import BaseModel, ConfigDict, Field


DEFAULT_BOOK_CORPUS = Path(__file__).resolve().parents[1] / "work" / "research_books" / "phase7b-book-corpus.sqlite3"


class RetrievedBookCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    position_id: str
    source_id: str
    source_title: str
    author: str
    source_url: str
    fen: str
    locator: str
    title: str
    annotated_move_san: str | None = None
    annotated_move_uci: str | None = None
    original_comment: str
    comment_scope: str
    theme_hints: list[str] = Field(default_factory=list)
    similarity: float = Field(ge=0, le=1)
    similarity_reasons: list[str] = Field(default_factory=list)
    exact_board_match: bool = False
    extraction_status: str


class BookRetrievalPackage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query_fen: str
    requested_themes: list[str] = Field(default_factory=list)
    cases: list[RetrievedBookCase] = Field(default_factory=list)
    safety_boundary: str = (
        "棋书原评只提供相似局面的人工思路，不是当前局面的棋盘事实；"
        "棋子、走法、威胁和评价必须由当前事实包与Stockfish重新验证。"
    )


@dataclass(frozen=True)
class _Features:
    counts: tuple[int, ...]
    pawn_files_white: frozenset[int]
    pawn_files_black: frozenset[int]
    king_zone_white: str
    king_zone_black: str
    phase: str
    side_to_move: bool
    coarse_pieces: frozenset[str]


def _king_zone(board: chess.Board, color: chess.Color) -> str:
    square = board.king(color)
    if square is None:
        return "missing"
    file_index = chess.square_file(square)
    return "queenside" if file_index <= 2 else "center" if file_index <= 4 else "kingside"


def _phase(board: chess.Board) -> str:
    non_pawn = sum(
        len(board.pieces(piece_type, color))
        for color in chess.COLORS
        for piece_type in (chess.KNIGHT, chess.BISHOP, chess.ROOK, chess.QUEEN)
    )
    queens = len(board.pieces(chess.QUEEN, chess.WHITE)) + len(board.pieces(chess.QUEEN, chess.BLACK))
    if non_pawn <= 4 or queens == 0 and non_pawn <= 6:
        return "endgame"
    if non_pawn >= 12:
        return "opening"
    return "middlegame"


def _features(board: chess.Board) -> _Features:
    counts = tuple(
        len(board.pieces(piece_type, color))
        for color in (chess.WHITE, chess.BLACK)
        for piece_type in range(chess.PAWN, chess.KING + 1)
    )
    coarse = set()
    for square, piece in board.piece_map().items():
        rank_band = min(chess.square_rank(square) // 2, 3)
        file_band = min(chess.square_file(square) // 2, 3)
        coarse.add(f"{piece.symbol()}:{file_band}:{rank_band}")
    return _Features(
        counts=counts,
        pawn_files_white=frozenset(chess.square_file(square) for square in board.pieces(chess.PAWN, chess.WHITE)),
        pawn_files_black=frozenset(chess.square_file(square) for square in board.pieces(chess.PAWN, chess.BLACK)),
        king_zone_white=_king_zone(board, chess.WHITE),
        king_zone_black=_king_zone(board, chess.BLACK),
        phase=_phase(board),
        side_to_move=board.turn,
        coarse_pieces=frozenset(coarse),
    )


def _jaccard(left: frozenset, right: frozenset) -> float:
    union = left | right
    return len(left & right) / len(union) if union else 1.0


def _score(query: _Features, candidate: _Features, themes: set[str], candidate_themes: set[str]) -> tuple[float, list[str]]:
    count_score = sum(
        1 - abs(left - right) / max(left, right, 1)
        for left, right in zip(query.counts, candidate.counts)
    ) / len(query.counts)
    pawn_score = (
        _jaccard(query.pawn_files_white, candidate.pawn_files_white)
        + _jaccard(query.pawn_files_black, candidate.pawn_files_black)
    ) / 2
    king_score = (
        int(query.king_zone_white == candidate.king_zone_white)
        + int(query.king_zone_black == candidate.king_zone_black)
    ) / 2
    phase_score = float(query.phase == candidate.phase)
    side_score = float(query.side_to_move == candidate.side_to_move)
    coarse_score = _jaccard(query.coarse_pieces, candidate.coarse_pieces)
    base = (
        0.25 * count_score
        + 0.20 * pawn_score
        + 0.15 * king_score
        + 0.15 * phase_score
        + 0.05 * side_score
        + 0.20 * coarse_score
    )
    theme_overlap = _jaccard(frozenset(themes), frozenset(candidate_themes)) if themes else 0.0
    score = min(1.0, base * 0.9 + theme_overlap * 0.1)
    reasons = []
    if phase_score:
        reasons.append(f"同为{query.phase}")
    if king_score == 1:
        reasons.append("双方王区位置相近")
    if pawn_score >= 0.7:
        reasons.append("兵线分布相近")
    if count_score >= 0.9:
        reasons.append("子力构成相近")
    if theme_overlap > 0:
        reasons.append("程序主题有交集")
    return score, reasons


class BookCorpusRetriever:
    """Retrieve human book commentary without treating it as current-position truth."""

    def __init__(self, database_path: Path | str = DEFAULT_BOOK_CORPUS) -> None:
        self.database_path = Path(database_path)

    def retrieve(
        self,
        fen: str,
        *,
        theme_hints: Iterable[str] = (),
        limit: int = 3,
        exclude_position_ids: Iterable[str] = (),
        exclude_exact_board: bool = False,
    ) -> BookRetrievalPackage:
        query_board = chess.Board(fen)
        query = _features(query_board)
        requested = sorted(set(theme_hints))
        excluded = set(exclude_position_ids)
        with sqlite3.connect(self.database_path) as connection:
            rows = connection.execute("""
                SELECT p.position_id, p.source_id, s.title, s.author, s.source_url,
                       p.fen, p.board_fen, p.locator, p.title, p.move_san, p.move_uci,
                       p.human_comment, p.comment_scope, p.theme_hints_json, p.extraction_status
                FROM positions p JOIN sources s ON s.source_id = p.source_id
            """).fetchall()
        ranked: list[RetrievedBookCase] = []
        for row in rows:
            if row[0] in excluded:
                continue
            exact = query_board.board_fen() == row[6]
            if exclude_exact_board and exact:
                continue
            candidate_themes = set(json.loads(row[13]))
            similarity, reasons = _score(query, _features(chess.Board(row[5])), set(requested), candidate_themes)
            if exact:
                similarity = 1.0
                reasons.insert(0, "棋子摆放完全一致")
            ranked.append(RetrievedBookCase(
                position_id=row[0], source_id=row[1], source_title=row[2], author=row[3], source_url=row[4],
                fen=row[5], locator=row[7], title=row[8], annotated_move_san=row[9], annotated_move_uci=row[10],
                original_comment=row[11], comment_scope=row[12], theme_hints=sorted(candidate_themes),
                similarity=round(similarity, 4), similarity_reasons=reasons, exact_board_match=exact,
                extraction_status=row[14],
            ))
        ranked.sort(key=lambda item: (-item.similarity, item.source_id, item.position_id))
        return BookRetrievalPackage(
            query_fen=query_board.fen(),
            requested_themes=requested,
            cases=ranked[:max(0, min(limit, 5))],
        )


def book_cases_for_prompt(package: BookRetrievalPackage, *, excerpt_limit: int = 900) -> dict[str, object]:
    """Return a bounded payload; callers must retain the safety boundary."""
    return {
        "safetyBoundary": package.safety_boundary,
        "cases": [
            {
                "source": f"{case.author}, {case.source_title}, {case.locator}",
                "similarity": case.similarity,
                "similarityReasons": case.similarity_reasons,
                "annotatedMove": case.annotated_move_san,
                "originalComment": case.original_comment[:max(200, min(excerpt_limit, 1200))],
                "warning": "只能借鉴分析角度和计划逻辑，不得复制其中的棋子、走法或评价到当前局面。",
            }
            for case in package.cases
        ],
    }
