"""T-GA-EDG-03 边缘端设备认证（API Key + HKDF）。

方案（`开发计划.md` T-GA-EDG-03）：
1. 设备注册时由服务端下发 API Key（高熵长期凭据，只存服务端哈希）；
2. 边缘端用 API Key 经 **HKDF-SHA256** 派生出**按设备隔离**的会话密钥
   （info 绑定 device_id，避免不同设备派生出同一密钥）；
3. 用会话密钥对 (device_id, 过期时间, nonce) 做 HMAC-SHA256 生成短期
   token，随 WebSocket 握手 / HTTP 请求带上；
4. 服务端用同一 API Key 重新派生会话密钥做常量时间比对 + 过期校验。

为什么不是直接签名 API Key：长期凭据一旦泄漏即不可撤销，短期 token
把暴露窗口压到 TTL 内，且 HKDF 保证"同一 API Key 不同设备密钥不同"。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

DEFAULT_HKDF_SALT = b"ultrasound-edge-device-auth-v1"
DEFAULT_TOKEN_TTL_SECONDS = 300
_SESSION_KEY_BYTES = 32

HEADER_DEVICE_ID = "X-Device-Id"
HEADER_DEVICE_TOKEN = "X-Device-Token"


class EdgeAuthError(Exception):
    """边缘端认证失败。"""


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def derive_session_key(
    api_key: str,
    device_id: str,
    *,
    salt: bytes = DEFAULT_HKDF_SALT,
) -> bytes:
    """由 API Key 派生设备专属会话密钥（HKDF-SHA256，info 绑定 device_id）。"""
    if not api_key:
        raise EdgeAuthError("api_key 不能为空")
    if not device_id:
        raise EdgeAuthError("device_id 不能为空")

    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=_SESSION_KEY_BYTES,
        salt=salt,
        info=device_id.encode("utf-8"),
    )
    return hkdf.derive(api_key.encode("utf-8"))


@dataclass(frozen=True)
class EdgeDeviceAuth:
    """单个边缘设备的认证器（服务端与边缘端共用同一实现）。"""

    device_id: str
    api_key: str
    token_ttl_seconds: int = DEFAULT_TOKEN_TTL_SECONDS
    salt: bytes = DEFAULT_HKDF_SALT

    def session_key(self) -> bytes:
        return derive_session_key(self.api_key, self.device_id, salt=self.salt)

    def make_token(
        self,
        now: float | None = None,
        nonce: str | None = None,
    ) -> str:
        """生成短期认证 token。

        格式：``device_id.expires.nonce.signature``（signature 为 base64url）
        """
        now = time.time() if now is None else now
        expires = int(now) + int(self.token_ttl_seconds)
        nonce = nonce or secrets.token_urlsafe(12)
        payload = f"{self.device_id}.{expires}.{nonce}"
        signature = hmac.new(
            self.session_key(), payload.encode("utf-8"), hashlib.sha256
        ).digest()
        return f"{payload}.{_b64url_encode(signature)}"

    def verify_token(self, token: str, now: float | None = None) -> bool:
        """校验 token（签名 + 设备绑定 + 过期）。"""
        now = time.time() if now is None else now
        try:
            device_id, expires_raw, nonce, signature_raw = token.split(".")
        except ValueError:
            return False

        if device_id != self.device_id:
            return False

        try:
            expires = int(expires_raw)
        except ValueError:
            return False

        if expires < int(now):
            return False

        payload = f"{device_id}.{expires_raw}.{nonce}"
        expected = hmac.new(
            self.session_key(), payload.encode("utf-8"), hashlib.sha256
        ).digest()
        try:
            provided = _b64url_decode(signature_raw)
        except Exception:
            return False
        return hmac.compare_digest(expected, provided)

    def build_headers(self, now: float | None = None) -> dict:
        """构建握手/请求认证头。"""
        return {
            HEADER_DEVICE_ID: self.device_id,
            HEADER_DEVICE_TOKEN: self.make_token(now=now),
        }


def verify_auth_headers(
    headers: dict,
    api_key_lookup,
    *,
    now: float | None = None,
    salt: bytes = DEFAULT_HKDF_SALT,
) -> str | None:
    """服务端校验认证头。

    Args:
        headers: 请求头（大小写不敏感即可，此处按标准头名取值）。
        api_key_lookup: ``callable(device_id) -> api_key | None``，
            按设备查询 API Key（通常是查库）。
        now: 注入当前时间（测试用）。

    Returns:
        校验通过返回 device_id；失败返回 None。
    """
    device_id = headers.get(HEADER_DEVICE_ID)
    token = headers.get(HEADER_DEVICE_TOKEN)
    if not device_id or not token:
        return None

    api_key = api_key_lookup(device_id)
    if not api_key:
        return None

    auth = EdgeDeviceAuth(device_id=device_id, api_key=api_key, salt=salt)
    return device_id if auth.verify_token(token, now=now) else None


__all__ = [
    "DEFAULT_HKDF_SALT",
    "DEFAULT_TOKEN_TTL_SECONDS",
    "HEADER_DEVICE_ID",
    "HEADER_DEVICE_TOKEN",
    "EdgeAuthError",
    "EdgeDeviceAuth",
    "derive_session_key",
    "verify_auth_headers",
]
