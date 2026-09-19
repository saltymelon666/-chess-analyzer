from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

try:
    import psycopg
    from psycopg.rows import dict_row
except ImportError:  # pragma: no cover - SQLite development does not need psycopg.
    psycopg = None
    dict_row = None


MembershipType = Literal["free", "monthly", "yearly"]
MembershipStatus = Literal["inactive", "active", "expired", "cancelled"]
PaymentStatus = Literal["none", "pending", "paid", "failed", "cancelled"]
ManualPaymentStatus = Literal["pending", "approved", "rejected"]


def _normalize_mainland_phone(value: str) -> str:
    normalized = re.sub(r"[\s-]", "", value.strip())
    if normalized.startswith("+86"):
        normalized = normalized[3:]
    elif normalized.startswith("86") and len(normalized) == 13:
        normalized = normalized[2:]
    if re.fullmatch(r"1[3-9]\d{9}", normalized) is None:
        raise ValueError("请输入有效的中国大陆手机号")
    return normalized


class RegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    phone: str = Field(min_length=11, max_length=24)
    password: str = Field(min_length=8, max_length=128)
    password_confirm: str = Field(min_length=8, max_length=128)

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, value: str) -> str:
        return _normalize_mainland_phone(value)

    @model_validator(mode="after")
    def validate_password_confirmation(self) -> "RegisterRequest":
        if self.password != self.password_confirm:
            raise ValueError("两次输入的密码不一致")
        return self


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    identifier: str = Field(min_length=5, max_length=254)
    password: str = Field(min_length=8, max_length=128)

    @field_validator("identifier")
    @classmethod
    def validate_identifier(cls, value: str) -> str:
        normalized = value.strip()
        if "@" in normalized:
            normalized = normalized.lower()
            if normalized.count("@") != 1 or normalized.startswith("@") or normalized.endswith("@"):
                raise ValueError("请输入有效手机号")
            return normalized
        return _normalize_mainland_phone(normalized)


class UserAccount(BaseModel):
    user_id: str
    phone: str | None = None
    email: str | None = None
    free_analysis_used: bool
    membership_status: MembershipStatus
    membership_type: MembershipType
    membership_start_date: str | None = None
    membership_expire_date: str | None = None
    first_month_discount_used: bool
    payment_status: PaymentStatus
    payment_order_id: str | None = None


class AuthResponse(BaseModel):
    token: str
    account: UserAccount


class OrderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    membership_type: Literal["monthly", "yearly"]
    payment_provider: Literal["wechat", "alipay"] | None = None
    client_type: Literal["desktop", "mobile"] = "desktop"


class PaymentOrder(BaseModel):
    order_id: str
    membership_type: Literal["monthly", "yearly"]
    amount_fen: int
    amount_display: str
    status: PaymentStatus
    payment_provider: Literal[
        "wechat", "alipay", "manual_wechat", "manual_alipay", "internal"
    ] = "internal"
    checkout_url: str | None = None
    qr_code_url: str | None = None
    expires_at: str | None = None


class PaymentCallback(BaseModel):
    model_config = ConfigDict(extra="forbid")

    order_id: str = Field(min_length=8, max_length=80)
    status: Literal["paid", "failed"]
    provider_transaction_id: str | None = Field(default=None, max_length=160)

    @field_validator("order_id")
    @classmethod
    def validate_order_id(cls, value: str) -> str:
        if not all(character.isalnum() or character in "_-" for character in value):
            raise ValueError("unsupported order identifier")
        return value


class ManualPaymentCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    membership_type: Literal["monthly", "yearly"]
    payment_provider: Literal["wechat", "alipay"]
    phone: str = Field(min_length=11, max_length=24)

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, value: str) -> str:
        return _normalize_mainland_phone(value)


class ManualPaymentReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["approve", "reject"]
    note: str = Field(default="", max_length=500)


class ManualPaymentRequest(BaseModel):
    request_id: str
    order_id: str
    user_id: str
    phone: str
    membership_type: Literal["monthly", "yearly"]
    payment_provider: Literal["wechat", "alipay"]
    amount_fen: int
    amount_display: str
    status: ManualPaymentStatus
    created_at: str
    reviewed_at: str | None = None
    reviewer_note: str = ""


class CoachMemory(BaseModel):
    key_lesson: str
    practical_focus: str
    error_types: list[str]
    key_positions: list[str]
    training_advice: str


class EntitlementDecision(BaseModel):
    allowed: bool
    reservation_id: str | None = None
    reason: Literal["member", "free_trial", "subscription_required"]


class AccountStore:
    """Persistent accounts, entitlements, payment state and per-user coach memory."""

    SESSION_DAYS = 30

    def __init__(self, database_location: Path | str) -> None:
        location = str(database_location)
        self._postgres = location.startswith(("postgresql://", "postgres://"))
        self.database_url = location if self._postgres else None
        self.database_path = None if self._postgres else Path(database_location)
        if self._postgres and psycopg is None:
            raise RuntimeError("Postgres accounts require psycopg")
        if self.database_path is not None:
            self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self):
        if self._postgres:
            return psycopg.connect(self.database_url, row_factory=dict_row)
        assert self.database_path is not None
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def _execute(self, connection, statement: str, parameters: tuple = ()):
        sql = statement.replace("?", "%s") if self._postgres else statement
        return connection.execute(sql, parameters)

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="milliseconds")

    @staticmethod
    def _password_hash(password: str, salt: bytes | None = None) -> str:
        actual_salt = salt or secrets.token_bytes(16)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), actual_salt, 310_000)
        return f"pbkdf2_sha256$310000${actual_salt.hex()}${digest.hex()}"

    @staticmethod
    def _password_valid(password: str, encoded: str) -> bool:
        try:
            algorithm, iterations, salt_hex, digest_hex = encoded.split("$", 3)
            if algorithm != "pbkdf2_sha256" or iterations != "310000":
                return False
            candidate = AccountStore._password_hash(password, bytes.fromhex(salt_hex))
            return hmac.compare_digest(candidate, encoded)
        except (ValueError, TypeError):
            return False

    @staticmethod
    def _token_hash(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def _initialize(self) -> None:
        schema = """
        CREATE TABLE IF NOT EXISTS users (
            user_id TEXT PRIMARY KEY,
            email TEXT NOT NULL UNIQUE,
            phone TEXT,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL,
            free_analysis_used INTEGER NOT NULL DEFAULT 0,
            membership_status TEXT NOT NULL DEFAULT 'inactive',
            membership_type TEXT NOT NULL DEFAULT 'free',
            membership_start_date TEXT,
            membership_expire_date TEXT,
            first_month_discount_used INTEGER NOT NULL DEFAULT 0,
            payment_status TEXT NOT NULL DEFAULT 'none',
            payment_order_id TEXT
        );
        CREATE TABLE IF NOT EXISTS user_sessions (
            token_hash TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(user_id)
        );
        CREATE INDEX IF NOT EXISTS idx_sessions_user ON user_sessions(user_id);
        CREATE TABLE IF NOT EXISTS payment_orders (
            order_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            membership_type TEXT NOT NULL,
            amount_fen INTEGER NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            paid_at TEXT,
            provider_transaction_id TEXT,
            payment_provider TEXT NOT NULL DEFAULT 'internal',
            expires_at TEXT,
            FOREIGN KEY(user_id) REFERENCES users(user_id)
        );
        CREATE INDEX IF NOT EXISTS idx_orders_user ON payment_orders(user_id, created_at);
        CREATE TABLE IF NOT EXISTS manual_payment_requests (
            request_id TEXT PRIMARY KEY,
            order_id TEXT NOT NULL UNIQUE,
            user_id TEXT NOT NULL,
            email TEXT NOT NULL,
            phone TEXT,
            membership_type TEXT NOT NULL,
            payment_provider TEXT NOT NULL,
            amount_fen INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL,
            reviewed_at TEXT,
            reviewer_note TEXT NOT NULL DEFAULT '',
            FOREIGN KEY(order_id) REFERENCES payment_orders(order_id),
            FOREIGN KEY(user_id) REFERENCES users(user_id)
        );
        CREATE INDEX IF NOT EXISTS idx_manual_payment_status_created
            ON manual_payment_requests(status, created_at);
        CREATE INDEX IF NOT EXISTS idx_manual_payment_user_created
            ON manual_payment_requests(user_id, created_at);
        CREATE TABLE IF NOT EXISTS analysis_entitlements (
            reservation_id TEXT PRIMARY KEY,
            analysis_id TEXT NOT NULL UNIQUE,
            user_id TEXT NOT NULL,
            entitlement_type TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            completed_at TEXT,
            FOREIGN KEY(user_id) REFERENCES users(user_id)
        );
        CREATE INDEX IF NOT EXISTS idx_entitlements_user ON analysis_entitlements(user_id, status);
        CREATE TABLE IF NOT EXISTS coach_memories (
            memory_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            analysis_id TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL,
            key_lesson TEXT NOT NULL,
            practical_focus TEXT NOT NULL,
            error_types_json TEXT NOT NULL,
            key_positions_json TEXT NOT NULL,
            training_advice TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(user_id)
        );
        CREATE INDEX IF NOT EXISTS idx_coach_user_created ON coach_memories(user_id, created_at);
        """
        with self._lock, self._connect() as connection:
            if self._postgres:
                for statement in schema.split(";"):
                    if statement.strip():
                        connection.execute(statement)
            else:
                connection.executescript(schema)
            # Incremental compatibility for databases created before real
            # provider routing was introduced.
            migration_statements = (
                "ALTER TABLE payment_orders ADD COLUMN IF NOT EXISTS payment_provider TEXT NOT NULL DEFAULT 'internal'",
                "ALTER TABLE payment_orders ADD COLUMN IF NOT EXISTS expires_at TEXT",
                "ALTER TABLE manual_payment_requests ADD COLUMN IF NOT EXISTS phone TEXT",
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS phone TEXT",
            ) if self._postgres else (
                "ALTER TABLE payment_orders ADD COLUMN payment_provider TEXT NOT NULL DEFAULT 'internal'",
                "ALTER TABLE payment_orders ADD COLUMN expires_at TEXT",
                "ALTER TABLE manual_payment_requests ADD COLUMN phone TEXT",
                "ALTER TABLE users ADD COLUMN phone TEXT",
            )
            for statement in migration_statements:
                try:
                    self._execute(connection, statement)
                except Exception as exc:
                    message = str(exc).lower()
                    if "duplicate" not in message and "already exists" not in message:
                        raise
            self._execute(
                connection,
                """CREATE UNIQUE INDEX IF NOT EXISTS idx_orders_provider_tx
                   ON payment_orders(payment_provider, provider_transaction_id)
                   WHERE provider_transaction_id IS NOT NULL""",
            )
            self._execute(
                connection,
                """CREATE UNIQUE INDEX IF NOT EXISTS idx_users_phone
                   ON users(phone) WHERE phone IS NOT NULL""",
            )

    def _row_account(self, row) -> UserAccount:
        status = str(row["membership_status"])
        expiry = row["membership_expire_date"]
        if status == "active" and expiry:
            try:
                if datetime.fromisoformat(str(expiry)) <= datetime.now(timezone.utc):
                    status = "expired"
            except ValueError:
                status = "expired"
        return UserAccount(
            user_id=str(row["user_id"]),
            phone=str(row["phone"]) if row["phone"] else None,
            email=None if row["phone"] else str(row["email"]),
            free_analysis_used=bool(row["free_analysis_used"]),
            membership_status=status,
            membership_type=str(row["membership_type"]),
            membership_start_date=row["membership_start_date"],
            membership_expire_date=expiry,
            first_month_discount_used=bool(row["first_month_discount_used"]),
            payment_status=str(row["payment_status"]),
            payment_order_id=row["payment_order_id"],
        )

    def register(self, identifier: str, password: str) -> AuthResponse:
        raw_identifier = identifier.strip()
        is_phone = "@" not in raw_identifier
        if is_phone:
            normalized_phone = _normalize_mainland_phone(raw_identifier)
            normalized_email = f"{normalized_phone}@phone.pawnlab.local"
        else:
            normalized_phone = None
            normalized_email = raw_identifier.lower()
            if normalized_email.count("@") != 1 or normalized_email.startswith("@") or normalized_email.endswith("@"):
                raise ValueError("请输入有效邮箱")
        user_id = f"usr_{uuid4().hex}"
        now = self._now()
        with self._lock, self._connect() as connection:
            try:
                self._execute(
                    connection,
                    "INSERT INTO users(user_id,email,phone,password_hash,created_at) VALUES (?,?,?,?,?)",
                    (
                        user_id,
                        normalized_email,
                        normalized_phone,
                        self._password_hash(password),
                        now,
                    ),
                )
            except Exception as exc:
                if "unique" in str(exc).lower() or "duplicate" in str(exc).lower():
                    raise ValueError("该手机号已经注册" if is_phone else "该邮箱已经注册") from exc
                raise
        return self._new_session(user_id)

    def login(self, identifier: str, password: str) -> AuthResponse:
        raw_identifier = identifier.strip()
        if "@" in raw_identifier:
            condition = "email = ?"
            normalized = raw_identifier.lower()
        else:
            condition = "phone = ?"
            normalized = _normalize_mainland_phone(raw_identifier)
        with self._lock, self._connect() as connection:
            row = self._execute(
                connection,
                f"SELECT * FROM users WHERE {condition}",
                (normalized,),
            ).fetchone()
        if row is None or not self._password_valid(password, str(row["password_hash"])):
            raise ValueError("手机号或密码不正确")
        return self._new_session(str(row["user_id"]))

    def _new_session(self, user_id: str) -> AuthResponse:
        token = secrets.token_urlsafe(32)
        now = datetime.now(timezone.utc)
        expires = now + timedelta(days=self.SESSION_DAYS)
        with self._lock, self._connect() as connection:
            self._execute(
                connection,
                "INSERT INTO user_sessions(token_hash,user_id,created_at,expires_at) VALUES (?,?,?,?)",
                (self._token_hash(token), user_id, now.isoformat(), expires.isoformat()),
            )
            row = self._execute(connection, "SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
        assert row is not None
        return AuthResponse(token=token, account=self._row_account(row))

    def account_for_token(self, token: str) -> UserAccount | None:
        now = self._now()
        with self._lock, self._connect() as connection:
            row = self._execute(
                connection,
                """SELECT u.* FROM user_sessions s JOIN users u ON u.user_id=s.user_id
                   WHERE s.token_hash=? AND s.expires_at>?""",
                (self._token_hash(token), now),
            ).fetchone()
            if row is None:
                return None
            account = self._row_account(row)
            if account.membership_status == "expired" and str(row["membership_status"]) == "active":
                self._execute(
                    connection,
                    "UPDATE users SET membership_status='expired' WHERE user_id=?",
                    (account.user_id,),
                )
            return account

    def logout(self, token: str) -> None:
        with self._lock, self._connect() as connection:
            self._execute(
                connection,
                "DELETE FROM user_sessions WHERE token_hash=?",
                (self._token_hash(token),),
            )

    def reserve_analysis(self, user_id: str, analysis_id: str) -> EntitlementDecision:
        reservation_id = f"ent_{uuid4().hex}"
        with self._lock, self._connect() as connection:
            if not self._postgres:
                connection.execute("BEGIN IMMEDIATE")
            suffix = " FOR UPDATE" if self._postgres else ""
            row = self._execute(
                connection,
                f"SELECT * FROM users WHERE user_id=?{suffix}",
                (user_id,),
            ).fetchone()
            if row is None:
                return EntitlementDecision(allowed=False, reason="subscription_required")
            account = self._row_account(row)
            if account.membership_status == "active":
                entitlement = "member"
            else:
                pending = self._execute(
                    connection,
                    "SELECT 1 FROM analysis_entitlements WHERE user_id=? AND entitlement_type='free_trial' AND status='reserved'",
                    (user_id,),
                ).fetchone()
                if account.free_analysis_used or pending is not None:
                    return EntitlementDecision(allowed=False, reason="subscription_required")
                entitlement = "free_trial"
            self._execute(
                connection,
                """INSERT INTO analysis_entitlements(
                    reservation_id,analysis_id,user_id,entitlement_type,status,created_at
                ) VALUES (?,?,?,?, 'reserved', ?)""",
                (reservation_id, analysis_id, user_id, entitlement, self._now()),
            )
        return EntitlementDecision(
            allowed=True,
            reservation_id=reservation_id,
            reason="member" if entitlement == "member" else "free_trial",
        )

    def finish_analysis(self, reservation_id: str, *, success: bool) -> None:
        with self._lock, self._connect() as connection:
            if not self._postgres:
                connection.execute("BEGIN IMMEDIATE")
            suffix = " FOR UPDATE" if self._postgres else ""
            row = self._execute(
                connection,
                f"SELECT * FROM analysis_entitlements WHERE reservation_id=?{suffix}",
                (reservation_id,),
            ).fetchone()
            if row is None or row["status"] != "reserved":
                return
            new_status = "completed" if success else "released"
            self._execute(
                connection,
                "UPDATE analysis_entitlements SET status=?,completed_at=? WHERE reservation_id=?",
                (new_status, self._now(), reservation_id),
            )
            if success and row["entitlement_type"] == "free_trial":
                self._execute(
                    connection,
                    "UPDATE users SET free_analysis_used=1 WHERE user_id=?",
                    (row["user_id"],),
                )

    def create_order(
        self,
        user_id: str,
        membership_type: str,
        checkout_base_url: str = "",
        payment_provider: str = "internal",
    ) -> PaymentOrder:
        if payment_provider not in {
            "wechat", "alipay", "manual_wechat", "manual_alipay", "internal"
        }:
            raise ValueError("不支持的支付方式")
        with self._lock, self._connect() as connection:
            row = self._execute(connection, "SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
            if row is None:
                raise ValueError("账号不存在")
            monthly_intro = not bool(row["first_month_discount_used"])
            amount_fen = 990 if membership_type == "monthly" and monthly_intro else 1990
            if membership_type == "yearly":
                amount_fen = 16800
            order_id = f"ord_{uuid4().hex}"
            expires_at = (datetime.now(timezone.utc) + timedelta(minutes=15)).isoformat(timespec="seconds")
            self._execute(
                connection,
                """INSERT INTO payment_orders(
                    order_id,user_id,membership_type,amount_fen,status,created_at,
                    payment_provider,expires_at
                ) VALUES (?,?,?,?, 'pending', ?,?,?)""",
                (
                    order_id, user_id, membership_type, amount_fen, self._now(),
                    payment_provider, expires_at,
                ),
            )
            self._execute(
                connection,
                "UPDATE users SET payment_status='pending',payment_order_id=? WHERE user_id=?",
                (order_id, user_id),
            )
        checkout_url = None
        if checkout_base_url:
            parsed = urlsplit(checkout_base_url)
            if parsed.scheme == "https" and parsed.netloc:
                checkout_url = f"{checkout_base_url.rstrip('/')}?order_id={order_id}"
        return PaymentOrder(
            order_id=order_id,
            membership_type=membership_type,
            amount_fen=amount_fen,
            amount_display=f"¥{amount_fen / 100:g}",
            status="pending",
            payment_provider=payment_provider,
            checkout_url=checkout_url,
            expires_at=expires_at,
        )

    def payment_order(self, order_id: str, user_id: str | None = None) -> PaymentOrder:
        with self._lock, self._connect() as connection:
            if user_id is None:
                row = self._execute(
                    connection, "SELECT * FROM payment_orders WHERE order_id=?", (order_id,)
                ).fetchone()
            else:
                row = self._execute(
                    connection,
                    "SELECT * FROM payment_orders WHERE order_id=? AND user_id=?",
                    (order_id, user_id),
                ).fetchone()
        if row is None:
            raise ValueError("订单不存在")
        return PaymentOrder(
            order_id=str(row["order_id"]),
            membership_type=str(row["membership_type"]),
            amount_fen=int(row["amount_fen"]),
            amount_display=f"¥{int(row['amount_fen']) / 100:g}",
            status=str(row["status"]),
            payment_provider=str(row["payment_provider"] or "internal"),
            expires_at=row["expires_at"],
        )

    @staticmethod
    def _row_manual_payment(row) -> ManualPaymentRequest:
        return ManualPaymentRequest(
            request_id=str(row["request_id"]),
            order_id=str(row["order_id"]),
            user_id=str(row["user_id"]),
            phone=str(row["phone"] or ""),
            membership_type=str(row["membership_type"]),
            payment_provider=str(row["payment_provider"]),
            amount_fen=int(row["amount_fen"]),
            amount_display=f"¥{int(row['amount_fen']) / 100:g}",
            status=str(row["status"]),
            created_at=str(row["created_at"]),
            reviewed_at=row["reviewed_at"],
            reviewer_note=str(row["reviewer_note"] or ""),
        )

    def create_manual_payment_request(
        self,
        user_id: str,
        phone: str,
        membership_type: str,
        payment_provider: str,
    ) -> ManualPaymentRequest:
        if membership_type not in {"monthly", "yearly"}:
            raise ValueError("不支持的会员方案")
        if payment_provider not in {"wechat", "alipay"}:
            raise ValueError("不支持的收款方式")
        normalized_phone = ManualPaymentCreateRequest(
            membership_type=membership_type,
            payment_provider=payment_provider,
            phone=phone,
        ).phone
        with self._lock, self._connect() as connection:
            if not self._postgres:
                connection.execute("BEGIN IMMEDIATE")
            suffix = " FOR UPDATE" if self._postgres else ""
            user = self._execute(
                connection, f"SELECT * FROM users WHERE user_id=?{suffix}", (user_id,)
            ).fetchone()
            if user is None:
                raise ValueError("账号不存在")
            existing = self._execute(
                connection,
                """SELECT * FROM manual_payment_requests
                   WHERE user_id=? AND membership_type=? AND payment_provider=?
                     AND status='pending'
                   ORDER BY created_at DESC LIMIT 1""",
                (user_id, membership_type, payment_provider),
            ).fetchone()
            if existing is not None:
                return self._row_manual_payment(existing)
            monthly_intro = not bool(user["first_month_discount_used"])
            amount_fen = 990 if membership_type == "monthly" and monthly_intro else 1990
            if membership_type == "yearly":
                amount_fen = 16800
            now = self._now()
            order_id = f"ord_{uuid4().hex}"
            request_id = f"mpr_{uuid4().hex}"
            provider = f"manual_{payment_provider}"
            expires_at = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat(timespec="seconds")
            self._execute(
                connection,
                """INSERT INTO payment_orders(
                    order_id,user_id,membership_type,amount_fen,status,created_at,
                    payment_provider,expires_at
                ) VALUES (?,?,?,?, 'pending', ?,?,?)""",
                (
                    order_id, user_id, membership_type, amount_fen, now,
                    provider, expires_at,
                ),
            )
            self._execute(
                connection,
                """INSERT INTO manual_payment_requests(
                    request_id,order_id,user_id,email,phone,membership_type,payment_provider,
                    amount_fen,status,created_at
                ) VALUES (?,?,?,?,?,?,?,?,'pending',?)""",
                (
                    request_id, order_id, user_id, str(user["email"]), normalized_phone,
                    membership_type, payment_provider, amount_fen, now,
                ),
            )
            self._execute(
                connection,
                "UPDATE users SET payment_status='pending',payment_order_id=? WHERE user_id=?",
                (order_id, user_id),
            )
            row = self._execute(
                connection,
                "SELECT * FROM manual_payment_requests WHERE request_id=?",
                (request_id,),
            ).fetchone()
        assert row is not None
        return self._row_manual_payment(row)

    def manual_payment_requests(
        self,
        *,
        user_id: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[ManualPaymentRequest]:
        if status is not None and status not in {"pending", "approved", "rejected"}:
            raise ValueError("不支持的审核状态")
        safe_limit = max(1, min(int(limit), 200))
        conditions: list[str] = []
        parameters: list[object] = []
        if user_id is not None:
            conditions.append("user_id=?")
            parameters.append(user_id)
        if status is not None:
            conditions.append("status=?")
            parameters.append(status)
        where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        parameters.append(safe_limit)
        with self._lock, self._connect() as connection:
            rows = self._execute(
                connection,
                f"SELECT * FROM manual_payment_requests{where} ORDER BY created_at DESC LIMIT ?",
                tuple(parameters),
            ).fetchall()
        return [self._row_manual_payment(row) for row in rows]

    def review_manual_payment(
        self,
        request_id: str,
        *,
        approve: bool,
        note: str = "",
    ) -> tuple[ManualPaymentRequest, bool, bool]:
        with self._lock, self._connect() as connection:
            if not self._postgres:
                connection.execute("BEGIN IMMEDIATE")
            suffix = " FOR UPDATE" if self._postgres else ""
            request_row = self._execute(
                connection,
                f"SELECT * FROM manual_payment_requests WHERE request_id=?{suffix}",
                (request_id,),
            ).fetchone()
            if request_row is None:
                raise ValueError("审核申请不存在")
            if request_row["status"] != "pending":
                return self._row_manual_payment(request_row), False, False
            order = self._execute(
                connection,
                f"SELECT * FROM payment_orders WHERE order_id=?{suffix}",
                (request_row["order_id"],),
            ).fetchone()
            if order is None:
                raise ValueError("关联订单不存在")
            expected_provider = f"manual_{request_row['payment_provider']}"
            if str(order["payment_provider"]) != expected_provider:
                raise ValueError("订单支付方式不匹配")
            if int(order["amount_fen"]) != int(request_row["amount_fen"]):
                raise ValueError("订单金额不匹配")
            reviewed_at = self._now()
            clean_note = note.strip()
            if not approve:
                self._execute(
                    connection,
                    "UPDATE payment_orders SET status='failed' WHERE order_id=? AND status<>'paid'",
                    (order["order_id"],),
                )
                self._execute(
                    connection,
                    """UPDATE users SET payment_status='failed',payment_order_id=?
                       WHERE user_id=? AND membership_status<>'active'""",
                    (order["order_id"], order["user_id"]),
                )
                self._execute(
                    connection,
                    """UPDATE manual_payment_requests
                       SET status='rejected',reviewed_at=?,reviewer_note=?
                       WHERE request_id=?""",
                    (reviewed_at, clean_note, request_id),
                )
                updated = self._execute(
                    connection,
                    "SELECT * FROM manual_payment_requests WHERE request_id=?",
                    (request_id,),
                ).fetchone()
                assert updated is not None
                return self._row_manual_payment(updated), True, False

            now = datetime.now(timezone.utc)
            membership_type = str(order["membership_type"])
            days = 365 if membership_type == "yearly" else 30
            user = self._execute(
                connection, "SELECT * FROM users WHERE user_id=?", (order["user_id"],)
            ).fetchone()
            if user is None:
                raise ValueError("账号不存在")
            is_renewal = self._row_account(user).membership_status == "active"
            start = now
            if is_renewal and user["membership_expire_date"]:
                try:
                    previous_expiry = datetime.fromisoformat(str(user["membership_expire_date"]))
                    if previous_expiry > now:
                        start = previous_expiry
                except ValueError:
                    pass
            expiry = start + timedelta(days=days)
            self._execute(
                connection,
                """UPDATE payment_orders
                   SET status='paid',paid_at=?,provider_transaction_id=?
                   WHERE order_id=?""",
                (now.isoformat(), f"manual:{request_id}", order["order_id"]),
            )
            self._execute(
                connection,
                """UPDATE users SET membership_status='active',membership_type=?,
                   membership_start_date=?,membership_expire_date=?,
                   first_month_discount_used=CASE WHEN ?='monthly' THEN 1 ELSE first_month_discount_used END,
                   payment_status='paid',payment_order_id=? WHERE user_id=?""",
                (
                    membership_type, now.isoformat(), expiry.isoformat(), membership_type,
                    order["order_id"], order["user_id"],
                ),
            )
            self._execute(
                connection,
                """UPDATE manual_payment_requests
                   SET status='approved',reviewed_at=?,reviewer_note=?
                   WHERE request_id=?""",
                (reviewed_at, clean_note, request_id),
            )
            updated = self._execute(
                connection,
                "SELECT * FROM manual_payment_requests WHERE request_id=?",
                (request_id,),
            ).fetchone()
        assert updated is not None
        return self._row_manual_payment(updated), True, is_renewal

    def apply_payment_callback(
        self,
        callback: PaymentCallback,
        *,
        expected_provider: str | None = None,
        expected_amount_fen: int | None = None,
        expected_currency: str = "CNY",
    ) -> tuple[str, bool, bool, str]:
        with self._lock, self._connect() as connection:
            if not self._postgres:
                connection.execute("BEGIN IMMEDIATE")
            suffix = " FOR UPDATE" if self._postgres else ""
            order = self._execute(
                connection,
                f"SELECT * FROM payment_orders WHERE order_id=?{suffix}",
                (callback.order_id,),
            ).fetchone()
            if order is None:
                raise ValueError("订单不存在")
            if expected_provider is not None and str(order["payment_provider"] or "internal") != expected_provider:
                raise ValueError("订单支付方式不匹配")
            if expected_currency != "CNY":
                raise ValueError("订单币种不匹配")
            if expected_amount_fen is not None and int(order["amount_fen"]) != expected_amount_fen:
                raise ValueError("订单金额不匹配")
            # Paid is terminal: a delayed or duplicated provider notification
            # must never downgrade an activated membership.
            if order["status"] == "paid":
                return str(order["user_id"]), False, False, str(order["membership_type"])
            if order["status"] == "failed" and callback.status == "failed":
                return str(order["user_id"]), False, False, str(order["membership_type"])
            if callback.status == "failed":
                self._execute(connection, "UPDATE payment_orders SET status='failed' WHERE order_id=?", (callback.order_id,))
                self._execute(
                    connection,
                    "UPDATE users SET payment_status='failed',payment_order_id=? WHERE user_id=?",
                    (callback.order_id, order["user_id"]),
                )
                return str(order["user_id"]), False, True, str(order["membership_type"])
            now = datetime.now(timezone.utc)
            membership_type = str(order["membership_type"])
            days = 365 if membership_type == "yearly" else 30
            existing = self._execute(connection, "SELECT * FROM users WHERE user_id=?", (order["user_id"],)).fetchone()
            is_renewal = bool(existing and self._row_account(existing).membership_status == "active")
            start = now
            if existing and existing["membership_status"] == "active" and existing["membership_expire_date"]:
                try:
                    previous_expiry = datetime.fromisoformat(str(existing["membership_expire_date"]))
                    if previous_expiry > now:
                        start = previous_expiry
                except ValueError:
                    pass
            expiry = start + timedelta(days=days)
            self._execute(
                connection,
                "UPDATE payment_orders SET status='paid',paid_at=?,provider_transaction_id=? WHERE order_id=?",
                (now.isoformat(), callback.provider_transaction_id, callback.order_id),
            )
            self._execute(
                connection,
                """UPDATE users SET membership_status='active',membership_type=?,
                   membership_start_date=?,membership_expire_date=?,
                   first_month_discount_used=CASE WHEN ?='monthly' THEN 1 ELSE first_month_discount_used END,
                   payment_status='paid',payment_order_id=? WHERE user_id=?""",
                (membership_type, now.isoformat(), expiry.isoformat(), membership_type, callback.order_id, order["user_id"]),
            )
            return str(order["user_id"]), is_renewal, True, membership_type

    def cancel_subscription(self, user_id: str) -> UserAccount:
        with self._lock, self._connect() as connection:
            self._execute(
                connection,
                # Cancellation stops renewal; already-paid access remains active until expiry.
                "UPDATE users SET payment_status='cancelled' WHERE user_id=?",
                (user_id,),
            )
            row = self._execute(connection, "SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
        if row is None:
            raise ValueError("账号不存在")
        return self._row_account(row)

    def save_coach_memory(self, user_id: str, analysis_id: str, memory: CoachMemory) -> None:
        with self._lock, self._connect() as connection:
            self._execute(
                connection,
                """INSERT INTO coach_memories(
                    memory_id,user_id,analysis_id,created_at,key_lesson,practical_focus,
                    error_types_json,key_positions_json,training_advice
                ) VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(analysis_id) DO NOTHING""",
                (
                    f"mem_{uuid4().hex}", user_id, analysis_id, self._now(),
                    memory.key_lesson, memory.practical_focus,
                    json.dumps(memory.error_types, ensure_ascii=False),
                    json.dumps(memory.key_positions, ensure_ascii=False), memory.training_advice,
                ),
            )

    def recent_coach_memories(
        self,
        user_id: str,
        limit: int = 5,
        exclude_analysis_id: str | None = None,
    ) -> list[CoachMemory]:
        safe_limit = max(1, min(limit, 5))
        with self._lock, self._connect() as connection:
            if exclude_analysis_id:
                rows = self._execute(
                    connection,
                    """SELECT * FROM coach_memories WHERE user_id=? AND analysis_id<>?
                       ORDER BY created_at DESC LIMIT ?""",
                    (user_id, exclude_analysis_id, safe_limit),
                ).fetchall()
            else:
                rows = self._execute(
                    connection,
                    """SELECT * FROM coach_memories WHERE user_id=?
                       ORDER BY created_at DESC LIMIT ?""",
                    (user_id, safe_limit),
                ).fetchall()
        return [
            CoachMemory(
                key_lesson=row["key_lesson"],
                practical_focus=row["practical_focus"],
                error_types=json.loads(row["error_types_json"]),
                key_positions=json.loads(row["key_positions_json"]),
                training_advice=row["training_advice"],
            )
            for row in rows
        ]


def callback_signature(secret: str, callback: PaymentCallback) -> str:
    message = f"{callback.order_id}:{callback.status}:{callback.provider_transaction_id or ''}"
    return hmac.new(secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()
