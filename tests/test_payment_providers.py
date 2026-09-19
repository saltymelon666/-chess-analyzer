from __future__ import annotations

import base64
import json
import sqlite3
import time
from dataclasses import replace
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi.testclient import TestClient

import app.api as api
from app.accounts import AccountStore, PaymentCallback
from app.payments import (
    AlipayProvider,
    PaymentProviderError,
    WeChatPayProvider,
)


def _keys() -> tuple[str, str, object]:
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    public_pem = private.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    return private_pem, public_pem, private


def _provider_settings():
    merchant_private, _, _ = _keys()
    _, platform_public, platform_private = _keys()
    settings = replace(
        api.settings,
        payment_public_origin="https://pawnlab.example",
        payment_frontend_origin="https://www.pawnlab.example",
        wechat_pay_mch_id="1900000001",
        wechat_pay_app_id="wx-test-app",
        wechat_pay_cert_serial="merchant-serial",
        wechat_pay_private_key=merchant_private,
        wechat_pay_api_v3_key="0123456789abcdef0123456789abcdef",
        wechat_pay_platform_public_key=platform_public,
        wechat_pay_platform_serial="platform-serial",
        alipay_app_id="2026000000000001",
        alipay_private_key=merchant_private,
        alipay_public_key=platform_public,
        alipay_seller_id="2088000000000001",
    )
    return settings, platform_private


def _wechat_notification(
    provider: WeChatPayProvider,
    platform_private,
    *,
    amount: int = 990,
    order_id: str = "ord_test_payment",
):
    transaction = {
        "mchid": provider.mch_id,
        "appid": provider.app_id,
        "out_trade_no": order_id,
        "transaction_id": "wx_transaction_1",
        "trade_state": "SUCCESS",
        "amount": {"total": amount, "currency": "CNY"},
    }
    nonce_resource = "abcdefghijkl"
    associated = "transaction"
    ciphertext = AESGCM(provider.api_v3_key.encode()).encrypt(
        nonce_resource.encode(), json.dumps(transaction).encode(), associated.encode()
    )
    body = json.dumps(
        {
            "resource": {
                "nonce": nonce_resource,
                "associated_data": associated,
                "ciphertext": base64.b64encode(ciphertext).decode(),
            }
        },
        separators=(",", ":"),
    ).encode()
    timestamp = str(int(time.time()))
    nonce = "notify-nonce"
    message = timestamp.encode() + b"\n" + nonce.encode() + b"\n" + body + b"\n"
    signature = base64.b64encode(
        platform_private.sign(message, padding.PKCS1v15(), hashes.SHA256())
    ).decode()
    headers = {
        "wechatpay-timestamp": timestamp,
        "wechatpay-nonce": nonce,
        "wechatpay-signature": signature,
        "wechatpay-serial": provider.platform_serial,
    }
    return body, headers


def test_wechat_notification_signature_decryption_and_amount() -> None:
    settings, platform_private = _provider_settings()
    provider = WeChatPayProvider(settings)
    body, headers = _wechat_notification(provider, platform_private)

    verified = provider.verify_notification(body, headers)

    assert verified.order_id == "ord_test_payment"
    assert verified.amount_fen == 990
    assert verified.currency == "CNY"
    with pytest.raises(PaymentProviderError, match="签名验证失败"):
        provider.verify_notification(body + b" ", headers)


def test_alipay_checkout_and_signed_notification() -> None:
    settings, platform_private = _provider_settings()
    provider = AlipayProvider(settings)
    checkout = provider.create_checkout(
        order_id="ord_test_payment",
        amount_fen=16800,
        membership_type="yearly",
        client_type="desktop",
    )
    query = parse_qs(urlsplit(checkout.checkout_url or "").query)
    assert json.loads(query["biz_content"][0])["total_amount"] == "168.00"
    assert query["method"] == ["alipay.trade.page.pay"]

    parameters = {
        "app_id": provider.app_id,
        "seller_id": provider.seller_id,
        "out_trade_no": "ord_test_payment",
        "trade_no": "ali_transaction_1",
        "trade_status": "TRADE_SUCCESS",
        "total_amount": "9.90",
        "sign_type": "RSA2",
    }
    canonical = provider._canonical(parameters)
    parameters["sign"] = base64.b64encode(
        platform_private.sign(canonical.encode(), padding.PKCS1v15(), hashes.SHA256())
    ).decode()

    verified = provider.verify_notification(parameters)

    assert verified.amount_fen == 990
    assert verified.provider_transaction_id == "ali_transaction_1"
    parameters["total_amount"] = "19.90"
    with pytest.raises(PaymentProviderError, match="签名验证失败"):
        provider.verify_notification(parameters)


def test_provider_amount_and_channel_are_checked_before_membership_activation(tmp_path) -> None:
    store = AccountStore(tmp_path / "payments.sqlite3")
    account = store.register("provider@example.com", "correct-horse").account
    order = store.create_order(account.user_id, "monthly", payment_provider="wechat")
    callback = PaymentCallback(
        order_id=order.order_id,
        status="paid",
        provider_transaction_id="wx_transaction_1",
    )

    with pytest.raises(ValueError, match="金额不匹配"):
        store.apply_payment_callback(
            callback, expected_provider="wechat", expected_amount_fen=1990
        )
    with pytest.raises(ValueError, match="支付方式不匹配"):
        store.apply_payment_callback(
            callback, expected_provider="alipay", expected_amount_fen=990
        )
    assert store.account_for_token(store.login("provider@example.com", "correct-horse").token).membership_status == "inactive"

    _, _, processed, _ = store.apply_payment_callback(
        callback, expected_provider="wechat", expected_amount_fen=990
    )
    assert processed is True
    assert store.payment_order(order.order_id, account.user_id).status == "paid"


def test_existing_payment_order_table_is_migrated_without_losing_rows(tmp_path) -> None:
    database = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute(
            """CREATE TABLE payment_orders (
                order_id TEXT PRIMARY KEY, user_id TEXT NOT NULL,
                membership_type TEXT NOT NULL, amount_fen INTEGER NOT NULL,
                status TEXT NOT NULL, created_at TEXT NOT NULL,
                paid_at TEXT, provider_transaction_id TEXT
            )"""
        )
        connection.execute(
            "INSERT INTO payment_orders VALUES (?,?,?,?,?,?,?,?)",
            ("ord_legacy", "usr_legacy", "monthly", 990, "pending", "2026-01-01", None, None),
        )

    store = AccountStore(database)
    migrated = store.payment_order("ord_legacy")

    assert migrated.amount_fen == 990
    assert migrated.payment_provider == "internal"


def test_order_status_is_private_and_unconfigured_provider_is_rejected(monkeypatch, tmp_path) -> None:
    store = AccountStore(tmp_path / "payments.sqlite3")
    owner = store.register("owner-pay@example.com", "correct-horse")
    other = store.register("other-pay@example.com", "correct-horse")
    order = store.create_order(owner.account.user_id, "yearly", payment_provider="alipay")
    monkeypatch.setattr(api, "account_store", store)
    client = TestClient(api.app)

    own = client.get(
        f"/api/billing/orders/{order.order_id}",
        headers={"Authorization": f"Bearer {owner.token}"},
    )
    denied = client.get(
        f"/api/billing/orders/{order.order_id}",
        headers={"Authorization": f"Bearer {other.token}"},
    )
    unavailable = client.post(
        "/api/billing/orders",
        json={"membership_type": "monthly", "payment_provider": "wechat"},
        headers={"Authorization": f"Bearer {owner.token}"},
    )

    assert own.status_code == 200
    assert denied.status_code == 404
    assert unavailable.status_code == 503
    assert unavailable.json()["detail"] == "微信支付尚未配置"


def test_wechat_api_notification_activates_immediately_and_is_idempotent(
    monkeypatch, tmp_path
) -> None:
    settings, platform_private = _provider_settings()
    store = AccountStore(tmp_path / "payments.sqlite3")
    auth = store.register("wechat-api@example.com", "correct-horse")
    order = store.create_order(
        auth.account.user_id, "monthly", payment_provider="wechat"
    )
    provider = WeChatPayProvider(settings)
    body, headers = _wechat_notification(
        provider, platform_private, amount=order.amount_fen, order_id=order.order_id
    )
    monkeypatch.setattr(api, "account_store", store)
    monkeypatch.setattr(api.payment_service, "wechat", provider)
    client = TestClient(api.app)

    first = client.post("/api/billing/wechat/notify", content=body, headers=headers)
    duplicate = client.post("/api/billing/wechat/notify", content=body, headers=headers)

    assert first.status_code == 200
    assert duplicate.status_code == 200
    account = store.account_for_token(auth.token)
    assert account is not None
    assert account.membership_status == "active"
    assert account.first_month_discount_used is True


def test_alipay_api_rejects_wrong_amount_then_activates_valid_order(
    monkeypatch, tmp_path
) -> None:
    settings, platform_private = _provider_settings()
    store = AccountStore(tmp_path / "payments.sqlite3")
    auth = store.register("alipay-api@example.com", "correct-horse")
    order = store.create_order(
        auth.account.user_id, "yearly", payment_provider="alipay"
    )
    provider = AlipayProvider(settings)
    monkeypatch.setattr(api, "account_store", store)
    monkeypatch.setattr(api.payment_service, "alipay", provider)
    client = TestClient(api.app)

    def notification(amount: str) -> str:
        parameters = {
            "app_id": provider.app_id,
            "seller_id": provider.seller_id,
            "out_trade_no": order.order_id,
            "trade_no": "ali_transaction_api",
            "trade_status": "TRADE_SUCCESS",
            "total_amount": amount,
            "sign_type": "RSA2",
        }
        parameters["sign"] = base64.b64encode(
            platform_private.sign(
                provider._canonical(parameters).encode(),
                padding.PKCS1v15(),
                hashes.SHA256(),
            )
        ).decode()
        return urlencode(parameters)

    wrong = client.post(
        "/api/billing/alipay/notify",
        content=notification("9.90"),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert wrong.status_code == 400
    assert store.account_for_token(auth.token).membership_status == "inactive"

    accepted = client.post(
        "/api/billing/alipay/notify",
        content=notification("168.00"),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert accepted.status_code == 200
    assert accepted.text == "success"
    assert store.account_for_token(auth.token).membership_type == "yearly"
