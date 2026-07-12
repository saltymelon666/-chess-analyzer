from __future__ import annotations

import asyncio
import logging

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from .ai_explainer import DeepSeekExplainer
from .config import load_settings
from .engine import StockfishService
from .models import HealthResponse, PositionResult, ReviewRequest, ReviewResponse


logger = logging.getLogger(__name__)
settings = load_settings()
stockfish = StockfishService(
    settings.stockfish_path,
    depth=settings.stockfish_depth,
    threads=settings.stockfish_threads,
    hash_mb=settings.stockfish_hash,
    multipv=settings.stockfish_multipv,
    timeout_seconds=settings.stockfish_timeout_seconds,
)
explainer = DeepSeekExplainer(
    api_key=settings.deepseek_api_key,
    base_url=settings.deepseek_base_url,
    model=settings.deepseek_model,
    timeout_seconds=settings.deepseek_timeout_seconds,
)

app = FastAPI(
    title="AI Chess Review API",
    version="0.1.0",
    description="Server-side Stockfish 18 analysis with optional DeepSeek explanation.",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.allowed_origins),
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type"],
)


@app.get("/api/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(
        status="ok" if stockfish.available() else "degraded",
        stockfish="available" if stockfish.available() else "missing",
        deepseek_configured=explainer.configured,
    )


@app.post("/api/review", response_model=ReviewResponse)
async def review(request: ReviewRequest) -> ReviewResponse:
    try:
        engine_result = await stockfish.analyze(request.fen)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except asyncio.TimeoutError as exc:
        raise HTTPException(status_code=504, detail="Stockfish 分析超时") from exc
    except Exception as exc:
        logger.exception("Stockfish analysis failed")
        raise HTTPException(status_code=503, detail="Stockfish 分析服务暂不可用") from exc

    explanation: str | None = None
    warning: str | None = None
    try:
        explanation = await explainer.explain(request.fen, engine_result)
    except (httpx.HTTPError, RuntimeError) as exc:
        logger.warning("DeepSeek explanation unavailable: %s", exc)
        warning = f"Stockfish 分析已完成，但 AI 解释暂不可用：{exc}"
    except Exception:
        logger.exception("Unexpected DeepSeek error")
        warning = "Stockfish 分析已完成，但 AI 解释暂不可用"

    side_to_move = "white" if request.fen.split()[1] == "w" else "black"
    return ReviewResponse(
        position=PositionResult(fen=request.fen, side_to_move=side_to_move),
        engine=engine_result,
        explanation=explanation,
        warning=warning,
    )

