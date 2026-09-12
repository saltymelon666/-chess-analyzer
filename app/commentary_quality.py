from __future__ import annotations

from collections.abc import Iterable
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .models import ProfessionalAnalysis


AtomKind = Literal[
    "verdict",
    "mechanism",
    "opponent_resource",
    "consequence",
    "practical_plan",
]
AtomTier = Literal["core", "detail"]


class GoldCommentaryAtom(BaseModel):
    """One expert-approved idea and the program evidence that may support it."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    atom_id: str = Field(alias="atomId", min_length=1)
    kind: AtomKind
    tier: AtomTier = "detail"
    summary: str = Field(min_length=1)
    acceptable_evidence_sets: list[list[str]] = Field(
        alias="acceptableEvidenceSets",
        min_length=1,
    )
    acceptable_claim_paths: list[str] = Field(
        alias="acceptableClaimPaths",
        min_length=1,
    )
    required_text_groups: list[list[str]] = Field(
        alias="requiredTextGroups",
        min_length=1,
    )
    required: bool = True


class CoreRouteGoldSpec(BaseModel):
    """Deterministic minimum coverage derived from the reviewed engine route."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    played_move: str = Field(alias="playedMove", min_length=1)
    best_move: str = Field(alias="bestMove", min_length=1)
    opponent_response: str = Field(alias="opponentResponse", min_length=1)
    played_is_best: bool = Field(alias="playedIsBest")


class CommentaryClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    kind: AtomKind
    text: str
    evidence_refs: list[str] = Field(min_length=1)


class CommentaryQualityMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    claim_count: int = Field(alias="claimCount", ge=0)
    supported_claim_count: int = Field(alias="supportedClaimCount", ge=0)
    reference_precision: float = Field(alias="referencePrecision", ge=0, le=1)
    required_gold_count: int = Field(alias="requiredGoldCount", ge=0)
    covered_gold_count: int = Field(alias="coveredGoldCount", ge=0)
    gold_atom_recall: float | None = Field(alias="goldAtomRecall", ge=0, le=1)
    required_core_count: int = Field(alias="requiredCoreCount", ge=0)
    covered_core_count: int = Field(alias="coveredCoreCount", ge=0)
    core_recall: float | None = Field(alias="coreRecall", ge=0, le=1)
    required_detail_count: int = Field(alias="requiredDetailCount", ge=0)
    covered_detail_count: int = Field(alias="coveredDetailCount", ge=0)
    detail_recall: float | None = Field(alias="detailRecall", ge=0, le=1)
    required_by_kind: dict[AtomKind, int] = Field(alias="requiredByKind")
    covered_by_kind: dict[AtomKind, int] = Field(alias="coveredByKind")
    recall_by_kind: dict[AtomKind, float | None] = Field(alias="recallByKind")
    unmatched_required_atoms: list[str] = Field(alias="unmatchedRequiredAtoms")
    unsupported_claim_paths: list[str] = Field(alias="unsupportedClaimPaths")


def _path_matches(path: str, pattern: str) -> bool:
    """Match a JSON-style path, with only ``[*]`` acting as a wildcard."""
    expression = re.escape(pattern).replace(r"\[\*\]", r"\[\d+\]")
    return re.fullmatch(expression, path) is not None


def build_core_route_gold_atoms(
    position_id: str,
    spec: CoreRouteGoldSpec,
) -> list[GoldCommentaryAtom]:
    """Build the three auditable core atoms shared by every quality position."""
    verdict_terms = (
        ["最佳着", "首选一致", "引擎首选", "就是首选"]
        if spec.played_is_best
        else [
            "与首选不同",
            "首选不同",
            "首选不一致",
            "不如首选",
            "并非最佳",
            "疑问着",
            "错着",
            "更精确",
            "差约",
            "差距很小",
        ]
    )
    return [
        GoldCommentaryAtom(
            atomId=f"{position_id}-core-verdict",
            kind="verdict",
            tier="core",
            summary=f"明确评价实战着{spec.played_move}及其与首选的关系",
            acceptableEvidenceSets=[[
                "move:played:1",
                "evaluation:before:1",
                "evaluation:after:1",
            ]],
            acceptableClaimPaths=["playedMoveAnalysis.evaluationReason"],
            requiredTextGroups=[[spec.played_move], verdict_terms],
        ),
        GoldCommentaryAtom(
            atomId=f"{position_id}-core-first-move",
            kind="mechanism",
            tier="core",
            summary=f"首选路线必须从{spec.best_move}开始",
            acceptableEvidenceSets=[["line:1"]],
            acceptableClaimPaths=["candidateLines[0].directPurpose"],
            requiredTextGroups=[[spec.best_move], ["第一步", "先走"]],
        ),
        GoldCommentaryAtom(
            atomId=f"{position_id}-core-response",
            kind="opponent_resource",
            tier="core",
            summary=f"指出首选路线中的对手回应{spec.opponent_response}",
            acceptableEvidenceSets=[["line:1"]],
            acceptableClaimPaths=["candidateLines[0].opponentResponse"],
            requiredTextGroups=[[spec.opponent_response]],
        ),
    ]


def extract_commentary_claims(analysis: ProfessionalAnalysis) -> list[CommentaryClaim]:
    """Turn the structured response into small, independently scored claims."""
    claims: list[CommentaryClaim] = []

    def add(path: str, kind: AtomKind, text: str, refs: Iterable[str]) -> None:
        normalized = text.strip()
        evidence_refs = list(dict.fromkeys(refs))
        if normalized and evidence_refs:
            claims.append(CommentaryClaim(
                path=path,
                kind=kind,
                text=normalized,
                evidence_refs=evidence_refs,
            ))

    danger = analysis.main_danger
    add("mainDanger.description", "opponent_resource", danger.description, danger.evidence_refs)
    add("mainDanger.consequence", "consequence", danger.consequence, danger.evidence_refs)

    for side in ("white", "black"):
        for index, plan in enumerate(getattr(analysis.plans, side)):
            path = f"plans.{side}[{index}]"
            add(f"{path}.description", "practical_plan", plan.description, plan.evidence_refs)
            add(
                f"{path}.requiredPreparation",
                "mechanism",
                plan.required_preparation,
                plan.evidence_refs,
            )

    for index, threat in enumerate(analysis.threats):
        path = f"threats[{index}]"
        add(f"{path}.description", "opponent_resource", threat.description, threat.evidence_refs)
        add(f"{path}.consequence", "consequence", threat.consequence, threat.evidence_refs)

    played = analysis.played_move_analysis
    add("playedMoveAnalysis.evaluationReason", "verdict", played.evaluation_reason, played.evidence_refs)
    add("playedMoveAnalysis.intention", "mechanism", played.intention, played.evidence_refs)
    for index, text in enumerate(played.positive_effects):
        add(f"playedMoveAnalysis.positiveEffects[{index}]", "mechanism", text, played.evidence_refs)
    for index, text in enumerate(played.problems):
        add(f"playedMoveAnalysis.problems[{index}]", "consequence", text, played.evidence_refs)

    for index, line in enumerate(analysis.candidate_lines):
        path = f"candidateLines[{index}]"
        add(f"{path}.directPurpose", "mechanism", line.direct_purpose, line.evidence_refs)
        add(f"{path}.opponentResponse", "opponent_resource", line.opponent_response, line.evidence_refs)
        add(f"{path}.whyThisRank", "verdict", line.why_this_rank, line.evidence_refs)
        for item_index, text in enumerate(line.advantages):
            add(f"{path}.advantages[{item_index}]", "consequence", text, line.evidence_refs)
        for item_index, text in enumerate(line.risks):
            add(f"{path}.risks[{item_index}]", "consequence", text, line.evidence_refs)

    comparison = analysis.comparison
    add("comparison.mainDifference", "mechanism", comparison.main_difference, comparison.evidence_refs)
    add(
        "comparison.whyFirstLineIsBest",
        "verdict",
        comparison.why_first_line_is_best,
        comparison.evidence_refs,
    )
    return claims


def evaluate_commentary_quality(
    claims: Iterable[CommentaryClaim],
    gold_atoms: Iterable[GoldCommentaryAtom],
    *,
    allowed_evidence_refs: set[str],
) -> CommentaryQualityMetrics:
    claim_list = list(claims)
    gold_list = [atom for atom in gold_atoms if atom.required]
    supported = [
        claim for claim in claim_list
        if set(claim.evidence_refs) <= allowed_evidence_refs
    ]
    unsupported_paths = [
        claim.path for claim in claim_list
        if not set(claim.evidence_refs) <= allowed_evidence_refs
    ]

    covered: set[str] = set()
    for atom in gold_list:
        acceptable_sets = [set(refs) for refs in atom.acceptable_evidence_sets]
        if any(
            claim.kind == atom.kind
            and any(_path_matches(claim.path, pattern) for pattern in atom.acceptable_claim_paths)
            and any(refs <= set(claim.evidence_refs) for refs in acceptable_sets)
            and all(
                any(term.casefold() in claim.text.casefold() for term in group)
                for group in atom.required_text_groups
            )
            for claim in supported
        ):
            covered.add(atom.atom_id)

    claim_count = len(claim_list)
    required_count = len(gold_list)
    core_atoms = [atom for atom in gold_list if atom.tier == "core"]
    detail_atoms = [atom for atom in gold_list if atom.tier == "detail"]
    covered_core_count = sum(atom.atom_id in covered for atom in core_atoms)
    covered_detail_count = sum(atom.atom_id in covered for atom in detail_atoms)
    kinds = list(dict.fromkeys(atom.kind for atom in gold_list))
    required_by_kind = {
        kind: sum(atom.kind == kind for atom in gold_list)
        for kind in kinds
    }
    covered_by_kind = {
        kind: sum(atom.kind == kind and atom.atom_id in covered for atom in gold_list)
        for kind in kinds
    }
    return CommentaryQualityMetrics(
        claimCount=claim_count,
        supportedClaimCount=len(supported),
        referencePrecision=(len(supported) / claim_count if claim_count else 1.0),
        requiredGoldCount=required_count,
        coveredGoldCount=len(covered),
        goldAtomRecall=(len(covered) / required_count if required_count else None),
        requiredCoreCount=len(core_atoms),
        coveredCoreCount=covered_core_count,
        coreRecall=(covered_core_count / len(core_atoms) if core_atoms else None),
        requiredDetailCount=len(detail_atoms),
        coveredDetailCount=covered_detail_count,
        detailRecall=(covered_detail_count / len(detail_atoms) if detail_atoms else None),
        requiredByKind=required_by_kind,
        coveredByKind=covered_by_kind,
        recallByKind={
            kind: covered_by_kind[kind] / required_by_kind[kind]
            for kind in kinds
        },
        unmatchedRequiredAtoms=[atom.atom_id for atom in gold_list if atom.atom_id not in covered],
        unsupportedClaimPaths=unsupported_paths,
    )
