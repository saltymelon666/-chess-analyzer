from __future__ import annotations

from pathlib import Path
import json
import sqlite3
import re
from typing import Iterable, Literal

import chess
from pydantic import BaseModel, ConfigDict, Field


DEFAULT_UNIFIED_BOOK_DATABASE = Path(__file__).resolve().parent / "data" / "unified-book-knowledge.sqlite3"
UNIFIED_BOOK_CONTEXT_VERSION = "1.2"

THEME_SEARCH_QUERIES = {
    "forcing_tactics": '"combination" OR "forcing" OR "tactical"',
    "king_attack_and_safety": '"king attack" OR "king safety" OR "attack on the king"',
    "pawn_structure_and_space": '"passed pawn" OR "weak pawn" OR "pawn majority" OR "pawn chain"',
    "piece_activity_and_coordination": '"development" OR "mobility" OR "coordination"',
    "conversion_and_compensation": '"advantage" OR "simplification" OR "endgame"',
}
UNSUITABLE_RUNTIME_PRINCIPLE_SOURCES = {
    "Chess Generalship, Vol. I. Grand Reconnaissance",
    "Studies of chess",
}


class BookKnowledgeRecord(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    record_id: str = Field(alias="recordId")
    record_type: Literal["exact_position", "chapter_principle", "opening_path", "annotated_game"] = Field(alias="recordType")
    phase: Literal["opening", "middlegame", "endgame", "general"]
    source_title: str = Field(alias="sourceTitle")
    author: str
    source_url: str = Field(alias="sourceUrl")
    title: str
    locator: str
    text: str
    text_zh: str | None = Field(default=None, alias="textZh")
    authority_level: str = Field(alias="authorityLevel")
    authority_boundary: str = Field(alias="authorityBoundary")
    fen: str | None = None
    move_uci: str | None = Field(default=None, alias="moveUci")


class BookAnalysisExcerpt(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    relation: Literal["exact_current_position", "analogous_position", "principle_only"]
    source: str
    locator: str
    phase: Literal["opening", "middlegame", "endgame", "general"]
    excerpt: str = Field(min_length=1, max_length=700)
    use_for: str = Field(alias="useFor")
    authority_boundary: str = Field(alias="authorityBoundary")


class BookAnalysisContext(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    version: Literal["1.2"] = UNIFIED_BOOK_CONTEXT_VERSION
    requested_themes: list[str] = Field(alias="requestedThemes", default_factory=list)
    excerpts: list[BookAnalysisExcerpt] = Field(default_factory=list, max_length=3)
    instruction: str = (
        "棋书摘录只用于学习人类棋手选择重点、解释机制和组织语言的方式。只有标为完全相同局面的原评"
        "才与当前棋盘状态直接对应；原则摘录不提供当前局面事实。当前棋子、格子、走法、评价、威胁和"
        "计划必须继续服从python-chess、Stockfish和当前事实包。"
    )

    def prompt_payload(self) -> dict[str, object]:
        return {
            "role": "human_chess_book_reasoning_reference",
            "instruction": self.instruction,
            "requestedThemes": self.requested_themes,
            "excerpts": [item.model_dump(by_alias=True) for item in self.excerpts],
        }


class UnifiedBookKnowledgeRepository:
    """Read-only search over exact positions, opening paths, and book principles."""

    def __init__(self, database_path: Path | str = DEFAULT_UNIFIED_BOOK_DATABASE) -> None:
        self.database_path = Path(database_path)
        self._analogy_cache: dict[str, list[tuple[BookKnowledgeRecord, "_BoardProfile"]]] = {}

    def search(
        self,
        query: str,
        *,
        phase: str | None = None,
        record_type: str | None = None,
        limit: int = 10,
    ) -> list[BookKnowledgeRecord]:
        if not self.database_path.exists():
            return []
        filters = []
        values: list[object] = [query]
        if phase:
            filters.append("k.phase=?")
            values.append(phase)
        if record_type:
            filters.append("k.record_type=?")
            values.append(record_type)
        where = " AND " + " AND ".join(filters) if filters else ""
        values.append(max(1, min(limit, 50)))
        sql = f"""
            SELECT k.record_id, k.record_type, k.phase, s.title, s.author, s.source_url,
                   k.title, k.locator, k.text_en, k.text_zh, k.authority_level,
                   k.authority_boundary, k.fen, k.move_uci
            FROM knowledge_fts f
            JOIN knowledge_records k ON k.record_id=f.record_id
            JOIN sources s ON s.source_id=k.source_id
            WHERE knowledge_fts MATCH ? {where}
            ORDER BY bm25(knowledge_fts), k.record_id
            LIMIT ?
        """
        with sqlite3.connect(self.database_path) as connection:
            rows = connection.execute(sql, values).fetchall()
        return [self._record(row) for row in rows]

    def lookup_exact(self, fen: str) -> list[BookKnowledgeRecord]:
        if not self.database_path.exists():
            return []
        board = chess.Board(fen)
        key = " ".join(board.fen().split()[:4])
        with sqlite3.connect(self.database_path) as connection:
            rows = connection.execute("""
                SELECT k.record_id, k.record_type, k.phase, s.title, s.author, s.source_url,
                       k.title, k.locator, k.text_en, k.text_zh, k.authority_level,
                       k.authority_boundary, k.fen, k.move_uci
                FROM knowledge_records k JOIN sources s ON s.source_id=k.source_id
                WHERE k.position_key=? AND k.record_type='exact_position'
                ORDER BY k.record_id
            """, (key,)).fetchall()
        return [self._record(row) for row in rows]

    def get_game_pgn(self, record_id: str) -> str | None:
        """Return the book's annotated score, including its stated omissions."""
        if not self.database_path.exists():
            return None
        with sqlite3.connect(self.database_path) as connection:
            row = connection.execute(
                "SELECT metadata_json FROM knowledge_records WHERE record_id=? AND record_type='annotated_game'",
                (record_id,),
            ).fetchone()
        if row is None:
            return None
        value = json.loads(row[0]).get("pgn")
        return value if isinstance(value, str) else None

    def analysis_context(
        self,
        fen: str,
        *,
        theme_hints: Iterable[str] = (),
        played_move_uci: str | None = None,
        best_move_uci: str | None = None,
        limit: int = 3,
    ) -> BookAnalysisContext:
        """Build a bounded prompt context without transferring source-position facts."""
        themes = list(dict.fromkeys(theme for theme in theme_hints if theme in THEME_SEARCH_QUERIES))
        maximum = max(0, min(limit, 3))
        if maximum == 0 or not self.database_path.exists():
            return BookAnalysisContext(requestedThemes=themes)

        board = chess.Board(fen)
        phase = _position_phase(board)
        selected: list[BookAnalysisExcerpt] = []
        used_records: set[str] = set()
        used_sources: set[str] = set()

        exact_targets = list(dict.fromkeys(
            move for move in (best_move_uci, played_move_uci) if move
        ))
        exact_records = self.lookup_exact(fen)
        if exact_targets:
            exact_records = [record for record in exact_records if record.move_uci in exact_targets]
            exact_records.sort(key=lambda record: (
                exact_targets.index(record.move_uci),
                -_theme_match_score(record, themes),
                record.record_id,
            ))
        exact_limit = min(maximum, 2)
        for record in exact_records:
            if record.source_title in used_sources:
                continue
            excerpt = _usable_excerpt(record)
            if not excerpt:
                continue
            selected.append(BookAnalysisExcerpt(
                relation="exact_current_position",
                source=_source_label(record),
                locator=record.locator,
                phase=record.phase,
                excerpt=excerpt,
                useFor="参考棋书作者在完全相同局面中如何判断重点；具体走法与评价仍须由当前事实包复核。",
                authorityBoundary=record.authority_boundary,
            ))
            used_records.add(record.record_id)
            used_sources.add(record.source_title)
            if len(selected) >= exact_limit:
                break

        for record in self._analogous_records(board, themes, limit=1):
            if record.record_id in used_records or record.source_title in used_sources:
                continue
            excerpt = _usable_excerpt(record, analogous=True)
            if not excerpt:
                continue
            selected.append(BookAnalysisExcerpt(
                relation="analogous_position",
                source=_source_label(record),
                locator=record.locator,
                phase=record.phase,
                excerpt=excerpt,
                useFor="只借鉴相似结构中棋手如何发现矛盾、比较选择和解释后果；来源局面的具体内容不得迁移。",
                authorityBoundary=(
                    "这是结构相似的来源局面，不是当前局面。来源棋子、格子、着法、评价、胜负、"
                    "威胁和计划一律不得作为当前事实。"
                ),
            ))
            used_records.add(record.record_id)
            used_sources.add(record.source_title)
            if len(selected) >= maximum:
                return BookAnalysisContext(requestedThemes=themes, excerpts=selected)

        for theme in themes:
            query = THEME_SEARCH_QUERIES[theme]
            candidates = [
                *self.search(query, phase=phase, record_type="chapter_principle", limit=8),
                *self.search(query, phase="general", record_type="chapter_principle", limit=5),
            ]
            preferred = [item for item in candidates if item.source_title not in used_sources]
            fallback = [item for item in candidates if item.source_title in used_sources]
            for record in [*preferred, *fallback]:
                if record.record_id in used_records:
                    continue
                if record.source_title in UNSUITABLE_RUNTIME_PRINCIPLE_SOURCES:
                    continue
                opening = (record.text_zh or record.text)[:350].casefold()
                if not any(keyword in opening for keyword in THEME_TEXT_KEYWORDS[theme]):
                    continue
                excerpt = _usable_excerpt(record, principle=True)
                if excerpt:
                    selected.append(BookAnalysisExcerpt(
                        relation="principle_only",
                        source=_source_label(record),
                        locator=record.locator,
                        phase=record.phase,
                        excerpt=excerpt,
                        useFor="只借鉴判断顺序、因果解释和教练式表达，不得复制为当前局面的事实或结论。",
                        authorityBoundary=record.authority_boundary,
                    ))
                    used_records.add(record.record_id)
                    used_sources.add(record.source_title)
                    break
            if len(selected) >= maximum:
                break

        return BookAnalysisContext(requestedThemes=themes, excerpts=selected)

    def _analogous_records(
        self,
        board: chess.Board,
        themes: list[str],
        *,
        limit: int,
    ) -> list[BookKnowledgeRecord]:
        phase = _position_phase(board)
        cached = self._analogy_cache.get(phase)
        if cached is None:
            with sqlite3.connect(self.database_path) as connection:
                rows = connection.execute("""
                    SELECT k.record_id, k.record_type, k.phase, s.title, s.author, s.source_url,
                           k.title, k.locator, k.text_en, k.text_zh, k.authority_level,
                       k.authority_boundary, k.fen, k.move_uci
                    FROM knowledge_records k JOIN sources s ON s.source_id=k.source_id
                    WHERE k.record_type='exact_position' AND k.phase=? AND k.fen IS NOT NULL
                      AND k.source_id LIKE 'CLASSIC:%' AND length(k.text_en)>=80
                    ORDER BY k.record_id
                """, (phase,)).fetchall()
            cached = []
            for row in rows:
                record = self._record(row)
                if not _usable_excerpt(record, analogous=True):
                    continue
                try:
                    cached.append((record, _board_profile(chess.Board(record.fen))))
                except (ValueError, TypeError):
                    continue
            self._analogy_cache[phase] = cached

        query_profile = _board_profile(board)
        query_key = " ".join(board.fen().split()[:4])
        ranked: list[tuple[float, BookKnowledgeRecord]] = []
        for record, profile in cached:
            if record.fen and " ".join(chess.Board(record.fen).fen().split()[:4]) == query_key:
                continue
            score = _profile_similarity(query_profile, profile)
            text = (record.text_zh or record.text).casefold()
            if any(keyword in text for theme in themes for keyword in THEME_TEXT_KEYWORDS.get(theme, ())):
                score = min(1.0, score + 0.05)
            if score >= 0.62:
                ranked.append((score, record))
        ranked.sort(key=lambda item: (-item[0], item[1].record_id))

        result: list[BookKnowledgeRecord] = []
        sources: set[str] = set()
        for _, record in ranked:
            if record.source_title in sources:
                continue
            result.append(record)
            sources.add(record.source_title)
            if len(result) >= max(0, min(limit, 2)):
                break
        return result

    @staticmethod
    def _record(row: tuple) -> BookKnowledgeRecord:
        return BookKnowledgeRecord(
            recordId=row[0], recordType=row[1], phase=row[2], sourceTitle=row[3],
            author=row[4], sourceUrl=row[5], title=row[6], locator=row[7], text=row[8],
            textZh=row[9], authorityLevel=row[10], authorityBoundary=row[11], fen=row[12],
            moveUci=row[13],
        )


def _position_phase(board: chess.Board) -> Literal["opening", "middlegame", "endgame"]:
    non_pawn_material = sum(
        len(board.pieces(piece_type, color)) * value
        for color in (chess.WHITE, chess.BLACK)
        for piece_type, value in (
            (chess.KNIGHT, 3),
            (chess.BISHOP, 3),
            (chess.ROOK, 5),
            (chess.QUEEN, 9),
        )
    )
    queens = sum(len(board.pieces(chess.QUEEN, color)) for color in (chess.WHITE, chess.BLACK))
    if non_pawn_material <= 16 or (queens == 0 and non_pawn_material <= 24):
        return "endgame"
    if board.fullmove_number <= 12:
        return "opening"
    return "middlegame"


THEME_TEXT_KEYWORDS = {
    "forcing_tactics": ("decisive", "forced", "threat", "combination"),
    "king_attack_and_safety": ("king", "attack", "defence", "defense"),
    "pawn_structure_and_space": ("pawn", "centre", "center", "space"),
    "piece_activity_and_coordination": ("develop", "active", "mobility", "coordinate"),
    "conversion_and_compensation": ("advantage", "simplif", "endgame", "winning"),
}


class _BoardProfile(BaseModel):
    counts: tuple[int, ...]
    white_pawn_files: frozenset[int]
    black_pawn_files: frozenset[int]
    white_pawn_bands: frozenset[str]
    black_pawn_bands: frozenset[str]
    center_pawns: frozenset[str]
    white_king_zone: str
    black_king_zone: str
    side_to_move: bool
    coarse_pieces: frozenset[str]


def _board_profile(board: chess.Board) -> _BoardProfile:
    coarse = set()
    for square, piece in board.piece_map().items():
        coarse.add(
            f"{piece.symbol()}:{chess.square_file(square) // 2}:{chess.square_rank(square) // 2}"
        )
    return _BoardProfile(
        counts=tuple(
            len(board.pieces(piece_type, color))
            for color in (chess.WHITE, chess.BLACK)
            for piece_type in range(chess.PAWN, chess.KING + 1)
        ),
        white_pawn_files=frozenset(
            chess.square_file(square) for square in board.pieces(chess.PAWN, chess.WHITE)
        ),
        black_pawn_files=frozenset(
            chess.square_file(square) for square in board.pieces(chess.PAWN, chess.BLACK)
        ),
        white_pawn_bands=frozenset(
            f"{chess.square_file(square)}:{chess.square_rank(square) // 2}"
            for square in board.pieces(chess.PAWN, chess.WHITE)
        ),
        black_pawn_bands=frozenset(
            f"{chess.square_file(square)}:{chess.square_rank(square) // 2}"
            for square in board.pieces(chess.PAWN, chess.BLACK)
        ),
        center_pawns=frozenset(
            f"{board.piece_at(square).symbol()}:{chess.square_name(square)}"
            for square in chess.SQUARES
            if board.piece_type_at(square) == chess.PAWN
            and chess.square_file(square) in range(2, 6)
            and chess.square_rank(square) in range(2, 6)
        ),
        white_king_zone=_king_zone(board, chess.WHITE),
        black_king_zone=_king_zone(board, chess.BLACK),
        side_to_move=board.turn,
        coarse_pieces=frozenset(coarse),
    )


def _profile_similarity(left: _BoardProfile, right: _BoardProfile) -> float:
    count_score = sum(
        1 - abs(a - b) / max(a, b, 1)
        for a, b in zip(left.counts, right.counts)
    ) / len(left.counts)
    pawn_score = (
        _jaccard(left.white_pawn_files, right.white_pawn_files)
        + _jaccard(left.black_pawn_files, right.black_pawn_files)
    ) / 2
    pawn_band_score = (
        _jaccard(left.white_pawn_bands, right.white_pawn_bands)
        + _jaccard(left.black_pawn_bands, right.black_pawn_bands)
    ) / 2
    king_score = (
        int(left.white_king_zone == right.white_king_zone)
        + int(left.black_king_zone == right.black_king_zone)
    ) / 2
    return (
        0.20 * count_score
        + 0.10 * pawn_score
        + 0.20 * pawn_band_score
        + 0.15 * _jaccard(left.center_pawns, right.center_pawns)
        + 0.10 * king_score
        + 0.05 * float(left.side_to_move == right.side_to_move)
        + 0.20 * _jaccard(left.coarse_pieces, right.coarse_pieces)
    )


def _theme_match_score(record: BookKnowledgeRecord, themes: list[str]) -> int:
    text = (record.text_zh or record.text).casefold()
    return sum(
        1
        for theme in themes
        for keyword in THEME_TEXT_KEYWORDS.get(theme, ())
        if keyword in text
    )


def _jaccard(left: frozenset, right: frozenset) -> float:
    union = left | right
    return len(left & right) / len(union) if union else 1.0


def _king_zone(board: chess.Board, color: chess.Color) -> str:
    square = board.king(color)
    if square is None:
        return "missing"
    file_index = chess.square_file(square)
    return "queenside" if file_index <= 2 else "center" if file_index <= 4 else "kingside"


def _usable_excerpt(
    record: BookKnowledgeRecord,
    *,
    analogous: bool = False,
    principle: bool = False,
) -> str:
    text = record.text_zh or record.text
    compact = re.sub(r"\s+", " ", text).strip()
    if len(compact) < 60:
        return ""
    if analogous or principle:
        first_letter = next((character for character in compact if character.isalpha()), "")
        old_notation = re.findall(
            r"\b(?:Kt|K|Q|B|R|P)\.?\s*(?:to|takes|[-—×x])",
            compact,
            flags=re.IGNORECASE,
        )
        numbered_moves = re.findall(r"(?:^|\s)\d{1,2}\.\s", compact)
        if (
            not first_letter
            or (first_letter.isascii() and first_letter.islower())
            or compact.startswith("]")
            or len(old_notation) >= 2
            or len(numbered_moves) >= 2
        ):
            return ""
    if principle and any(
        marker in compact
        for marker in (
            "Strategic Front", "Column of Attack", "_=", "=_", "_Conclusion_",
        )
    ):
        return ""
    return compact[:700].rstrip()


def _source_label(record: BookKnowledgeRecord) -> str:
    return f"{record.author}，《{record.source_title}》"
