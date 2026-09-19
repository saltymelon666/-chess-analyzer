from __future__ import annotations

import base64
import io
import json
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Literal, Mapping
from urllib.parse import urlencode, urlsplit

import httpx
import qrcode
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .config import Settings


PaymentProviderName = Literal["wechat", "alipay"]
PaymentClientType = Literal["desktop", "mobile"]


class PaymentSetupError(RuntimeError):
    """The selected provider is not fully configured."""


class PaymentProviderError(RuntimeError):
    """A configured payment provider rejected or could not process a request."""


@dataclass(frozen=True)
class ProviderCheckout:
    checkout_url: str | None = None
    qr_code_url: str | None = None


@dataclass(frozen=True)
class VerifiedPayment:
    order_id: str
    provider_transaction_id: str
    amount_fen: int
    currency: str
    status: Literal["paid", "failed"]


def _pem(value: str) -> bytes:
    return value.replace("\\n", "\n").strip().encode("utf-8")


def _private_key(value: str):
    try:
        return serialization.load_pem_private_key(_pem(value), password=None)
    except (TypeError, ValueError) as exc:
        raise PaymentSetupError("支付商户私钥格式无效") from exc


def _public_key(value: str):
    try:
        return serialization.load_pem_public_key(_pem(value))
    except (TypeError, ValueError) as exc:
        raise PaymentSetupError("支付平台公钥格式无效") from exc


def _qr_data_uri(content: str) -> str:
    image = qrcode.make(content)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


class WeChatPayProvider:
    def __init__(self, settings: Settings) -> None:
        self.mch_id = settings.wechat_pay_mch_id
        self.app_id = settings.wechat_pay_app_id
        self.cert_serial = settings.wechat_pay_cert_serial
        self.private_key_pem = settings.wechat_pay_private_key
        self.api_v3_key = settings.wechat_pay_api_v3_key
        self.platform_public_key_pem = settings.wechat_pay_platform_public_key
        self.platform_serial = settings.wechat_pay_platform_serial
        self.gateway = settings.wechat_pay_gateway
        self.public_origin = settings.payment_public_origin
        self.frontend_origin = settings.payment_frontend_origin

    @property
    def configured(self) -> bool:
        return all(
            (
                self.mch_id,
                self.app_id,
                self.cert_serial,
                self.private_key_pem,
                self.api_v3_key,
                self.platform_public_key_pem,
                self.platform_serial,
                self.public_origin.startswith("https://"),
                self.frontend_origin.startswith("https://"),
            )
        ) and len(self.api_v3_key.encode("utf-8")) == 32

    def _require_configured(self) -> None:
        if not self.configured:
            raise PaymentSetupError("微信支付商户参数尚未完整配置")

    def _authorization(self, method: str, path: str, body: str) -> str:
        timestamp = str(int(time.time()))
        nonce = secrets.token_hex(16)
        message = f"{method}\n{path}\n{timestamp}\n{nonce}\n{body}\n".encode("utf-8")
        signature = _private_key(self.private_key_pem).sign(
            message, padding.PKCS1v15(), hashes.SHA256()
        )
        token = base64.b64encode(signature).decode("ascii")
        return (
            'WECHATPAY2-SHA256-RSA2048 '
            f'mchid="{self.mch_id}",nonce_str="{nonce}",timestamp="{timestamp}",'
            f'serial_no="{self.cert_serial}",signature="{token}"'
        )

    def _verify_platform_signature(
        self, body: bytes, timestamp: str, nonce: str, signature: str, serial: str
    ) -> None:
        if serial != self.platform_serial:
            raise PaymentProviderError("微信支付平台证书序列号不匹配")
        message = timestamp.encode() + b"\n" + nonce.encode() + b"\n" + body + b"\n"
        try:
            _public_key(self.platform_public_key_pem).verify(
                base64.b64decode(signature), message, padding.PKCS1v15(), hashes.SHA256()
            )
        except Exception as exc:
            raise PaymentProviderError("微信支付签名验证失败") from exc

    async def create_checkout(
        self,
        *,
        order_id: str,
        amount_fen: int,
        membership_type: str,
        client_type: PaymentClientType,
        client_ip: str,
    ) -> ProviderCheckout:
        self._require_configured()
        mobile = client_type == "mobile"
        path = "/v3/pay/transactions/h5" if mobile else "/v3/pay/transactions/native"
        payload: dict[str, object] = {
            "appid": self.app_id,
            "mchid": self.mch_id,
            "description": "PawnLab月会员" if membership_type == "monthly" else "PawnLab年会员",
            "out_trade_no": order_id,
            "time_expire": (datetime.now(timezone.utc) + timedelta(minutes=15)).isoformat(timespec="seconds"),
            "notify_url": f"{self.public_origin}/api/billing/wechat/notify",
            "amount": {"total": amount_fen, "currency": "CNY"},
        }
        if mobile:
            payload["scene_info"] = {
                "payer_client_ip": client_ip,
                "h5_info": {"type": "Wap", "app_name": "PawnLab", "app_url": self.frontend_origin},
            }
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        headers = {
            "Authorization": self._authorization("POST", path, body),
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "PawnLab/1.0",
        }
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.post(f"{self.gateway}{path}", content=body.encode(), headers=headers)
        except httpx.HTTPError as exc:
            raise PaymentProviderError("暂时无法连接微信支付，请稍后重试") from exc
        if response.status_code not in {200, 201}:
            raise PaymentProviderError("微信支付下单失败，请稍后重试")
        required_headers = (
            response.headers.get("Wechatpay-Timestamp", ""),
            response.headers.get("Wechatpay-Nonce", ""),
            response.headers.get("Wechatpay-Signature", ""),
            response.headers.get("Wechatpay-Serial", ""),
        )
        if not all(required_headers):
            raise PaymentProviderError("微信支付响应缺少验签信息")
        self._verify_platform_signature(response.content, *required_headers)
        data = response.json()
        if mobile:
            url = str(data.get("h5_url", ""))
            if not url.startswith("https://"):
                raise PaymentProviderError("微信支付未返回有效付款地址")
            return ProviderCheckout(checkout_url=url)
        code_url = str(data.get("code_url", ""))
        if not code_url.startswith("weixin://"):
            raise PaymentProviderError("微信支付未返回有效二维码")
        return ProviderCheckout(qr_code_url=_qr_data_uri(code_url))

    def verify_notification(self, body: bytes, headers: Mapping[str, str]) -> VerifiedPayment:
        self._require_configured()
        timestamp = headers.get("wechatpay-timestamp", "")
        nonce = headers.get("wechatpay-nonce", "")
        signature = headers.get("wechatpay-signature", "")
        serial = headers.get("wechatpay-serial", "")
        if not all((timestamp, nonce, signature, serial)):
            raise PaymentProviderError("微信支付通知缺少验签信息")
        try:
            if abs(int(time.time()) - int(timestamp)) > 300:
                raise PaymentProviderError("微信支付通知已过期")
        except ValueError as exc:
            raise PaymentProviderError("微信支付通知时间无效") from exc
        self._verify_platform_signature(body, timestamp, nonce, signature, serial)
        try:
            envelope = json.loads(body)
            resource = envelope["resource"]
            plaintext = AESGCM(self.api_v3_key.encode("utf-8")).decrypt(
                resource["nonce"].encode("utf-8"),
                base64.b64decode(resource["ciphertext"]),
                resource.get("associated_data", "").encode("utf-8"),
            )
            data = json.loads(plaintext)
        except Exception as exc:
            raise PaymentProviderError("微信支付通知解密失败") from exc
        if data.get("mchid") != self.mch_id or data.get("appid") != self.app_id:
            raise PaymentProviderError("微信支付商户信息不匹配")
        state = str(data.get("trade_state", ""))
        if state != "SUCCESS":
            raise PaymentProviderError("微信支付通知不是成功状态")
        amount = data.get("amount") or {}
        if not data.get("out_trade_no") or not data.get("transaction_id"):
            raise PaymentProviderError("微信支付通知缺少订单标识")
        return VerifiedPayment(
            order_id=str(data.get("out_trade_no", "")),
            provider_transaction_id=str(data.get("transaction_id", "")),
            amount_fen=int(amount.get("total", -1)),
            currency=str(amount.get("currency", "")),
            status="paid",
        )


class AlipayProvider:
    def __init__(self, settings: Settings) -> None:
        self.app_id = settings.alipay_app_id
        self.private_key_pem = settings.alipay_private_key
        self.public_key_pem = settings.alipay_public_key
        self.seller_id = settings.alipay_seller_id
        self.gateway = settings.alipay_gateway
        self.public_origin = settings.payment_public_origin
        self.frontend_origin = settings.payment_frontend_origin

    @property
    def configured(self) -> bool:
        parsed = urlsplit(self.gateway)
        return all(
            (
                self.app_id,
                self.private_key_pem,
                self.public_key_pem,
                self.seller_id,
                parsed.scheme == "https",
                parsed.netloc,
                self.public_origin.startswith("https://"),
                self.frontend_origin.startswith("https://"),
            )
        )

    def _require_configured(self) -> None:
        if not self.configured:
            raise PaymentSetupError("支付宝商户参数尚未完整配置")

    @staticmethod
    def _canonical(parameters: Mapping[str, str]) -> str:
        return "&".join(
            f"{key}={parameters[key]}"
            for key in sorted(parameters)
            if key not in {"sign", "sign_type"} and parameters[key] != ""
        )

    def _sign(self, parameters: Mapping[str, str]) -> str:
        signature = _private_key(self.private_key_pem).sign(
            self._canonical(parameters).encode("utf-8"),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
        return base64.b64encode(signature).decode("ascii")

    def create_checkout(
        self,
        *,
        order_id: str,
        amount_fen: int,
        membership_type: str,
        client_type: PaymentClientType,
    ) -> ProviderCheckout:
        self._require_configured()
        mobile = client_type == "mobile"
        parameters = {
            "app_id": self.app_id,
            "method": "alipay.trade.wap.pay" if mobile else "alipay.trade.page.pay",
            "format": "JSON",
            "charset": "utf-8",
            "sign_type": "RSA2",
            "timestamp": datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S"),
            "version": "1.0",
            "notify_url": f"{self.public_origin}/api/billing/alipay/notify",
            "return_url": f"{self.frontend_origin}/?payment=return&order_id={order_id}",
            "biz_content": json.dumps(
                {
                    "out_trade_no": order_id,
                    "total_amount": f"{Decimal(amount_fen) / 100:.2f}",
                    "subject": "PawnLab月会员" if membership_type == "monthly" else "PawnLab年会员",
                    "product_code": "QUICK_WAP_WAY" if mobile else "FAST_INSTANT_TRADE_PAY",
                    "timeout_express": "15m",
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ),
        }
        parameters["sign"] = self._sign(parameters)
        return ProviderCheckout(checkout_url=f"{self.gateway}?{urlencode(parameters)}")

    def verify_notification(self, parameters: Mapping[str, str]) -> VerifiedPayment:
        self._require_configured()
        signature = parameters.get("sign", "")
        if parameters.get("sign_type") != "RSA2" or not signature:
            raise PaymentProviderError("支付宝通知缺少有效签名")
        try:
            _public_key(self.public_key_pem).verify(
                base64.b64decode(signature),
                self._canonical(parameters).encode("utf-8"),
                padding.PKCS1v15(),
                hashes.SHA256(),
            )
        except Exception as exc:
            raise PaymentProviderError("支付宝通知签名验证失败") from exc
        if parameters.get("app_id") != self.app_id or parameters.get("seller_id") != self.seller_id:
            raise PaymentProviderError("支付宝商户信息不匹配")
        if parameters.get("trade_status") not in {"TRADE_SUCCESS", "TRADE_FINISHED"}:
            raise PaymentProviderError("支付宝通知不是成功状态")
        try:
            amount_value = Decimal(parameters.get("total_amount", "")) * 100
            if amount_value != amount_value.to_integral_value():
                raise PaymentProviderError("支付宝通知金额精度无效")
            amount_fen = int(amount_value)
        except (InvalidOperation, ValueError) as exc:
            raise PaymentProviderError("支付宝通知金额无效") from exc
        if not parameters.get("out_trade_no") or not parameters.get("trade_no"):
            raise PaymentProviderError("支付宝通知缺少订单标识")
        return VerifiedPayment(
            order_id=parameters.get("out_trade_no", ""),
            provider_transaction_id=parameters.get("trade_no", ""),
            amount_fen=amount_fen,
            currency="CNY",
            status="paid",
        )


class PaymentService:
    def __init__(self, settings: Settings) -> None:
        self.wechat = WeChatPayProvider(settings)
        self.alipay = AlipayProvider(settings)

    def configured(self, provider: PaymentProviderName) -> bool:
        return self.wechat.configured if provider == "wechat" else self.alipay.configured

    @property
    def any_configured(self) -> bool:
        return self.wechat.configured or self.alipay.configured
