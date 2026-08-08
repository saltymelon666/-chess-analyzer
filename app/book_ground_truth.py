from __future__ import annotations

import json
from pathlib import Path
import sqlite3

import chess
from pydantic import BaseModel, ConfigDict, Field

from .book_retrieval import DEFAULT_BOOK_CORPUS


BOOK_GROUND_TRUTH_VERSION = "1.0"
DEFAULT_BOOK_GROUND_TRUTH_DATASET = (
    Path(__file__).resolve().parents[1]
    / "docs"
    / "research"
    / "phase7i-book-ground-truth-dataset.json"
)


class BookGroundTruthCase(BaseModel):
    """Book-authored reference attached only to its exact source position."""

    model_config = ConfigDict(extra="forbid")

    position_id: str
    source_id: str
    source_title: str
    author: str
    source_url: str
    rights_boundary: str
    fen: str
    locator: str
    title: str
    annotated_move_san: str | None = None
    annotated_move_uci: str | None = None
    annotated_move_legal: bool | None = None
    reference_explanation: str = Field(min_length=1)
    comment_scope: str
    extraction_status: str
    authority_scope: str = (
        "棋书原评只适用于这个完全相同源局面（完整状态也必须一致）；"
        "合法性、当前Stockfish评价和候选路线仍由程序验证。"
    )


class BookGroundTruthPackage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str = BOOK_GROUND_TRUTH_VERSION
    canonical_position_key: str
    match_type: str = "exact_position_only"
    cases: list[BookGroundTruthCase] = Field(default_factory=list)

    @property
    def matched(self) -> bool:
        return bool(self.cases)


def canonical_position_key(fen: str) -> str:
    """Ignore clocks but preserve every state component that changes legal play."""
    board = chess.Board(fen)
    ep = chess.square_name(board.ep_square) if board.ep_square is not None else "-"
    return " ".join((
        board.board_fen(),
        "w" if board.turn == chess.WHITE else "b",
        board.castling_xfen() or "-",
        ep,
    ))


class BookGroundTruthRepository:
    def __init__(
        self,
        database_path: Path | str = DEFAULT_BOOK_CORPUS,
        dataset_path: Path | str = DEFAULT_BOOK_GROUND_TRUTH_DATASET,
    ) -> None:
        self.database_path = Path(database_path)
        self.dataset_path = Path(dataset_path)
        self._dataset_cases: list[BookGroundTruthCase] | None = None
        self._dataset_index: dict[str, list[BookGroundTruthCase]] | None = None

    def lookup_exact(self, fen: str) -> BookGroundTruthPackage:
        query_board = chess.Board(fen)
        key = canonical_position_key(fen)
        rows = []
        if self.database_path.exists():
            with sqlite3.connect(self.database_path) as connection:
                rows = connection.execute("""
                SELECT p.position_id, p.source_id, s.title, s.author, s.source_url,
                       s.rights_boundary, p.fen, p.locator, p.title,
                       p.move_san, p.move_uci, p.human_comment, p.comment_scope,
                       p.extraction_status
                FROM positions p JOIN sources s ON s.source_id=p.source_id
                WHERE p.board_fen=?
                ORDER BY p.source_id, p.position_id
                """, (query_board.board_fen(),)).fetchall()
        cases = []
        for row in rows:
            if canonical_position_key(row[6]) != key:
                continue
            annotated_move_legal: bool | None = None
            if row[10]:
                try:
                    annotated_move_legal = chess.Move.from_uci(row[10]) in query_board.legal_moves
                except ValueError:
                    annotated_move_legal = False
            cases.append(BookGroundTruthCase(
                position_id=row[0], source_id=row[1], source_title=row[2], author=row[3],
                source_url=row[4], rights_boundary=row[5], fen=row[6], locator=row[7],
                title=row[8], annotated_move_san=row[9], annotated_move_uci=row[10],
                annotated_move_legal=annotated_move_legal,
                reference_explanation=row[11], comment_scope=row[12], extraction_status=row[13],
            ))
        cases.extend(self._load_dataset_index().get(key, []))
        unique = {
            (case.source_id, case.position_id): case
            for case in cases
        }
        return BookGroundTruthPackage(
            canonical_position_key=key,
            cases=sorted(unique.values(), key=lambda case: (case.source_id, case.position_id)),
        )

    def _load_dataset_cases(self) -> list[BookGroundTruthCase]:
        if self._dataset_cases is not None:
            return self._dataset_cases
        if not self.dataset_path.exists():
            self._dataset_cases = []
            return self._dataset_cases
        payload = json.loads(self.dataset_path.read_text(encoding="utf-8"))
        self._dataset_cases = [
            BookGroundTruthCase.model_validate(case)
            for case in payload.get("cases", [])
        ]
        return self._dataset_cases

    def _load_dataset_index(self) -> dict[str, list[BookGroundTruthCase]]:
        if self._dataset_index is not None:
            return self._dataset_index
        index: dict[str, list[BookGroundTruthCase]] = {}
        for case in self._load_dataset_cases():
            index.setdefault(canonical_position_key(case.fen), []).append(case)
        self._dataset_index = index
        return index
