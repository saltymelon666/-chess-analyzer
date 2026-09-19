from __future__ import annotations

from dataclasses import replace

from fastapi.testclient import TestClient

import app.api as api
from app.accounts import AccountStore, PaymentCallback, callback_signature
from app.models import GameReviewResponse
from tests.test_api import sample_move_review


def test_backend_enforces_free_trial_and_signed_payment_activates_immediately(
    monkeypatch, tmp_path
) -> None:
    store = AccountStore(tmp_path / "commerce.sqlite3")
    monkeypatch.setattr(api, "account_store", store)
    monkeypatch.setattr(api, "analytics_store", None)
    monkeypatch.setattr(
        api,
        "settings",
        replace(
            api.settings,
            billing_enforced=True,
            payment_callback_secret="callback-secret",
            payment_checkout_base_url="",
        ),
    )
    api.game_cache.clear()
    api.game_cache_owners.clear()

    async def fake_analyze_pgn(**kwargs):
        review = sample_move_review()
        return GameReviewResponse(
            analysis_id=kwargs["analysis_id"],
            depth=10,
            move_count=1,
            moves=[review],
        )

    monkeypatch.setattr(api, "analyze_pgn", fake_analyze_pgn)
    client = TestClient(api.app)
    registered = client.post(
        "/api/auth/register",
        json={
            "phone": "13800138000",
            "password": "correct-horse",
            "password_confirm": "correct-horse",
        },
    )
    assert registered.status_code == 200
    token = registered.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    first = client.post("/api/game-review", json={"pgn": "1. e4"}, headers=headers)
    assert first.status_code == 200
    assert client.get("/api/auth/me", headers=headers).json()["free_analysis_used"] is True

    blocked = client.post("/api/game-review", json={"pgn": "1. e4"}, headers=headers)
    assert blocked.status_code == 402
    assert blocked.json()["detail"]["code"] == "subscription_required"

    order = client.post(
        "/api/billing/orders",
        json={"membership_type": "monthly"},
        headers=headers,
    )
    assert order.status_code == 200
    assert order.json()["amount_fen"] == 990
    callback = PaymentCallback(
        order_id=order.json()["order_id"],
        status="paid",
        provider_transaction_id="provider-transaction",
    )
    denied = client.post("/api/billing/payment-callback", json=callback.model_dump())
    assert denied.status_code == 401
    accepted = client.post(
        "/api/billing/payment-callback",
        json=callback.model_dump(),
        headers={"X-Payment-Signature": callback_signature("callback-secret", callback)},
    )
    assert accepted.status_code == 204
    assert client.get("/api/auth/me", headers=headers).json()["membership_status"] == "active"
    assert client.post("/api/game-review", json={"pgn": "1. e4"}, headers=headers).status_code == 200


def test_phone_registration_requires_matching_password_confirmation(
    monkeypatch, tmp_path
) -> None:
    store = AccountStore(tmp_path / "phone-auth.sqlite3")
    monkeypatch.setattr(api, "account_store", store)
    monkeypatch.setattr(api, "analytics_store", None)
    client = TestClient(api.app)

    mismatch = client.post(
        "/api/auth/register",
        json={
            "phone": "13800138000",
            "password": "correct-horse",
            "password_confirm": "different-password",
        },
    )
    registered = client.post(
        "/api/auth/register",
        json={
            "phone": "+86 138-0013-8000",
            "password": "correct-horse",
            "password_confirm": "correct-horse",
        },
    )
    logged_in = client.post(
        "/api/auth/login",
        json={"identifier": "13800138000", "password": "correct-horse"},
    )

    assert mismatch.status_code == 422
    assert "两次输入的密码不一致" in str(mismatch.json())
    assert registered.status_code == 200
    assert registered.json()["account"]["phone"] == "13800138000"
    assert registered.json()["account"]["email"] is None
    assert logged_in.status_code == 200


def test_billing_mode_rejects_unauthenticated_analysis(monkeypatch) -> None:
    monkeypatch.setattr(api, "settings", replace(api.settings, billing_enforced=True))
    response = TestClient(api.app).post("/api/game-review", json={"pgn": "1. e4"})
    assert response.status_code == 401


def test_completed_free_analysis_survives_coach_memory_write_failure(
    monkeypatch, tmp_path
) -> None:
    store = AccountStore(tmp_path / "commerce.sqlite3")
    monkeypatch.setattr(api, "account_store", store)
    monkeypatch.setattr(api, "analytics_store", None)
    monkeypatch.setattr(api, "settings", replace(api.settings, billing_enforced=True))

    async def fake_analyze_pgn(**kwargs):
        return GameReviewResponse(
            analysis_id=kwargs["analysis_id"],
            depth=10,
            move_count=1,
            moves=[sample_move_review()],
        )

    def fail_memory(*args, **kwargs):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(api, "analyze_pgn", fake_analyze_pgn)
    monkeypatch.setattr(store, "save_coach_memory", fail_memory)
    registered = store.register("memory-failure@example.com", "correct-horse")
    headers = {"Authorization": f"Bearer {registered.token}"}
    response = TestClient(api.app).post(
        "/api/game-review", json={"pgn": "1. e4"}, headers=headers
    )

    assert response.status_code == 200
    account = store.account_for_token(registered.token)
    assert account is not None
    assert account.free_analysis_used is True


def test_cached_analysis_endpoints_do_not_leak_between_users(monkeypatch, tmp_path) -> None:
    store = AccountStore(tmp_path / "commerce.sqlite3")
    monkeypatch.setattr(api, "account_store", store)
    monkeypatch.setattr(api, "settings", replace(api.settings, billing_enforced=True))
    api.game_cache.clear()
    api.game_cache_owners.clear()
    owner = store.register("owner@example.com", "correct-horse")
    other = store.register("other@example.com", "correct-horse")
    analysis_id = "private-analysis"
    api.game_cache[analysis_id] = [sample_move_review()]
    api.game_cache_owners[analysis_id] = owner.account.user_id
    client = TestClient(api.app)
    other_headers = {"Authorization": f"Bearer {other.token}"}

    for endpoint in ("move-explanation", "move-facts", "professional-analysis"):
        response = client.post(
            f"/api/{endpoint}",
            json={"analysis_id": analysis_id, "move_index": 1},
            headers=other_headers,
        )
        assert response.status_code == 404
        assert response.json()["detail"] == "找不到这次棋局分析"


def test_browser_scan_session_is_owned_and_selected_move_is_server_verified(
    monkeypatch, tmp_path
) -> None:
    store = AccountStore(tmp_path / "local-scan.sqlite3")
    monkeypatch.setattr(api, "account_store", store)
    monkeypatch.setattr(api, "analytics_store", None)
    monkeypatch.setattr(api, "settings", replace(api.settings, billing_enforced=True))
    api.local_game_sessions.clear()
    api.local_game_session_owners.clear()
    api.local_verified_moves.clear()
    api.local_verify_tasks.clear()

    async def fake_analyze_pgn(**kwargs):
        return GameReviewResponse(
            analysis_id=kwargs["analysis_id"],
            depth=20,
            move_count=1,
            moves=[sample_move_review()],
        )

    monkeypatch.setattr(api, "analyze_pgn", fake_analyze_pgn)
    owner = store.register("owner-local@example.com", "correct-horse")
    other = store.register("other-local@example.com", "correct-horse")
    client = TestClient(api.app)
    owner_headers = {"Authorization": f"Bearer {owner.token}"}
    other_headers = {"Authorization": f"Bearer {other.token}"}

    started = client.post(
        "/api/game-review/start-local",
        json={"pgn": "1. e4 e5 2. Nf3"},
        headers=owner_headers,
    )
    assert started.status_code == 200
    analysis_id = started.json()["analysis_id"]
    assert started.json()["move_count"] == 3
    assert store.account_for_token(owner.token).free_analysis_used is True

    denied = client.post(
        "/api/game-review/position",
        json={"analysis_id": analysis_id, "move_index": 2},
        headers=other_headers,
    )
    assert denied.status_code == 404

    verified = client.post(
        "/api/game-review/position",
        json={"analysis_id": analysis_id, "move_index": 2},
        headers=owner_headers,
    )
    assert verified.status_code == 200
    assert verified.json()["authoritative"] is True
    assert verified.json()["move_index"] == 2
    assert verified.json()["review"]["index"] == 2
    assert api.local_verified_moves[(analysis_id, 2)].before_fen
