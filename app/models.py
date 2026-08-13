from __future__ import annotations

from typing import Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from .opening_knowledge import OpeningPresentation
from .strategic_plans import StrategicPlanFact
from .threat_analysis import ThreatFact


class ReviewRequest(BaseModel):
    fen: str = Field(min_length=15, max_length=120)


class PositionResult(BaseModel):
    fen: str
    side_to_move: str


class MoveResult(BaseModel):
    move: str
    san: str
    centipawn: int | None = None
    mate_in: int | None = None
    pv: list[str]
    depth: int
    rank: int = 1
    line: list["VariationMove"] = Field(default_factory=list)
    resulting_fen: str | None = None
    verified: bool = True
    verification_error: str | None = None


class VariationMove(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str = ""
    ply: int = Field(alias="plyIndex")
    move_number: int = Field(alias="fullMoveNumber")
    side: str
    san: str
    uci: str
    from_square: str = Field(alias="from")
    to_square: str = Field(alias="to")
    piece: str
    capture: bool
    captured_piece: str | None = Field(alias="capturedPiece", default=None)
    check: bool
    checkmate: bool
    castling: bool
    promotion: str | None = None


class EngineResult(BaseModel):
    evaluation: str
    perspective: Literal["white"] = "white"
    centipawn: int | None = None
    evaluation_pawns: float | None = None
    mate_in: int | None = None
    depth: int
    nodes: int
    time_ms: int
    top_moves: list[MoveResult]

    def model_post_init(self, __context: object) -> None:
        if self.evaluation_pawns is None and self.centipawn is not None:
            self.evaluation_pawns = round(self.centipawn / 100, 2)


class ReviewResponse(BaseModel):
    position: PositionResult
    engine: EngineResult
    explanation: str | None = None
    warning: str | None = None
    threats: list[ThreatFact] = Field(default_factory=list)
    plans: list[StrategicPlanFact] = Field(default_factory=list)


class HealthResponse(BaseModel):
    status: str
    stockfish: str
    deepseek_configured: bool
    deepseek_key_format_valid: bool = False
    deepseek_model: str | None = None


class DeepSeekConnectionResult(BaseModel):
    status_code: int
    message: str
    elapsed_ms: int
    model: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None


class GameReviewRequest(BaseModel):
    pgn: str = Field(min_length=3, max_length=100_000)
    visitor_id: str | None = Field(default=None, min_length=8, max_length=80)
    analysis_id: str | None = Field(default=None, min_length=8, max_length=80)


class EvaluationSnapshot(BaseModel):
    evaluation: str
    perspective: Literal["white"] = "white"
    centipawn: int | None = None
    evaluation_pawns: float | None = None
    mate_in: int | None = None

    def model_post_init(self, __context: object) -> None:
        if self.evaluation_pawns is None and self.centipawn is not None:
            self.evaluation_pawns = round(self.centipawn / 100, 2)


class MoveFacts(BaseModel):
    id: str = ""
    san: str
    uci: str
    from_square: str
    to_square: str
    piece: str
    capture: bool
    captured_piece: str | None = None
    check: bool
    checkmate: bool
    castling: bool
    promotion: str | None = None


class ComplexityFactors(BaseModel):
    legal_move_count: int
    candidate_gap_cp: int | None = None
    only_reasonable_move: bool
    pv_length: int
    evaluation_swing_cp: int | None = None
    forcing_line_plies: int
    engaged_piece_count: int
    direct_piece_loss: bool = False
    tactical_motif_count: int = 0
    multi_step_tactic: bool = False
    opponent_forcing_options: int = 0
    multiple_threats: bool = False


class VerifiedTactic(BaseModel):
    name: str
    side: str
    move_uci: str
    description: str
    squares: list[str] = Field(default_factory=list)


class EvidenceFact(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str = ""
    category: str = Field(alias="type")
    description: str
    evidence: list[str] = Field(min_length=1)
    side: str | None = None
    squares: list[str] = Field(default_factory=list)


class PositionFacts(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    side_to_move: str = Field(alias="sideToMove", default="white")
    pieces: list[dict[str, str]] = Field(default_factory=list)
    material: dict[str, object] = Field(default_factory=dict)
    piece_activity: list[EvidenceFact] = Field(alias="pieceActivity", default_factory=list)
    king_safety: list[EvidenceFact] = Field(alias="kingSafety", default_factory=list)
    pawn_structure: list[EvidenceFact] = Field(alias="pawnStructure", default_factory=list)
    threats: list[EvidenceFact] = Field(default_factory=list)
    open_files: list[str] = Field(alias="openFiles", default_factory=list)
    semi_open_files: dict[str, list[str]] = Field(alias="semiOpenFiles", default_factory=dict)
    immediate_checks: list[MoveFacts] = Field(alias="immediateChecks", default_factory=list)
    immediate_captures: list[MoveFacts] = Field(alias="immediateCaptures", default_factory=list)


class CandidateLine(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str = ""
    rank: int
    depth: int
    centipawn: int | None = Field(alias="evaluation", default=None)
    mate_in: int | None = Field(alias="mate", default=None)
    first_move: MoveFacts = Field(alias="firstMove")
    moves: list[VariationMove] = Field(alias="pv", default_factory=list)
    resulting_fen: str = Field(alias="resultingFen")
    resulting_position_facts: PositionFacts | None = Field(alias="resultingPositionFacts", default=None)
    verified: bool = True
    verification_error: str | None = Field(alias="verificationError", default=None)


class MoveReview(BaseModel):
    index: int
    move_number: int
    notation: str
    side: str
    san: str
    uci: str
    from_square: str
    to_square: str
    before_fen: str
    after_fen: str
    before: EvaluationSnapshot
    after: EvaluationSnapshot
    played_move: MoveFacts
    best_move: MoveFacts | None = None
    opponent_reply: MoveFacts | None = None
    centipawn_loss: int | None = None
    best_move_uci: str | None = None
    best_move_san: str | None = None
    best_pv: list[str] = Field(default_factory=list)
    quality_key: str
    quality_symbol: str
    quality_label: str
    mate_involved: bool
    only_legal_move: bool
    principal_variation: list[str] = Field(default_factory=list)
    principal_variation_facts: list[MoveFacts] = Field(default_factory=list)
    opponent_variation: list[str] = Field(default_factory=list)
    opponent_variation_facts: list[MoveFacts] = Field(default_factory=list)
    complexity: str
    complexity_factors: ComplexityFactors
    verified_facts: list[str] = Field(default_factory=list)
    allowed_squares: list[str] = Field(default_factory=list)
    allowed_moves: list[str] = Field(default_factory=list)
    pieces_before: dict[str, str] = Field(default_factory=dict)
    verified_tactics: list[VerifiedTactic] = Field(default_factory=list)
    candidate_lines: list[CandidateLine] = Field(default_factory=list)
    actual_move_line: CandidateLine | None = None
    position_facts: PositionFacts = Field(default_factory=PositionFacts)
    position_facts_after: PositionFacts = Field(default_factory=PositionFacts)
    opening_context: OpeningPresentation | None = Field(alias="openingContext", default=None)


class GameReviewResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    analysis_id: str
    depth: int
    move_count: int
    moves: list[MoveReview]
    opening_summary: OpeningPresentation | None = Field(
        alias="openingSummary", default=None
    )


class MoveExplanationRequest(BaseModel):
    analysis_id: str = Field(min_length=8, max_length=80)
    move_index: int = Field(ge=1)


class CurrentMoveRecord(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    ply_index: int = Field(alias="plyIndex")
    full_move_number: int = Field(alias="fullMoveNumber")
    side: str
    fen_before: str = Field(alias="fenBefore")
    fen_after: str = Field(alias="fenAfter")
    played_move: MoveFacts = Field(alias="playedMove")


class MoveFactPackage(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    analysis_id: str = Field(alias="analysisId")
    current_move: CurrentMoveRecord = Field(alias="currentMove")
    position_before: PositionFacts = Field(alias="positionBefore")
    position_after: PositionFacts = Field(alias="positionAfter")
    played_move_continuation: CandidateLine | None = Field(alias="playedMoveContinuation")
    candidate_lines: list[CandidateLine] = Field(alias="candidateLines")


class ProfessionalComplexity(BaseModel):
    level: Literal["simple", "normal", "complex"]
    reasons: list[str] = Field(default_factory=list)


class ProfessionalEvidenceText(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    description: str
    evidence_refs: list[str] = Field(alias="evidenceRefs", min_length=1)


class ProfessionalKingSafety(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    is_relevant: bool = Field(alias="isRelevant")
    white: ProfessionalEvidenceText | None = None
    black: ProfessionalEvidenceText | None = None


class ProfessionalPositionAssessment(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    summary: str
    # Material remains available in raw facts and validation, but is not a fixed user section.
    material: ProfessionalEvidenceText | None = None
    king_safety: ProfessionalKingSafety = Field(alias="kingSafety")
    piece_activity: ProfessionalEvidenceText | None = Field(alias="pieceActivity", default=None)
    pawn_structure: ProfessionalEvidenceText | None = Field(alias="pawnStructure", default=None)


class ProfessionalMainDanger(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    side_in_danger: Literal["white", "black", "both", "none"] = Field(alias="sideInDanger")
    level: Literal["immediate", "short_term", "long_term"]
    description: str
    consequence: str
    evidence_refs: list[str] = Field(alias="evidenceRefs", min_length=1)


StrategyTag = Literal[
    "king_attack",
    "improve_king_safety",
    "center_break",
    "center_control",
    "kingside_expansion",
    "queenside_expansion",
    "control_open_file",
    "occupy_weak_square",
    "improve_worst_piece",
    "exchange_and_simplify",
    "create_passed_pawn",
    "defend_immediate_threat",
    "pawn_break",
    "transition_to_endgame",
]


class ProfessionalPlan(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    strategy_tag: StrategyTag = Field(alias="strategyTag")
    description: str
    required_preparation: str = Field(alias="requiredPreparation")
    evidence_refs: list[str] = Field(alias="evidenceRefs", min_length=1)


class ProfessionalPlans(BaseModel):
    model_config = ConfigDict(extra="forbid")

    white: list[ProfessionalPlan] = Field(default_factory=list)
    black: list[ProfessionalPlan] = Field(default_factory=list)


class ProfessionalWeakness(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    description: str
    exploitation: str
    evidence_refs: list[str] = Field(alias="evidenceRefs", min_length=1)


class ProfessionalWeaknesses(BaseModel):
    model_config = ConfigDict(extra="forbid")

    white: list[ProfessionalWeakness] = Field(default_factory=list)
    black: list[ProfessionalWeakness] = Field(default_factory=list)


class ProfessionalThreat(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    side: Literal["white", "black"]
    level: Literal["immediate", "short_term", "long_term"]
    scope: Literal["current_position"] = "current_position"
    description: str
    attacker: str
    target: str
    preparation: str
    consequence: str
    evidence_refs: list[str] = Field(alias="evidenceRefs", min_length=1)


class ProfessionalLineEvent(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    scope: Literal["candidate_line_1", "candidate_line_2", "candidate_line_3"]
    description: str
    significance: str
    evidence_refs: list[str] = Field(alias="evidenceRefs", min_length=1)


class ProfessionalContinuationPhase(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    phase: str = ""
    moves: list[str] = Field(default_factory=list)
    explanation: str
    evidence_refs: list[str] = Field(alias="evidenceRefs", min_length=1)


class ProfessionalPlayedMoveAnalysis(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    move: str
    intention: str
    positive_effects: list[str] = Field(alias="positiveEffects", default_factory=list)
    problems: list[str] = Field(default_factory=list)
    strongest_response: str = Field(alias="strongestResponse")
    continuation_phases: list[ProfessionalContinuationPhase] = Field(alias="continuationPhases", default_factory=list)
    resulting_position: str = Field(alias="resultingPosition")
    evaluation_reason: str = Field(alias="evaluationReason")
    error_type: Literal["tactical", "strategic", "both", "none"] = Field(alias="errorType")
    evidence_refs: list[str] = Field(alias="evidenceRefs", min_length=1)


class ProfessionalCandidateLineAnalysis(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    rank: int = Field(ge=1, le=3)
    first_move: str = Field(alias="firstMove")
    strategy_tags: list[StrategyTag] = Field(alias="strategyTags", default_factory=list)
    direct_purpose: str = Field(alias="directPurpose")
    opponent_response: str = Field(alias="opponentResponse")
    continuation_phases: list[ProfessionalContinuationPhase] = Field(alias="continuationPhases", default_factory=list)
    resulting_position: str = Field(alias="resultingPosition")
    advantages: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    events: list[ProfessionalLineEvent] = Field(default_factory=list, max_length=2)
    why_this_rank: str = Field(alias="whyThisRank")
    evidence_refs: list[str] = Field(alias="evidenceRefs", min_length=1)


class ProfessionalComparison(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    main_difference: str = Field(alias="mainDifference")
    why_first_line_is_best: str = Field(alias="whyFirstLineIsBest")
    evidence_refs: list[str] = Field(alias="evidenceRefs", min_length=1)


class ProfessionalAnalysis(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    complexity: Literal["simple", "normal", "complex"]
    position_assessment: ProfessionalPositionAssessment = Field(alias="positionAssessment")
    main_danger: ProfessionalMainDanger = Field(alias="mainDanger")
    plans: ProfessionalPlans
    weaknesses: ProfessionalWeaknesses
    threats: list[ProfessionalThreat] = Field(default_factory=list)
    played_move_analysis: ProfessionalPlayedMoveAnalysis = Field(alias="playedMoveAnalysis")
    candidate_lines: list[ProfessionalCandidateLineAnalysis] = Field(alias="candidateLines", max_length=3)
    comparison: ProfessionalComparison


class ProfessionalDraftPositionAssessment(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    summary: str = Field(min_length=4, max_length=500)


class ProfessionalDraftMainDanger(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    level: Literal["immediate", "short_term", "medium_term", "long_term", "none"]
    danger_ref: str | None = Field(alias="dangerRef", default=None)
    explanation: str = Field(default="", max_length=500)
    consequence: str = Field(default="", max_length=500)
    evidence_refs: list[str] = Field(alias="evidenceRefs", default_factory=list, max_length=100)


class ProfessionalDraftPlan(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    strategy_tag: StrategyTag = Field(alias="strategyTag")
    explanation: str = Field(
        min_length=4,
        max_length=500,
        validation_alias=AliasChoices("explanation", "description"),
        serialization_alias="explanation",
    )
    required_preparation: str = Field(alias="requiredPreparation", min_length=1, max_length=400)
    evidence_refs: list[str] = Field(alias="evidenceRefs", default_factory=list, max_length=100)


class ProfessionalDraftPlans(BaseModel):
    model_config = ConfigDict(extra="forbid")

    white: list[ProfessionalDraftPlan] = Field(default_factory=list, max_length=2)
    black: list[ProfessionalDraftPlan] = Field(default_factory=list, max_length=2)


class ProfessionalDraftPlanExplanation(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    plan_id: str = Field(alias="planId")
    explanation: str = Field(min_length=4, max_length=500)


class ProfessionalDraftWeakness(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    explanation: str = Field(min_length=4, max_length=500)
    exploitation: str = Field(min_length=4, max_length=400)
    evidence_refs: list[str] = Field(alias="evidenceRefs", min_length=1, max_length=100)


class ProfessionalDraftWeaknesses(BaseModel):
    model_config = ConfigDict(extra="forbid")

    white: list[ProfessionalDraftWeakness] = Field(default_factory=list, max_length=1)
    black: list[ProfessionalDraftWeakness] = Field(default_factory=list, max_length=1)


class ProfessionalDraftThreat(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    side: Literal["white", "black"]
    level: Literal["immediate", "short_term", "medium_term", "long_term"]
    target_ref: str = Field(alias="targetRef")
    explanation: str = Field(min_length=4, max_length=500)
    evidence_refs: list[str] = Field(alias="evidenceRefs", min_length=1, max_length=100)


class ProfessionalDraftContinuationPhase(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    phase: str = Field(min_length=1, max_length=80)
    ply_refs: list[str] = Field(alias="plyRefs", min_length=1, max_length=10)
    explanation: str = Field(min_length=4, max_length=500)


class ProfessionalDraftPlayedMoveAnalysis(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    move_ref: str = Field(alias="moveRef")
    intention: str = Field(min_length=4, max_length=500)
    positive_effects: list[str] = Field(alias="positiveEffects", default_factory=list, max_length=3)
    problems: list[str] = Field(default_factory=list, max_length=3)
    strongest_reply_ref: str = Field(alias="strongestReplyRef")
    ply_refs: list[str] = Field(alias="plyRefs", min_length=1, max_length=10)
    continuation_explanation: str = Field(alias="continuationExplanation", min_length=4, max_length=500)
    error_type: Literal["tactical", "strategic", "both", "none"] = Field(alias="errorType")
    evidence_refs: list[str] = Field(alias="evidenceRefs", min_length=1, max_length=100)


class ProfessionalDraftCandidateLine(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    line_ref: str = Field(alias="lineRef")
    strategy_tags: list[StrategyTag] = Field(alias="strategyTags", default_factory=list, max_length=3)
    direct_purpose: str = Field(alias="directPurpose", min_length=4, max_length=500)
    ply_refs: list[str] = Field(alias="plyRefs", min_length=1, max_length=10)
    continuation_explanation: str = Field(alias="continuationExplanation", min_length=4, max_length=500)
    advantages: list[str] = Field(default_factory=list, max_length=3)
    risks: list[str] = Field(default_factory=list, max_length=3)
    evidence_refs: list[str] = Field(alias="evidenceRefs", default_factory=list, max_length=100)


class ProfessionalDraftComparison(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    main_difference: str = Field(alias="mainDifference", min_length=4, max_length=500)
    why_first_line_is_best: str = Field(alias="whyFirstLineIsBest", min_length=4, max_length=500)
    evidence_refs: list[str] = Field(alias="evidenceRefs", min_length=1, max_length=100)


class ProfessionalAnalysisDraft(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    complexity: Literal["simple", "normal", "complex"]
    position_assessment: ProfessionalDraftPositionAssessment = Field(alias="positionAssessment")
    main_danger: ProfessionalDraftMainDanger = Field(alias="mainDanger")
    plans: ProfessionalDraftPlans = Field(default_factory=ProfessionalDraftPlans)
    plan_explanations: list[ProfessionalDraftPlanExplanation] = Field(
        alias="planExplanations",
        default_factory=list,
        max_length=8,
    )
    played_move_analysis: ProfessionalDraftPlayedMoveAnalysis = Field(alias="playedMoveAnalysis")
    candidate_lines: list[ProfessionalDraftCandidateLine] = Field(alias="candidateLines", min_length=1, max_length=3)
    comparison: ProfessionalDraftComparison


class ProfessionalAnalysisUsage(BaseModel):
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    elapsed_ms: int
    attempts: int
    network_ms: int = 0
    validation_ms: int = 0
    postprocess_ms: int = 0


class GeneratedProfessionalAnalysis(BaseModel):
    analysis: ProfessionalAnalysis
    complexity_reasons: list[str] = Field(default_factory=list)
    validation_warnings: list[str] = Field(default_factory=list)
    usage: ProfessionalAnalysisUsage


class ProfessionalBookReference(BaseModel):
    """Book-authored commentary for one exact, fully matching position."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    position_id: str = Field(alias="positionId")
    source_title: str = Field(alias="sourceTitle")
    author: str
    source_url: str = Field(alias="sourceUrl")
    locator: str
    annotated_move: str | None = Field(alias="annotatedMove", default=None)
    original_comment: str = Field(alias="originalComment")
    extraction_status: str = Field(alias="extractionStatus")
    authority_scope: str = Field(alias="authorityScope")


class ProfessionalAnalysisResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    analysis: ProfessionalAnalysis | None = None
    opening_context: OpeningPresentation | None = Field(
        alias="openingContext",
        default=None,
    )
    book_references: list[ProfessionalBookReference] = Field(
        alias="bookReferences",
        default_factory=list,
    )
    complexity_reasons: list[str] = Field(default_factory=list)
    validation_warnings: list[str] = Field(default_factory=list)
    usage: ProfessionalAnalysisUsage | None = None
    warning: str | None = None
    cached: bool = False


class MoveThreatExplanation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    threat_id: str = Field(alias="threatId")
    explanation: str = Field(min_length=1, max_length=500)


class MovePlanExplanation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_id: str = Field(alias="planId")
    explanation: str = Field(min_length=1, max_length=500)


class MoveExplanationDetails(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    complexity: str
    conclusion: str
    current_situation: str = Field(alias="currentSituation")
    opponent_threat: str = Field(alias="opponentThreat")
    played_move_idea: str = Field(alias="playedMoveIdea")
    problem: str
    better_move: str = Field(alias="betterMove")
    variation_explanation: list[str] = Field(alias="variationExplanation", default_factory=list)
    threat_explanations: list[MoveThreatExplanation] = Field(
        alias="threatExplanations",
        default_factory=list,
        max_length=5,
    )
    plan_explanations: list[MovePlanExplanation] = Field(
        alias="planExplanations",
        default_factory=list,
        max_length=8,
    )
    child_tip: str = Field(alias="childTip")


class GeneratedMoveExplanation(BaseModel):
    explanation: str
    details: MoveExplanationDetails


class MoveExplanationResponse(BaseModel):
    explanation: str | None = None
    details: MoveExplanationDetails | None = None
    warning: str | None = None
    cached: bool = False
