from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.accounts import AccountStore, CoachMemory, PaymentCallback, callback_signature


def test_phone_account_normalizes_login_and_keeps_legacy_email_login(tmp_path) -> None:
    store = AccountStore(tmp_path / "phone-accounts.sqlite3")
    phone_auth = store.register("+86 139-0013-9000", "correct-horse")
    legacy_auth = store.register("legacy@example.com", "correct-horse")

    assert phone_auth.account.phone == "13900139000"
    assert phone_auth.account.email is None
    assert store.login("13900139000", "correct-horse").account.user_id == phone_auth.account.user_id
    assert store.login("legacy@example.com", "correct-horse").account.user_id == legacy_auth.account.user_id

    try:
        store.register("13900139000", "another-password")
    except ValueError as exc:
        assert "手机号已经注册" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("duplicate phone should be rejected")


def test_free_analysis_is_reserved_atomically_and_consumed_only_on_success(tmp_path) -> None:
    store = AccountStore(tmp_path / "accounts.sqlite3")
    auth = store.register("new@example.com", "correct-horse")

    first = store.reserve_analysis(auth.account.user_id, "analysis-one")
    concurrent = store.reserve_analysis(auth.account.user_id, "analysis-two")
    assert first.allowed is True
    assert first.reason == "free_trial"
    assert concurrent.allowed is False

    store.finish_analysis(first.reservation_id, success=False)
    retry = store.reserve_analysis(auth.account.user_id, "analysis-two")
    assert retry.allowed is True
    store.finish_analysis(retry.reservation_id, success=True)

    account = store.account_for_token(auth.token)
    assert account is not None
    assert account.free_analysis_used is True
    assert store.reserve_analysis(auth.account.user_id, "analysis-three").allowed is False
    store.logout(auth.token)
    assert store.account_for_token(auth.token) is None


def test_signed_payment_activates_membership_and_intro_price_is_once(tmp_path) -> None:
    store = AccountStore(tmp_path / "accounts.sqlite3")
    auth = store.register("member@example.com", "correct-horse")
    order = store.create_order(auth.account.user_id, "monthly")
    assert order.amount_fen == 990
    callback = PaymentCallback(
        order_id=order.order_id,
        status="paid",
        provider_transaction_id="provider-1",
    )
    assert len(callback_signature("secret", callback)) == 64
    user_id, renewal, processed, membership_type = store.apply_payment_callback(callback)
    assert user_id == auth.account.user_id
    assert renewal is False
    assert processed is True
    assert membership_type == "monthly"
    assert store.apply_payment_callback(callback)[2] is False

    active = store.account_for_token(auth.token)
    assert active is not None
    assert active.membership_status == "active"
    assert active.membership_type == "monthly"
    assert active.first_month_discount_used is True
    assert store.reserve_analysis(active.user_id, "member-analysis").reason == "member"
    assert store.create_order(active.user_id, "monthly").amount_fen == 1990


def test_expired_membership_loses_access_and_is_persisted(tmp_path) -> None:
    store = AccountStore(tmp_path / "accounts.sqlite3")
    auth = store.register("expired@example.com", "correct-horse")
    expired_at = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    with store._connect() as connection:
        connection.execute(
            "UPDATE users SET membership_status='active', membership_type='monthly', membership_expire_date=? WHERE user_id=?",
            (expired_at, auth.account.user_id),
        )

    expired = store.account_for_token(auth.token)
    assert expired is not None
    assert expired.membership_status == "expired"
    assert store.reserve_analysis(expired.user_id, "after-expiry").reason == "free_trial"
    with store._connect() as connection:
        persisted = connection.execute(
            "SELECT membership_status FROM users WHERE user_id=?", (expired.user_id,)
        ).fetchone()
    assert persisted["membership_status"] == "expired"


def test_renewal_extends_current_expiry_and_failed_callbacks_are_idempotent(tmp_path) -> None:
    store = AccountStore(tmp_path / "accounts.sqlite3")
    auth = store.register("renew@example.com", "correct-horse")
    first_order = store.create_order(auth.account.user_id, "monthly")
    first_callback = PaymentCallback(order_id=first_order.order_id, status="paid")
    store.apply_payment_callback(first_callback)
    first_expiry = datetime.fromisoformat(
        store.account_for_token(auth.token).membership_expire_date
    )

    renewal_order = store.create_order(auth.account.user_id, "monthly")
    renewal_callback = PaymentCallback(order_id=renewal_order.order_id, status="paid")
    _, renewal, processed, _ = store.apply_payment_callback(renewal_callback)
    renewed_expiry = datetime.fromisoformat(
        store.account_for_token(auth.token).membership_expire_date
    )
    assert renewal is True
    assert processed is True
    assert renewed_expiry == first_expiry + timedelta(days=30)
    assert store.apply_payment_callback(renewal_callback)[2] is False
    delayed_failure = PaymentCallback(order_id=renewal_order.order_id, status="failed")
    assert store.apply_payment_callback(delayed_failure)[2] is False
    still_active = store.account_for_token(auth.token)
    assert still_active is not None
    assert still_active.membership_status == "active"
    assert still_active.payment_status == "paid"

    failed_order = store.create_order(auth.account.user_id, "yearly")
    failed_callback = PaymentCallback(order_id=failed_order.order_id, status="failed")
    assert store.apply_payment_callback(failed_callback)[2] is True
    assert store.apply_payment_callback(failed_callback)[2] is False


def test_coach_memory_is_isolated_by_user_and_current_game_can_be_excluded(tmp_path) -> None:
    store = AccountStore(tmp_path / "accounts.sqlite3")
    first = store.register("first@example.com", "correct-horse")
    second = store.register("second@example.com", "correct-horse")
    memory = CoachMemory(
        key_lesson="先处理直接威胁",
        practical_focus="先看将军和吃子",
        error_types=["战术检查"],
        key_positions=["18.Ng5"],
        training_advice="复盘该局面",
    )
    store.save_coach_memory(first.account.user_id, "analysis-a", memory)
    store.save_coach_memory(second.account.user_id, "analysis-b", memory)

    assert len(store.recent_coach_memories(first.account.user_id)) == 1
    assert store.recent_coach_memories(first.account.user_id, exclude_analysis_id="analysis-a") == []
    assert len(store.recent_coach_memories(second.account.user_id)) == 1
