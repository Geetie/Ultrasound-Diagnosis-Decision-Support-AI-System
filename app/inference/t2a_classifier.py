"""T2a 影像分类器客户端（8003 ``POST /v1/classify``）—— 生产链**旁路**接线。

## 为什么需要这个模块

T2a（微调 visual tower + LR 探针）此前**只被审计脚本调用**
（``scripts/audit/p29_t2a_aul_holdout.py``），生产代码里 ``app/`` 下没有任何
``/v1/classify`` 调用点 —— 于是"离线 AUC 0.8950"只是**离线模型指标**，
不是系统能力（见《架构真相-以代码为准-20261025.md》§3.3）。

本模块只做两件事：
1. :func:`classify_frame` —— 把一个代表帧 PNG 送去 8003 分类，返回
   ``probs / predicted / malignant_prob``；
2. :func:`probe_t2a_classifier` —— 供 ``/api/v1/health`` 暴露**能力状态**
   （可照抄 ``app/rag/image_leg.py::probe_image_leg`` 的写法）。

## 定位红线（写死在代码里）

**只作辅助参考**：结果写进 ``findings["t2a_classification"]``，**不参与**结论
选择、不参与 ``confidence``、不参与 ``status``。任何一个分支失败都只是
``available=False`` + 真实 ``reason``，绝不影响主判读 —— 与
:func:`app.services.diagnosis_pipeline._safe_nodule_contour` 同一套失败安全哲学。

## 失败原因必须**分开**（AGENTS.md 硬规则 8）

"取不到影像" / "服务没起" / "服务起来了但没加载模型" 是三件完全不同的事，
医生据此要做的事也不同（换影像 / 找运维 / 等模型加载）。历史教训：把
``best is None`` 一律报成 ``image_bytes_unavailable``，导致"这是屏幕截图、
本来不该勾画"被显示成"系统取不到影像"，医生白跑一趟。故本模块的
``reason`` 取值是**封闭集合**：

* ``disabled``              —— 配置开关关着（默认，零开销）
* ``no_image_ids``          —— 本次请求没有影像 ID
* ``image_not_indexed``     —— 有 ID 但 ``dicom_images`` 里没有对应行
* ``no_image_bytes``        —— 有行但存储里取不到字节（多帧/落盘失败）
* ``frame_decode_failed``   —— 有字节但 DICOM 解码不出帧
* ``service_unavailable``   —— 连不上 / 超时 / 非 2xx（**不含**模型缺失）
* ``model_missing``         —— 服务活着，但分类模型未加载（404/503 + 特征串）
* ``invalid_response``      —— 200 但响应体里没有可用 ``results``

## 配置键（**需要 Lead 在 settings.py 落地**，本模块用 getattr 兜默认值）

* ``t2a_classifier_in_pipeline_enabled: bool = False``（默认关，与分割旁路同风格）
* ``t2a_classifier_timeout_s: float = 10.0``
* ``t2a_classifier_max_frames: int = 1``（每例最多送几帧，控成本）
"""
from __future__ import annotations

import httpx
import structlog

logger = structlog.get_logger(__name__)

#: 端点路径（p29 第 97-118 行：multipart，字段名固定 `images`）
CLASSIFY_PATH = "/v1/classify"

#: 模型未加载时服务端可能的特征串（用于把 model_missing 与 service_unavailable 分开）
_MODEL_MISSING_MARKERS = (
    "model not loaded",
    "model_not_loaded",
    "no model",
    "model missing",
    "checkpoint not found",
    "not initialized",
    "尚未加载",
    "模型未加载",
)

#: 与诊断结论无关、只用于"能力是否可用"的健康探测超时
_PROBE_TIMEOUT_S = 2.0


def classify_reason_text(reason: str) -> str:
    """把内部 ``reason`` 翻译成给医生看的一句话（读路径/报告层可直接用）。

    与 ``segmentation_reason_text`` 同款：内置语言、**不认识的原因不编**
    （回退成"未知原因(<原样>)"，便于运维按原串检索日志）。
    """
    mapping = {
        "disabled": "T2a 分类未启用（配置开关关闭）",
        "no_image_ids": "本次请求未上传影像",
        "image_not_indexed": "影像未完成入库索引",
        "no_image_bytes": "影像文件取不到（可能未成功落盘）",
        "frame_decode_failed": "影像解码失败（文件损坏或非 DICOM）",
        "service_unavailable": "影像分类服务不可用",
        "model_missing": "影像分类服务已启动但分类模型未加载",
        "invalid_response": "影像分类服务返回了无法解析的结果",
    }
    return mapping.get(reason, f"未知原因({reason})")


def classify_url(*, host: str, port: int) -> str:
    """拼 classify 端点（纯函数，供守卫钉住 —— 避免各处手拼出不一致的 URL）。"""
    return f"http://{host}:{port}{CLASSIFY_PATH}"


def _has_model_missing_marker(text: str) -> bool:
    """响应体里是否有"模型未加载"的特征串（与状态码无关）。

    分成两个函数是因为两处判据不同：``/v1/classify`` 只有在**出错响应**里出现
    这个串才算 model_missing（200 正常结果里出现同名子串不应误判）；而
    ``/health`` 探测看的是**服务自述**，200 也可能自述"模型没加载"。
    """
    lowered = (text or "").lower()
    return any(marker.lower() in lowered for marker in _MODEL_MISSING_MARKERS)


def _looks_like_model_missing(status_code: int, text: str) -> bool:
    """判定"服务活着但没有模型"（区别于服务不可用）。"""
    if status_code not in (404, 409, 422, 500, 503):
        return False
    return _has_model_missing_marker(text)


def parse_classify_payload(body: object) -> dict | None:
    """解析 8003 的 classify 响应（纯函数）。

    期望形状（p29 第 97-118 行 + 第 200-210 行消费方式）::

        {"results": [{"probs": {"Benign": 0.7, "Malignant": 0.2, "Normal": 0.1},
                      "predicted": "Benign"}]}

    只认**第一个**结果（本模块一次只送一帧）。任何形状不符 → ``None``
    （由调用方报 ``invalid_response``，不猜一个假概率）。
    """
    if not isinstance(body, dict):
        return None
    results = body.get("results")
    if not isinstance(results, list) or not results:
        return None
    first = results[0]
    if not isinstance(first, dict):
        return None
    raw_probs = first.get("probs")
    if not isinstance(raw_probs, dict) or not raw_probs:
        return None
    probs: dict[str, float] = {}
    for key, value in raw_probs.items():
        try:
            probs[str(key)] = float(value)
        except (TypeError, ValueError) as exc:
            # 单个标签的分数不是数字 → 整条结果不可信（不跳过、不默认 0）。
            # 必须留痕：静默丢弃会让"模型返回了畸形概率"变成查不到原因。
            logger.warning(
                "t2a_classify_prob_not_numeric",
                label=str(key)[:40],
                error=str(exc)[:80],
                error_type=type(exc).__name__,
            )
            return None
    predicted = first.get("predicted")
    if not isinstance(predicted, str) or not predicted:
        predicted = max(probs, key=probs.get)
    malignant = probs.get("Malignant")
    return {
        "probs": probs,
        "predicted": predicted,
        "malignant_prob": malignant,
    }


async def classify_frame(
    png_bytes: bytes,
    *,
    host: str,
    port: int,
    timeout_s: float = 10.0,
    request_id: str = "",
) -> dict:
    """送一帧 PNG 去 8003 分类。**永不抛异常**（失败即 ``available=False + reason``）。

    Returns:
        成功: ``{"available": True, "probs": {...}, "predicted": str,
        "malignant_prob": float|None, "endpoint": str}``
        失败: ``{"available": False, "reason": <封闭集合中的一项>,
        "detail": str, "endpoint": str}``
    """
    endpoint = classify_url(host=host, port=port)
    if not png_bytes:
        return {
            "available": False,
            "reason": "no_image_bytes",
            "detail": "empty frame bytes",
            "endpoint": endpoint,
        }
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            resp = await client.post(
                endpoint,
                files={"images": ("frame.png", png_bytes, "image/png")},
            )
    except httpx.TimeoutException as exc:
        logger.warning(
            "t2a_classify_timeout",
            request_id=request_id,
            endpoint=endpoint,
            timeout_s=timeout_s,
            error=str(exc),
        )
        return {
            "available": False,
            "reason": "service_unavailable",
            "detail": f"TimeoutException: {exc}"[:200],
            "endpoint": endpoint,
        }
    except httpx.HTTPError as exc:
        logger.warning(
            "t2a_classify_transport_failed",
            request_id=request_id,
            endpoint=endpoint,
            error_type=type(exc).__name__,
            error=str(exc)[:160],
        )
        return {
            "available": False,
            "reason": "service_unavailable",
            "detail": f"{type(exc).__name__}: {exc}"[:200],
            "endpoint": endpoint,
        }

    if resp.status_code >= 400:
        text = resp.text or ""
        reason = (
            "model_missing"
            if _looks_like_model_missing(resp.status_code, text)
            else "service_unavailable"
        )
        logger.warning(
            "t2a_classify_http_error",
            request_id=request_id,
            endpoint=endpoint,
            status_code=resp.status_code,
            reason=reason,
        )
        return {
            "available": False,
            "reason": reason,
            "detail": f"HTTP {resp.status_code}: {text[:160]}",
            "endpoint": endpoint,
            "http_status": resp.status_code,
        }

    try:
        body = resp.json()
    except ValueError as exc:
        logger.warning(
            "t2a_classify_invalid_json",
            request_id=request_id,
            endpoint=endpoint,
            error=str(exc)[:160],
        )
        return {
            "available": False,
            "reason": "invalid_response",
            "detail": "response is not JSON",
            "endpoint": endpoint,
        }

    parsed = parse_classify_payload(body)
    if parsed is None:
        logger.warning(
            "t2a_classify_invalid_payload",
            request_id=request_id,
            endpoint=endpoint,
            body_keys=sorted(body.keys()) if isinstance(body, dict) else None,
        )
        return {
            "available": False,
            "reason": "invalid_response",
            "detail": "no usable results/probs in response",
            "endpoint": endpoint,
        }
    return {"available": True, **parsed, "endpoint": endpoint}


async def probe_t2a_classifier(*, host: str, port: int) -> dict:
    """能力探测（供 ``/api/v1/health`` 展示；**不参与** ``all_healthy``）。

    判据（照 ``probe_image_leg`` 的口径：能力状态必须可见，不许静默空转）：

    * 开关关着 → ``status="disabled"`` —— **短路、不发请求**：开关关着时
      每 30 秒一次的 health 不该产生一次跨进程调用（与旁路自身的零开销一致）。
      需要看"服务到底在不在"时把开关打开再看 health，语义更清楚。
    * ``/health`` 200 且没有"模型未加载"迹象 → ``ready``；
    * ``/health`` 200 但自述模型缺失 → ``model_missing``；
    * 连不上/超时/非 2xx → ``unreachable`` + ``probe_error``。
    """
    from app.config.settings import settings

    enabled = bool(getattr(settings, "t2a_classifier_in_pipeline_enabled", False))
    base = f"http://{host}:{port}"
    result: dict = {
        "enabled": enabled,
        "endpoint": classify_url(host=host, port=port),
        "status": "disabled",
    }
    if not enabled:
        return result

    try:
        async with httpx.AsyncClient(timeout=_PROBE_TIMEOUT_S) as client:
            resp = await client.get(f"{base}/health")
    except httpx.HTTPError as exc:
        result["status"] = "unreachable"
        result["probe_error"] = f"{type(exc).__name__}: {exc}"[:200]
        # 探测失败本身就是要报告的状态（`status=unreachable` 会随 health 出去），
        # 但日志也要留一条：否则"health 里看不见"与"服务真挂了"无法区分。
        logger.warning(
            "t2a_classifier_probe_failed",
            endpoint=f"{base}/health",
            error_type=type(exc).__name__,
            error=str(exc)[:160],
        )
        return result

    text = resp.text or ""
    if resp.status_code >= 400:
        result["status"] = (
            "model_missing"
            if _looks_like_model_missing(resp.status_code, text)
            else "unreachable"
        )
        result["probe_error"] = f"HTTP {resp.status_code}"
        return result
    if _has_model_missing_marker(text):
        result["status"] = "model_missing"
        return result
    result["status"] = "ready"
    return result


__all__ = [
    "CLASSIFY_PATH",
    "classify_frame",
    "classify_reason_text",
    "classify_url",
    "parse_classify_payload",
    "probe_t2a_classifier",
]
