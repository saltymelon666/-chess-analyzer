from __future__ import annotations

import json
import math
from pathlib import Path
import time
from typing import Any, Literal

import chess
import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .position_factor_ranker import FactorFamily
from .position_importance_ranker import FAMILIES


DEFAULT_DATASET = Path(__file__).resolve().parents[1] / "docs" / "research" / "phase7i-book-ground-truth-dataset.json"
DEFAULT_LABELS = Path(__file__).resolve().parents[1] / "docs" / "research" / "phase7j-book-theme-labels.json"
DEFAULT_RANKING = Path(__file__).resolve().parents[1] / "docs" / "research" / "phase7j-importance-ranking-results.json"

ReasonCode = Literal[
    "same_phase",
    "similar_material",
    "similar_pawn_structure",
    "similar_king_safety",
    "same_program_theme",
    "useful_human_priority",
]


THEME_QUESTIONS: dict[FactorFamily, str] = {
    "forcing_tactics": "先检查当前事实包中是否存在立即合法的将军、吃子、双攻或被迫回应。",
    "king_attack_and_safety": "检查当前王区受攻格、进攻子和多条强制路线是否共同构成真实王安全压力。",
    "pawn_structure_and_space": "检查当前弱兵、通路兵、兵突破和空间限制中，哪一项会长期约束双方计划。",
    "piece_activity_and_coordination": "检查当前最差棋子、开放线和子力协同中，哪一项真正影响候选路线质量。",
    "conversion_and_compensation": "检查交换、简化或转入残局后，当前评价和剩余资源是否支持优势兑现或补偿。",
}


class PerspectiveQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    position_id: str
    fen: str
    position_phase: Literal["opening", "middlegame", "endgame"]
    factor_scores: dict[FactorFamily, float] = Field(default_factory=dict)
    current_threat_types: list[str] = Field(default_factory=list)
    prepared_threat_types: list[str] = Field(default_factory=list)
    verified_plan_types: list[str] = Field(default_factory=list)


class BookPerspectiveCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    source_id: str
    source_label: str
    comment_for_selector: str = Field(min_length=1, max_length=500)
    theme_hints: list[FactorFamily] = Field(default_factory=list)
    similarity: float = Field(ge=0, le=1)
    similarity_reasons: list[ReasonCode] = Field(default_factory=list)


class BookPerspectiveRetrieval(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: PerspectiveQuery
    recalled: list[BookPerspectiveCandidate] = Field(default_factory=list, max_length=20)
    filtered: list[BookPerspectiveCandidate] = Field(default_factory=list, max_length=5)
    boundary: str = (
        "候选棋书原评只在独立选择步骤中帮助选择观察角度；来源局面的棋子、格子、着法、"
        "评价和结论不得进入当前局面的最终分析。"
    )


class PerspectiveSelectionDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    selected_case_ids: list[str] = Field(default_factory=list, max_length=3)
    theme_priority: list[FactorFamily] = Field(default_factory=list, max_length=3)
    reason_codes: dict[str, list[ReasonCode]] = Field(default_factory=dict)
    confidence: Literal["high", "medium", "low", "unknown"]


class BookPerspectiveGuidance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    theme_priority: list[FactorFamily] = Field(default_factory=list, max_length=3)
    coaching_questions: list[str] = Field(default_factory=list, max_length=3)
    selected_case_ids: list[str] = Field(default_factory=list, max_length=3)
    confidence: Literal["high", "medium", "low", "unknown"]
    forbidden_claims: list[str] = Field(default_factory=list, min_length=5)
    boundary: str = (
        "该包只调整解释顺序，不提供当前局面的事实、着法、评价、计划或主动权结论。"
    )

    def prompt_payload(self) -> dict[str, object]:
        """Payload intentionally omits source prose, FENs, moves and evaluations."""
        return {
            "role": "human_attention_order_only",
            "themePriority": self.theme_priority,
            "coachingQuestions": self.coaching_questions,
            "confidence": self.confidence,
            "boundary": self.boundary,
            "forbiddenClaims": self.forbidden_claims,
        }


class _IndexedCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    source_id: str
    source_label: str
    fen: str
    comment: str
    themes: list[FactorFamily]
    scores: dict[FactorFamily, float]


class BookPerspectiveIndex:
    """In-memory research index over exact-source book comments and program signals."""

    def __init__(self, cases: list[_IndexedCase]) -> None:
        self.cases = cases

    @classmethod
    def load(
        cls,
        dataset_path: Path = DEFAULT_DATASET,
        labels_path: Path = DEFAULT_LABELS,
        ranking_path: Path = DEFAULT_RANKING,
    ) -> "BookPerspectiveIndex":
        dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
        labels = json.loads(labels_path.read_text(encoding="utf-8"))
        ranking = json.loads(ranking_path.read_text(encoding="utf-8"))
        cases_by_id = {item["position_id"]: item for item in dataset["cases"]}
        ranking_by_id = {item["positionId"]: item for item in ranking["records"]}
        indexed: list[_IndexedCase] = []
        for item in labels["records"]:
            label = item["label"]
            themes = label["acceptable_themes"]
            if not themes:
                continue
            source = cases_by_id[item["positionId"]]
            ranked = ranking_by_id[item["positionId"]]
            indexed.append(_IndexedCase(
                case_id=item["positionId"],
                source_id=item["sourceId"],
                source_label=f"{source['author']}, {source['source_title']}",
                fen=source["fen"],
                comment=source["reference_explanation"],
                themes=themes,
                scores=ranked["scores"],
            ))
        return cls(indexed)

    def retrieve(
        self,
        query: PerspectiveQuery,
        *,
        exclude_source_ids: set[str] | None = None,
        exclude_case_ids: set[str] | None = None,
    ) -> BookPerspectiveRetrieval:
        excluded_sources = exclude_source_ids or set()
        excluded_cases = exclude_case_ids or set()
        query_board = chess.Board(query.fen)
        ranked: list[BookPerspectiveCandidate] = []
        for case in self.cases:
            if case.source_id in excluded_sources or case.case_id in excluded_cases:
                continue
            candidate_board = chess.Board(case.fen)
            if _canonical_state(query_board) == _canonical_state(candidate_board):
                continue
            similarity, reasons = _similarity(query, query_board, case, candidate_board)
            ranked.append(BookPerspectiveCandidate(
                case_id=case.case_id,
                source_id=case.source_id,
                source_label=case.source_label,
                comment_for_selector=_bounded_comment(case.comment),
                theme_hints=case.themes,
                similarity=round(similarity, 4),
                similarity_reasons=reasons,
            ))
        ranked.sort(key=lambda item: (-item.similarity, item.source_id, item.case_id))
        recalled = ranked[:20]
        filtered: list[BookPerspectiveCandidate] = []
        source_counts: dict[str, int] = {}
        query_top = set(_top_families(query.factor_scores, 3))
        for candidate in recalled:
            if candidate.similarity < 0.45:
                continue
            if query_top and not query_top.intersection(candidate.theme_hints):
                continue
            if source_counts.get(candidate.source_id, 0) >= 2:
                continue
            filtered.append(candidate)
            source_counts[candidate.source_id] = source_counts.get(candidate.source_id, 0) + 1
            if len(filtered) == 5:
                break
        return BookPerspectiveRetrieval(query=query, recalled=recalled, filtered=filtered)


class DeepSeekPerspectiveSelector:
    """Separate stateless call: source prose can influence only enum-based attention order."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        timeout_seconds: float,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_seconds = max(30.0, timeout_seconds)

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    async def select(self, retrieval: BookPerspectiveRetrieval) -> BookPerspectiveGuidance:
        if not retrieval.filtered:
            return _empty_guidance()
        if not self.configured:
            raise RuntimeError("DeepSeek is not configured")
        prompt = _selection_prompt(retrieval)
        content = await self._chat(prompt)
        try:
            return parse_selection(content, retrieval)
        except ValueError as first_error:
            correction = (
                f"{prompt}\n\n上一次输出未通过结构校验：{first_error}。"
                "请重新返回完整JSON。theme_priority中的每一项都必须至少由一个"
                "selected_case_ids对应案例的themeHints支持；不要返回解释文字。"
            )
            corrected = await self._chat(correction)
            return parse_selection(corrected, retrieval)

    async def _chat(self, prompt: str) -> str:
        started = time.perf_counter()
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(
                f"{self.base_url}/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": _selector_system_prompt()},
                        {"role": "user", "content": prompt},
                    ],
                    "max_tokens": 500,
                    "temperature": 0.0,
                    "thinking": {"type": "disabled"},
                    "response_format": {"type": "json_object"},
                },
            )
            response.raise_for_status()
            data: dict[str, Any] = response.json()
        content = data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
        if not content:
            raise RuntimeError("DeepSeek perspective selector returned empty content")
        _ = started  # retained for future offline usage diagnostics
        return content


def parse_selection(content: str, retrieval: BookPerspectiveRetrieval) -> BookPerspectiveGuidance:
    try:
        payload = json.loads(_strip_fence(content))
        draft = PerspectiveSelectionDraft.model_validate(payload)
    except (json.JSONDecodeError, ValidationError) as exc:
        raise ValueError(f"invalid perspective selection: {exc}") from exc
    allowed_cases = {case.case_id: case for case in retrieval.filtered}
    selected = list(dict.fromkeys(draft.selected_case_ids))
    if any(case_id not in allowed_cases for case_id in selected):
        raise ValueError("selector returned a case outside the filtered candidate set")
    if set(draft.reason_codes) - set(selected):
        raise ValueError("selector returned reason codes for an unselected case")
    allowed_themes = {
        theme
        for case_id in selected
        for theme in allowed_cases[case_id].theme_hints
    }
    themes = list(dict.fromkeys(draft.theme_priority))
    if any(theme not in allowed_themes for theme in themes):
        raise ValueError("selector returned a theme unsupported by selected book cases")
    if selected and not themes:
        raise ValueError("selected book cases require at least one supported theme")
    return BookPerspectiveGuidance(
        theme_priority=themes,
        coaching_questions=[THEME_QUESTIONS[theme] for theme in themes],
        selected_case_ids=selected,
        confidence=draft.confidence,
        forbidden_claims=[
            "不得复制来源案例的棋子、格子或着法",
            "不得复制来源案例的评价、胜负或物质结论",
            "不得把来源案例的变化事件升级为当前威胁",
            "不得创建当前程序没有验证的战略计划",
            "不得因为案例相似就声称某方拥有主动权",
            "不得把棋书案例ID作为当前事实证据引用",
        ],
    )


def _selection_prompt(retrieval: BookPerspectiveRetrieval) -> str:
    query = retrieval.query
    candidates = [
        {
            "caseId": case.case_id,
            "source": case.source_label,
            "similarity": case.similarity,
            "similarityReasons": case.similarity_reasons,
            "themeHints": case.theme_hints,
            "sourceCommentOnly": case.comment_for_selector,
        }
        for case in retrieval.filtered
    ]
    payload = {
        "currentProgramSignals": {
            "positionPhase": query.position_phase,
            "factorScores": query.factor_scores,
            "currentThreatTypes": query.current_threat_types,
            "preparedThreatTypes": query.prepared_threat_types,
            "verifiedPlanTypes": query.verified_plan_types,
        },
        "candidateSourceCases": candidates,
        "allowedThemes": list(FAMILIES),
        "allowedReasonCodes": [
            "same_phase", "similar_material", "similar_pawn_structure",
            "similar_king_safety", "same_program_theme", "useful_human_priority",
        ],
        "outputSchema": {
            "selected_case_ids": ["最多3个候选caseId"],
            "theme_priority": ["最多3个allowedThemes"],
            "reason_codes": {"caseId": ["allowedReasonCodes"]},
            "confidence": "high|medium|low|unknown",
        },
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _selector_system_prompt() -> str:
    return (
        "你只负责从候选棋书案例中选择对当前分析顺序有帮助的观察角度。"
        "候选原评描述的是来源局面，不是当前局面。"
        "只能返回指定JSON枚举字段，不得输出解释文字、棋子、格子、着法、评价、胜负或计划。"
        "selected_case_ids必须来自候选；theme_priority必须由所选案例themeHints支持。"
        "证据不足时返回空数组和unknown。"
    )


def _empty_guidance() -> BookPerspectiveGuidance:
    return BookPerspectiveGuidance(
        confidence="unknown",
        forbidden_claims=[
            "不得复制来源案例的棋子、格子或着法",
            "不得复制来源案例的评价、胜负或物质结论",
            "不得把来源案例的变化事件升级为当前威胁",
            "不得创建当前程序没有验证的战略计划",
            "不得因为案例相似就声称某方拥有主动权",
        ],
    )


def _canonical_state(board: chess.Board) -> str:
    return " ".join(board.fen().split()[:4])


def _bounded_comment(comment: str) -> str:
    compact = " ".join(comment.split())
    return compact[:500] or "No usable source comment."


def _phase(board: chess.Board) -> str:
    non_pawn = sum(
        len(board.pieces(piece_type, color))
        for color in chess.COLORS
        for piece_type in (chess.KNIGHT, chess.BISHOP, chess.ROOK, chess.QUEEN)
    )
    queens = len(board.pieces(chess.QUEEN, chess.WHITE)) + len(board.pieces(chess.QUEEN, chess.BLACK))
    if non_pawn <= 4 or (queens == 0 and non_pawn <= 6):
        return "endgame"
    if non_pawn >= 12:
        return "opening"
    return "middlegame"


def _similarity(
    query: PerspectiveQuery,
    query_board: chess.Board,
    case: _IndexedCase,
    candidate_board: chess.Board,
) -> tuple[float, list[ReasonCode]]:
    factor = _cosine(query.factor_scores, case.scores)
    material = _material_similarity(query_board, candidate_board)
    pawns = (
        _jaccard(_pawn_files(query_board, chess.WHITE), _pawn_files(candidate_board, chess.WHITE))
        + _jaccard(_pawn_files(query_board, chess.BLACK), _pawn_files(candidate_board, chess.BLACK))
    ) / 2
    kings = (
        int(_king_zone(query_board, chess.WHITE) == _king_zone(candidate_board, chess.WHITE))
        + int(_king_zone(query_board, chess.BLACK) == _king_zone(candidate_board, chess.BLACK))
    ) / 2
    same_phase = float(query.position_phase == _phase(candidate_board))
    score = 0.55 * factor + 0.15 * material + 0.12 * pawns + 0.08 * kings + 0.10 * same_phase
    reasons: list[ReasonCode] = []
    if same_phase:
        reasons.append("same_phase")
    if material >= 0.9:
        reasons.append("similar_material")
    if pawns >= 0.65:
        reasons.append("similar_pawn_structure")
    if kings == 1:
        reasons.append("similar_king_safety")
    if set(_top_families(query.factor_scores, 3)).intersection(case.themes):
        reasons.append("same_program_theme")
    return max(0.0, min(1.0, score)), reasons


def _cosine(left: dict[FactorFamily, float], right: dict[FactorFamily, float]) -> float:
    left_values = [float(left.get(family, 0.0)) for family in FAMILIES]
    right_values = [float(right.get(family, 0.0)) for family in FAMILIES]
    numerator = sum(a * b for a, b in zip(left_values, right_values))
    denominator = math.sqrt(sum(a * a for a in left_values) * sum(b * b for b in right_values))
    return numerator / denominator if denominator else 0.0


def _material_similarity(left: chess.Board, right: chess.Board) -> float:
    counts_left = [
        len(left.pieces(piece_type, color))
        for color in chess.COLORS for piece_type in range(chess.PAWN, chess.KING + 1)
    ]
    counts_right = [
        len(right.pieces(piece_type, color))
        for color in chess.COLORS for piece_type in range(chess.PAWN, chess.KING + 1)
    ]
    return sum(
        1 - abs(a - b) / max(a, b, 1)
        for a, b in zip(counts_left, counts_right)
    ) / len(counts_left)


def _pawn_files(board: chess.Board, color: chess.Color) -> frozenset[int]:
    return frozenset(chess.square_file(square) for square in board.pieces(chess.PAWN, color))


def _king_zone(board: chess.Board, color: chess.Color) -> str:
    square = board.king(color)
    if square is None:
        return "missing"
    file_index = chess.square_file(square)
    return "queenside" if file_index <= 2 else "center" if file_index <= 4 else "kingside"


def _jaccard(left: frozenset[int], right: frozenset[int]) -> float:
    union = left | right
    return len(left & right) / len(union) if union else 1.0


def _top_families(scores: dict[FactorFamily, float], limit: int) -> list[FactorFamily]:
    return [
        family for family, score in sorted(scores.items(), key=lambda item: (-item[1], item[0]))
        if score > 0
    ][:limit]


def _strip_fence(content: str) -> str:
    stripped = content.strip()
    if stripped.startswith("```"):
        stripped = stripped.split("\n", 1)[1] if "\n" in stripped else stripped[3:]
        if stripped.endswith("```"):
            stripped = stripped[:-3]
    return stripped.strip()
