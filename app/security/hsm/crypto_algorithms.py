"""应用层算法注册表与国密支持（二期 §9）。

范围界定：本模块只做**应用层**算法抽象与国密实现。TLS 层国密（SM2/3/4 套件）依赖
TASSL/OpenSSL 国密分支，属系统级部署件，不在代码层范围内——规格里这一项本轮不做。

能力探测而不是硬依赖
--------------------
SM2/SM4 需要 ``gmssl``。把它写成硬依赖会让"没装的环境直接起不来"，而国密是**可配置
切换**的合规选项，不是所有部署都需要。所以：

- 提供 ``CryptoProvider`` 协议 + 注册表；
- 国密提供方在 ``gmssl`` 缺失时 ``available()`` 返回 False；
- ``security_algorithm_profile`` 选 ``gm`` 但提供方不可用时，**启动即报错**（fail-fast），
  而不是运行时静默回退到非国密——静默回退是最糟的选择：合规审计会以为在跑国密。

关于"默认国密"
--------------
规格写"默认国密"。当前离线环境未安装 ``gmssl``，若把默认值设成 ``gm``，任何环境
都起不来。因此这里默认 ``standard``，并在校验器里对 ``gm`` 档位做提供方可用性检查；
部署侧装好 ``gmssl`` 后改一个配置项即可切到国密。这是**有意识的偏离**，不是遗漏。
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional, Protocol

import structlog

logger = structlog.get_logger()


class CryptoAlgorithm(str, Enum):
    RS256 = "RS256"
    RS384 = "RS384"
    RS512 = "RS512"
    SM2 = "SM2"
    SM4 = "SM4"


class AlgorithmProfile(str, Enum):
    STANDARD = "standard"
    GM = "gm"


GM_ALGORITHMS = (CryptoAlgorithm.SM2, CryptoAlgorithm.SM4)
STANDARD_ALGORITHMS = (
    CryptoAlgorithm.RS256,
    CryptoAlgorithm.RS384,
    CryptoAlgorithm.RS512,
)

# 各档位允许的算法集合：档位决定"签名/加密能用哪些算法"，
# 而不是让调用方随手传一个字符串——白名单是合规判据的落点。
PROFILE_ALGORITHMS: Dict[str, tuple] = {
    AlgorithmProfile.STANDARD.value: STANDARD_ALGORITHMS,
    AlgorithmProfile.GM.value: GM_ALGORITHMS,
}


class CryptoProvider(Protocol):
    """算法提供方协议。"""

    @property
    def name(self) -> str:  # pragma: no cover - 协议声明
        ...

    def supports(self, algorithm: CryptoAlgorithm) -> bool:  # pragma: no cover
        ...

    def available(self) -> bool:  # pragma: no cover
        ...

    def sign(self, algorithm: CryptoAlgorithm, key: bytes, data: bytes) -> bytes:
        ...

    def verify(
        self, algorithm: CryptoAlgorithm, key: bytes, data: bytes, signature: bytes
    ) -> bool:  # pragma: no cover - 协议声明
        ...

    def encrypt(
        self, algorithm: CryptoAlgorithm, key: bytes, plaintext: bytes
    ) -> bytes:  # pragma: no cover
        ...

    def decrypt(
        self, algorithm: CryptoAlgorithm, key: bytes, ciphertext: bytes
    ) -> bytes:  # pragma: no cover
        ...


class StandardCryptoProvider:
    """标准算法（RSA 家族的签名/校验交给既有 HSM 实现）。"""

    @property
    def name(self) -> str:
        return "standard-rsa"

    def supports(self, algorithm: CryptoAlgorithm) -> bool:
        return algorithm in STANDARD_ALGORITHMS

    def available(self) -> bool:
        return True

    def sign(self, algorithm: CryptoAlgorithm, key: bytes, data: bytes) -> bytes:
        raise NotImplementedError(
            "标准算法签名由 HSM EncryptionService 承担（密钥不可导出）"
        )

    def verify(
        self, algorithm: CryptoAlgorithm, key: bytes, data: bytes, signature: bytes
    ) -> bool:
        raise NotImplementedError("标准算法校验由 HSM EncryptionService 承担")

    def encrypt(self, algorithm: CryptoAlgorithm, key: bytes, plaintext: bytes) -> bytes:
        raise NotImplementedError("标准算法加密由 HSM EncryptionService 承担")

    def decrypt(self, algorithm: CryptoAlgorithm, key: bytes, ciphertext: bytes) -> bytes:
        raise NotImplementedError("标准算法解密由 HSM EncryptionService 承担")


class GmsslProvider:
    """国密算法提供方（gmssl）。未安装时 available() 为 False。"""

    def __init__(self) -> None:
        self._gmssl = None
        self._import_attempted = False

    def _load(self):
        if not self._import_attempted:
            self._import_attempted = True
            try:
                import gmssl  # type: ignore

                self._gmssl = gmssl
            except Exception as exc:  # noqa: BLE001 - 缺失依赖是预期情况
                logger.info("gmssl_not_installed", error=str(exc))
                self._gmssl = None
        return self._gmssl

    @property
    def name(self) -> str:
        return "gmssl"

    def supports(self, algorithm: CryptoAlgorithm) -> bool:
        return algorithm in GM_ALGORITHMS

    def available(self) -> bool:
        return self._load() is not None

    def sign(self, algorithm: CryptoAlgorithm, key: bytes, data: bytes) -> bytes:
        if algorithm is not CryptoAlgorithm.SM2:
            raise ValueError(f"GmsslProvider cannot sign with {algorithm}")
        from gmssl import sm2  # type: ignore  # noqa: PLC0415

        private_key, public_key = _split_sm2_key(key)
        crypt = sm2.CryptSM2(private_key=private_key, public_key=public_key)
        return bytes.fromhex(crypt.sign(_to_hex(data), _to_hex(key[:32])))

    def verify(
        self, algorithm: CryptoAlgorithm, key: bytes, data: bytes, signature: bytes
    ) -> bool:
        if algorithm is not CryptoAlgorithm.SM2:
            raise ValueError(f"GmsslProvider cannot verify with {algorithm}")
        from gmssl import sm2  # type: ignore  # noqa: PLC0415

        private_key, public_key = _split_sm2_key(key)
        crypt = sm2.CryptSM2(private_key=private_key, public_key=public_key)
        try:
            return bool(
                crypt.verify(_to_hex(signature), _to_hex(data), _to_hex(key[:32]))
            )
        except Exception as exc:  # noqa: BLE001 - 校验失败按 False 处理
            logger.warning("sm2_verify_failed", error=str(exc))
            return False

    def encrypt(self, algorithm: CryptoAlgorithm, key: bytes, plaintext: bytes) -> bytes:
        if algorithm is not CryptoAlgorithm.SM4:
            raise ValueError(f"GmsslProvider cannot encrypt with {algorithm}")
        from gmssl import sm4  # type: ignore  # noqa: PLC0415

        crypt = sm4.CryptSM4()
        crypt.set_key(key, sm4.SM4_ENCRYPT)
        iv = key[:16] if len(key) >= 16 else key.ljust(16, b"\x00")
        padded = _pkcs7_pad(plaintext, 16)
        return crypt.crypt_cbc(iv, padded)

    def decrypt(self, algorithm: CryptoAlgorithm, key: bytes, ciphertext: bytes) -> bytes:
        if algorithm is not CryptoAlgorithm.SM4:
            raise ValueError(f"GmsslProvider cannot decrypt with {algorithm}")
        from gmssl import sm4  # type: ignore  # noqa: PLC0415

        crypt = sm4.CryptSM4()
        crypt.set_key(key, sm4.SM4_DECRYPT)
        iv = key[:16] if len(key) >= 16 else key.ljust(16, b"\x00")
        return _pkcs7_unpad(crypt.crypt_cbc(iv, ciphertext), 16)


class AlgorithmRegistry:
    """算法 -> 提供方 的解析与可用性判定。"""

    def __init__(self, providers: Optional[List[CryptoProvider]] = None) -> None:
        self._providers = list(providers) if providers is not None else [
            StandardCryptoProvider(),
            GmsslProvider(),
        ]

    def providers(self) -> List[CryptoProvider]:
        return list(self._providers)

    def resolve(
        self, algorithm: CryptoAlgorithm
    ) -> Optional[CryptoProvider]:
        for provider in self._providers:
            if provider.supports(algorithm) and provider.available():
                return provider
        return None

    def is_available(self, algorithm: CryptoAlgorithm) -> bool:
        return self.resolve(algorithm) is not None

    def available_algorithms(self) -> List[str]:
        return [
            algorithm.value
            for algorithm in CryptoAlgorithm
            if self.is_available(algorithm)
        ]

    def require(self, algorithm: CryptoAlgorithm) -> CryptoProvider:
        provider = self.resolve(algorithm)
        if provider is None:
            raise RuntimeError(
                f"算法 {algorithm.value} 无可用提供方"
                "（国密需安装 gmssl；不允许静默回退到非国密）"
            )
        return provider

    def allowed_algorithms(self, profile: str) -> List[str]:
        """某档位下允许使用的算法（合规白名单）。"""
        return [algorithm.value for algorithm in PROFILE_ALGORITHMS.get(profile, ())]

    def validate_profile(self, profile: str) -> List[str]:
        """返回该档位不可用的算法列表（空表示该档位可启用）。"""
        if profile not in PROFILE_ALGORITHMS:
            return []
        return [
            algorithm.value
            for algorithm in PROFILE_ALGORITHMS[profile]
            if not self.is_available(algorithm)
        ]


_registry: Optional[AlgorithmRegistry] = None


def get_algorithm_registry() -> AlgorithmRegistry:
    global _registry
    if _registry is None:
        _registry = AlgorithmRegistry()
    return _registry


def set_algorithm_registry(registry: Optional[AlgorithmRegistry]) -> None:
    """替换注册表（测试注入用）。"""
    global _registry
    _registry = registry


def active_profile() -> str:
    from app.config.settings import settings

    return settings.security_algorithm_profile


def _split_sm2_key(key: bytes) -> tuple:
    """SM2 密钥约定：前 32 字节私钥，其余为公钥（十六进制串）。"""
    private_key = key[:32]
    public_key = key[32:] or private_key
    return private_key.hex(), public_key.hex()


def _to_hex(data: bytes) -> str:
    return data.hex()


def _pkcs7_pad(data: bytes, block_size: int) -> bytes:
    padding = block_size - len(data) % block_size
    return data + bytes([padding]) * padding


def _pkcs7_unpad(data: bytes, block_size: int) -> bytes:
    if not data:
        return data
    padding = data[-1]
    if padding < 1 or padding > block_size or len(data) < padding:
        raise ValueError("invalid PKCS7 padding")
    return data[:-padding]


@dataclass(frozen=True)
class AlgorithmStatus:
    """当前生效算法状态（给设置页展示，避免界面自己猜）。"""

    profile: str
    available: List[str]
    allowed: List[str]
    unavailable: List[str]

    def as_dict(self) -> Dict[str, object]:
        return {
            "profile": self.profile,
            "available": self.available,
            "allowed": self.allowed,
            "unavailable": self.unavailable,
        }


def current_algorithm_status() -> AlgorithmStatus:
    registry = get_algorithm_registry()
    profile = active_profile()
    return AlgorithmStatus(
        profile=profile,
        available=registry.available_algorithms(),
        allowed=registry.allowed_algorithms(profile),
        unavailable=registry.validate_profile(profile),
    )
