from __future__ import annotations

from pydantic import BaseModel, Field


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
    centipawn_loss: int | None = None
    best_move_uci: str | None = None
    best_move_san: str | None = None
    best_pv: list[str] = Field(default_factory=list)
    quality_key: str
    quality_symbol: str
    quality_label: str
    mate_involved: bool
    only_legal_move: bool


class GameReviewResponse(BaseModel):
    analysis_id: str
    depth: int
    move_count: int
    moves: list[MoveReview]


class MoveExplanationRequest(BaseModel):
    analysis_id: str = Field(min_length=8, max_length=80)
    move_index: int = Field(ge=1)


class MoveExplanationResponse(BaseModel):
    explanation: str | None = None
    warning: str | None = None
    cached: bool = False
