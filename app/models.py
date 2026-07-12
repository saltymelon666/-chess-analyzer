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

