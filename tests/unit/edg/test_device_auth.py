"""T-GA-EDG-03 边缘端设备认证（API Key + HKDF）单测。"""
import pytest

from edge.device_auth import (
    HEADER_DEVICE_ID,
    HEADER_DEVICE_TOKEN,
    EdgeAuthError,
    EdgeDeviceAuth,
    derive_session_key,
    verify_auth_headers,
)

pytestmark = pytest.mark.track_edg

API_KEY = "edge-api-key-0123456789abcdef"
DEVICE_ID = "dev_edge_001"


class TestSessionKeyDerivation:
    def test_deterministic_for_same_inputs(self):
        first = derive_session_key(API_KEY, DEVICE_ID)
        second = derive_session_key(API_KEY, DEVICE_ID)

        assert first == second
        assert len(first) == 32

    def test_device_isolation_same_api_key(self):
        # 同一 API Key，不同设备必须派生出不同会话密钥（info 绑定 device_id）
        key_a = derive_session_key(API_KEY, "dev_a")
        key_b = derive_session_key(API_KEY, "dev_b")

        assert key_a != key_b

    def test_different_api_key_differs(self):
        assert derive_session_key("key-1", DEVICE_ID) != derive_session_key("key-2", DEVICE_ID)

    def test_empty_inputs_rejected(self):
        with pytest.raises(EdgeAuthError):
            derive_session_key("", DEVICE_ID)
        with pytest.raises(EdgeAuthError):
            derive_session_key(API_KEY, "")


class TestTokenRoundTrip:
    def test_make_and_verify(self):
        auth = EdgeDeviceAuth(device_id=DEVICE_ID, api_key=API_KEY, token_ttl_seconds=300)
        token = auth.make_token(now=1000.0)

        assert auth.verify_token(token, now=1000.0) is True
        assert auth.verify_token(token, now=1299.0) is True

    def test_expired_token_rejected(self):
        auth = EdgeDeviceAuth(device_id=DEVICE_ID, api_key=API_KEY, token_ttl_seconds=300)
        token = auth.make_token(now=1000.0)

        assert auth.verify_token(token, now=1301.0) is False

    def test_token_format_has_four_parts(self):
        auth = EdgeDeviceAuth(device_id=DEVICE_ID, api_key=API_KEY)
        token = auth.make_token(now=1000.0)

        assert len(token.split(".")) == 4

    def test_tampered_signature_rejected(self):
        auth = EdgeDeviceAuth(device_id=DEVICE_ID, api_key=API_KEY)
        token = auth.make_token(now=1000.0)
        device_id, expires, nonce, signature = token.split(".")

        tampered = f"{device_id}.{expires}.{nonce}.{signature[:-4]}AAAA"

        assert auth.verify_token(tampered, now=1000.0) is False

    def test_tampered_expiry_rejected(self):
        auth = EdgeDeviceAuth(device_id=DEVICE_ID, api_key=API_KEY, token_ttl_seconds=60)
        token = auth.make_token(now=1000.0)
        device_id, expires, nonce, signature = token.split(".")

        # 把过期时间改到很靠后 → 签名不再匹配
        forged = f"{device_id}.9999999999.{nonce}.{signature}"

        assert auth.verify_token(forged, now=1000.0) is False

    def test_other_device_token_rejected(self):
        issuer = EdgeDeviceAuth(device_id=DEVICE_ID, api_key=API_KEY)
        other = EdgeDeviceAuth(device_id="dev_other", api_key=API_KEY)

        assert other.verify_token(issuer.make_token(now=1000.0), now=1000.0) is False

    @pytest.mark.parametrize("token", ["", "garbage", "a.b.c", "a.b.c.d.e"])
    def test_malformed_token_rejected(self, token):
        auth = EdgeDeviceAuth(device_id=DEVICE_ID, api_key=API_KEY)

        assert auth.verify_token(token, now=1000.0) is False

    def test_wrong_api_key_rejected(self):
        issuer = EdgeDeviceAuth(device_id=DEVICE_ID, api_key=API_KEY)
        verifier = EdgeDeviceAuth(device_id=DEVICE_ID, api_key="another-key")

        assert verifier.verify_token(issuer.make_token(now=1000.0), now=1000.0) is False


class TestHeaders:
    def test_build_headers_carries_raw_api_key(self):
        """build_headers 携带原始 API Key（服务端哈希校验，见 device_auth 模块文档）。"""
        auth = EdgeDeviceAuth(device_id=DEVICE_ID, api_key=API_KEY)

        headers = auth.build_headers(now=1000.0)

        assert headers[HEADER_DEVICE_ID] == DEVICE_ID
        assert headers[HEADER_DEVICE_TOKEN] == API_KEY

    @staticmethod
    def _hmac_headers(auth: EdgeDeviceAuth) -> dict:
        """构造 HMAC 短期 token 头（verify_auth_headers 的预留协议形态）。"""
        return {
            HEADER_DEVICE_ID: auth.device_id,
            HEADER_DEVICE_TOKEN: auth.make_token(now=1000.0),
        }

    def test_verify_auth_headers_happy_path(self):
        auth = EdgeDeviceAuth(device_id=DEVICE_ID, api_key=API_KEY)
        headers = self._hmac_headers(auth)

        device_id = verify_auth_headers(
            headers, lambda did: API_KEY if did == DEVICE_ID else None, now=1000.0
        )

        assert device_id == DEVICE_ID

    def test_verify_auth_headers_unknown_device(self):
        auth = EdgeDeviceAuth(device_id=DEVICE_ID, api_key=API_KEY)
        headers = self._hmac_headers(auth)

        assert verify_auth_headers(headers, lambda did: None, now=1000.0) is None

    def test_verify_auth_headers_missing_headers(self):
        assert verify_auth_headers({}, lambda did: API_KEY, now=1000.0) is None
        assert verify_auth_headers({HEADER_DEVICE_ID: DEVICE_ID}, lambda did: API_KEY) is None

    def test_verify_auth_headers_expired(self):
        auth = EdgeDeviceAuth(device_id=DEVICE_ID, api_key=API_KEY, token_ttl_seconds=10)
        headers = self._hmac_headers(auth)

        assert verify_auth_headers(headers, lambda did: API_KEY, now=2000.0) is None
