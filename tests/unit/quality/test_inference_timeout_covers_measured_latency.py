"""推理超时必须覆盖实测生成延迟（2026-11-08）。

## 缺陷

`inference_timeout_seconds` 曾是 **30.0 s**，而 `p107b` 实测 II-Medical-8B
的**单项**生成延迟为：

    p50 = 46.04 s / p90 = 94.1 s / max = 101.4 s   （104 次调用）

→ **93/104 = 89.42% 的调用必然超时** → `E2002_TIMEOUT` → `circuit_breaker_open`
→ 后续请求全被拒。

也就是说原来的 30 s 不是"保护"，而是**让系统在正常延迟下自我熔断**。
任何在该配置下测出的生产指标，测的都是"熔断率"而不是"模型能力"。

本守卫把「超时必须 ≥ 实测最大值」钉死，防止有人再按直觉把它调回 30 s。

## 2026-11-09 补充：分任务超时（**这才是本轮踩到的真坑**）

代码默认早已改成 180 s，但**生产 `~/uai/.env` 里写着 `INFERENCE_TIMEOUT_SECONDS=30`**
—— 环境变量覆盖了代码默认值，于是"改了代码"在线上等于没改（本轮真机实测确认：
`/tmp/_smoke2.py` 加载生产 .env 后打印 `inference_timeout_seconds = 30.0`）。
这也是 A.171 记的"30s 缺陷"必须连 `.env` 一起修的原因。

另外，"一个超时打天下"本身就是错的：判读（1024 token 富文本）与
摘要/判官（≤256 token）**延迟差两个数量级**（P.212 实测 p50 0.66 s / max 1.58 s）。
所以本轮拆成三档：判读 / 快速（摘要·判官） / 结构化重试。本文件把三档的关系钉住。
"""

from __future__ import annotations

from app.config.settings import settings

#: p107b 实测的生成延迟（秒）。改这些数必须同时更新依据。
MEASURED_LATENCY_P50_S = 46.04
MEASURED_LATENCY_P90_S = 94.1
MEASURED_LATENCY_MAX_S = 101.4
MEASURED_N_CALLS = 104
MEASURED_TIMEOUT_RATE_AT_30S = 93 / 104  # 89.42%

#: P.212（2026-11-09 真机）实测的**摘要/判官**类短生成延迟（max_tokens≤256，自由文本）。
#: 这是"分任务超时"里 fast 档的依据：给它判读档的 180s 等于让上游故障把主链路多挂 3 分钟。
MEASURED_FAST_P50_S = 0.66
MEASURED_FAST_MAX_S = 1.58

#: P.206（2026-11-09 真机）实测的**结构化重试**延迟（约束解码，输出 `{"grading":"TR4a"}`）。
MEASURED_STRUCTURED_P50_S = 0.41
MEASURED_STRUCTURED_MAX_S = 0.67


def test_timeout_covers_measured_max_latency():
    """超时必须 ≥ 实测单项最大值，否则正常请求会系统性超时。"""
    assert settings.inference_timeout_seconds >= MEASURED_LATENCY_MAX_S, (
        f"inference_timeout_seconds={settings.inference_timeout_seconds} "
        f"< 实测 max {MEASURED_LATENCY_MAX_S}s —— 正常请求会被误判超时"
    )


def test_timeout_not_the_old_30s_value():
    """钉住：不允许回到 30 s。该值下 89.42% 的调用必然超时。"""
    assert settings.inference_timeout_seconds > 30.0, (
        "inference_timeout_seconds 回到 30s：p107b 实测 89.42% 调用会超时"
        "（p50 就有 46.04s）"
    )


def test_timeout_leaves_headroom_above_max():
    """留余量：超时应至少是实测 max 的 1.5 倍（应对长尾与负载波动）。"""
    ratio = settings.inference_timeout_seconds / MEASURED_LATENCY_MAX_S
    assert ratio >= 1.5, f"余量不足：{ratio:.2f}×（要求 ≥1.5×）"


def test_fast_timeout_covers_measured_max():
    """快速档（摘要/判官）也必须 ≥ 实测 max —— 否则短任务反被误杀。"""
    assert settings.inference_timeout_fast_seconds >= MEASURED_FAST_MAX_S, (
        f"fast 档 {settings.inference_timeout_fast_seconds}s < 实测 max {MEASURED_FAST_MAX_S}s"
    )


def test_fast_timeout_much_smaller_than_full():
    """快速档必须**明显小于**判读档 —— 否则"分任务超时"没有意义。"""
    assert settings.inference_timeout_fast_seconds <= settings.inference_timeout_seconds / 2, (
        f"fast={settings.inference_timeout_fast_seconds}s 与 full="
        f"{settings.inference_timeout_seconds}s 差不够大（要求 ≤ full/2）"
    )


def test_structured_timeout_covers_measured_and_is_tight():
    """结构化重试档：覆盖实测 max，且不超过判读档（它是"重试"，不该比主调用还宽）。"""
    assert settings.inference_timeout_structured_seconds >= MEASURED_STRUCTURED_MAX_S
    assert settings.inference_timeout_structured_seconds <= settings.inference_timeout_seconds


def test_fast_retries_fit_inside_one_full_timeout():
    """快速档 × 重试次数不应超过判读档一次超时 —— 否则判官的 3 次重试比主判读还慢。"""
    worst = settings.inference_timeout_fast_seconds * settings.inference_max_retries
    assert worst <= settings.inference_timeout_seconds, (
        f"fast {settings.inference_timeout_fast_seconds}s × retries "
        f"{settings.inference_max_retries} = {worst}s > full {settings.inference_timeout_seconds}s"
    )


def test_timeout_below_retry_explosion():
    """上限保护：超时 × 重试次数不应超过 10 分钟（否则一个请求能挂 10+ 分钟）。"""
    worst_case = settings.inference_timeout_seconds * settings.inference_max_retries
    assert worst_case <= 600, (
        f"超时 {settings.inference_timeout_seconds}s × 重试 {settings.inference_max_retries} "
        f"= {worst_case}s 超过 600s 上限，一个请求可能挂 10+ 分钟"
    )
