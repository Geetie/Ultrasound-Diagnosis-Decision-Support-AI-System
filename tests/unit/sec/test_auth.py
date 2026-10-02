import base64
import json
from datetime import timedelta

import pytest
from fastapi import HTTPException

from app.security.auth import (
    PERMISSIONS,
    ROLES,
    RoleChecker,
    check_permission,
    create_access_token,
    decode_access_token,
    get_password_hash,
    verify_password,
)

pytestmark = [pytest.mark.track_sec, pytest.mark.track_api, pytest.mark.sec_domain_auth]

def test_password_hashing():
    password = "test_password_123"
    hashed = get_password_hash(password)
    assert verify_password(password, hashed)
    assert not verify_password("wrong_password", hashed)

def test_jwt_token_creation_and_decoding():
    data = {"sub": "test_user", "user_info": {"username": "test_user", "role": "ULTRASOUND_DOCTOR"}}
    token = create_access_token(data, expires_delta=timedelta(minutes=1))

    decoded = decode_access_token(token)
    assert decoded["sub"] == "test_user"
    assert decoded["user_info"]["username"] == "test_user"
    assert decoded["user_info"]["role"] == "ULTRASOUND_DOCTOR"

def test_role_checker():
    checker = RoleChecker(["ULTRASOUND_DOCTOR", "DEPARTMENT_HEAD"])

    user1 = {"role": "ULTRASOUND_DOCTOR", "username": "doctor"}
    user2 = {"role": "DEPARTMENT_HEAD", "username": "head"}
    user3 = {"role": "SYSTEM_ADMIN", "username": "admin"}
    user4 = {"role": "EMERGENCY_DOCTOR", "username": "emergency"}

    assert checker(user1) == user1
    assert checker(user2) == user2

    from fastapi import HTTPException
    with pytest.raises(HTTPException) as excinfo:
        checker(user3)
    assert excinfo.value.status_code == 403

    with pytest.raises(HTTPException) as excinfo:
        checker(user4)
    assert excinfo.value.status_code == 403

def test_check_permission():
    user_doctor = {"role": "ULTRASOUND_DOCTOR"}
    user_head = {"role": "DEPARTMENT_HEAD"}
    user_admin = {"role": "SYSTEM_ADMIN"}
    user_emergency = {"role": "EMERGENCY_DOCTOR"}

    assert check_permission("submit_diagnosis", user_doctor) is True
    assert check_permission("view_results", user_doctor) is True
    assert check_permission("review_l3", user_doctor) is False

    assert check_permission("review_l3", user_head) is True
    assert check_permission("configure_thresholds", user_head) is True

    assert check_permission("submit_diagnosis", user_admin) is True
    assert check_permission("review_l3", user_admin) is True
    assert check_permission("view_audit_logs", user_admin) is True

    assert check_permission("submit_feedback", user_emergency) is False

def test_roles_and_permissions_structure():
    assert len(ROLES) == 5
    assert "ULTRASOUND_DOCTOR" in ROLES
    assert "SYSTEM_ADMIN" in ROLES

    assert len(PERMISSIONS) == 5
    assert "ULTRASOUND_DOCTOR" in PERMISSIONS
    assert "SYSTEM_ADMIN" in PERMISSIONS
    assert "*" in PERMISSIONS["SYSTEM_ADMIN"]

def test_jwt_expired_token_raises_401():
    """过期Token解码应抛出401"""
    data = {"sub": "test_user", "user_info": {"username": "test_user", "role": "ULTRASOUND_DOCTOR"}}
    token = create_access_token(data, expires_delta=timedelta(seconds=-1))

    with pytest.raises(HTTPException) as exc_info:
        decode_access_token(token)
    assert exc_info.value.status_code == 401

def test_jwt_tampered_signature_raises_401():
    """篡改Token签名应抛出401"""
    data = {"sub": "test_user", "user_info": {"username": "test_user", "role": "ULTRASOUND_DOCTOR"}}
    token = create_access_token(data)

    parts = token.split('.')
    tampered_payload = parts[1][:10] + "XXXX" + parts[1][14:]
    tampered_token = f"{parts[0]}.{tampered_payload}.{parts[2]}"

    with pytest.raises(HTTPException) as exc_info:
        decode_access_token(tampered_token)
    assert exc_info.value.status_code == 401

def test_jwt_invalid_format_raises_401():
    """无效JWT格式应抛出401"""
    with pytest.raises(HTTPException) as exc_info:
        decode_access_token("not.a.valid.jwt.token.format")
    assert exc_info.value.status_code == 401

def test_jwt_empty_string_raises_401():
    """空字符串JWT应抛出401"""
    with pytest.raises(HTTPException) as exc_info:
        decode_access_token("")
    assert exc_info.value.status_code == 401

def test_jwt_contains_jti_claim():
    """JWT应包含jti声明"""
    data = {"sub": "test_user", "user_info": {"username": "test_user", "role": "ULTRASOUND_DOCTOR"}}
    token = create_access_token(data)
    decoded = decode_access_token(token)
    assert "jti" in decoded
    assert decoded["jti"] is not None

def test_jwt_contains_iat_exp_claims():
    """JWT应包含iat和exp声明"""
    data = {"sub": "test_user", "user_info": {"username": "test_user", "role": "ULTRASOUND_DOCTOR"}}
    token = create_access_token(data)
    decoded = decode_access_token(token)
    assert "iat" in decoded
    assert "exp" in decoded
    assert decoded["exp"] > decoded["iat"]

def test_role_checker_no_role_user():
    """无角色用户应被RoleChecker拒绝"""
    checker = RoleChecker(["SYSTEM_ADMIN"])
    user_no_role = {"user_id": "unknown"}
    with pytest.raises(HTTPException) as exc_info:
        checker(user_no_role)
    assert exc_info.value.status_code == 403

def test_role_checker_none_role_user():
    """role为None的用户应被RoleChecker拒绝"""
    checker = RoleChecker(["SYSTEM_ADMIN"])
    user_none_role = {"user_id": "unknown", "role": None}
    with pytest.raises(HTTPException) as exc_info:
        checker(user_none_role)
    assert exc_info.value.status_code == 403


class TestJWTAlgorithmConfusionAttacks:
    """JWT算法混淆攻击测试"""

    @staticmethod
    def _b64url_encode(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).rstrip(b'=').decode('ascii')

    @staticmethod
    def _b64url_decode(data: str) -> bytes:
        padding = 4 - len(data) % 4
        if padding != 4:
            data += '=' * padding
        return base64.urlsafe_b64decode(data)

    @staticmethod
    def _ensure_jwt_key():
        create_access_token({"sub": "__key_setup__"})

    def test_alg_none_token_rejected(self):
        """alg为none的Token应被拒绝"""
        self._ensure_jwt_key()
        header = {"alg": "none", "typ": "JWT"}
        payload = {"sub": "attacker", "user_info": {"username": "attacker", "role": "SYSTEM_ADMIN"}}
        header_b64 = self._b64url_encode(json.dumps(header, separators=(',', ':')).encode())
        payload_b64 = self._b64url_encode(json.dumps(payload, separators=(',', ':')).encode())
        fake_sig = self._b64url_encode(b"fakesignature")
        token = f"{header_b64}.{payload_b64}.{fake_sig}"
        with pytest.raises(HTTPException) as exc_info:
            decode_access_token(token)
        assert exc_info.value.status_code == 401

    def test_alg_hs256_token_rejected(self):
        """alg为HS256的Token应被拒绝（算法混淆攻击）"""
        self._ensure_jwt_key()
        import hashlib
        import hmac

        from app.security.hsm import hsm_manager
        public_key = hsm_manager.get_public_key("jwt_signing_key")
        if public_key is None:
            pytest.skip("HSM public key not available")
        header = {"alg": "HS256", "typ": "JWT"}
        payload = {"sub": "attacker", "user_info": {"username": "attacker", "role": "SYSTEM_ADMIN"}}
        header_b64 = self._b64url_encode(json.dumps(header, separators=(',', ':')).encode())
        payload_b64 = self._b64url_encode(json.dumps(payload, separators=(',', ':')).encode())
        signing_input = f"{header_b64}.{payload_b64}".encode()
        signature = hmac.new(public_key.encode(), signing_input, hashlib.sha256).digest()
        sig_b64 = self._b64url_encode(signature)
        token = f"{header_b64}.{payload_b64}.{sig_b64}"
        with pytest.raises(HTTPException) as exc_info:
            decode_access_token(token)
        assert exc_info.value.status_code == 401

    def test_alg_none_with_empty_signature(self):
        """alg为none且签名为空的Token应被拒绝"""
        self._ensure_jwt_key()
        header = {"alg": "none", "typ": "JWT"}
        payload = {"sub": "attacker", "user_info": {"username": "attacker", "role": "SYSTEM_ADMIN"}}
        header_b64 = self._b64url_encode(json.dumps(header, separators=(',', ':')).encode())
        payload_b64 = self._b64url_encode(json.dumps(payload, separators=(',', ':')).encode())
        token = f"{header_b64}.{payload_b64}."
        with pytest.raises(HTTPException) as exc_info:
            decode_access_token(token)
        assert exc_info.value.status_code == 401

    def test_tampered_alg_field(self):
        """篡改JWT header中alg字段为none应被拒绝"""
        data = {"sub": "test_user", "user_info": {"username": "test_user", "role": "ULTRASOUND_DOCTOR"}}
        token = create_access_token(data)
        parts = token.split('.')
        header_bytes = self._b64url_decode(parts[0])
        header = json.loads(header_bytes)
        header["alg"] = "none"
        tampered_header_b64 = self._b64url_encode(json.dumps(header, separators=(',', ':')).encode())
        tampered_token = f"{tampered_header_b64}.{parts[1]}.{parts[2]}"
        with pytest.raises(HTTPException) as exc_info:
            decode_access_token(tampered_token)
        assert exc_info.value.status_code == 401


class TestKeyRotationJWTCompatibility:

    def test_old_token_valid_after_rotation(self):
        from app.security.hsm import hsm_manager
        data = {"sub": "test_user", "user_info": {"username": "test_user", "role": "ULTRASOUND_DOCTOR"}}
        old_token = create_access_token(data)
        hsm_manager.rotate_key("jwt_signing_key")
        decoded = decode_access_token(old_token)
        assert decoded["sub"] == "test_user"
        new_token = create_access_token(data)
        new_decoded = decode_access_token(new_token)
        assert new_decoded["sub"] == "test_user"

    def test_new_token_valid_after_rotation(self):
        from app.security.hsm import hsm_manager
        hsm_manager.rotate_key("jwt_signing_key")
        data = {"sub": "test_user", "user_info": {"username": "test_user", "role": "ULTRASOUND_DOCTOR"}}
        token = create_access_token(data)
        decoded = decode_access_token(token)
        assert decoded["sub"] == "test_user"

    def test_multiple_rotations(self):
        from app.security.hsm import hsm_manager
        data = {"sub": "test_user", "user_info": {"username": "test_user", "role": "ULTRASOUND_DOCTOR"}}
        token1 = create_access_token(data)
        hsm_manager.rotate_key("jwt_signing_key")
        token2 = create_access_token(data)
        hsm_manager.rotate_key("jwt_signing_key")
        token3 = create_access_token(data)
        decoded1 = decode_access_token(token1)
        assert decoded1["sub"] == "test_user"
        decoded2 = decode_access_token(token2)
        assert decoded2["sub"] == "test_user"
        decoded3 = decode_access_token(token3)
        assert decoded3["sub"] == "test_user"

    def test_rotated_key_naming_convention(self):
        import re

        from app.security.hsm import KeySlot, hsm_manager
        hsm_manager.rotate_key("jwt_signing_key")
        all_keys = hsm_manager.list_keys_by_slot(KeySlot.JWT_SIGNING)
        rotated_keys = [k for k in all_keys if k.startswith("jwt_signing_key_v")]
        assert len(rotated_keys) >= 1
        for key_name in rotated_keys:
            assert re.match(r"^jwt_signing_key_v\d+$", key_name)
