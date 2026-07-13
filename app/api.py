from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict
from uuid import uuid4

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from .ai_explainer import DeepSeekExplainer
from .config import load_settings
from .engine import StockfishService
from .game_review import analyze_pgn
from .models import (
    GameReviewRequest,
    GameReviewResponse,
    HealthResponse,
    MoveExplanationRequest,
    MoveExplanationResponse,
    MoveReview,
    PositionResult,
    ReviewRequest,
    ReviewResponse,
)


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
game_cache: OrderedDict[str, list[MoveReview]] = OrderedDict()
explanation_cache: dict[tuple[str, int], str] = {}
explanation_tasks: dict[tuple[str, int], asyncio.Task[str]] = {}
MAX_CACHED_GAMES = 20

app = FastAPI(
    title="AI Chess Review API",
    version="0.2.0",
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


@app.post("/api/game-review", response_model=GameReviewResponse)
async def game_review(request: GameReviewRequest) -> GameReviewResponse:
    analysis_id = uuid4().hex
    try:
        result = await analyze_pgn(
            pgn=request.pgn,
            stockfish=stockfish,
            analysis_id=analysis_id,
            depth=settings.game_analysis_depth,
            timeout_seconds=settings.game_analysis_timeout_seconds,
            max_plies=settings.game_analysis_max_plies,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except asyncio.TimeoutError as exc:
        raise HTTPException(status_code=504, detail="整盘 Stockfish 分析超时，请缩短棋谱后重试") from exc
    except Exception as exc:
        logger.exception("Full game analysis failed")
        raise HTTPException(status_code=503, detail="整盘分析服务暂不可用") from exc

    game_cache[analysis_id] = result.moves
    game_cache.move_to_end(analysis_id)
    while len(game_cache) > MAX_CACHED_GAMES:
        expired_id, _ = game_cache.popitem(last=False)
        for cache_key in [key for key in explanation_cache if key[0] == expired_id]:
            explanation_cache.pop(cache_key, None)
    return result


@app.post("/api/move-explanation", response_model=MoveExplanationResponse)
async def move_explanation(request: MoveExplanationRequest) -> MoveExplanationResponse:
    moves = game_cache.get(request.analysis_id)
    if moves is None:
        raise HTTPException(status_code=404, detail="本次分析缓存已过期，请重新分析棋谱")
    if request.move_index > len(moves):
        raise HTTPException(status_code=404, detail="找不到这一步的分析数据")

    cache_key = (request.analysis_id, request.move_index)
    if cache_key in explanation_cache:
        return MoveExplanationResponse(
            explanation=explanation_cache[cache_key],
            cached=True,
        )

    move = moves[request.move_index - 1]
    created_task = False
    try:
        task = explanation_tasks.get(cache_key)
        if task is None:
            task = asyncio.create_task(explainer.explain_move(move))
            explanation_tasks[cache_key] = task
            created_task = True
        explanation = await asyncio.shield(task)
        explanation_cache[cache_key] = explanation
        return MoveExplanationResponse(explanation=explanation, cached=not created_task)
    except (httpx.HTTPError, RuntimeError) as exc:
        logger.warning("Move explanation unavailable: %s", exc)
        return MoveExplanationResponse(
            warning=f"AI 解释暂不可用：{exc}",
            cached=False,
        )
    except Exception:
        logger.exception("Unexpected move explanation error")
        return MoveExplanationResponse(
            warning="AI 解释暂不可用，但 Stockfish 评价仍然有效",
            cached=False,
        )
    finally:
        task = explanation_tasks.get(cache_key)
        if task is not None and task.done():
            explanation_tasks.pop(cache_key, None)
