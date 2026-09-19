from __future__ import annotations

import sqlite3
from dataclasses import replace

from fastapi.testclient import TestClient

import app.api as api
from app.accounts import AccountStore


def test_manual_payment_request_uses_normalized_phone_and_server_price(tmp_path) -> None:
    store = AccountStore(tmp_path / "manual.sqlite3")
    auth = store.register("buyer@example.com", "correct-horse")

    request = store.create_manual_payment_request(
        auth.account.user_id,
        "+86 138-0013-8000",
        "monthly",
        "wechat",
    )
    duplicate = store.create_manual_payment_request(
        auth.account.user_id,
        "13800138000",
        "monthly",
        "wechat",
    )

    assert request.amount_fen == 990
    assert request.phone == "13800138000"
    assert duplicate.request_id == request.request_id
    assert store.payment_order(request.order_id, auth.account.user_id).payment_provider == "manual_wechat"

    try:
        store.create_manual_payment_request(
            auth.account.user_id,
            "12800138000",
            "yearly",
            "alipay",
        )
    except ValueError as exc:
        assert "中国大陆手机号" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("invalid phone should be rejected")


def test_manual_approval_activates_once_and_rejection_does_not_activate(tmp_path) -> None:
    store = AccountStore(tmp_path / "manual.sqlite3")
    approved_auth = store.register("approved@example.com", "correct-horse")
    rejected_auth = store.register("rejected@example.com", "correct-horse")
    approved = store.create_manual_payment_request(
        approved_auth.account.user_id,
        "13900139000",
        "yearly",
        "alipay",
    )
    rejected = store.create_manual_payment_request(
        rejected_auth.account.user_id,
        "13700137000",
        "monthly",
        "wechat",
    )

    reviewed, processed, renewal = store.review_manual_payment(
        approved.request_id, approve=True, note="到账已核对"
    )
    repeated, repeated_processed, _ = store.review_manual_payment(
        approved.request_id, approve=True
    )
    rejected_result, rejected_processed, _ = store.review_manual_payment(
        rejected.request_id, approve=False, note="未查询到款项"
    )

    assert reviewed.status == "approved"
    assert processed is True and renewal is False
    assert repeated.status == "approved" and repeated_processed is False
    assert store.account_for_token(approved_auth.token).membership_type == "yearly"
    assert rejected_result.status == "rejected" and rejected_processed is True
    assert store.account_for_token(rejected_auth.token).membership_status == "inactive"


def test_manual_payment_api_requires_valid_phone_and_admin_key(
    monkeypatch, tmp_path
) -> None:
    store = AccountStore(tmp_path / "manual.sqlite3")
    auth = store.register("api-buyer@example.com", "correct-horse")
    monkeypatch.setattr(api, "account_store", store)
    monkeypatch.setattr(api, "analytics_store", None)
    monkeypatch.setattr(
        api,
        "settings",
        replace(
            api.settings,
            billing_enforced=True,
            admin_statistics_key="manual-review-secret",
            manual_wechat_qr_url="/assets/payment/wechat-personal-qr.png",
            manual_alipay_qr_url="/assets/payment/alipay-personal-qr.png",
        ),
    )
    client = TestClient(api.app)
    headers = {"Authorization": f"Bearer {auth.token}"}

    config = client.get("/api/billing/manual/config")
    mismatch = client.post(
        "/api/billing/manual-requests",
        json={
            "membership_type": "monthly",
            "payment_provider": "wechat",
            "phone": "10086",
        },
        headers=headers,
    )
    created = client.post(
        "/api/billing/manual-requests",
        json={
            "membership_type": "monthly",
            "payment_provider": "wechat",
            "phone": "+86 136-0013-6000",
        },
        headers=headers,
    )
    denied = client.get("/api/admin/manual-payments?status=pending")
    admin_headers = {"X-Admin-Key": "manual-review-secret"}
    pending = client.get(
        "/api/admin/manual-payments?status=pending", headers=admin_headers
    )
    approved = client.post(
        f"/api/admin/manual-payments/{created.json()['request_id']}/review",
        json={"action": "approve", "note": "人工确认到账"},
        headers=admin_headers,
    )

    assert config.status_code == 200 and config.json()["enabled"] is True
    assert mismatch.status_code == 422
    assert created.status_code == 200 and created.json()["amount_fen"] == 990
    assert created.json()["phone"] == "13600136000"
    assert "email" not in created.json()
    assert denied.status_code == 401
    assert pending.status_code == 200 and len(pending.json()) == 1
    assert approved.status_code == 200 and approved.json()["status"] == "approved"
    assert client.get("/api/auth/me", headers=headers).json()["membership_status"] == "active"


def test_manual_payment_phone_column_is_added_to_legacy_database(tmp_path) -> None:
    database = tmp_path / "legacy-manual.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute(
            """CREATE TABLE manual_payment_requests (
                request_id TEXT PRIMARY KEY,
                order_id TEXT NOT NULL UNIQUE,
                user_id TEXT NOT NULL,
                email TEXT NOT NULL,
                membership_type TEXT NOT NULL,
                payment_provider TEXT NOT NULL,
                amount_fen INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL,
                reviewed_at TEXT,
                reviewer_note TEXT NOT NULL DEFAULT ''
            )"""
        )

    AccountStore(database)

    with sqlite3.connect(database) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(manual_payment_requests)")}
    assert "phone" in columns
