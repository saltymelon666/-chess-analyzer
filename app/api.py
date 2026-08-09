from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict
from uuid import uuid4

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from .ai_explainer import DeepSeekExplainer
from .analysis_report import (
    AnalysisReportResponse,
    GeneratedAnalysisReport,
    build_analysis_report,
)
from .book_ground_truth import BookGroundTruthRepository
from .chess_facts import build_engine_fact_package, build_move_fact_package
from .config import load_settings
from .config import OFFICIAL_DEEPSEEK_BASE_URL
from .deepseek_connection import check_deepseek_connection
from .engine import StockfishService
from .endgame_knowledge import (
    EndgameKnowledgeRepository,
    EndgameLookupRequest,
    EndgameLookupResponse,
)
from .game_review import analyze_pgn
from .narrative_generator import NarrativeGenerator
from .opening_knowledge import (
    OpeningKnowledgeRepository,
    OpeningLookupRequest,
    OpeningLookupResponse,
    OpeningPresentation,
)
from .position_facts import extract_position_facts
from .professional_analysis import ProfessionalAnalysisService, professional_cache_key
from .strategic_plans import StrategicPlanAnalyzer
from .threat_analysis import ThreatAnalyzer
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
    ProfessionalBookReference,
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
narrative_generator = NarrativeGenerator(
    api_key=settings.deepseek_api_key,
    base_url=settings.deepseek_base_url,
    model=settings.deepseek_model,
    timeout_seconds=settings.deepseek_timeout_seconds,
)
threat_analyzer = ThreatAnalyzer()
strategic_plan_analyzer = StrategicPlanAnalyzer()
book_ground_truth = BookGroundTruthRepository()
opening_knowledge = OpeningKnowledgeRepository()
endgame_knowledge = EndgameKnowledgeRepository()
game_cache: OrderedDict[str, list[MoveReview]] = OrderedDict()
explanation_cache: dict[tuple[str, int], GeneratedMoveExplanation | str] = {}
explanation_tasks: dict[tuple[str, int], asyncio.Task[GeneratedMoveExplanation | str]] = {}
professional_cache: OrderedDict[str, GeneratedProfessionalAnalysis] = OrderedDict()
professional_tasks: dict[str, asyncio.Task[GeneratedProfessionalAnalysis]] = {}
analysis_report_cache: OrderedDict[tuple[str, int], GeneratedAnalysisReport] = OrderedDict()
analysis_report_tasks: dict[
    tuple[str, int],
    asyncio.Task[GeneratedAnalysisReport],
] = {}
MAX_CACHED_GAMES = 20
MAX_CACHED_PROFESSIONAL_ANALYSES = 100
MAX_CACHED_ANALYSIS_REPORTS = 100

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


@app.post(
    "/api/opening-lookup",
    response_model=OpeningLookupResponse,
    response_model_by_alias=True,
)
async def opening_lookup(request: OpeningLookupRequest) -> OpeningLookupResponse:
    """Return deterministic opening identity without Stockfish or DeepSeek."""
    try:
        return opening_knowledge.lookup(pgn=request.pgn, fen=request.fen)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        logger.error("Opening catalog unavailable: %s", exc)
        raise HTTPException(status_code=503, detail="开局目录暂不可用") from exc


@app.post(
    "/api/endgame-lookup",
    response_model=EndgameLookupResponse,
    response_model_by_alias=True,
)
async def endgame_lookup(request: EndgameLookupRequest) -> EndgameLookupResponse:
    """Return an exact tablebase-verified book ending without DeepSeek."""
    try:
        return endgame_knowledge.lookup(request.fen)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        logger.error("Endgame knowledge unavailable: %s", exc)
        raise HTTPException(status_code=503, detail="残局知识数据暂不可用") from exc


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

    fact_package = build_engine_fact_package(request.fen, engine_result)
    threat_package = None
    try:
        threat_package = await threat_analyzer.analyze(
            fact_package,
            stockfish=stockfish,
        )
        fact_package.threats = threat_package.threats
    except Exception:
        logger.exception("Threat analysis failed; continuing with an empty package")
    try:
        position_facts = extract_position_facts(
            request.fen,
            candidate_lines=[],
            actual_move_line=None,
            tactics=[],
            namespace="review-position",
        )
        plan_package = strategic_plan_analyzer.analyze(
            fact_package,
            position_facts=position_facts,
            threat_package=threat_package,
        )
        fact_package.plans = plan_package.plans
    except Exception:
        logger.exception("Strategic plan analysis failed; continuing with an empty package")

    explanation: str | None = None
    warning: str | None = None
    try:
        explain_facts = getattr(explainer, "explain_fact_package", None)
        if callable(explain_facts):
            explanation = await explain_facts(fact_package)
        else:
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
        threats=fact_package.threats,
        plans=[
            plan for plan in fact_package.plans
            if plan.confidence == "high"
        ],
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
        for cache_key in [key for key in analysis_report_cache if key[0] == expired_id]:
            analysis_report_cache.pop(cache_key, None)
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
    opening_context = _professional_opening_context(moves, request.move_index)
    book_references = _professional_book_references(move.before_fen)
    depth = max((line.depth for line in move.candidate_lines), default=settings.game_analysis_depth)
    cache_key = professional_cache_key(
        move,
        stockfish_version="Stockfish 18",
        stockfish_depth=depth,
        opening_id=opening_context.opening_id if opening_context else None,
    )
    cached = professional_cache.get(cache_key)
    if cached is not None:
        professional_cache.move_to_end(cache_key)
        return ProfessionalAnalysisResponse(
            analysis=cached.analysis,
            openingContext=opening_context,
            book_references=book_references,
            complexity_reasons=cached.complexity_reasons,
            validation_warnings=cached.validation_warnings,
            usage=cached.usage,
            cached=True,
        )

    created_task = False
    try:
        task = professional_tasks.get(cache_key)
        if task is None:
            task = asyncio.create_task(
                _generate_professional_analysis(move, opening_context=opening_context)
            )
            professional_tasks[cache_key] = task
            created_task = True
        generated = await asyncio.shield(task)
        professional_cache[cache_key] = generated
        professional_cache.move_to_end(cache_key)
        while len(professional_cache) > MAX_CACHED_PROFESSIONAL_ANALYSES:
            professional_cache.popitem(last=False)
        return ProfessionalAnalysisResponse(
            analysis=generated.analysis,
            openingContext=opening_context,
            book_references=book_references,
            complexity_reasons=generated.complexity_reasons,
            validation_warnings=generated.validation_warnings,
            usage=generated.usage,
            cached=not created_task,
        )
    except (httpx.HTTPError, RuntimeError) as exc:
        logger.warning("Professional analysis unavailable: %s", exc)
        return ProfessionalAnalysisResponse(
            openingContext=opening_context,
            book_references=book_references,
            warning=f"专业分析暂不可用：{exc}",
            cached=False,
        )
    except Exception:
        logger.exception("Unexpected professional analysis error")
        return ProfessionalAnalysisResponse(
            openingContext=opening_context,
            book_references=book_references,
            warning="专业分析暂不可用，但Stockfish事实包仍然有效",
            cached=False,
        )
    finally:
        task = professional_tasks.get(cache_key)
        if task is not None and task.done():
            professional_tasks.pop(cache_key, None)


@app.post(
    "/api/analysis-report",
    response_model=AnalysisReportResponse,
    response_model_exclude_none=True,
)
async def analysis_report(request: MoveExplanationRequest) -> AnalysisReportResponse:
    """Generate the independent Phase 4 report without invoking the legacy LLM path."""
    moves = game_cache.get(request.analysis_id)
    if moves is None:
        raise HTTPException(status_code=404, detail="本次分析缓存已过期，请重新分析棋谱")
    if request.move_index > len(moves):
        raise HTTPException(status_code=404, detail="找不到这一步的事实数据")

    cache_key = (request.analysis_id, request.move_index)
    cached = analysis_report_cache.get(cache_key)
    if cached is not None:
        analysis_report_cache.move_to_end(cache_key)
        return AnalysisReportResponse(
            report=cached.report,
            validation_warnings=cached.validation_warnings,
            usage=cached.usage,
            cached=True,
        )

    move = moves[request.move_index - 1]
    created_task = False
    try:
        task = analysis_report_tasks.get(cache_key)
        if task is None:
            task = asyncio.create_task(_generate_analysis_report(move))
            analysis_report_tasks[cache_key] = task
            created_task = True
        generated = await asyncio.shield(task)
        analysis_report_cache[cache_key] = generated
        analysis_report_cache.move_to_end(cache_key)
        while len(analysis_report_cache) > MAX_CACHED_ANALYSIS_REPORTS:
            analysis_report_cache.popitem(last=False)
        return AnalysisReportResponse(
            report=generated.report,
            validation_warnings=generated.validation_warnings,
            usage=generated.usage,
            cached=not created_task,
        )
    except Exception as exc:
        logger.exception("Unexpected analysis report error")
        raise HTTPException(status_code=503, detail="专业复盘报告暂不可用") from exc
    finally:
        task = analysis_report_tasks.get(cache_key)
        if task is not None and task.done():
            analysis_report_tasks.pop(cache_key, None)


async def _generate_analysis_report(move: MoveReview) -> GeneratedAnalysisReport:
    fact_package = build_move_fact_package(move)
    threat_package = await threat_analyzer.analyze(
        fact_package,
        stockfish=stockfish,
    )
    fact_package.threats = threat_package.threats
    strategic_plan_package = strategic_plan_analyzer.analyze(
        fact_package,
        position_facts=move.position_facts,
        threat_package=threat_package,
    )
    fact_package.plans = strategic_plan_package.plans
    package = build_analysis_report(
        move,
        fact_package,
        threat_package,
        strategic_plan_package,
    )
    return await narrative_generator.generate(package)


async def _generate_professional_analysis(
    move: MoveReview,
    *,
    opening_context: OpeningPresentation | None = None,
) -> GeneratedProfessionalAnalysis:
    fact_package = build_move_fact_package(move)
    threat_package = await threat_analyzer.analyze(
        fact_package,
        stockfish=stockfish,
    )
    kwargs = {"threat_package": threat_package}
    if opening_context is not None:
        kwargs["opening_context"] = opening_context
    return await professional_service.analyze(move, **kwargs)


def _professional_opening_context(
    moves: list[MoveReview],
    move_index: int,
) -> OpeningPresentation | None:
    """Recognize the position after the selected node using only verified moves."""
    selected = moves[:move_index]
    if not selected:
        return None
    try:
        return opening_knowledge.presentation_for_moves(
            [item.played_move.uci for item in selected],
            initial_fen=selected[0].before_fen,
        )
    except (ValueError, RuntimeError, OSError) as exc:
        logger.warning("Optional opening recognition unavailable: %s", exc)
        return None


def _professional_book_references(fen: str) -> list[ProfessionalBookReference]:
    """Return source prose only for an exact legal-state match."""
    try:
        package = book_ground_truth.lookup_exact(fen)
    except Exception as exc:
        logger.warning("Optional book ground truth unavailable: %s", exc)
        return []
    return [
        ProfessionalBookReference(
            positionId=case.position_id,
            sourceTitle=case.source_title,
            author=case.author,
            sourceUrl=case.source_url,
            locator=case.locator,
            annotatedMove=case.annotated_move_san,
            originalComment=case.reference_explanation,
            extractionStatus=case.extraction_status,
            authorityScope=case.authority_scope,
        )
        for case in package.cases
    ]
