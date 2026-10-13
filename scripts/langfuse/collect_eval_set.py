"""Langfuse 评测集采集（19 号计划：把「双引擎评测集 ≥700 例」变成可持续运营的资产）。

做什么
------
从自托管 Langfuse 拉真实判读 trace，按「同患者 + 同影像」**去重**后落到评测集 JSONL，
并打印相对 ≥700 目标的进度。

    python scripts/langfuse/collect_eval_set.py --out data/eval/langfuse_eval_set.jsonl \
        --target 700 --max-traces 500

退出码：0 = 成功（含未达目标，进度如实打印）；1 = 达到目标；2 = 缺输入/接口不可用。

为什么要脚本化（而不是人肉导出）
--------------------------------
19 号把它定位为**可持续运营**的资产：每次判读自动产生「输入 + 输出 + 正确口径」入集；
人肉导出无法去重、无法复现、也无法在 CI 里看进度。

去重键（同患者同影像 hash）
---------------------------
trace 的 metadata 里由应用侧写入（`app/observability/tracing.py` 的 span 属性）：
``patient_id``（脱敏后的患者标识）+ ``image_sha256``（影像字节 sha256）。
两者任一缺失时**退化为** trace id 作键（如实标注 dedup_fallback 计数，不假装去重过）。

注意：本脚本只读 Langfuse、只写本地 JSONL，**不**改远端任何数据。
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

SAMPLE_LIMIT = 100  # 单次请求上限（Langfuse public API 约束）


def _basic_auth_from_env_or(pk: str, sk: str) -> str:
    """优先用命令行/环境给的一对 key，其次从 OTLP 头里解析（部署时通常只配了后者）。"""
    if pk and sk:
        raw = f"{pk}:{sk}"
    else:
        header = os.environ.get("OBS_TRACING_OTLP_HEADERS", "")
        token = header.split("Basic", 1)[-1].strip() if "Basic" in header else ""
        if not token:
            raise SystemExit(
                "缺凭据：请给 --public-key/--secret-key，或设置 OBS_TRACING_OTLP_HEADERS"
            )
        raw = base64.b64decode(token).decode()
    return "Basic " + base64.b64encode(raw.encode()).decode()


def _get(base: str, path: str, auth: str, timeout: float = 30.0) -> dict:
    req = urllib.request.Request(f"{base}{path}", method="GET")
    req.add_header("Authorization", auth)
    req.add_header("Accept", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:  # 4xx/5xx 必须显式失败（不静默当"没有数据"）
        detail = exc.read().decode("utf-8", "replace")[:200]
        raise SystemExit(f"Langfuse API {path} 失败：HTTP {exc.code} {detail}") from exc
    except Exception as exc:  # 网络不可达同样显式失败
        raise SystemExit(f"Langfuse API {path} 不可达：{type(exc).__name__}: {exc}") from exc


def _dedup_key(trace: dict) -> tuple[str, bool]:
    """返回 (去重键, 是否退化)。metadata 在 trace 顶层或 input 里（版本差异）。"""
    meta = trace.get("metadata") or {}
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except json.JSONDecodeError:
            meta = {}
    patient = meta.get("patient_id") or trace.get("userId") or ""
    image = meta.get("image_sha256") or meta.get("image_id") or ""
    if patient and image:
        return f"{patient}|{image}", False
    if patient:
        return f"{patient}|trace:{trace.get('id')}", True
    return f"trace:{trace.get('id')}", True


def _text_of(value) -> str:
    """把 trace 的 input/output 拍平成可判定文本（版本间结构差异大，保底 json）。"""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", default=os.environ.get("LANGFUSE_HOST", "http://127.0.0.1:30100"))
    ap.add_argument("--public-key", default=os.environ.get("LANGFUSE_PUBLIC_KEY", ""))
    ap.add_argument("--secret-key", default=os.environ.get("LANGFUSE_SECRET_KEY", ""))
    ap.add_argument("--out", default="data/eval/langfuse_eval_set.jsonl")
    ap.add_argument("--target", type=int, default=700, help="评测集目标条数（19 号口径）")
    ap.add_argument("--max-traces", type=int, default=1000, help="最多拉取多少条 trace")
    args = ap.parse_args()

    auth = _basic_auth_from_env_or(args.public_key, args.secret_key)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    seen: set[str] = set()
    kept: list[dict] = []
    fallback = 0
    page = 1
    while len(kept) < args.max_traces:
        data = _get(args.base, f"/api/public/traces?page={page}&limit={SAMPLE_LIMIT}", auth)
        items = data.get("data") or []
        if not items:
            break
        for tr in items:
            key, degraded = _dedup_key(tr)
            if degraded:
                fallback += 1
            if key in seen:
                continue
            seen.add(key)
            kept.append(
                {
                    "trace_id": tr.get("id"),
                    "timestamp": tr.get("timestamp"),
                    "name": tr.get("name"),
                    "dedup_key": key,
                    "input": _text_of(tr.get("input")),
                    "output": _text_of(tr.get("output")),
                    "metadata": tr.get("metadata"),
                    "tags": tr.get("tags"),
                    "latency_s": tr.get("latency"),
                }
            )
            if len(kept) >= args.max_traces:
                break
        page += 1

    with out_path.open("w", encoding="utf-8") as fh:
        for row in kept:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    # 内容指纹：让"两次导出是否同一份数据"可核对（sha256 落台账）
    digest = hashlib.sha256(
        "\n".join(json.dumps(r, ensure_ascii=False, sort_keys=True) for r in kept).encode()
    ).hexdigest()[:16]

    print(f"评测集：{len(kept)} 条（去重键 {len(seen)} 个；退化键 {fallback} 条）→ {out_path}")
    print(f"进度：{len(kept)}/{args.target}（{len(kept) / args.target:.1%}）  sha256:{digest}")
    return 1 if len(kept) >= args.target else 0


if __name__ == "__main__":
    sys.exit(main())
