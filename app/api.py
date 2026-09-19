from __future__ import annotations

import asyncio
import hmac
import inspect
import logging
import time
from collections import OrderedDict
from datetime import date as calendar_date, datetime, time as datetime_time, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import httpx
import chess
import chess.pgn
from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from pydantic import BaseModel

from .accounts import (
    AccountStore,
    AuthResponse,
    CoachMemory,
    LoginRequest,
    ManualPaymentCreateRequest,
    ManualPaymentRequest,
    ManualPaymentReviewRequest,
    OrderRequest,
    PaymentCallback,
    PaymentOrder,
    RegisterRequest,
    UserAccount,
    callback_signature,
)

from .ai_explainer import DeepSeekExplainer
from .analytics import (
    AnalyticsEventRequest,
    AnalyticsEventResponse,
    AnalyticsStore,
    DailyStatistics,
    FeedbackSummary,
    RecentAnalysis,
    RecentFeedback,
    StatisticsSummary,
)
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
from .engine import StockfishBusyError, StockfishService
from .endgame_knowledge import (
    EndgameKnowledgeRepository,
    EndgameLookupRequest,
    EndgameLookupResponse,
)
from .game_review import analyze_pgn, parse_pgn_facts
from .narrative_generator import NarrativeGenerator
from .opening_knowledge import (
    OpeningKnowledgeRepository,
    OpeningLookupRequest,
    OpeningLookupResponse,
    OpeningPresentation,
)
from .position_facts import extract_position_facts
from .prompt_compression import (
    build_coach_memory,
    compress_coach_memories,
    compress_game_context,
)
from .professional_analysis import ProfessionalAnalysisService, professional_cache_key
from .payments import PaymentProviderError, PaymentService, PaymentSetupError
from .unified_book_knowledge import UnifiedBookKnowledgeRepository
from .request_protection import PUBLIC_BETA_POLICIES, RequestProtector
from .strategic_plans import StrategicPlanAnalyzer
from .threat_analysis import ThreatAnalyzer
from .models import (
    GameReviewRequest,
    GameReviewResponse,
    LocalGameStartResponse,
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
    VerifiedMoveResponse,
)


logger = logging.getLogger(__name__)
ADMIN_PAGE_PATH = Path(__file__).resolve().parent.parent / "admin.html"
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
unified_book_knowledge = UnifiedBookKnowledgeRepository()
professional_service = ProfessionalAnalysisService(
    api_key=settings.deepseek_api_key,
    base_url=settings.deepseek_base_url,
    model=settings.deepseek_model,
    timeout_seconds=settings.deepseek_timeout_seconds,
    book_knowledge=unified_book_knowledge,
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
try:
    analytics_store: AnalyticsStore | None = AnalyticsStore(
        settings.analytics_database_url or settings.analytics_database_path,
        input_price_per_million=settings.deepseek_input_price_per_million,
        output_price_per_million=settings.deepseek_output_price_per_million,
    )
except Exception:
    analytics_store = None
    logger.exception("Analytics database unavailable; core analysis will continue")
try:
    account_store: AccountStore | None = AccountStore(
        settings.analytics_database_url or settings.analytics_database_path
    )
except Exception:
    account_store = None
    logger.exception("Account database unavailable")
    if settings.billing_enforced:
        raise
request_protector = RequestProtector()
payment_service = PaymentService(settings)
game_cache: OrderedDict[str, list[MoveReview]] = OrderedDict()
game_cache_owners: dict[str, str] = {}
local_game_sessions: OrderedDict[str, tuple[str, list[dict[str, object]]]] = OrderedDict()
local_game_session_owners: dict[str, str] = {}
local_verified_moves: dict[tuple[str, int], MoveReview] = {}
local_verify_tasks: dict[tuple[str, int], asyncio.Task[MoveReview]] = {}
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
MAX_LOCAL_GAME_SESSIONS = 40
MAX_CACHED_PROFESSIONAL_ANALYSES = 100
MAX_CACHED_ANALYSIS_REPORTS = 100


class AdminProtectionPolicy(BaseModel):
    label: str
    per_minute: int
    per_day: int
    global_scope: bool


class AdminConfiguration(BaseModel):
    environment: str
    analytics_persistent_storage: bool
    admin_key_configured: bool
    token_pricing_configured: bool
    deepseek_configured: bool
    deepseek_model: str
    game_analysis_max_plies: int
    billing_enforced: bool
    payment_channel_configured: bool


class ManualPaymentConfiguration(BaseModel):
    enabled: bool
    wechat_qr_url: str | None = None
    alipay_qr_url: str | None = None


class AdminDashboard(BaseModel):
    statistics: DailyStatistics
    historical_statistics: StatisticsSummary
    recent_analyses: list[RecentAnalysis]
    recent_feedback: list[RecentFeedback]
    feedback_summary: FeedbackSummary
    protection_policies: list[AdminProtectionPolicy]
    configuration: AdminConfiguration


async def _record_analytics(method_name: str, *args, **kwargs) -> None:
    """Keep optional beta telemetry from interrupting chess analysis."""
    if analytics_store is None:
        return
    try:
        method = getattr(analytics_store, method_name)
        await asyncio.to_thread(method, *args, **kwargs)
    except Exception:
        logger.exception("Analytics write failed: %s", method_name)

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
    allow_headers=["Content-Type", "X-Admin-Key", "Authorization", "X-Payment-Signature"],
)


def _bearer_token(authorization: str | None) -> str | None:
    if not authorization or not isinstance(authorization, str):
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="登录凭证格式不正确")
    return token.strip()


def _current_account(
    authorization: str | None,
    *,
    required: bool | None = None,
) -> UserAccount | None:
    must_login = settings.billing_enforced if required is None else required
    token = _bearer_token(authorization)
    if token is None:
        if must_login:
            raise HTTPException(status_code=401, detail="请先注册或登录")
        return None
    if account_store is None:
        raise HTTPException(status_code=503, detail="账号服务暂不可用")
    account = account_store.account_for_token(token)
    if account is None:
        raise HTTPException(status_code=401, detail="登录已过期，请重新登录")
    return account


def _owned_game(analysis_id: str, account: UserAccount | None) -> list[MoveReview]:
    moves = game_cache.get(analysis_id)
    if moves is None:
        raise HTTPException(status_code=404, detail="本次分析缓存已过期，请重新分析棋谱")
    owner = game_cache_owners.get(analysis_id)
    if owner is not None and (account is None or owner != account.user_id):
        raise HTTPException(status_code=404, detail="找不到这次棋局分析")
    return moves


def _owned_local_session(
    analysis_id: str,
    account: UserAccount | None,
) -> tuple[str, list[dict[str, object]]]:
    session = local_game_sessions.get(analysis_id)
    if session is None:
        raise HTTPException(status_code=404, detail="本次本机分析任务已过期，请重新分析棋谱")
    owner = local_game_session_owners.get(analysis_id)
    if owner is not None and (account is None or owner != account.user_id):
        raise HTTPException(status_code=404, detail="找不到这次棋局分析")
    local_game_sessions.move_to_end(analysis_id)
    return session


def _owned_move(
    analysis_id: str,
    move_index: int,
    account: UserAccount | None,
) -> tuple[MoveReview, list[MoveReview]]:
    if analysis_id in local_game_sessions:
        _owned_local_session(analysis_id, account)
        move = local_verified_moves.get((analysis_id, move_index))
        if move is None:
            raise HTTPException(status_code=409, detail="请先让后台确认这个局面")
        verified = [
            item for (item_analysis_id, _), item in local_verified_moves.items()
            if item_analysis_id == analysis_id
        ]
        verified.sort(key=lambda item: item.index)
        return move, verified
    moves = _owned_game(analysis_id, account)
    if move_index > len(moves):
        raise HTTPException(status_code=404, detail="找不到这一步的分析数据")
    return moves[move_index - 1], moves


async def _commercial_event(user_id: str, event: str, analysis_id: str | None = None) -> None:
    await _record_analytics(
        "record_event",
        AnalyticsEventRequest(visitor_id=user_id, event=event, analysis_id=analysis_id),
    )


@app.middleware("http")
async def protect_public_beta(request: Request, call_next):
    path = request.url.path
    client_key = request_protector.client_key(request)
    if path == "/api/event":
        policy = PUBLIC_BETA_POLICIES["event"]
        allowed = request_protector.allow(
            policy.scope,
            client_key,
            per_minute=policy.per_minute,
            per_day=policy.per_day,
        ).allowed
    elif path in {"/api/auth/register", "/api/auth/login"}:
        policy = PUBLIC_BETA_POLICIES["auth"]
        allowed = request_protector.allow(
            policy.scope,
            client_key,
            per_minute=policy.per_minute,
            per_day=policy.per_day,
        ).allowed
    elif path in {"/api/billing/orders", "/api/billing/manual-requests"}:
        policy = PUBLIC_BETA_POLICIES["billing"]
        allowed = request_protector.allow(
            policy.scope,
            client_key,
            per_minute=policy.per_minute,
            per_day=policy.per_day,
        ).allowed
    elif path in {
        "/api/game-review",
        "/api/game-review/start-local",
    }:
        policy = PUBLIC_BETA_POLICIES["game-review"]
        allowed = request_protector.allow(
            policy.scope,
            client_key,
            per_minute=policy.per_minute,
            per_day=policy.per_day,
        ).allowed
    elif path == "/api/game-review/position":
        policy = PUBLIC_BETA_POLICIES["position-verify"]
        allowed = request_protector.allow(
            policy.scope,
            client_key,
            per_minute=policy.per_minute,
            per_day=policy.per_day,
        ).allowed
    elif path in {
        "/api/review",
        "/api/move-explanation",
        "/api/professional-analysis",
        "/api/analysis-report",
    }:
        policy = PUBLIC_BETA_POLICIES["deepseek"]
        global_policy = PUBLIC_BETA_POLICIES["deepseek-global"]
        allowed = request_protector.allow(
            policy.scope,
            client_key,
            per_minute=policy.per_minute,
            per_day=policy.per_day,
        ).allowed and request_protector.allow_global(
            global_policy.scope,
            per_minute=global_policy.per_minute,
        ).allowed
    else:
        allowed = True
    if not allowed:
        return JSONResponse(
            status_code=429,
            content={"detail": "当前请求较多，请稍后再试"},
            headers={"Retry-After": "60"},
        )
    return await call_next(request)


@app.get("/api/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(
        status="ok" if stockfish.available() else "degraded",
        stockfish="available" if stockfish.available() else "missing",
        deepseek_configured=explainer.configured,
        deepseek_key_format_valid=deepseek_key_format_valid,
        deepseek_model=settings.deepseek_model,
    )


@app.get("/admin", include_in_schema=False)
@app.get("/admin.html", include_in_schema=False)
async def backend_admin_page() -> FileResponse:
    if not ADMIN_PAGE_PATH.is_file():
        raise HTTPException(status_code=503, detail="运营后台页面暂不可用")
    return FileResponse(
        ADMIN_PAGE_PATH,
        media_type="text/html",
        headers={"Cache-Control": "no-store"},
    )


@app.get("/runtime-config.js", include_in_schema=False)
async def backend_runtime_config() -> Response:
    return Response(
        'window.CHESS_API_BASE_URL = "";\n',
        media_type="application/javascript",
        headers={"Cache-Control": "no-store"},
    )


@app.post("/api/auth/register", response_model=AuthResponse)
async def register(request: RegisterRequest) -> AuthResponse:
    if account_store is None:
        raise HTTPException(status_code=503, detail="账号服务暂不可用")
    try:
        response = await asyncio.to_thread(account_store.register, request.phone, request.password)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await _commercial_event(response.account.user_id, "signup")
    return response


@app.post("/api/auth/login", response_model=AuthResponse)
async def login(request: LoginRequest) -> AuthResponse:
    if account_store is None:
        raise HTTPException(status_code=503, detail="账号服务暂不可用")
    try:
        return await asyncio.to_thread(account_store.login, request.identifier, request.password)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


@app.get("/api/auth/me", response_model=UserAccount)
async def auth_me(authorization: str | None = Header(default=None)) -> UserAccount:
    account = _current_account(authorization, required=True)
    assert account is not None
    return account


@app.post("/api/auth/logout", status_code=204)
async def logout(authorization: str | None = Header(default=None)) -> None:
    account = _current_account(authorization, required=True)
    token = _bearer_token(authorization)
    assert account is not None and token is not None and account_store is not None
    await asyncio.to_thread(account_store.logout, token)


@app.get("/api/coach/history", response_model=list[CoachMemory])
async def coach_history(
    authorization: str | None = Header(default=None),
) -> list[CoachMemory]:
    account = _current_account(authorization, required=True)
    assert account is not None and account_store is not None
    return await asyncio.to_thread(
        account_store.recent_coach_memories, account.user_id, 5
    )


@app.post("/api/billing/orders", response_model=PaymentOrder)
async def create_payment_order(
    request: OrderRequest,
    http_request: Request,
    authorization: str | None = Header(default=None),
) -> PaymentOrder:
    account = _current_account(authorization, required=True)
    assert account is not None and account_store is not None
    provider = request.payment_provider
    if provider is not None and not payment_service.configured(provider):
        raise HTTPException(status_code=503, detail=f"{'微信支付' if provider == 'wechat' else '支付宝'}尚未配置")
    order = await asyncio.to_thread(
        account_store.create_order,
        account.user_id,
        request.membership_type,
        settings.payment_checkout_base_url if provider is None else "",
        provider or "internal",
    )
    if provider is None:
        return order
    try:
        if provider == "wechat":
            checkout = await payment_service.wechat.create_checkout(
                order_id=order.order_id,
                amount_fen=order.amount_fen,
                membership_type=order.membership_type,
                client_type=request.client_type,
                client_ip=http_request.client.host if http_request.client else "127.0.0.1",
            )
        else:
            checkout = payment_service.alipay.create_checkout(
                order_id=order.order_id,
                amount_fen=order.amount_fen,
                membership_type=order.membership_type,
                client_type=request.client_type,
            )
    except PaymentSetupError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except PaymentProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    order.checkout_url = checkout.checkout_url
    order.qr_code_url = checkout.qr_code_url
    return order


@app.get("/api/billing/orders/{order_id}", response_model=PaymentOrder)
async def get_payment_order(
    order_id: str,
    authorization: str | None = Header(default=None),
) -> PaymentOrder:
    account = _current_account(authorization, required=True)
    assert account is not None and account_store is not None
    try:
        return await asyncio.to_thread(account_store.payment_order, order_id, account.user_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _public_qr_url(value: str) -> str | None:
    if not value:
        return None
    if value.startswith("/") or value.startswith("https://"):
        return value
    return None


@app.get("/api/billing/manual/config", response_model=ManualPaymentConfiguration)
async def manual_payment_config() -> ManualPaymentConfiguration:
    wechat = _public_qr_url(settings.manual_wechat_qr_url)
    alipay = _public_qr_url(settings.manual_alipay_qr_url)
    return ManualPaymentConfiguration(
        enabled=bool(wechat or alipay),
        wechat_qr_url=wechat,
        alipay_qr_url=alipay,
    )


@app.post("/api/billing/manual-requests", response_model=ManualPaymentRequest)
async def create_manual_payment_request(
    request: ManualPaymentCreateRequest,
    authorization: str | None = Header(default=None),
) -> ManualPaymentRequest:
    account = _current_account(authorization, required=True)
    assert account is not None and account_store is not None
    qr_url = (
        _public_qr_url(settings.manual_wechat_qr_url)
        if request.payment_provider == "wechat"
        else _public_qr_url(settings.manual_alipay_qr_url)
    )
    if qr_url is None:
        raise HTTPException(status_code=503, detail="该收款方式尚未配置")
    try:
        return await asyncio.to_thread(
            account_store.create_manual_payment_request,
            account.user_id,
            request.phone,
            request.membership_type,
            request.payment_provider,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/billing/manual-requests/me", response_model=list[ManualPaymentRequest])
async def my_manual_payment_requests(
    authorization: str | None = Header(default=None),
) -> list[ManualPaymentRequest]:
    account = _current_account(authorization, required=True)
    assert account is not None and account_store is not None
    return await asyncio.to_thread(
        account_store.manual_payment_requests,
        user_id=account.user_id,
        limit=20,
    )


def _require_payment_admin(x_admin_key: str | None) -> None:
    configured_key = settings.admin_statistics_key
    if not configured_key:
        raise HTTPException(status_code=503, detail="后台审核密钥尚未配置")
    if not x_admin_key or not hmac.compare_digest(x_admin_key, configured_key):
        raise HTTPException(status_code=401, detail="后台访问凭据无效")


@app.get("/api/admin/manual-payments", response_model=list[ManualPaymentRequest])
async def admin_manual_payments(
    status: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=200),
    x_admin_key: str | None = Header(default=None),
) -> list[ManualPaymentRequest]:
    _require_payment_admin(x_admin_key)
    assert account_store is not None
    try:
        return await asyncio.to_thread(
            account_store.manual_payment_requests,
            status=status,
            limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post(
    "/api/admin/manual-payments/{request_id}/review",
    response_model=ManualPaymentRequest,
)
async def review_manual_payment(
    request_id: str,
    review: ManualPaymentReviewRequest,
    x_admin_key: str | None = Header(default=None),
) -> ManualPaymentRequest:
    _require_payment_admin(x_admin_key)
    assert account_store is not None
    try:
        result, newly_processed, is_renewal = await asyncio.to_thread(
            account_store.review_manual_payment,
            request_id,
            approve=review.action == "approve",
            note=review.note,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if newly_processed:
        if review.action == "approve":
            await _commercial_event(result.user_id, "payment_success")
            await _commercial_event(
                result.user_id,
                "monthly_purchase" if result.membership_type == "monthly" else "yearly_purchase",
            )
            if is_renewal:
                await _commercial_event(result.user_id, "subscription_renew")
        else:
            await _commercial_event(result.user_id, "payment_failed")
    return result


async def _apply_verified_provider_payment(provider: str, verified) -> None:
    assert account_store is not None
    callback = PaymentCallback(
        order_id=verified.order_id,
        status=verified.status,
        provider_transaction_id=verified.provider_transaction_id,
    )
    user_id, is_renewal, newly_processed, membership_type = await asyncio.to_thread(
        account_store.apply_payment_callback,
        callback,
        expected_provider=provider,
        expected_amount_fen=verified.amount_fen,
        expected_currency=verified.currency,
    )
    if not newly_processed:
        return
    await _commercial_event(user_id, "payment_success")
    await _commercial_event(
        user_id,
        "monthly_purchase" if membership_type == "monthly" else "yearly_purchase",
    )
    if is_renewal:
        await _commercial_event(user_id, "subscription_renew")


@app.post("/api/billing/wechat/notify")
async def wechat_payment_notify(request: Request):
    try:
        verified = payment_service.wechat.verify_notification(
            await request.body(), {key.lower(): value for key, value in request.headers.items()}
        )
        await _apply_verified_provider_payment("wechat", verified)
    except PaymentSetupError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except (PaymentProviderError, ValueError) as exc:
        return JSONResponse(
            status_code=400,
            content={"code": "FAIL", "message": str(exc)},
        )
    return {"code": "SUCCESS", "message": "成功"}


@app.post("/api/billing/alipay/notify", response_class=PlainTextResponse)
async def alipay_payment_notify(request: Request):
    from urllib.parse import parse_qsl

    try:
        body = (await request.body()).decode("utf-8", errors="strict")
        pairs = parse_qsl(body, keep_blank_values=True)
        if len({key for key, _ in pairs}) != len(pairs):
            raise ValueError("支付宝通知包含重复字段")
        parameters = dict(pairs)
        verified = payment_service.alipay.verify_notification(parameters)
        await _apply_verified_provider_payment("alipay", verified)
    except (PaymentSetupError, PaymentProviderError, ValueError, UnicodeError):
        return PlainTextResponse("failure", status_code=400)
    return PlainTextResponse("success")


@app.post("/api/billing/payment-callback", status_code=204)
async def payment_callback(
    callback: PaymentCallback,
    x_payment_signature: str | None = Header(default=None),
) -> None:
    if settings.environment == "production":
        raise HTTPException(status_code=404, detail="接口不存在")
    secret = settings.payment_callback_secret
    if not secret:
        raise HTTPException(status_code=503, detail="支付回调尚未配置")
    expected = callback_signature(secret, callback)
    if not x_payment_signature or not hmac.compare_digest(x_payment_signature, expected):
        raise HTTPException(status_code=401, detail="支付回调签名无效")
    assert account_store is not None
    try:
        user_id, is_renewal, newly_processed, membership_type = await asyncio.to_thread(
            account_store.apply_payment_callback,
            callback,
            expected_provider="internal",
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if newly_processed:
        await _commercial_event(
            user_id,
            "payment_success" if callback.status == "paid" else "payment_failed",
        )
        if callback.status == "paid":
            await _commercial_event(
                user_id,
                "monthly_purchase" if membership_type == "monthly" else "yearly_purchase",
            )
        if callback.status == "paid" and is_renewal:
            await _commercial_event(user_id, "subscription_renew")


@app.post("/api/billing/cancel", response_model=UserAccount)
async def cancel_subscription(
    authorization: str | None = Header(default=None),
) -> UserAccount:
    account = _current_account(authorization, required=True)
    assert account is not None and account_store is not None
    cancelled = await asyncio.to_thread(
        account_store.cancel_subscription, account.user_id
    )
    await _commercial_event(account.user_id, "subscription_cancel")
    return cancelled


@app.post("/api/event", response_model=AnalyticsEventResponse, status_code=202)
async def record_event(event: AnalyticsEventRequest) -> AnalyticsEventResponse:
    await _record_analytics("record_event", event)
    return AnalyticsEventResponse()


@app.get("/api/admin/statistics", response_model=DailyStatistics)
async def admin_statistics(
    x_admin_key: str | None = Header(default=None),
    day: str | None = Query(default=None, alias="date"),
) -> DailyStatistics:
    store = _authorized_analytics_store(x_admin_key)
    requested_day = _admin_day(day)
    return await asyncio.to_thread(store.daily_statistics, requested_day)


@app.get("/api/admin/dashboard", response_model=AdminDashboard)
async def admin_dashboard(
    x_admin_key: str | None = Header(default=None),
    day: str | None = Query(default=None, alias="date"),
    limit: int = Query(default=100, ge=1, le=1000),
) -> AdminDashboard:
    store = _authorized_analytics_store(x_admin_key)
    requested_day = _admin_day(day)
    statistics, historical, recent, feedback, feedback_totals = await asyncio.gather(
        asyncio.to_thread(store.daily_statistics, requested_day),
        asyncio.to_thread(store.all_time_statistics),
        asyncio.to_thread(store.analysis_history, limit=limit),
        asyncio.to_thread(store.feedback_history, limit=limit),
        asyncio.to_thread(store.feedback_summary),
    )
    policies = [
        AdminProtectionPolicy(
            label=policy.label,
            per_minute=policy.per_minute,
            per_day=policy.per_day,
            global_scope=policy.global_scope,
        )
        for policy in PUBLIC_BETA_POLICIES.values()
    ]
    return AdminDashboard(
        statistics=statistics,
        historical_statistics=historical,
        recent_analyses=recent,
        recent_feedback=feedback,
        feedback_summary=feedback_totals,
        protection_policies=policies,
        configuration=AdminConfiguration(
            environment=settings.environment,
            analytics_persistent_storage=settings.analytics_persistent_storage,
            admin_key_configured=bool(settings.admin_statistics_key),
            token_pricing_configured=(
                settings.deepseek_input_price_per_million > 0
                or settings.deepseek_output_price_per_million > 0
            ),
            deepseek_configured=explainer.configured,
            deepseek_model=settings.deepseek_model,
            game_analysis_max_plies=settings.game_analysis_max_plies,
            billing_enforced=settings.billing_enforced,
            payment_channel_configured=payment_service.any_configured,
        ),
    )


def _authorized_analytics_store(x_admin_key: str | None) -> AnalyticsStore:
    configured_key = settings.admin_statistics_key
    if configured_key:
        if not x_admin_key or not hmac.compare_digest(x_admin_key, configured_key):
            raise HTTPException(status_code=401, detail="后台访问凭据无效")
    elif settings.environment != "development":
        raise HTTPException(status_code=503, detail="后台统计凭据尚未配置")
    if analytics_store is None:
        raise HTTPException(status_code=503, detail="后台统计暂不可用")
    return analytics_store


def _admin_day(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = calendar_date.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="统计日期格式必须为 YYYY-MM-DD") from exc
    china_timezone = timezone(timedelta(hours=8), name="Asia/Shanghai")
    return datetime.combine(parsed, datetime_time.min, tzinfo=china_timezone)


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
async def review(
    request: ReviewRequest,
    authorization: str | None = Header(default=None),
) -> ReviewResponse:
    account = _current_account(authorization)
    if settings.billing_enforced and (
        account is None or account.membership_status != "active"
    ):
        if account is not None:
            await _commercial_event(account.user_id, "paywall_view")
        raise HTTPException(
            status_code=402,
            detail={"code": "subscription_required", "message": "单局面分析包含在会员中，请使用完整免费分析或开通会员"},
        )
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


@app.post("/api/game-review/start-local", response_model=LocalGameStartResponse)
async def start_local_game_review(
    request: GameReviewRequest,
    authorization: str | None = Header(default=None),
) -> LocalGameStartResponse:
    """Validate a PGN and reserve one analysis without running server Stockfish.

    This endpoint only creates the owned session. Browser engine output is
    deliberately not uploaded or trusted. Every opened move is independently
    rebuilt by ``verify_local_game_position`` using the server engine.
    """
    account = _current_account(authorization)
    try:
        facts, _ = parse_pgn_facts(
            request.pgn,
            max_plies=settings.game_analysis_max_plies,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    analysis_id = uuid4().hex if account is not None else (request.analysis_id or uuid4().hex)
    visitor_id = account.user_id if account else (request.visitor_id or f"server_{uuid4().hex}")
    reservation_id: str | None = None
    entitlement_reason: str | None = None
    if account is not None:
        assert account_store is not None
        decision = await asyncio.to_thread(
            account_store.reserve_analysis,
            account.user_id,
            analysis_id,
        )
        if not decision.allowed:
            await _commercial_event(account.user_id, "paywall_view")
            raise HTTPException(
                status_code=402,
                detail={"code": "subscription_required", "message": "免费完整分析已使用，请选择会员方案"},
            )
        reservation_id = decision.reservation_id
        entitlement_reason = decision.reason
        if decision.reason == "free_trial":
            await _commercial_event(account.user_id, "free_analysis_start", analysis_id)

    await _record_analytics("start_analysis", analysis_id, visitor_id, request.pgn)
    local_game_sessions[analysis_id] = (request.pgn, facts)
    if account is not None:
        local_game_session_owners[analysis_id] = account.user_id
    local_game_sessions.move_to_end(analysis_id)
    while len(local_game_sessions) > MAX_LOCAL_GAME_SESSIONS:
        expired_id, _ = local_game_sessions.popitem(last=False)
        local_game_session_owners.pop(expired_id, None)
        for cache_key in [key for key in local_verified_moves if key[0] == expired_id]:
            local_verified_moves.pop(cache_key, None)

    if reservation_id and account_store:
        await asyncio.to_thread(account_store.finish_analysis, reservation_id, success=True)
        if entitlement_reason == "free_trial" and account is not None:
            await _commercial_event(account.user_id, "free_analysis_complete", analysis_id)
    await _record_analytics(
        "finish_analysis",
        analysis_id,
        success=True,
        stockfish_ms=0,
        total_ms=0,
        move_count=len(facts),
    )
    return LocalGameStartResponse(analysis_id=analysis_id, move_count=len(facts))


async def _verify_local_move(
    analysis_id: str,
    move_index: int,
    facts: list[dict[str, object]],
) -> MoveReview:
    fact = facts[move_index - 1]
    board = chess.Board(str(fact["before_fen"]))
    played = fact["played_move"]
    played_uci = getattr(played, "uci", "")
    try:
        move = chess.Move.from_uci(played_uci)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="棋谱中的走法无法复核") from exc
    if move not in board.legal_moves:
        raise HTTPException(status_code=422, detail="棋谱中的走法已无法合法重放")

    one_move_game = chess.pgn.Game()
    one_move_game.setup(board)
    one_move_game.add_variation(move)
    result = await analyze_pgn(
        pgn=str(one_move_game),
        stockfish=stockfish,
        analysis_id=analysis_id,
        depth=settings.game_analysis_depth,
        timeout_seconds=settings.game_analysis_timeout_seconds,
        max_plies=1,
    )
    review = result.moves[0].model_copy(
        update={
            "index": move_index,
            "notation": str(fact["notation"]),
        }
    )
    try:
        review = review.model_copy(
            update={"opening_context": _professional_opening_context([review], 1)}
        )
    except Exception:
        logger.exception("Opening context enrichment failed for verified local move")
    return review


@app.post("/api/game-review/position", response_model=VerifiedMoveResponse)
async def verify_local_game_position(
    request: MoveExplanationRequest,
    authorization: str | None = Header(default=None),
) -> VerifiedMoveResponse:
    account = _current_account(authorization)
    _, facts = _owned_local_session(request.analysis_id, account)
    if request.move_index > len(facts):
        raise HTTPException(status_code=404, detail="找不到这一步的棋盘位置")
    cache_key = (request.analysis_id, request.move_index)
    review = local_verified_moves.get(cache_key)
    if review is None:
        task = local_verify_tasks.get(cache_key)
        if task is None:
            task = asyncio.create_task(
                _verify_local_move(request.analysis_id, request.move_index, facts)
            )
            local_verify_tasks[cache_key] = task
        try:
            review = await asyncio.shield(task)
            local_verified_moves[cache_key] = review
        except asyncio.TimeoutError as exc:
            raise HTTPException(status_code=504, detail="后台确认局面超时，请重试") from exc
        except HTTPException:
            raise
        except Exception as exc:
            logger.exception("Local-scan position verification failed")
            raise HTTPException(status_code=503, detail="后台暂时无法确认这个局面") from exc
        finally:
            running = local_verify_tasks.get(cache_key)
            if running is not None and running.done():
                local_verify_tasks.pop(cache_key, None)

    verified_for_game = [
        item for (item_analysis_id, _), item in local_verified_moves.items()
        if item_analysis_id == request.analysis_id
    ]
    verified_for_game.sort(key=lambda item: item.index)
    if account is not None and account_store is not None and verified_for_game:
        try:
            memory = build_coach_memory(verified_for_game)
            await asyncio.to_thread(
                account_store.save_coach_memory,
                account.user_id,
                request.analysis_id,
                memory,
            )
        except Exception:
            logger.exception("Incremental coach memory persistence failed")
    return VerifiedMoveResponse(
        analysis_id=request.analysis_id,
        move_index=request.move_index,
        review=review,
    )


@app.post("/api/game-review", response_model=GameReviewResponse)
async def game_review(
    request: GameReviewRequest,
    authorization: str | None = Header(default=None),
) -> GameReviewResponse:
    account = _current_account(authorization)
    analysis_id = uuid4().hex if account is not None else (request.analysis_id or uuid4().hex)
    visitor_id = account.user_id if account else (request.visitor_id or f"server_{uuid4().hex}")
    reservation_id: str | None = None
    entitlement_reason: str | None = None
    if account is not None:
        assert account_store is not None
        decision = await asyncio.to_thread(
            account_store.reserve_analysis, account.user_id, analysis_id
        )
        if not decision.allowed:
            await _commercial_event(account.user_id, "paywall_view")
            raise HTTPException(
                status_code=402,
                detail={"code": "subscription_required", "message": "免费完整分析已使用，请选择会员方案"},
            )
        reservation_id = decision.reservation_id
        entitlement_reason = decision.reason
        if decision.reason == "free_trial":
            await _commercial_event(account.user_id, "free_analysis_start", analysis_id)
    started = time.perf_counter()
    await _record_analytics(
        "start_analysis",
        analysis_id,
        visitor_id,
        request.pgn,
    )
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
        elapsed_ms = round((time.perf_counter() - started) * 1000)
        await _record_analytics(
            "finish_analysis",
            analysis_id,
            success=False,
            stockfish_ms=elapsed_ms,
            total_ms=elapsed_ms,
        )
        if reservation_id and account_store:
            await asyncio.to_thread(account_store.finish_analysis, reservation_id, success=False)
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except StockfishBusyError as exc:
        await _record_analytics("discard_analysis", analysis_id)
        raise HTTPException(
            status_code=503,
            detail="当前分析任务较多，请稍后重试",
            headers={"Retry-After": "5"},
        ) from exc
    except asyncio.TimeoutError as exc:
        elapsed_ms = round((time.perf_counter() - started) * 1000)
        await _record_analytics(
            "finish_analysis",
            analysis_id,
            success=False,
            stockfish_ms=elapsed_ms,
            total_ms=elapsed_ms,
        )
        if reservation_id and account_store:
            await asyncio.to_thread(account_store.finish_analysis, reservation_id, success=False)
        raise HTTPException(status_code=504, detail="整盘 Stockfish 分析超时，请缩短棋谱后重试") from exc
    except Exception as exc:
        logger.exception("Full game analysis failed")
        elapsed_ms = round((time.perf_counter() - started) * 1000)
        await _record_analytics(
            "finish_analysis",
            analysis_id,
            success=False,
            stockfish_ms=elapsed_ms,
            total_ms=elapsed_ms,
        )
        if reservation_id and account_store:
            await asyncio.to_thread(account_store.finish_analysis, reservation_id, success=False)
        raise HTTPException(status_code=503, detail="整盘分析服务暂不可用") from exc

    try:
        result.moves = [
            item.model_copy(
                update={
                    "opening_context": _professional_opening_context(result.moves, item.index)
                }
            )
            for item in result.moves
        ]
        result.opening_summary = _game_opening_context(result.moves)
    except Exception:
        # Opening presentation is additive and must not consume a free analysis
        # without returning the already completed Stockfish review.
        logger.exception("Opening context enrichment failed")
    game_cache[analysis_id] = result.moves
    if account is not None:
        game_cache_owners[analysis_id] = account.user_id
    game_cache.move_to_end(analysis_id)
    while len(game_cache) > MAX_CACHED_GAMES:
        expired_id, _ = game_cache.popitem(last=False)
        game_cache_owners.pop(expired_id, None)
        for cache_key in [key for key in explanation_cache if key[0] == expired_id]:
            explanation_cache.pop(cache_key, None)
        for cache_key in [key for key in analysis_report_cache if key[0] == expired_id]:
            analysis_report_cache.pop(cache_key, None)
    elapsed_ms = round((time.perf_counter() - started) * 1000)
    await _record_analytics(
        "finish_analysis",
        analysis_id,
        success=True,
        stockfish_ms=elapsed_ms,
        total_ms=elapsed_ms,
        move_count=result.move_count,
    )
    if reservation_id and account_store:
        await asyncio.to_thread(account_store.finish_analysis, reservation_id, success=True)
        if entitlement_reason == "free_trial" and account is not None:
            await _commercial_event(account.user_id, "free_analysis_complete", analysis_id)
    if account is not None and account_store is not None:
        try:
            memory = build_coach_memory(result.moves)
            await asyncio.to_thread(
                account_store.save_coach_memory,
                account.user_id,
                analysis_id,
                memory,
            )
        except Exception:
            # Coach memory is auxiliary; a persistence outage must not turn a
            # completed and charged analysis into a client-visible failure.
            logger.exception("Coach memory persistence failed")
    return result


@app.post("/api/move-explanation", response_model=MoveExplanationResponse)
async def move_explanation(
    request: MoveExplanationRequest,
    authorization: str | None = Header(default=None),
) -> MoveExplanationResponse:
    account = _current_account(authorization)
    move, moves = _owned_move(request.analysis_id, request.move_index, account)

    cache_key = (request.analysis_id, request.move_index)
    if cache_key in explanation_cache:
        cached_result = explanation_cache[cache_key]
        return MoveExplanationResponse(
            explanation=cached_result if isinstance(cached_result, str) else cached_result.explanation,
            details=None if isinstance(cached_result, str) else cached_result.details,
            cached=True,
        )

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
async def move_facts(
    request: MoveExplanationRequest,
    authorization: str | None = Header(default=None),
) -> MoveFactPackage:
    """Return the verified data package for one selected ply without calling DeepSeek."""
    account = _current_account(authorization)
    move, _ = _owned_move(request.analysis_id, request.move_index, account)
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
async def professional_analysis(
    request: MoveExplanationRequest,
    authorization: str | None = Header(default=None),
) -> ProfessionalAnalysisResponse:
    account = _current_account(authorization)
    move, moves = _owned_move(request.analysis_id, request.move_index, account)
    # The game review owns opening identity. Reuse that exact result everywhere
    # instead of allowing the professional-analysis path to classify it again.
    opening_context = move.opening_context
    recent_moves = [item for item in moves if item.index < move.index][-5:]
    # A single-position fixture has no game history to compress and should keep
    # the legacy compact cache key. Real games receive the bounded game context.
    game_context = (
        compress_game_context(moves)
        if len(moves) > 1 and request.analysis_id not in local_game_sessions
        else None
    )
    coach_context = None
    if account is not None and account_store is not None:
        memories = await asyncio.to_thread(
            account_store.recent_coach_memories,
            account.user_id,
            5,
            request.analysis_id,
        )
        coach_context = compress_coach_memories(
            memories,
            current_game=build_coach_memory(moves),
        )
    book_references = _professional_book_references(move.before_fen)
    depth = max((line.depth for line in move.candidate_lines), default=settings.game_analysis_depth)
    cache_key = professional_cache_key(
        move,
        stockfish_version="Stockfish 18",
        stockfish_depth=depth,
        opening_id=opening_context.opening_id if opening_context else None,
        recent_moves=recent_moves,
        game_context=game_context,
        coach_context=coach_context,
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
                _generate_professional_analysis(
                    move,
                    opening_context=opening_context,
                    recent_moves=recent_moves,
                    game_context=game_context,
                    coach_context=coach_context,
                )
            )
            professional_tasks[cache_key] = task
            created_task = True
        generated = await asyncio.shield(task)
        professional_cache[cache_key] = generated
        professional_cache.move_to_end(cache_key)
        while len(professional_cache) > MAX_CACHED_PROFESSIONAL_ANALYSES:
            professional_cache.popitem(last=False)
        if created_task:
            await _record_analytics(
                "add_deepseek_usage",
                request.analysis_id,
                elapsed_ms=generated.usage.elapsed_ms,
                prompt_tokens=generated.usage.prompt_tokens,
                completion_tokens=generated.usage.completion_tokens,
                total_tokens=generated.usage.total_tokens,
            )
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
async def analysis_report(
    request: MoveExplanationRequest,
    authorization: str | None = Header(default=None),
) -> AnalysisReportResponse:
    """Generate the independent Phase 4 report without invoking the legacy LLM path."""
    account = _current_account(authorization)
    move, _ = _owned_move(request.analysis_id, request.move_index, account)

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
        if created_task:
            await _record_analytics(
                "add_deepseek_usage",
                request.analysis_id,
                elapsed_ms=generated.usage.elapsed_ms,
                prompt_tokens=generated.usage.prompt_tokens,
                completion_tokens=generated.usage.completion_tokens,
                total_tokens=generated.usage.total_tokens,
            )
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
    recent_moves: list[MoveReview] | None = None,
    game_context: dict[str, object] | None = None,
    coach_context: dict[str, object] | None = None,
) -> GeneratedProfessionalAnalysis:
    fact_package = build_move_fact_package(move)
    threat_package = await threat_analyzer.analyze(
        fact_package,
        stockfish=stockfish,
    )
    kwargs = {"threat_package": threat_package}
    if opening_context is not None:
        kwargs["opening_context"] = opening_context
    if recent_moves:
        kwargs["recent_moves"] = recent_moves
    supported = inspect.signature(professional_service.analyze).parameters
    if game_context and "game_context" in supported:
        kwargs["game_context"] = game_context
    if coach_context and "coach_context" in supported:
        kwargs["coach_context"] = coach_context
    return await professional_service.analyze(move, **kwargs)


def _professional_opening_context(
    moves: list[MoveReview],
    move_index: int,
) -> OpeningPresentation | None:
    """Recognize the selected move's starting position from verified prior moves."""
    prior_moves = moves[:max(move_index - 1, 0)]
    if not prior_moves:
        return None
    try:
        return opening_knowledge.presentation_for_moves(
            [item.played_move.uci for item in prior_moves],
            initial_fen=moves[0].before_fen,
        )
    except (ValueError, RuntimeError, OSError) as exc:
        logger.warning("Optional opening recognition unavailable: %s", exc)
        return None


def _game_opening_context(moves: list[MoveReview]) -> OpeningPresentation | None:
    """Return the deepest verified opening reached anywhere in the game."""
    if not moves:
        return None
    candidates = [item.opening_context for item in moves if item.opening_context]
    try:
        final_context = opening_knowledge.presentation_for_moves(
            [item.played_move.uci for item in moves],
            initial_fen=moves[0].before_fen,
        )
    except (ValueError, RuntimeError, OSError) as exc:
        logger.warning("Optional final opening recognition unavailable: %s", exc)
        final_context = None
    if final_context is not None:
        candidates.append(final_context)
    deepest = max(candidates, key=lambda item: item.query_ply, default=None)
    return deepest


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
