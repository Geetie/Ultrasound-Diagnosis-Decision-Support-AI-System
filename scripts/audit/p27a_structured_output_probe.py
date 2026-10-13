"""p27a —— 采样层结构化输出**有效性实测**（vLLM，服务器上跑）。

问的是什么
----------
`AGENTS.md` 硬规则 #5 断言「新版 vLLM 会**静默忽略** `guided_json`」，因此结构化
输出要改走 OpenAI 兼容的 `response_format`。那是经验结论 —— 本探针用**真实调用**
把它钉成可复核的数字，并回答：

- `response_format`（`{"type":"json_schema","json_schema":{...}}`）在本机 vLLM 上
  到底生不生效？
- `guided_json` 是否真的静默失效（输出仍是自由文本）？
- 两者同发会怎样？

判据
----
同一条 prompt 下，输出能否被 `json.loads` 解析为 `{"grading": <枚举内值>}`。
三臂对照：`baseline`（无约束）/ `guided_json` / `response_format`。

用法（服务器，venv 或系统 python 均可，纯标准库）：

    python scripts/audit/p27a_structured_output_probe.py

退出码：0 = `response_format` 生效（默认路径可用）；1 = 未生效（需回落/改道）。
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

BASE = os.getenv("UAI_VLLM_BASE", "http://127.0.0.1:8002").rstrip("/")
ROUNDS = int(os.getenv("UAI_P27A_ROUNDS", "3"))

SCHEMA = {
    "type": "object",
    "properties": {"grading": {"type": "string", "enum": ["2", "3", "4a", "4b", "5"]}},
    "required": ["grading"],
}
PROMPT = (
    "你是超声诊断 AI。请对甲状腺结节给出 ACR TI-RADS 分级。"
    "只输出结果，不要解释。"
)


def _post(path: str, payload: dict) -> tuple[int, dict | str]:
    req = urllib.request.Request(
        f"{BASE}{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:  # 4xx/5xx：把 body 带回来定位原因
        return exc.code, exc.read().decode("utf-8", "replace")[:300]
    except Exception as exc:  # 探针要如实报告任何失败
        return -1, f"{type(exc).__name__}: {exc}"


def _text_of(data: dict) -> str:
    try:
        return data["choices"][0]["message"]["content"] or ""
    except Exception:
        return ""


def _conforms(text: str) -> bool:
    """输出是否 = `{"grading": <enum>}`（容忍前后缀/围栏）。"""
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return False
    try:
        obj = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return False
    value = obj.get("grading") if isinstance(obj, dict) else None
    return isinstance(value, str) and value.strip() in SCHEMA["properties"]["grading"]["enum"]


def _model_name() -> str:
    with urllib.request.urlopen(f"{BASE}/v1/models", timeout=15) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data["data"][0]["id"]


def main() -> int:
    try:
        model = _model_name()
    except Exception as exc:
        print(f"[FAIL] vLLM 不可达 {BASE}: {type(exc).__name__}: {exc}")
        return 1

    arms: dict[str, dict] = {
        "baseline": {},
        "guided_json": {"guided_json": SCHEMA},
        "response_format": {
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "thyroid_grading", "schema": SCHEMA},
            }
        },
    }

    results: dict[str, dict] = {}
    for arm, extra in arms.items():
        ok = 0
        statuses: list[int] = []
        samples: list[str] = []
        for _ in range(ROUNDS):
            payload = {
                "model": model,
                "messages": [{"role": "user", "content": PROMPT}],
                "max_tokens": 128,
                "temperature": 0.05,
                **extra,
            }
            status, data = _post("/v1/chat/completions", payload)
            statuses.append(status)
            if status == 200 and isinstance(data, dict):
                text = _text_of(data)
                if _conforms(text):
                    ok += 1
                if len(samples) < 3:
                    samples.append(text.strip().replace("\n", " ")[:90])
            elif len(samples) < 3:
                samples.append(f"<HTTP {status}> {str(data)[:90]}")
        results[arm] = {"ok": ok, "rounds": ROUNDS, "statuses": statuses, "samples": samples}

    print(f"== p27a 结构化输出实测（model={model}, rounds={ROUNDS}）==")
    print(f"   prompt={PROMPT[:40]}…")
    for arm, r in results.items():
        rate = r["ok"] / r["rounds"] if r["rounds"] else 0.0
        print(
            f"   {arm:16s} 符合 schema {r['ok']}/{r['rounds']} ({rate:.0%}) "
            f"HTTP={r['statuses']}"
        )
        for s in r["samples"]:
            print(f"        sample: {s}")

    rf_ok = results["response_format"]["ok"] > 0
    gj_ok = results["guided_json"]["ok"] > 0
    print("\n   结论：")
    print(f"     response_format 生效：{rf_ok}")
    print(f"     guided_json   生效：{gj_ok}（硬规则 #5 认为新版 vLLM 会静默忽略）")
    report = {"model": model, "rounds": ROUNDS, "results": results}
    out = os.getenv("UAI_P27A_OUT", "/tmp/uai_p27a_structured.json")
    try:
        with open(out, "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=2)
        print(f"   报告：{out}")
    except OSError as exc:
        print(f"   [warn] 报告写入失败：{exc}")

    if not rf_ok:
        print("\n[FAIL] response_format 未生效 —— 采样层约束不可用，需回落/改道")
        return 1
    print("\n[PASS] response_format 可用（结构化输出走它）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
