from __future__ import annotations

import hashlib
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .book_retrieval import BookRetrievalPackage, RetrievedBookCase


BOOK_CASE_TRANSFER_VERSION = "1.0"


THEME_NAMES_ZH = {
    "forcing_tactics": "强制战术",
    "king_attack_and_safety": "攻王与王安全",
    "pawn_structure_and_space": "兵形与空间",
    "piece_activity_and_coordination": "子力活动与协调",
    "conversion_and_compensation": "优势转换与补偿",
}
EVIDENCE_QUESTIONS = {
    "forcing_tactics": "当前局面是否存在经合法性验证的将军、吃子、双攻或强制回应？",
    "king_attack_and_safety": "当前王区有哪些具体受攻格、进攻子和经验证的强制路线？",
    "pawn_structure_and_space": "当前兵形中哪些弱兵、通路兵、兵突破或空间限制能够由棋盘直接确认？",
    "piece_activity_and_coordination": "当前哪枚棋子的活动、线路或协同真正影响候选路线？",
    "conversion_and_compensation": "交换或简化后，Stockfish评价和剩余物质是否仍支持这一转换？",
}


class TransferableBookCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    source: str
    locator: str
    similarity: float = Field(ge=0, le=1)
    reasoning_themes: list[str] = Field(default_factory=list)
    source_analysis_excerpt: str = Field(min_length=1, max_length=1200)
    source_move: str | None = None
    source_scope: Literal["analogous_source_case_only"] = "analogous_source_case_only"
    evidence_questions: list[str] = Field(default_factory=list)
    prohibited_transfers: list[str] = Field(default_factory=list, min_length=4)


class BookCaseTransferPackage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal["1.0"] = BOOK_CASE_TRANSFER_VERSION
    position_id: str
    cases: list[TransferableBookCase] = Field(default_factory=list, max_length=3)
    instruction: str = (
        "以下内容只提供人类分析的观察角度和思考顺序。当前局面的棋盘事实、走法、评价、威胁和计划"
        "只能来自当前事实包及Stockfish证据；无法重新验证时必须省略。"
    )

    def prompt_payload(self) -> dict[str, object]:
        return {
            "role": "analogous_human_reasoning_examples",
            "instruction": self.instruction,
            "cases": [
                {
                    "source": case.source,
                    "locator": case.locator,
                    "reasoningThemes": case.reasoning_themes,
                    "sourceAnalysis": case.source_analysis_excerpt,
                    "sourceMove": case.source_move,
                    "sourceScope": "仅属于来源棋书的相似局面，不是当前局面",
                    "questionsForCurrentPosition": case.evidence_questions,
                    "mustNotTransfer": case.prohibited_transfers,
                }
                for case in self.cases
            ],
        }


def build_book_case_transfer_package(
    retrieval: BookRetrievalPackage,
    *,
    excerpt_limit: int = 900,
) -> BookCaseTransferPackage:
    limit = max(200, min(excerpt_limit, 1200))
    cases = [
        _transfer_case(case, excerpt_limit=limit)
        for case in retrieval.cases[:3]
        if case.original_comment.strip()
    ]
    return BookCaseTransferPackage(
        position_id=f"position:{hashlib.sha256(retrieval.query_fen.encode()).hexdigest()[:16]}",
        cases=cases,
    )


def _transfer_case(case: RetrievedBookCase, *, excerpt_limit: int) -> TransferableBookCase:
    themes = [THEME_NAMES_ZH.get(theme, theme) for theme in case.theme_hints]
    questions = [
        EVIDENCE_QUESTIONS[theme]
        for theme in case.theme_hints
        if theme in EVIDENCE_QUESTIONS
    ]
    if not questions:
        questions = ["当前事实包中是否存在与该案例思考方式相对应的明确证据？"]
    return TransferableBookCase(
        case_id=case.position_id,
        source=f"{case.author}，《{case.source_title}》",
        locator=case.locator,
        similarity=case.similarity,
        reasoning_themes=themes,
        source_analysis_excerpt=case.original_comment[:excerpt_limit],
        source_move=case.annotated_move_san,
        evidence_questions=questions,
        prohibited_transfers=[
            "不得把来源案例的棋子位置或格子写成当前局面事实",
            "不得把来源案例的着法写成当前合法着、最佳着或当前威胁",
            "不得把来源案例的评价、胜负或物质结论复制到当前局面",
            "不得把来源案例变化内部的吃子、将军或将杀升级为当前直接威胁",
            "不得因为案例相似就宣称当前一方拥有主动权",
        ],
    )
