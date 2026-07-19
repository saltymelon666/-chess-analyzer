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
from .config import OFFICIAL_DEEPSEEK_BASE_URL
from .deepseek_connection import check_deepseek_connection
from .engine import StockfishService
from .game_review import analyze_pgn
from .professional_analysis import ProfessionalAnalysisService, professional_cache_key
from .models import (
    GameReviewRequest,
    GameReviewResponse,
    GeneratedMoveExplanation,
    HealthResponse,
    CurrentMoveRecord,
    MoveFactPackage,
    MoveExplanationRequest,
    MoveExplanationResponse,
    MoveReview,
    PositionResult,
    GeneratedProfessionalAnalysis,
    DeepSeekConnectionResult,
    ProfessionalAnalysisResponse,
    ReviewRequest,
    ReviewResponse,
)


logger = logging.getLogger(__name__)
settings = load_settings()
deepseek_key_present = bool(settings.deepseek_api_key)
deepseek_key_format_valid = deepseek_key_present and settings.deepseek_api_key.startswith("sk-")
if not deepseek_key_present:
    logger.warning("未配置DeepSeek API Key")
elif not deepseek_key_format_valid:
    logger.warning("DeepSeek API Key已加载，但格式不是sk-前缀")
else:
    logger.info("DeepSeek API Key已安全加载；模型=%s", settings.deepseek_model)
if settings.deepseek_base_url != OFFICIAL_DEEPSEEK_BASE_URL:
    logger.warning("DeepSeek baseURL不是官方地址：%s", settings.deepseek_base_url)
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
professional_service = ProfessionalAnalysisService(
    api_key=settings.deepseek_api_key,
    base_url=settings.deepseek_base_url,
    model=settings.deepseek_model,
    timeout_seconds=settings.deepseek_timeout_seconds,
)
game_cache: OrderedDict[str, list[MoveReview]] = OrderedDict()
explanation_cache: dict[tuple[str, int], GeneratedMoveExplanation | str] = {}
explanation_tasks: dict[tuple[str, int], asyncio.Task[GeneratedMoveExplanation | str]] = {}
professional_cache: OrderedDict[str, GeneratedProfessionalAnalysis] = OrderedDict()
professional_tasks: dict[str, asyncio.Task[GeneratedProfessionalAnalysis]] = {}
MAX_CACHED_GAMES = 20
MAX_CACHED_PROFESSIONAL_ANALYSES = 100

app = FastAPI(
    title="AI Chess Review API",
    version="0.3.0",
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
        deepseek_key_format_valid=deepseek_key_format_valid,
        deepseek_model=settings.deepseek_model,
    )


if settings.environment == "development":
    @app.post("/api/dev/deepseek-connection", response_model=DeepSeekConnectionResult)
    async def deepseek_connection_test() -> DeepSeekConnectionResult:
        result = await check_deepseek_connection(
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_base_url,
            model=settings.deepseek_model,
            timeout_seconds=min(settings.deepseek_timeout_seconds, 30.0),
        )
        logger.info(
            "DeepSeek development connection check: status=%s elapsed_ms=%s total_tokens=%s",
            result.status_code,
            result.elapsed_ms,
            result.total_tokens,
        )
        return result


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
        cached_result = explanation_cache[cache_key]
        return MoveExplanationResponse(
            explanation=cached_result if isinstance(cached_result, str) else cached_result.explanation,
            details=None if isinstance(cached_result, str) else cached_result.details,
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
        generated = await asyncio.shield(task)
        explanation_cache[cache_key] = generated
        return MoveExplanationResponse(
            explanation=generated if isinstance(generated, str) else generated.explanation,
            details=None if isinstance(generated, str) else generated.details,
            cached=not created_task,
        )
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


@app.post("/api/move-facts", response_model=MoveFactPackage)
async def move_facts(request: MoveExplanationRequest) -> MoveFactPackage:
    """Return the verified data package for one selected ply without calling DeepSeek."""
    moves = game_cache.get(request.analysis_id)
    if moves is None:
        raise HTTPException(status_code=404, detail="本次分析缓存已过期，请重新分析棋谱")
    if request.move_index > len(moves):
        raise HTTPException(status_code=404, detail="找不到这一步的事实数据")
    move = moves[request.move_index - 1]
    return MoveFactPackage(
        analysisId=request.analysis_id,
        currentMove=CurrentMoveRecord(
            plyIndex=move.index,
            fullMoveNumber=move.move_number,
            side=move.side,
            fenBefore=move.before_fen,
            fenAfter=move.after_fen,
            playedMove=move.played_move,
        ),
        positionBefore=move.position_facts,
        positionAfter=move.position_facts_after,
        playedMoveContinuation=move.actual_move_line,
        candidateLines=move.candidate_lines,
    )


@app.post(
    "/api/professional-analysis",
    response_model=ProfessionalAnalysisResponse,
    response_model_exclude_none=True,
)
async def professional_analysis(request: MoveExplanationRequest) -> ProfessionalAnalysisResponse:
    moves = game_cache.get(request.analysis_id)
    if moves is None:
        raise HTTPException(status_code=404, detail="本次分析缓存已过期，请重新分析棋谱")
    if request.move_index > len(moves):
        raise HTTPException(status_code=404, detail="找不到这一步的事实数据")
    move = moves[request.move_index - 1]
    depth = max((line.depth for line in move.candidate_lines), default=settings.game_analysis_depth)
    cache_key = professional_cache_key(
        move,
        stockfish_version="Stockfish 18",
        stockfish_depth=depth,
    )
    cached = professional_cache.get(cache_key)
    if cached is not None:
        professional_cache.move_to_end(cache_key)
        return ProfessionalAnalysisResponse(
            analysis=cached.analysis,
            complexity_reasons=cached.complexity_reasons,
            validation_warnings=cached.validation_warnings,
            usage=cached.usage,
            cached=True,
        )

    created_task = False
    try:
        task = professional_tasks.get(cache_key)
        if task is None:
            task = asyncio.create_task(professional_service.analyze(move))
            professional_tasks[cache_key] = task
            created_task = True
        generated = await asyncio.shield(task)
        professional_cache[cache_key] = generated
        professional_cache.move_to_end(cache_key)
        while len(professional_cache) > MAX_CACHED_PROFESSIONAL_ANALYSES:
            professional_cache.popitem(last=False)
        return ProfessionalAnalysisResponse(
            analysis=generated.analysis,
            complexity_reasons=generated.complexity_reasons,
            validation_warnings=generated.validation_warnings,
            usage=generated.usage,
            cached=not created_task,
        )
    except (httpx.HTTPError, RuntimeError) as exc:
        logger.warning("Professional analysis unavailable: %s", exc)
        return ProfessionalAnalysisResponse(
            warning=f"专业分析暂不可用：{exc}",
            cached=False,
        )
    except Exception:
        logger.exception("Unexpected professional analysis error")
        return ProfessionalAnalysisResponse(
            warning="专业分析暂不可用，但Stockfish事实包仍然有效",
            cached=False,
        )
    finally:
        task = professional_tasks.get(cache_key)
        if task is not None and task.done():
            professional_tasks.pop(cache_key, None)
