"""
Knowledge Base Version Management
Implements version parsing and comparison for knowledge base versions.
"""
import re
from dataclasses import dataclass


@dataclass
class KBVersion:
    """Knowledge base version data structure."""
    major: int
    minor: int
    patch: int
    hospital_id: str | None = None

    def __str__(self) -> str:
        if self.hospital_id:
            return f"kb_v{self.major}.{self.minor}.{self.patch}~{self.hospital_id}"
        return f"kb_v{self.major}.{self.minor}.{self.patch}"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, KBVersion):
            return NotImplemented
        return (self.major == other.major and
                self.minor == other.minor and
                self.patch == other.patch and
                self.hospital_id == other.hospital_id)

    # BUG-013 fix: hospital_id now included in comparison
    def __lt__(self, other: object) -> bool:
        if not isinstance(other, KBVersion):
            return NotImplemented
        if self.major != other.major:
            return self.major < other.major
        if self.minor != other.minor:
            return self.minor < other.minor
        if self.patch != other.patch:
            return self.patch < other.patch
        if self.hospital_id is None and other.hospital_id is None:
            return False
        if self.hospital_id is None:
            return True
        if other.hospital_id is None:
            return False
        return self.hospital_id < other.hospital_id

    def __le__(self, other: object) -> bool:
        if not isinstance(other, KBVersion):
            return NotImplemented
        return self < other or self == other

    def to_tuple(self) -> tuple[int, int, int]:
        """Return version as a tuple (major, minor, patch)."""
        return (self.major, self.minor, self.patch)


_VERSION_PATTERN = re.compile(
    r'^kb_v(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)(?:~(?P<hospital>[a-zA-Z0-9_]+))?$'
)


def parse_kb_version(version_str: str) -> KBVersion | None:
    """
    Parse a knowledge base version string.

    Args:
        version_str: Version string in format "kb_v{major}.{minor}.{patch}~{hospital_id}"
                    or "kb_v{major}.{minor}.{patch}"

    Returns:
        KBVersion object or None if parsing fails

    Examples:
        >>> parse_kb_version("kb_v1.2.3")
        KBVersion(major=1, minor=2, patch=3, hospital_id=None)
        >>> parse_kb_version("kb_v1.2.3~hospital_A")
        KBVersion(major=1, minor=2, patch=3, hospital_id="hospital_A")
    """
    if not version_str:
        return None

    match = _VERSION_PATTERN.match(version_str.strip())
    if not match:
        return None

    try:
        major = int(match.group('major'))
        minor = int(match.group('minor'))
        patch = int(match.group('patch'))
        hospital_id = match.group('hospital')

        return KBVersion(
            major=major,
            minor=minor,
            patch=patch,
            hospital_id=hospital_id
        )
    except (ValueError, TypeError):
        return None


_VERSION_TOKEN_PATTERN = re.compile(r"[vV](\d+(?:\.\d+)*)")
_BARE_VERSION_TOKEN_PATTERN = re.compile(r"(?<![A-Za-z0-9])(\d+(?:\.\d+)*)")


def parse_model_version(version_str: str) -> tuple[int, int] | None:
    """解析模型版本号，返回 (major, minor)。

    比 :func:`_parse_simple_version` 更宽容：配置里的模型版本常带器官/骨干前缀
    （如 ``"thyroid_v1.0.0"``、``"resnet50_v2.0"``），直接按点号切分会解析失败，
    进而让兼容性校验对所有真实配置一律判"不兼容"。

    解析优先级：
    1. 纯版本写法（``"v1.0"`` / ``"1.2.3"``）直接解析；
    2. 取 ``v``/``V`` 前缀的版本片段（``"thyroid_v1.0.0"`` -> ``1.0``）；
    3. 兜底取非字母数字边界后的裸版本片段。
    """
    direct = _parse_simple_version(version_str)
    if direct is not None:
        return direct

    for pattern in (_VERSION_TOKEN_PATTERN, _BARE_VERSION_TOKEN_PATTERN):
        match = pattern.search(version_str or "")
        if not match:
            continue
        parsed = _parse_simple_version(match.group(1))
        if parsed is not None:
            return parsed

    return None


def is_compatible(model_version: str, kb_version: str) -> bool:
    """
    Check if model version and knowledge base version are compatible.

    According to compatibility matrix:
    - Model v1.x with kb_v1.x: Compatible
    - Model v1.x with kb_v2.x: Degraded compatible
    - Model v2.x with kb_v2.x: Compatible
    - Model v2.x with kb_v1.x: Not compatible

    Args:
        model_version: Model version string
        kb_version: Knowledge base version string

    Returns:
        True if compatible, False otherwise
    """
    model_ver = parse_model_version(model_version)
    kb_ver = parse_kb_version(kb_version)

    if not model_ver or not kb_ver:
        return False

    model_major, _ = model_ver

    if model_major == 1:
        return kb_ver.major in (1, 2)
    elif model_major == 2:
        return kb_ver.major == 2

    return False


def _parse_simple_version(version_str: str) -> tuple[int, int] | None:
    """Parse simple version like "v1.2" or "1.2.3"."""
    if not version_str:
        return None

    # Strip leading 'v' if present
    clean = version_str.strip().lstrip('vV')
    parts = clean.split('.')

    try:
        major = int(parts[0]) if parts else 0
        minor = int(parts[1]) if len(parts) > 1 else 0
        return (major, minor)
    except (ValueError, IndexError):
        return None
