import asyncio

import pytest
from fastapi.testclient import TestClient

from app import api
from app.models import EngineResult, EvaluationSnapshot, MoveResult, MoveReview


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

    async def explain_move(self, move: MoveReview) -> str:
        self.move_calls += 1
        return "这步让局面稍微变难了。\n\n记住：先看看对手的回应。"


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
    assert body["explanation"].startswith("局面接近均势")


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
    api.game_cache[analysis_id] = [
        MoveReview(
            index=1,
            move_number=1,
            notation="1.e4",
            side="white",
            san="e4",
            uci="e2e4",
            from_square="e2",
            to_square="e4",
            before_fen="start",
            after_fen="after",
            before=EvaluationSnapshot(evaluation="+0.20", centipawn=20),
            after=EvaluationSnapshot(evaluation="+0.10", centipawn=10),
            centipawn_loss=10,
            best_move_uci="e2e4",
            best_move_san="e4",
            best_pv=["e4", "e5"],
            quality_key="best",
            quality_symbol="!",
            quality_label="最佳着",
            mate_involved=False,
            only_legal_move=False,
        )
    ]
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
    assert second.json()["cached"] is True
    assert fake_explainer.move_calls == 1


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
    api.game_cache[analysis_id] = [
        MoveReview(
            index=1,
            move_number=1,
            notation="1.e4",
            side="white",
            san="e4",
            uci="e2e4",
            from_square="e2",
            to_square="e4",
            before_fen="start",
            after_fen="after",
            before=EvaluationSnapshot(evaluation="+0.20", centipawn=20),
            after=EvaluationSnapshot(evaluation="+0.10", centipawn=10),
            centipawn_loss=10,
            best_move_uci="e2e4",
            best_move_san="e4",
            best_pv=["e4", "e5"],
            quality_key="best",
            quality_symbol="!",
            quality_label="最佳着",
            mate_involved=False,
            only_legal_move=False,
        )
    ]
    request = api.MoveExplanationRequest(analysis_id=analysis_id, move_index=1)
    first, second = await asyncio.gather(
        api.move_explanation(request),
        api.move_explanation(request),
    )

    assert first.explanation == second.explanation
    assert fake_explainer.move_calls == 1
