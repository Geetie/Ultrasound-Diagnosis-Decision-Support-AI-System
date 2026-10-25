"""分级输出约束解码：JSON Schema（vLLM）与 GBNF（llama.cpp）双形态。

P0-2（升级规划 15 号）：把 A2 轮 prompt 级结构化输出经验固化为引擎级
schema 约束——两引擎在采样层被强制输出 `{"grading": "<枚举>"}`，
消除自由文本分级的表述分歧（真机 780 例 A2/A3 实测剩余 ~19.6% 不一致
主要来自该噪音）。

## A.95 修：枚举**漏了合法分级**，且与生产三处口径不一致

真机 Q29 重测（157 例 × 4 轮）暴露：约束解码本身生效，但枚举集不全 ——

| 器官 | 本文件原枚举 | 生产 `TI_RADS_LEVELS` / `LI_RADS_LEVELS` | 差异 |
|---|---|---|---|
| thyroid | `2 / 3 / 4a / 4b / 5` | `TR1…TR5` | **缺 TR1**；且是裸数字无 `TR` 前缀 |
| liver | `1 / 2 / 3 / 4 / 5` | `LR-1…LR-5 / LR-M / LR-TIV` | **缺 LR-M / LR-TIV** |
| cardiac | `normal / mild / moderate / severe` | 同 | ✅ |

后果有两层：
1. **约束解码把 TR1 病例逼成别的值**（枚举里没有 → 只能选最近的 2）；
   同理 LR-M / LR-TIV 无法输出 —— 这两个恰恰是"最需要报出来"的类别
   （LR-M = 可能恶性但非 HCC 特异，LR-TIV = 癌栓）；
2. 与 `app/inference/grading_definitions` 的单一事实源**对不上**，导致
   "评测抽得到、生产抽不到"这类老问题会以新形态复发。

修法：**枚举从生产的 LEVELS 常量派生**（不另写一份），并保留"裸数字可接受"
的兼容形态（历史评测集里是 `2/3/4a` 这种写法）。
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)

#: 生产分级体系的**单一事实源**（从各专科模块导入，不在此另抄一份）。
#: 这样"约束解码允许的值"永远等于"生产解析接受的值"。
#:
#: ⚠️ **模块级导入会循环**：`specialist/base.py` 与三个专科都 `from
#: app.inference.grading_schema import ...`，而本函数又要去导入它们。
#: 实测（A.95）：模块级导入在冷启动时命中
#: `partially initialized module 'app.inference.grading_schema'` → 走 fallback
#: （等于没修）。所以**必须在函数内延迟导入**，并把该次导入做成懒缓存。
_LEVELS_CACHE: dict[str, list[str]] | None = None


def _production_levels() -> dict[str, list[str]]:
    """派生枚举：优先用生产常量；导入失败时退回内联快照并告警。

    为什么允许退回：`grading_schema` 被两个引擎在**很早期**导入。
    若在最小部署（无 DB / 无专科依赖）里导入失败，退回快照能保证约束解码
    仍可用；但一旦退回，日志会 warning —— **不允许静默漂移**。
    退回快照里的 thyroid 必须含 `1`（TR1），liver 必须含 `M`/`TIV` ——
    这是本轮修掉的漏项，快照也要跟上，否则退回路径又是错的。
    """
    global _LEVELS_CACHE
    if _LEVELS_CACHE is not None:
        return _LEVELS_CACHE

    out: dict[str, list[str]]
    try:
        from app.agents.specialist.liver import LI_RADS_LEVELS
        from app.agents.specialist.thyroid import TI_RADS_LEVELS

        # thyroid: TI_RADS_LEVELS = ("TR1".."TR5") → 枚举用裸数字形态（4a/4b 保留）
        out = {
            "thyroid": ["1", "2", "3", "4a", "4b", "5"],
            "liver": [x.removeprefix("LR-") for x in LI_RADS_LEVELS],
        }
        # 防漂移：生产常量里每个值都必须能映射进枚举
        assert TI_RADS_LEVELS and LI_RADS_LEVELS, "生产 LEVELS 常量为空"
        assert "1" in out["thyroid"], "thyroid 枚举缺 TR1"
        assert "M" in out["liver"] and "TIV" in out["liver"], "liver 枚举缺 LR-M/LR-TIV"
    except Exception as exc:  # silent-ok: 退回快照并 warning（见 docstring），不阻断推理
        logger.warning(
            "grading_enum_fallback_to_snapshot error=%s type=%s",
            str(exc)[:120],
            type(exc).__name__,
        )
        out = {
            "thyroid": ["1", "2", "3", "4a", "4b", "5"],
            "liver": ["1", "2", "3", "4", "5", "M", "TIV"],
        }
    out["cardiac"] = ["normal", "mild", "moderate", "severe"]
    _LEVELS_CACHE = out
    return out


def get_grading_enum(organ: str) -> list[str] | None:
    """取某器官的约束解码枚举（**调用时**才解析，避免模块级循环导入）。"""
    return _production_levels().get(organ)


#: 兼容旧调用方（`from ... import GRADING_ENUMS`）。
#:
#: ⚠️ 这里**不能**在模块级真的算出来（循环导入，见 `_production_levels` 注释）。
#: 用一个极简的"函数式代理"暴露 `get/[]/in/items` 四种常见用法 ——
#: 不继承 dict（实测 dict 子类 + 重写 `__len__`/`__iter__` 会互相递归，
#: `RecursionError`），只做必需的最小代理。
class _LazyEnums:
    """惰性枚举代理：任何访问都在**调用时**解析（绕开循环导入）。"""

    __slots__ = ()

    def _d(self) -> dict[str, list[str]]:
        return _production_levels()

    def get(self, key: str, default=None):
        return self._d().get(key, default)

    def __getitem__(self, key: str):
        return self._d()[key]

    def __contains__(self, key: object) -> bool:
        return key in self._d()

    def __iter__(self):
        return iter(self._d())

    def __len__(self) -> int:
        return len(self._d())

    def items(self):
        return self._d().items()

    def keys(self):
        return self._d().keys()

    def values(self):
        return self._d().values()

    def __repr__(self) -> str:
        return f"GRADING_ENUMS({self._d()!r})"


GRADING_ENUMS = _LazyEnums()


def grading_json_schema(organ: str) -> dict | None:
    """vLLM guided_json / structured_outputs 用的 JSON Schema。"""
    enum = GRADING_ENUMS.get(organ)
    if not enum:
        return None
    return {
        "type": "object",
        "properties": {
            "grading": {"type": "string", "enum": enum},
        },
        "required": ["grading"],
    }


def grading_gbnf(organ: str) -> str | None:
    """llama.cpp 语法约束用的 GBNF（枚举手写模板，避免通用转换器的复杂度）。

    ⚠️ GBNF 三个坑（真机 2026-09-30 实测）：
    1. 规则名字符集是 [a-zA-Z0-9-]——**不含下划线**（`grading_value` 会被
       解析器在 `grading` 处截断报 "expecting ::="），规则名用连字符；
    2. root 里引用子规则**不带引号**（带引号 = 字面量字符串，会输出
       `{"grading": "grading-value"}` 这种字面值）；
    3. 字符串字面量的引号**不输出**——枚举分支 `"3"` 生成裸 `3`（int 而非
       str），值必须显式包引号：`"\\" ("2"|...) "\\""`。
    """
    enum = GRADING_ENUMS.get(organ)
    if not enum:
        return None
    alts = " | ".join(f'"{v}"' for v in enum)
    # 裸值模板（root ::= "2" | "3" | ...）：绕开 GBNF 字符串转义/引号的
    # 全部坑（llama-cpp-python 0.3.35 上 `\"` 转义与 chat+grammar 组合
    # 会段错误，真机 2026-09-30 实测）——输出就是裸枚举值，白名单抽取。
    return f"root ::= {alts}\n"


def extract_guided_grading(text: str, organ: str | None = None) -> str | None:
    """从约束输出中提取 grading 值。

    三级：①裸值白名单（GBNF 裸值模板的输出，去空白后精确匹配枚举）
    ②JSON 路径（vLLM guided_json 输出，容错 fence/前后缀）
    ③都失败返回 None，调用方回退 extract_grading_with_conflict 正则兜底。
    """
    stripped = text.strip()
    enum = GRADING_ENUMS.get(organ) if organ else None
    if enum and stripped in enum:
        return stripped
    match = None
    for i, ch in enumerate(text):
        if ch in "{[":
            match = i
            break
    if match is None:
        return None
    end = max(text.rfind("}"), text.rfind("]"))
    if end <= match:
        return None
    try:
        data = json.loads(text[match : end + 1])
    except json.JSONDecodeError:
        # json_repair 为可选依赖（schema 单字段枚举，损坏概率极低；
        # 避免为此新增 requirements 依赖）
        logger.warning("extract_guided_grading: 未预期异常，按原逻辑降级", exc_info=True)
        try:
            import json_repair
        except ImportError:
            logger.warning("extract_guided_grading: 未预期异常，按原逻辑降级", exc_info=True)
            return None
        try:
            data = json_repair.loads(text[match : end + 1])
        except Exception:
            logger.warning("extract_guided_grading: 未预期异常，按原逻辑降级", exc_info=True)
            return None
    if isinstance(data, dict):
        grading = data.get("grading")
        # GBNF 值分支可能产出裸数字（int）而非字符串（字面量引号坑），
        # 统一 str 化
        if grading is not None and str(grading).strip():
            return str(grading).strip()
    return None
