import asyncio

import chess
import pytest
from fastapi.testclient import TestClient

from app import api
from app.analysis_report import (
    AnalysisReportUsage,
    GeneratedAnalysisReport,
    build_fallback_report,
)
from app.models import (
    ComplexityFactors,
    EngineResult,
    EvaluationSnapshot,
    GeneratedMoveExplanation,
    MoveExplanationDetails,
    MoveFacts,
    MoveResult,
    MoveReview,
)


class FakeStockfish:
    def available(self) -> bool:
        return True

    async def analyze(self, fen: str) -> EngineResult:
        return EngineResult(
            evaluation="+0.25",
            centipawn=25,
            mate_in=None,
            depth=16,
            nodes=1234,
            time_ms=20,
            top_moves=[
                MoveResult(
                    move="e2e4",
                    san="e4",
                    centipawn=25,
                    mate_in=None,
                    pv=["e5", "Nf3"],
                    depth=16,
                )
            ],
        )


class FakeExplainer:
    configured = True

    def __init__(self) -> None:
        self.move_calls = 0

    async def explain(self, fen: str, result: EngineResult) -> str:
        return "局面接近均势，白方可以争夺中心。"

    async def explain_move(self, move: MoveReview) -> GeneratedMoveExplanation:
        self.move_calls += 1
        details = MoveExplanationDetails(
            complexity="simple",
            conclusion="这步让局面稍微变难了。",
            currentSituation="",
            opponentThreat="",
            playedMoveIdea="",
            problem="引擎评价发生了一点变化。",
            betterMove="实战走法接近第一选择。",
            variationExplanation=[],
            childTip="记住：先看看对手的回应。",
        )
        return GeneratedMoveExplanation(
            explanation="这步让局面稍微变难了。\n\n记住：先看看对手的回应。",
            details=details,
        )


class FakeNarrativeGenerator:
    def __init__(self) -> None:
        self.calls = 0

    async def generate(self, package) -> GeneratedAnalysisReport:
        self.calls += 1
        return GeneratedAnalysisReport(
            report=build_fallback_report(package),
            usage=AnalysisReportUsage(attempts=1, used_fallback=False),
        )


def sample_move_review() -> MoveReview:
    after = chess.Board()
    after.push_uci("e2e4")
    played = MoveFacts(
        san="e4",
        uci="e2e4",
        from_square="e2",
        to_square="e4",
        piece="white_pawn",
        capture=False,
        check=False,
        checkmate=False,
        castling=False,
    )
    return MoveReview(
        index=1,
        move_number=1,
        notation="1.e4",
        side="white",
        san="e4",
        uci="e2e4",
        from_square="e2",
        to_square="e4",
        before_fen=chess.STARTING_FEN,
        after_fen=after.fen(),
        before=EvaluationSnapshot(evaluation="+0.20", centipawn=20),
        after=EvaluationSnapshot(evaluation="+0.10", centipawn=10),
        played_move=played,
        best_move=played,
        centipawn_loss=10,
        best_move_uci="e2e4",
        best_move_san="e4",
        best_pv=["e4", "e5"],
        quality_key="best",
        quality_symbol="!",
        quality_label="最佳着",
        mate_involved=False,
        only_legal_move=False,
        principal_variation=["e4", "e5"],
        complexity="simple",
        complexity_factors=ComplexityFactors(
            legal_move_count=20,
            only_reasonable_move=False,
            pv_length=2,
            forcing_line_plies=0,
            engaged_piece_count=0,
        ),
        verified_facts=["白方实战走了 e4。"],
        allowed_squares=["e2", "e4", "e7", "e5"],
        allowed_moves=["e4", "e2e4", "e5", "e7e5"],
        pieces_before={"e2": "white_pawn"},
    )


def test_health_and_review(monkeypatch) -> None:
    monkeypatch.setattr(api, "stockfish", FakeStockfish())
    monkeypatch.setattr(api, "explainer", FakeExplainer())
    client = TestClient(api.app)

    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.json()["stockfish"] == "available"

    response = client.post(
        "/api/review",
        json={"fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["position"]["side_to_move"] == "white"
    assert body["engine"]["top_moves"][0]["san"] == "e4"
    assert body["engine"]["perspective"] == "white"
    assert body["engine"]["evaluation_pawns"] == 0.25
    assert body["engine"]["top_moves"][0]["verified"] is True
    assert body["explanation"].startswith("局面接近均势")
    assert body["threats"] == []
    assert body["plans"] == []


def test_request_validation(monkeypatch) -> None:
    monkeypatch.setattr(api, "stockfish", FakeStockfish())
    monkeypatch.setattr(api, "explainer", FakeExplainer())
    client = TestClient(api.app)

    response = client.post("/api/review", json={"fen": "short"})
    assert response.status_code == 422


def test_move_explanation_is_cached(monkeypatch) -> None:
    fake_explainer = FakeExplainer()
    monkeypatch.setattr(api, "explainer", fake_explainer)
    api.game_cache.clear()
    api.explanation_cache.clear()
    api.explanation_tasks.clear()
    analysis_id = "analysis-cache-test"
    api.game_cache[analysis_id] = [sample_move_review()]
    client = TestClient(api.app)
    first = client.post(
        "/api/move-explanation",
        json={"analysis_id": analysis_id, "move_index": 1},
    )
    second = client.post(
        "/api/move-explanation",
        json={"analysis_id": analysis_id, "move_index": 1},
    )
    assert first.status_code == 200
    assert first.json()["cached"] is False
    assert first.json()["details"]["complexity"] == "simple"
    assert first.json()["details"]["childTip"].startswith("记住：")
    assert second.json()["cached"] is True
    assert fake_explainer.move_calls == 1


def test_move_facts_endpoint_returns_selected_ply_without_deepseek(monkeypatch) -> None:
    fake_explainer = FakeExplainer()
    monkeypatch.setattr(api, "explainer", fake_explainer)
    api.game_cache.clear()
    analysis_id = "analysis-facts-test"
    api.game_cache[analysis_id] = [sample_move_review()]
    client = TestClient(api.app)

    response = client.post(
        "/api/move-facts",
        json={"analysis_id": analysis_id, "move_index": 1},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["currentMove"]["playedMove"]["uci"] == "e2e4"
    assert payload["currentMove"]["plyIndex"] == 1
    assert payload["candidateLines"] == []
    assert fake_explainer.move_calls == 0


@pytest.mark.asyncio
async def test_simultaneous_move_explanation_requests_share_one_call(monkeypatch) -> None:
    class SlowExplainer(FakeExplainer):
        async def explain_move(self, move: MoveReview) -> str:
            self.move_calls += 1
            await asyncio.sleep(0.02)
            return "先观察局面变化。\n\n记住：落子前先看对手的回应。"

    fake_explainer = SlowExplainer()
    monkeypatch.setattr(api, "explainer", fake_explainer)
    api.game_cache.clear()
    api.explanation_cache.clear()
    api.explanation_tasks.clear()
    analysis_id = "parallel-cache-test"
    api.game_cache[analysis_id] = [sample_move_review()]
    request = api.MoveExplanationRequest(analysis_id=analysis_id, move_index=1)
    first, second = await asyncio.gather(
        api.move_explanation(request),
        api.move_explanation(request),
    )

    assert first.explanation == second.explanation
    assert fake_explainer.move_calls == 1


def test_analysis_report_endpoint_is_independent_and_cached(monkeypatch) -> None:
    class ForbiddenProfessionalService:
        async def analyze(self, move: MoveReview):
            raise AssertionError("新报告接口不得调用旧professional-analysis")

    narrative = FakeNarrativeGenerator()
    monkeypatch.setattr(api, "narrative_generator", narrative)
    monkeypatch.setattr(api, "professional_service", ForbiddenProfessionalService())
    api.game_cache.clear()
    api.analysis_report_cache.clear()
    api.analysis_report_tasks.clear()
    analysis_id = "analysis-report-cache-test"
    api.game_cache[analysis_id] = [sample_move_review()]
    client = TestClient(api.app)

    first = client.post(
        "/api/analysis-report",
        json={"analysis_id": analysis_id, "move_index": 1},
    )
    second = client.post(
        "/api/analysis-report",
        json={"analysis_id": analysis_id, "move_index": 1},
    )

    assert first.status_code == 200
    assert first.json()["cached"] is False
    assert first.json()["report"]["version"] == "1.0"
    assert first.json()["report"]["summary_section"]["source_refs"]
    assert "analysis" not in first.json()
    assert second.status_code == 200
    assert second.json()["cached"] is True
    assert narrative.calls == 1


def test_analysis_report_endpoint_preserves_existing_cache_errors(monkeypatch) -> None:
    monkeypatch.setattr(api, "narrative_generator", FakeNarrativeGenerator())
    api.game_cache.clear()
    api.analysis_report_cache.clear()
    api.analysis_report_tasks.clear()
    client = TestClient(api.app)

    missing = client.post(
        "/api/analysis-report",
        json={"analysis_id": "missing-analysis", "move_index": 1},
    )

    assert missing.status_code == 404
    assert "缓存已过期" in missing.json()["detail"]
