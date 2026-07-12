from fastapi.testclient import TestClient

from app import api
from app.models import EngineResult, MoveResult


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

    async def explain(self, fen: str, result: EngineResult) -> str:
        return "局面接近均势，白方可以争夺中心。"


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

