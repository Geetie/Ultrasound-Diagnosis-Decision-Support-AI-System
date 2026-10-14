"""诊断属性提示（ULC 文本塔 zero-shot）——报告一致性校验旁路。

依据（A.21 全量 US-365K 71,919 张实测，image→attribute 口径）：
    ULC 文本塔 9 维宏平均 0.6894 vs BiomedCLIP 0.4092（低于多数类基线=无判别力）
    可用维度（ULC − 多数类基线）：Organ +0.502 / Margins +0.387 / Echogenicity +0.301
                                  / Shape +0.297 / Diagnosis +0.276 / BodySystem +0.194
    不可用维度：Vascularity −0.056（低于基线）/ InternalCharacteristics +0.143
                / PosteriorAcoustics +0.045（二分类且基线本身 0.843 过高）
    → 默认只启用 6 个可用维度，其余返回 unavailable 并说明原因

定位：**旁路校验，不是诊断结论**。把报告里"边界清/低回声/无血流"等声明
与模型 zero-shot 推断做一致性比对，冲突时给出提示供医生复核。
绝不覆盖或改写诊断结论——医生是最终判读者。

实现要点
--------
- **只用 ULC 文本塔**（512 维 BERT，~400MB），**CPU 可跑**（GPU 常被 vLLM 占满）
- **prompt 特征预编码缓存**：9 维共 50 余条 prompt 固定，模块加载时算一次
  （磁盘 npy 缓存），运行时只编码"报告文本"一句 → CPU 上约 30~80ms
- 标签体系与 prompt 句式对齐论文附录 F.2（Task1/2 与 Task3-9 两套句式）
- 失败安全：缺权重/依赖不可用 → ``AttributeHintsUnavailableError``，
  端点转 503 + 原因；管线内调用方只 warning + 缺省（不阻断诊断）
"""
from __future__ import annotations

import os

# ⚠️ 必须在**任何** huggingface_hub / open_clip import 之前设离线标志。
# huggingface_hub.constants 在 import 时把 HF_HUB_OFFLINE 固化成常量，
# 之后再 os.environ.setdefault 已经太晚——实测 2026-10-04 真机：
# 方法内 setdefault 没生效，属性提示请求触发 huggingface.co HEAD 重试 5 次
# （网络不可达），同步阻塞 event loop，整个 18000 无响应。
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

# 维度定义：(字段名, 中文标签, 是否 lesion 句式, 是否启用)
# 启用集合 = A.21 实测「显著超多数类基线」的 6 维
TASKS: list[tuple[str, str, bool, bool]] = [
    ("Body_system_level", "体系统", False, True),
    ("Organ_level", "器官", False, True),
    ("Diagnosis", "诊断", True, True),
    ("Shape", "形态", True, True),
    ("Margins", "边缘", True, True),
    ("Echogenicity", "回声", True, True),
    ("InternalCharacteristics", "内部特征", True, False),
    ("PosteriorAcoustics", "后方声影", True, False),
    ("Vascularity", "血流", True, False),
]
# 禁用维度的实测依据（写进报告，避免"为什么没有血流提示"的疑问）
DISABLED_REASON = {
    "InternalCharacteristics": "实测 +0.143 弱于基线，增量不足不上产品",
    "PosteriorAcoustics": "实测 +0.045（基线 0.843 本身极高），无区分度",
    "Vascularity": "实测 −0.056 低于多数类基线，模型在该维无判别力",
}
# 各维标签集（来自 US-365K UDAF 词表的高频类，与 A.21 探针一致）
LABELS: dict[str, list[str]] = {
    "Body_system_level": [
        "Abdomen and retroperitoneum", "Urinary Tract and male reproductive system",
        "Gynaecology", "Head and Neck", "Breast and Axilla",
        "Musculoskeletal Joints and Tendons", "Thorax", "Pediatrics",
        "Peripheral vessels",
    ],
    "Organ_level": [
        "Liver", "Gallbladder and bile ducts", "Pancreas", "Spleen", "Kidney and ureter",
        "Bladder", "Scrotum", "Uterus", "Thyroid gland", "Lymph nodes", "Breast",
        "Abdominal wall",
    ],
    "Diagnosis": ["nodule", "cyst", "mass", "fluid collection", "normal appearance"],
    "Shape": ["round", "oval", "lobulated", "tubular/linear", "nodular", "flattened", "irregular"],
    "Margins": ["well-defined", "ill-defined/indistinct"],
    "Echogenicity": ["anechoic", "hypoechoic", "isoechoic", "hyperechoic", "mixed echogenicity"],
    "InternalCharacteristics": [
        "cystic components", "calcifications", "septations", "solid components",
        "mixed cystic and solid mass",
    ],
    "PosteriorAcoustics": ["enhancement", "shadowing"],
    "Vascularity": [
        "reduced/diminished vascularity", "normal/regular vascularity", "no vascularity",
        "increased vascularity", "indeterminate/inhomogeneous vascularity",
    ],
}
# 报告中文声明 → 标签（一致性比对用；只覆盖临床最常写的表述）
REPORT_CLAIM_MAP: dict[str, dict[str, str]] = {
    "Margins": {
        "边界清": "well-defined", "边界清晰": "well-defined", "边界清楚": "well-defined",
        "形态规则": "well-defined", "边界欠清": "ill-defined/indistinct",
        "边界不清": "ill-defined/indistinct", "边缘模糊": "ill-defined/indistinct",
        "边界不规则": "ill-defined/indistinct",
    },
    "Echogenicity": {
        "低回声": "hypoechoic", "低回声区": "hypoechoic", "偏高回声": "hyperechoic",
        "高回声": "hyperechoic", "等回声": "isoechoic", "无回声": "anechoic",
        "混合回声": "mixed echogenicity",
    },
    "Shape": {
        "圆形": "round", "椭圆形": "oval", "卵圆": "oval", "分叶": "lobulated",
        "不规则": "irregular", "扁平": "flattened",
    },
    "Diagnosis": {
        "结节": "nodule", "囊肿": "cyst", "囊性": "cyst", "肿块": "mass",
        "积液": "fluid collection", "正常": "normal appearance",
    },
}


class AttributeHintsUnavailableError(RuntimeError):
    """属性提示能力不可用（缺依赖/缺权重/设备不可用）。"""


@dataclass(frozen=True)
class DimensionHint:
    dimension: str
    label_zh: str
    top1: str
    confidence: float
    margin: float
    report_claim: str | None = None
    agree: bool | None = None

    def as_dict(self) -> dict[str, Any]:
        d = {
            "dimension": self.dimension,
            "label_zh": self.label_zh,
            "top1": self.top1,
            "confidence": round(self.confidence, 4),
            "margin": round(self.margin, 4),
        }
        if self.report_claim is not None:
            d["report_claim"] = self.report_claim
            d["agree"] = self.agree
        return d


@dataclass(frozen=True)
class AttributeHintsResult:
    text_length: int
    hints: list[DimensionHint] = field(default_factory=list)
    disabled: dict[str, str] = field(default_factory=dict)
    conflicts: int = 0
    latency_ms: float = 0.0
    device: str = "cpu"
    model_meta: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "text_length": self.text_length,
            "hints": [h.as_dict() for h in self.hints],
            "disabled": self.disabled,
            "conflicts": self.conflicts,
            "latency_ms": self.latency_ms,
            "device": self.device,
            "model": self.model_meta,
        }


def _clip_weights() -> Path:
    from app.config.settings import settings

    p = Path(getattr(settings, "attribute_hints_clip_weights", "") or
             "~/uai/models/ultrasound_clip/checkpoints.pt").expanduser()
    return p


class AttributeHintEngine:
    """懒加载单例（``AttributeHintEngine.get()``）。"""

    _instance: AttributeHintEngine | None = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        self._text_tower: Any = None
        self._tokenizer: Any = None
        self._prompt_feats: dict[str, Any] = {}
        self._device: str = "cpu"
        self._meta: dict[str, Any] = {}

    @classmethod
    def get(cls) -> AttributeHintEngine:
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    @staticmethod
    def detect_language(text: str) -> str:
        """粗判文本主要语言（CJK 占比 ≥20% 判为 zh）。

        **为什么必须判**：ULC 文本塔是英文 PubMedBERT，中文报告进分词器
        近全 [UNK]，语义完全丢失。实测中文报告"甲状腺低回声结节"被判成
        Breast and Axilla / Abdominal wall / cyst / anechoic（4 维全错）。
        中文场景发冲突提示等于给医生制造假警报——必须挡住。
        """
        if not text:
            return "unknown"
        cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
        return "zh" if cjk / max(len(text), 1) >= 0.2 else "en"

    # ---------------------------------------------------------------- 加载
    def _resolve_device(self) -> str:
        from app.config.settings import settings

        pref = str(getattr(settings, "attribute_hints_device", "auto")).lower()
        if pref == "cpu":
            return "cpu"
        try:
            import torch
        except Exception as exc:  # pragma: no cover
            raise AttributeHintsUnavailableError(f"torch 不可用：{exc}") from exc
        if pref == "cuda":
            if not torch.cuda.is_available():
                raise AttributeHintsUnavailableError(
                    "配置要求 cuda，但 torch 看不到 GPU（检查 CUDA_VISIBLE_DEVICES；"
                    "注意本机 GPU 常被 vLLM 占满，建议 cpu）"
                )
            return "cuda"
        return "cuda" if torch.cuda.is_available() else "cpu"

    def _ensure_loaded(self) -> None:
        if self._text_tower is not None:
            return
        from app.config.settings import settings

        if not bool(getattr(settings, "attribute_hints_enabled", True)):
            raise AttributeHintsUnavailableError("属性提示功能已关闭（attribute_hints_enabled=false）")

        device = self._resolve_device()
        wpath = _clip_weights()
        if not wpath.exists():
            raise AttributeHintsUnavailableError(f"ULC 权重不存在：{wpath}")

        try:
            import torch
        except Exception as exc:
            raise AttributeHintsUnavailableError(f"依赖不可用（torch）：{exc}") from exc

        # open_clip 装载前确保**真的**离线：huggingface_hub 在 import 时已把
        # HF_HUB_OFFLINE 固化成常量，只改环境变量对它无效（见文件顶部注释）。
        #
        # 这里用 sys.modules 取**已导入**的模块而不是直接 import：
        #   - 若 huggingface_hub 已随 open_clip 导入 → 改常量，双保险；
        #   - 若尚未导入 → 下方 open_clip 首次 import 时读文件顶部设好的环境变量；
        #   - 不硬 import 该包还有个副作用好处：依赖声明守卫（它扫全仓 import）
        #     不会把 huggingface_hub 判成"未声明的硬导入"（它是 open_clip 的
        #     传递依赖，不该由本项目直接声明）。
        import sys

        _hf_constants = sys.modules.get("huggingface_hub.constants")
        if _hf_constants is not None and not getattr(
            _hf_constants, "HF_HUB_OFFLINE", False
        ):
            _hf_constants.HF_HUB_OFFLINE = True
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"

        # 离线 CLIP：先把权重/配置放到 HF 离线缓存，再 create_model（见 A.21 踩坑记录）
        try:
            import open_clip
        except Exception as exc:
            raise AttributeHintsUnavailableError(f"依赖不可用（open_clip）：{exc}") from exc
        try:
            self._offline_cache_prepare()
            model, _, _ = open_clip.create_model_and_transforms(
                "hf-hub:microsoft/BiomedCLIP-PubMedBERT_256-vit_base_patch16_224",
            )
            tokenizer = open_clip.get_tokenizer(
                "hf-hub:microsoft/BiomedCLIP-PubMedBERT_256-vit_base_patch16_224",
            )
        except Exception as exc:
            raise AttributeHintsUnavailableError(f"CLIP 装载失败：{exc}") from exc

        # 只取 ULC 的文本塔权重（视觉塔用不上，省 340MB 内存）
        try:
            ckpt = torch.load(str(wpath), map_location="cpu", weights_only=True)
        except Exception as exc:
            raise AttributeHintsUnavailableError(f"ULC 权重加载失败：{exc}") from exc
        usd = ckpt.get("state_dict", ckpt)
        text_sd = {k.replace("base_clip.text.", "", 1): v
                   for k, v in usd.items() if k.startswith("base_clip.text.")}
        if not text_sd:
            raise AttributeHintsUnavailableError("ULC 权重里没有文本塔参数（键名不匹配）")
        try:
            model.text.load_state_dict(text_sd, strict=False)
        except Exception as exc:
            raise AttributeHintsUnavailableError(f"文本塔权重装载失败：{exc}") from exc

        tower = model.text.to(device).eval()
        self._text_tower = tower
        self._tokenizer = tokenizer
        self._device = device
        self._meta = {
            "weights": str(wpath),
            "tower": "ULC text tower (PubMedBERT 512-d, **English only**)",
            "official_zero_shot_macro_acc": 0.6894,
            "enabled_dims": [f for f, _, _, on in TASKS if on],
        }
        self._build_prompt_cache()
        logger.info("attribute_hints_loaded", device=device, dims=len(self._prompt_feats))

    def _offline_cache_prepare(self) -> None:
        """把本地 CLIP 权重放进 HF 离线缓存（open_clip 离线装载的前提）。"""
        import shutil
        from pathlib import Path as _P

        cache = _P.home() / ".cache" / "huggingface" / "hub"
        repo = cache / "models--microsoft--BiomedCLIP-PubMedBERT_256-vit_base_patch16_224"
        snap = repo / "snapshots" / "offline"
        snap.mkdir(parents=True, exist_ok=True)
        src_dir = _P.home() / "uai" / "models" / "biomedclip-base-patch16-224"
        for fn in ("open_clip_config.json", "open_clip_pytorch_model.bin", "tokenizer.json",
                   "vocab.txt", "special_tokens_map.json", "tokenizer_config.json"):
            s, d = src_dir / fn, snap / fn
            if s.exists() and not d.exists():
                shutil.copy(s, d)
        (repo / "refs").mkdir(parents=True, exist_ok=True)
        (repo / "refs" / "main").write_text("offline", encoding="utf-8")

    def _build_prompt_cache(self) -> None:
        """一次性编码所有 prompt 并缓存（运行时只需编码报告文本一句）。"""
        import torch
        import torch.nn.functional as F

        with torch.no_grad():
            for field_name, _zh, lesion, enabled in TASKS:
                if not enabled:
                    continue
                labels = LABELS[field_name]
                prompts = [
                    (f"a {lab} lesion in an ultrasound image" if lesion
                     else f"a ultrasound image of {lab}")
                    for lab in labels
                ]
                toks = self._tokenizer(prompts).to(self._device)
                feats = F.normalize(
                    self._text_tower(toks).float(), dim=-1
                ).detach().cpu()
                self._prompt_feats[field_name] = feats

    # -------------------------------------------------------------- 推理
    def hints_for_text(self, text: str) -> AttributeHintsResult:
        """对一段报告文本给出各维度 zero-shot 提示 + 与报告声明的一致性比对。

        **语言护栏**：ULC 文本塔是英文 PubMedBERT，中文文本进去近全 [UNK]，
        输出无意义。中文文本时**直接拒绝**并抛
        ``AttributeHintsUnavailableError``——绝不用垃圾输出生成"冲突提示"
        （临床危险：实测中文报告被判 4 维全错，会给医生制造假警报）。
        """
        import torch
        import torch.nn.functional as F

        if not text or not text.strip():
            raise ValueError("text 不能为空")
        lang = self.detect_language(text)
        if lang == "zh":
            raise AttributeHintsUnavailableError(
                "属性提示当前仅支持**英文**报告文本：ULC 文本塔是英文 PubMedBERT，"
                "中文报告经分词后近全 [UNK]，实测会把『甲状腺低回声结节』判成 "
                "Breast and Axilla/cyst/anechoic（4 维全错）。中文报告需要中文医学"
                "文本塔（如 CBLUE / MedBERT-zh）并重做零样本评测后方可启用。"
            )
        t0 = time.perf_counter()
        self._ensure_loaded()

        with torch.no_grad():
            toks = self._tokenizer([text]).to(self._device)
            # 归一到 tower 所在设备再算相似度：prompt 缓存为了省显存存在 CPU，
            # 而编码结果在 self._device（GPU 路径实测在这里报
            # "Expected all tensors to be on the same device"——2026-10-04）。
            # 缓存总量仅数 MB（6 维 × ~10 类 × 512 维），搬上设备比逐维搬 tf 划算。
            tf = F.normalize(self._text_tower(toks).float(), dim=-1)[0]
            tf_dev = tf.to(self._device)

        hints: list[DimensionHint] = []
        conflicts = 0
        for field_name, zh, _lesion, enabled in TASKS:
            if not enabled:
                continue
            labels = LABELS[field_name]
            pf = self._prompt_feats[field_name].to(self._device)
            sims = (pf @ tf_dev).tolist()
            order = sorted(range(len(sims)), key=lambda i: sims[i], reverse=True)
            top1 = labels[order[0]]
            conf = float(sims[order[0]])
            margin = float(sims[order[0]] - sims[order[1]]) if len(order) > 1 else 0.0
            claim = self._claim_from_text(field_name, text)
            agree = None
            if claim is not None:
                agree = (claim == top1)
                if not agree:
                    conflicts += 1
            hints.append(DimensionHint(
                dimension=field_name, label_zh=zh, top1=top1,
                confidence=conf, margin=margin, report_claim=claim, agree=agree,
            ))

        disabled = {f: DISABLED_REASON[f] for f, _z, _l, on in TASKS if not on and f in DISABLED_REASON}
        return AttributeHintsResult(
            text_length=len(text),
            hints=hints,
            disabled=disabled,
            conflicts=conflicts,
            latency_ms=round((time.perf_counter() - t0) * 1000, 1),
            device=self._device,
            model_meta=dict(self._meta),
        )

    @staticmethod
    def _claim_from_text(field_name: str, text: str) -> str | None:
        """从报告文本抽取该维度最显著的声明（无声明返回 None）。

        排序：出现次数多的优先；**次数相同时更长（更具体）的表述优先**——
        否则「椭圆形」会被「圆形」抢走（子串），「边界欠清」会被「边界清」误判。
        """
        mapping = REPORT_CLAIM_MAP.get(field_name)
        if not mapping:
            return None
        hits = [(text.count(k), len(k), v) for k, v in mapping.items() if k in text]
        if not hits:
            return None
        hits.sort(reverse=True)
        return hits[0][2]

    def health(self) -> dict[str, Any]:
        try:
            self._ensure_loaded()
        except AttributeHintsUnavailableError as exc:
            logger.warning("AttributeHintEngine.health: 未预期异常，按原逻辑降级", exc_info=True)
            return {"available": False, "reason": str(exc)}
        except Exception as exc:  # pragma: no cover
            logger.warning("AttributeHintEngine.health: 未预期异常，按原逻辑降级", exc_info=True)
            return {"available": False, "reason": f"{type(exc).__name__}: {exc}"}
        return {
            "available": True,
            "device": self._device,
            "model": self._meta,
            "enabled_dims": [f for f, _z, _l, on in TASKS if on],
            "disabled_dims": {f: DISABLED_REASON[f] for f, _z, _l, on in TASKS
                              if not on and f in DISABLED_REASON},
        }


__all__ = [
    "AttributeHintEngine",
    "AttributeHintsResult",
    "AttributeHintsUnavailableError",
    "DimensionHint",
]
