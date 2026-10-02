"""GmsslProvider 真实路径覆盖：注入 fake gmssl 模块（本地未装 gmssl 也能测）。"""
from __future__ import annotations

import sys
import types

import pytest

from app.security.hsm.crypto_algorithms import (
    AlgorithmRegistry,
    CryptoAlgorithm,
    GmsslProvider,
    _split_sm2_key,
)


class FakeCryptSM2:
    def __init__(self, private_key, public_key):
        self.private_key = private_key
        self.public_key = public_key

    def sign(self, data_hex, rnd_hex):
        return "a3" * 32

    def verify(self, sig_hex, data_hex, rnd_hex):
        if sig_hex == "ff":
            raise ValueError("bad sig")
        return sig_hex == "a3" * 32


class FakeCryptSM4:
    SM4_ENCRYPT = 1
    SM4_DECRYPT = 0

    def __init__(self):
        self.key = None

    def set_key(self, key, mode):
        self.key = key

    def crypt_cbc(self, iv, data):
        # 自反变换（XOR 0xFF）：encrypt/decrypt 用同一函数即可往返
        assert iv is not None
        return bytes(b ^ 0xFF for b in data)


def _install_fake_gmssl(monkeypatch, sm2_cls=FakeCryptSM2, sm4_cls=FakeCryptSM4):
    gmssl = types.ModuleType("gmssl")
    sm2_mod = types.ModuleType("gmssl.sm2")
    sm4_mod = types.ModuleType("gmssl.sm4")
    sm2_mod.CryptSM2 = sm2_cls
    sm4_mod.CryptSM4 = sm4_cls
    sm4_mod.SM4_ENCRYPT = 1
    sm4_mod.SM4_DECRYPT = 0
    gmssl.sm2 = sm2_mod
    gmssl.sm4 = sm4_mod
    monkeypatch.setitem(sys.modules, "gmssl", gmssl)
    monkeypatch.setitem(sys.modules, "gmssl.sm2", sm2_mod)
    monkeypatch.setitem(sys.modules, "gmssl.sm4", sm4_mod)


@pytest.fixture
def gm_provider(monkeypatch):
    _install_fake_gmssl(monkeypatch)
    return GmsslProvider()


SM2_KEY = bytes(range(64))  # 前 32 私钥 + 后 32 公钥
SM4_KEY = b"k" * 16


class TestGmsslRealPaths:
    def test_available_with_fake_module(self, gm_provider):
        assert gm_provider.available() is True

    def test_sm2_sign_roundtrip(self, gm_provider):
        sig = gm_provider.sign(CryptoAlgorithm.SM2, SM2_KEY, b"hello")
        assert gm_provider.verify(CryptoAlgorithm.SM2, SM2_KEY, b"hello", sig) is True
        assert gm_provider.verify(CryptoAlgorithm.SM2, SM2_KEY, b"hello", b"\xff" * 32) is False

    def test_sm2_wrong_algorithm_rejected(self, gm_provider):
        with pytest.raises(ValueError, match="cannot sign"):
            gm_provider.sign(CryptoAlgorithm.SM4, SM2_KEY, b"d")
        with pytest.raises(ValueError, match="cannot verify"):
            gm_provider.verify(CryptoAlgorithm.SM4, SM2_KEY, b"d", b"s")

    def test_sm2_verify_exception_returns_false(self, gm_provider):
        # "ff" 触发 fake verify 抛异常 → provider 必须吞掉并返回 False
        assert (
            gm_provider.verify(CryptoAlgorithm.SM2, SM2_KEY, b"data", b"\xff" * 32)
            is False
        )

    def test_sm4_encrypt_decrypt_roundtrip(self, gm_provider):
        ct = gm_provider.encrypt(CryptoAlgorithm.SM4, SM4_KEY, b"plain")
        assert ct != b"plain"  # 确有变换发生
        pt = gm_provider.decrypt(CryptoAlgorithm.SM4, SM4_KEY, ct)
        assert pt == b"plain"

    def test_sm4_wrong_algorithm_rejected(self, gm_provider):
        with pytest.raises(ValueError, match="cannot encrypt"):
            gm_provider.encrypt(CryptoAlgorithm.SM2, SM4_KEY, b"d")
        with pytest.raises(ValueError, match="cannot decrypt"):
            gm_provider.decrypt(CryptoAlgorithm.SM2, SM4_KEY, b"d")

    def test_import_failure_cached(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "gmssl", None)  # 模拟 ImportError
        # import gmssl 对 None 值会成功返回 None？——sys.modules 命中 None 会抛
        # ImportError（Python >=3.6 行为），正好覆盖 except 分支
        provider = GmsslProvider()
        assert provider.available() is False
        assert provider.available() is False  # 第二次走 _import_attempted 缓存


class TestRegistryExtras:
    def test_providers_list_default(self):
        registry = AlgorithmRegistry()
        names = [p.name for p in registry.providers()]
        assert "standard-rsa" in names and "gmssl" in names

    def test_validate_profile_unknown_returns_empty(self):
        registry = AlgorithmRegistry()
        assert registry.validate_profile("nonsense") == []

    def test_split_sm2_key_public_fallback(self):
        priv_hex, pub_hex = _split_sm2_key(b"\x01" * 32)
        assert pub_hex == "01" * 32  # 公钥缺省回退私钥
        priv_hex, pub_hex = _split_sm2_key(bytes(range(64)))
        assert priv_hex == bytes(range(32)).hex()
        assert pub_hex == bytes(range(32, 64)).hex()
