"""应用层算法注册表与国密支持（二期 §9）。

不变量：
1. 档位白名单决定"能用哪些算法"——合规判据落在白名单上，而不是靠调用方自律；
2. **不可用时必须失败，不得静默回退**：档案选国密却没有提供方时，启动就该报错；
   静默回退到非国密是最糟的选择（审计会以为在跑国密）；
3. 提供方缺失是**预期情况**（离线环境没装 gmssl），不能因此让其它档位不可用。
"""
import pytest

from app.config.settings import settings
from app.security.hsm.crypto_algorithms import (
    GM_ALGORITHMS,
    STANDARD_ALGORITHMS,
    AlgorithmProfile,
    AlgorithmRegistry,
    AlgorithmStatus,
    CryptoAlgorithm,
    GmsslProvider,
    StandardCryptoProvider,
    active_profile,
    current_algorithm_status,
    get_algorithm_registry,
    set_algorithm_registry,
)


class _AlwaysAvailable:
    def __init__(self, algorithms, name="fake"):
        self._algorithms = tuple(algorithms)
        self._name = name

    @property
    def name(self):
        return self._name

    def supports(self, algorithm):
        return algorithm in self._algorithms

    def available(self):
        return True

    def sign(self, algorithm, key, data):
        return b"sig"

    def verify(self, algorithm, key, data, signature):
        return signature == b"sig"

    def encrypt(self, algorithm, key, plaintext):
        return b"enc:" + plaintext

    def decrypt(self, algorithm, key, ciphertext):
        return ciphertext[4:]


class _Unavailable(_AlwaysAvailable):
    def available(self):
        return False


class TestAlgorithmProfileWhitelist:
    def test_standard_profile_allows_only_rsa_family(self):
        registry = AlgorithmRegistry()

        allowed = registry.allowed_algorithms(AlgorithmProfile.STANDARD.value)

        assert set(allowed) == {algorithm.value for algorithm in STANDARD_ALGORITHMS}
        assert "SM2" not in allowed

    def test_gm_profile_allows_only_sm_algorithms(self):
        registry = AlgorithmRegistry()

        allowed = registry.allowed_algorithms(AlgorithmProfile.GM.value)

        assert set(allowed) == {algorithm.value for algorithm in GM_ALGORITHMS}
        assert "RS256" not in allowed

    def test_unknown_profile_has_no_whitelist(self):
        registry = AlgorithmRegistry()

        assert registry.allowed_algorithms("nonsense") == []


class TestResolution:
    def test_resolves_standard_algorithms(self):
        registry = AlgorithmRegistry([StandardCryptoProvider()])

        assert registry.is_available(CryptoAlgorithm.RS256) is True
        assert registry.is_available(CryptoAlgorithm.SM2) is False

    def test_resolves_gm_when_provider_present(self):
        registry = AlgorithmRegistry([_AlwaysAvailable(GM_ALGORITHMS, "fake-gm")])

        assert registry.is_available(CryptoAlgorithm.SM4) is True
        assert registry.resolve(CryptoAlgorithm.SM4).name == "fake-gm"

    def test_unavailable_provider_is_not_resolved(self):
        registry = AlgorithmRegistry([_Unavailable(GM_ALGORITHMS)])

        assert registry.is_available(CryptoAlgorithm.SM2) is False
        assert registry.resolve(CryptoAlgorithm.SM2) is None

    def test_require_raises_with_actionable_message(self):
        """报错信息必须给出可执行的动作，否则运维只能猜。"""
        registry = AlgorithmRegistry([StandardCryptoProvider()])

        with pytest.raises(RuntimeError, match="gmssl"):
            registry.require(CryptoAlgorithm.SM2)

    def test_require_returns_provider(self):
        provider = _AlwaysAvailable(STANDARD_ALGORITHMS, "stdio")

        assert AlgorithmRegistry([provider]).require(CryptoAlgorithm.RS256) is provider

    def test_available_algorithms_lists_only_resolvable(self):
        registry = AlgorithmRegistry([_AlwaysAvailable([CryptoAlgorithm.RS256])])

        assert registry.available_algorithms() == ["RS256"]

    def test_validate_profile_reports_missing(self):
        registry = AlgorithmRegistry([StandardCryptoProvider()])

        missing = registry.validate_profile(AlgorithmProfile.GM.value)

        assert set(missing) == {"SM2", "SM4"}

    def test_validate_profile_empty_when_satisfied(self):
        registry = AlgorithmRegistry([_AlwaysAvailable(STANDARD_ALGORITHMS)])

        assert registry.validate_profile(AlgorithmProfile.STANDARD.value) == []


class TestGmsslProvider:
    def test_reports_availability_without_crashing(self):
        """没装 gmssl 时必须安静返回 False，而不是抛 ImportError。"""
        provider = GmsslProvider()

        assert provider.available() in (True, False)

    def test_supports_only_gm_algorithms(self):
        provider = GmsslProvider()

        assert provider.supports(CryptoAlgorithm.SM2) is True
        assert provider.supports(CryptoAlgorithm.SM4) is True
        assert provider.supports(CryptoAlgorithm.RS256) is False

    def test_rejects_wrong_algorithm(self):
        provider = GmsslProvider()

        with pytest.raises(ValueError):
            provider.encrypt(CryptoAlgorithm.RS256, b"k" * 16, b"data")

    def test_named_provider_is_identifiable(self):
        assert GmsslProvider().name == "gmssl"
        assert StandardCryptoProvider().name == "standard-rsa"


class TestStandardProvider:
    def test_does_not_pretend_to_implement_hsm_operations(self):
        """标准算法由 HSM 承担（密钥不可导出），这里显式不实现而不是悄悄降级。"""
        provider = StandardCryptoProvider()

        with pytest.raises(NotImplementedError, match="HSM"):
            provider.sign(CryptoAlgorithm.RS256, b"key", b"data")


class TestProfileValidation:
    def test_gm_profile_fails_fast_without_provider(self, monkeypatch):
        from app.config.validator import validate_config

        monkeypatch.setattr(settings, "security_algorithm_profile", "gm")
        monkeypatch.setattr(
            "app.security.hsm.crypto_algorithms.get_algorithm_registry",
            lambda: AlgorithmRegistry([StandardCryptoProvider()]),
        )

        errors = validate_config()

        assert any("SECURITY_ALGORITHM_PROFILE=gm" in error for error in errors)

    def test_unknown_profile_rejected(self, monkeypatch):
        from app.config.validator import validate_config

        monkeypatch.setattr(settings, "security_algorithm_profile", "quantum")

        errors = validate_config()

        assert any("SECURITY_ALGORITHM_PROFILE" in error for error in errors)

    def test_gm_profile_passes_when_provider_present(self, monkeypatch):
        from app.config.validator import validate_config

        monkeypatch.setattr(settings, "security_algorithm_profile", "gm")
        monkeypatch.setattr(
            "app.security.hsm.crypto_algorithms.get_algorithm_registry",
            lambda: AlgorithmRegistry([_AlwaysAvailable(GM_ALGORITHMS, "fake-gm")]),
        )

        errors = validate_config()

        assert not any("SECURITY_ALGORITHM_PROFILE" in error for error in errors)

    def test_default_profile_is_valid(self):
        from app.config.validator import validate_config

        assert not any(
            "SECURITY_ALGORITHM_PROFILE" in error for error in validate_config()
        )


class TestStatusReporting:
    def test_status_shape_for_settings_page(self, monkeypatch):
        monkeypatch.setattr(
            "app.security.hsm.crypto_algorithms.get_algorithm_registry",
            lambda: AlgorithmRegistry([StandardCryptoProvider()]),
        )
        monkeypatch.setattr(settings, "security_algorithm_profile", "standard")

        status = current_algorithm_status()

        assert isinstance(status, AlgorithmStatus)
        payload = status.as_dict()
        assert set(payload) == {"profile", "available", "allowed", "unavailable"}
        assert payload["profile"] == "standard"

    def test_active_profile_reads_settings(self, monkeypatch):
        monkeypatch.setattr(settings, "security_algorithm_profile", "gm")

        assert active_profile() == "gm"


class TestSingleton:
    def test_set_and_get(self):
        original = get_algorithm_registry()
        replacement = AlgorithmRegistry([StandardCryptoProvider()])
        try:
            set_algorithm_registry(replacement)
            assert get_algorithm_registry() is replacement
        finally:
            set_algorithm_registry(original)


class TestPadding:
    def test_pkcs7_roundtrip(self):
        from app.security.hsm.crypto_algorithms import _pkcs7_pad, _pkcs7_unpad

        for length in (1, 15, 16, 17, 32):
            data = b"x" * length
            assert _pkcs7_unpad(_pkcs7_pad(data, 16), 16) == data

    def test_invalid_padding_rejected(self):
        from app.security.hsm.crypto_algorithms import _pkcs7_unpad

        with pytest.raises(ValueError):
            _pkcs7_unpad(b"short", 16)

    def test_empty_input_returns_empty(self):
        from app.security.hsm.crypto_algorithms import _pkcs7_unpad

        assert _pkcs7_unpad(b"", 16) == b""
