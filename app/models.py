from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


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


class EngineResult(BaseModel):
    evaluation: str
    centipawn: int | None = None
    mate_in: int | None = None
    depth: int
    nodes: int
    time_ms: int
    top_moves: list[MoveResult]


class ReviewResponse(BaseModel):
    position: PositionResult
    engine: EngineResult
    explanation: str | None = None
    warning: str | None = None


class HealthResponse(BaseModel):
    status: str
    stockfish: str
    deepseek_configured: bool


class GameReviewRequest(BaseModel):
    pgn: str = Field(min_length=3, max_length=100_000)


class EvaluationSnapshot(BaseModel):
    evaluation: str
    centipawn: int | None = None
    mate_in: int | None = None


class MoveFacts(BaseModel):
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


class GameReviewResponse(BaseModel):
    analysis_id: str
    depth: int
    move_count: int
    moves: list[MoveReview]


class MoveExplanationRequest(BaseModel):
    analysis_id: str = Field(min_length=8, max_length=80)
    move_index: int = Field(ge=1)


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
    child_tip: str = Field(alias="childTip")


class GeneratedMoveExplanation(BaseModel):
    explanation: str
    details: MoveExplanationDetails


class MoveExplanationResponse(BaseModel):
    explanation: str | None = None
    details: MoveExplanationDetails | None = None
    warning: str | None = None
    cached: bool = False
