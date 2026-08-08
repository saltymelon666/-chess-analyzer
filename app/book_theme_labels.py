from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .position_factor_ranker import FactorFamily


LabelConfidence = Literal["high", "medium", "unknown"]


class BookThemeEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    family: FactorFamily
    pattern_id: str
    matched_text: str
    weight: int = Field(ge=1, le=5)


class BookThemeLabel(BaseModel):
    """A traceable offline label derived only from one source-book comment."""

    model_config = ConfigDict(extra="forbid")

    primary_theme: FactorFamily | None = None
    acceptable_themes: list[FactorFamily] = Field(default_factory=list)
    confidence: LabelConfidence = "unknown"
    evidence: list[BookThemeEvidence] = Field(default_factory=list)
    label_source: Literal["book_comment_text_only"] = "book_comment_text_only"
    boundary: str = (
        "该标签只表示原评明确强调的主题；没有命中不等于局面不存在该主题，"
        "棋书文字不得作为排序器输入。"
    )


_PATTERNS: dict[FactorFamily, tuple[tuple[str, int, str], ...]] = {
    "forcing_tactics": (
        ("forced_mate", 5, r"\b(?:forced mate|mates? in \w+|mating (?:attack|net|threat)|checkmate)\b"),
        ("combination", 4, r"\b(?:combination|combinative|tactical coup|brilliancy)\b"),
        ("tactical_motif", 4, r"\b(?:fork|double attack|discovered (?:attack|check)|skewer|absolute pin)\b"),
        ("sacrifice", 3, r"\b(?:sacrific(?:e|es|ed|ing)|give(?:s|n)? up (?:the )?(?:queen|rook|bishop|knight|piece))\b"),
        ("forcing_threat", 3, r"\b(?:threatens?|threatening|forced|forcing move|only move)\b"),
        ("material_tactic", 3, r"\b(?:wins?|winning|lose(?:s|ing)?) (?:the |a )?(?:queen|rook|bishop|knight|piece|exchange|pawn)\b"),
    ),
    "king_attack_and_safety": (
        ("king_attack", 5, r"\b(?:attack(?:ing)? (?:on |against )?the king|king attack|assault on the king)\b"),
        ("king_exposure", 4, r"\b(?:exposed king|king(?:'s)? position|king safety|unsafe king|king in the cent(?:er|re))\b"),
        ("castling", 3, r"\b(?:castle|castles|castled|castling)\b"),
        ("king_wing", 2, r"\b(?:king'?s? side|king-side|kingside|pawn shield|flight square)\b"),
    ),
    "pawn_structure_and_space": (
        ("passed_pawn", 5, r"\b(?:passed pawns?|connected passed pawns?|queen(?:ing)? the pawn)\b"),
        ("pawn_weakness", 4, r"\b(?:isolated|backward|doubled|weak|fixed) pawns?\b"),
        ("pawn_majority", 4, r"\b(?:pawn majority|majority of pawns|minority attack|pawn chain)\b"),
        ("space_center", 3, r"\b(?:space advantage|command of the cent(?:er|re)|control of the cent(?:er|re)|occupy the cent(?:er|re))\b"),
        ("square_structure", 3, r"\b(?:weak squares?|holes?|outposts?|blockad(?:e|es|ed|ing))\b"),
        ("pawn_play", 2, r"\b(?:pawn formation|pawn structure|pawn advance|advance of (?:the )?pawns?|pawn break)\b"),
    ),
    "piece_activity_and_coordination": (
        ("development", 5, r"\b(?:develop(?:ment|ed|ing)?|undeveloped)\b"),
        ("activity", 4, r"\b(?:piece activity|active pieces?|pieces? into action|bring(?:ing)? (?:his |her |the )?pieces? into play)\b"),
        ("open_line", 4, r"\b(?:open files?|open diagonals?|seventh rank|7th rank)\b"),
        ("coordination", 4, r"\b(?:co-?ordinat(?:e|ed|ion)|combined forces|pieces? co-?operate)\b"),
        ("piece_quality", 3, r"\b(?:well[- ]placed|bad bishop|good bishop|active rook|rook activity|mobility|greater scope)\b"),
        ("restriction", 3, r"\b(?:restrict(?:s|ed|ing|ion)|shut in|confine(?:s|d|ment))\b"),
    ),
    "conversion_and_compensation": (
        ("compensation", 5, r"\b(?:compensation|insufficient compensation|adequate compensation)\b"),
        ("endgame", 4, r"\b(?:endgame|ending|pawn ending|rook ending|queen ending)\b"),
        ("simplification", 4, r"\b(?:simplif(?:y|ies|ied|ication)|exchange all|exchange of queens|trade queens)\b"),
        ("material_balance", 3, r"\b(?:material advantage|material superiority|material deficit|piece ahead|pawn ahead|exchange ahead|piece behind|pawn behind)\b"),
        ("result_conversion", 3, r"\b(?:winning position|won game|lost game|drawn game|draw the game|save the game|convert(?:s|ed|ing)? the advantage)\b"),
        ("evaluation_balance", 2, r"\b(?:better chances|equal game|even game|superior game|inferior game)\b"),
    ),
}


def label_book_comment(comment: str) -> BookThemeLabel:
    text = re.sub(r"\s+", " ", comment).strip()
    evidence: list[BookThemeEvidence] = []
    totals: dict[FactorFamily, int] = {family: 0 for family in _PATTERNS}
    first_offsets: dict[FactorFamily, int] = {family: len(text) + 1 for family in _PATTERNS}
    for family, patterns in _PATTERNS.items():
        for pattern_id, weight, pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match is None:
                continue
            totals[family] += weight
            first_offsets[family] = min(first_offsets[family], match.start())
            evidence.append(BookThemeEvidence(
                family=family,
                pattern_id=pattern_id,
                matched_text=match.group(0),
                weight=weight,
            ))
    ranked = sorted(
        (family for family, score in totals.items() if score > 0),
        key=lambda family: (-totals[family], first_offsets[family], family),
    )
    if not ranked:
        return BookThemeLabel()
    primary = ranked[0]
    acceptable = [family for family in ranked if totals[family] >= max(2, totals[primary] - 3)]
    confidence: LabelConfidence = "high" if totals[primary] >= 5 else "medium"
    return BookThemeLabel(
        primary_theme=primary,
        acceptable_themes=acceptable[:3],
        confidence=confidence,
        evidence=[item for item in evidence if item.family in acceptable[:3]],
    )
