"""A.75：**parse 环取证** —— 结构化重试为什么救不回来？（只读，不写库）

**链路事实**：`p55` 逐环审计显示带图请求里 9 条死在 `parse`（`grade_unparseable` 5、
`lvef_unparseable` 4）。而 `specialist/base.py` **本来就有**一次结构化重试：
`_grading_unparseable` → `_call_llm_response(structured=True)` → `_grade_from_structured`。
所以问题不是"没重试"，而是**重试也没救回来**。

**本探针要回答三个问题**（对每条失败记录，用**生产同款载荷**重放）：
1. 首次（plain）输出到底长什么样？是不是**被 max_tokens 截断**（`finish_reason=length`）？
2. 结构化（`response_format: json_schema`）输出**是不是真 JSON**、能不能被生产抽取器
   `extract_guided_grading` 取到分级？
3. 若结构化能取到而生产没取到 → 说明**生产重试确实没生效**（接线 bug）；
   若结构化也取不到 → 是**模型/约束**问题（改 prompt 或换代）。

复跑：
    ~/uai/venv/bin/python scripts/audit/run_with_env.py scripts/audit/p56_parse_link_probe.py
"""

from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import re
import sys

_SCRIPTS = pathlib.Path(__file__).resolve().parents[1]
for _p in (str(_SCRIPTS), str(_SCRIPTS / "audit"), str(_SCRIPTS / "langfuse")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import httpx  # noqa: E402
from collect_eval_set import _conclusion_of  # noqa: E402

BASE = "http://127.0.0.1:8002"
#: 与专科默认一致（`RAGSpecialistAgent.__init__: max_tokens: int = 1024`）
MAX_TOKENS = 1024

_TR = re.compile(r"TR\s*([1-5])", re.I)
_LVEF = re.compile(r"LVEF[^\d]{0,12}(\d{1,3})\s*%", re.I)
_SEVERITY = re.compile(r"(normal|mildly_reduced|moderately_reduced|severely_reduced|hyperdynamic)", re.I)


def extract_plain(text: str, organ: str) -> str | None:
    """生产的**首次**抽取口径（简版）：甲状腺看 TRn，心脏看 LVEF/严重度。纯函数（守卫钉住）。"""
    t = text or ""
    if organ == "thyroid":
        m = _TR.search(t)
        return f"TR{m.group(1)}" if m else None
    if organ == "cardiac":
        if _LVEF.search(t):
            return "lvef_present"
        m = _SEVERITY.search(t)
        return m.group(1).lower() if m else None
    return None


def parse_structured_json(text: str) -> dict | None:
    """结构化输出 → dict（容忍代码围栏/前后废话）。纯函数（守卫钉住）。"""
    t = (text or "").strip()
    if not t:
        return None
    t = re.sub(r"^```(?:json)?|```$", "", t, flags=re.MULTILINE).strip()
    start, end = t.find("{"), t.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(t[start : end + 1])
    except json.JSONDecodeError:  # silent-ok: 非 JSON 不是异常，返回 None 由调用方判"抽不出"
        return None
    return data if isinstance(data, dict) else None


def grading_of(data: dict | None) -> str | None:
    """从结构化 dict 里取分级字段（生产 `extract_guided_grading` 的等价口径）。"""
    if not data:
        return None
    for key in ("grading", "grade", "tirads", "level", "severity"):
        val = data.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return None


async def _load_failed() -> list[dict]:
    """从库里取 parse 环失败的记录（带临床文本、器官、检索到的 chunk）。"""
    from sqlalchemy import select

    from app.config.database import AsyncSessionLocal
    from app.models.database import DiagnosisResult, KnowledgeChunkModel

    async with AsyncSessionLocal() as session:
        rows = (
            await session.execute(
                select(
                    DiagnosisResult.request_id,
                    DiagnosisResult.organ,
                    DiagnosisResult.clinical_text,
                    DiagnosisResult.knowledge_ids,
                    DiagnosisResult.findings,
                ).order_by(DiagnosisResult.created_at)
            )
        ).all()

        failed = []
        for rid, organ, ctext, kids, findings in rows:
            body = _conclusion_of(findings).lower()
            if "grade_unparseable" in body or "lvef_unparseable" in body:
                failed.append(
                    {
                        "request_id": str(rid),
                        "organ": str(organ or ""),
                        "clinical_text": ctext or "",
                        "knowledge_ids": [str(k) for k in (kids or [])],
                    }
                )
        if not failed:
            return []

        wanted = {k for f in failed for k in f["knowledge_ids"]}
        chunks = (
            await session.execute(
                select(KnowledgeChunkModel.chunk_id, KnowledgeChunkModel.content).where(
                    KnowledgeChunkModel.chunk_id.in_(wanted)
                )
            )
        ).all()
    content_of = {str(cid): (text or "") for cid, text in chunks}

    for f in failed:
        f["evidence"] = "\n".join(
            f"[{i}] {content_of.get(cid, '')[:600]} (source: {cid})"
            for i, cid in enumerate(f["knowledge_ids"][:5], 1)
        )
    return failed


def _prompt_of(item: dict) -> str:
    tpl_path = _SCRIPTS.parent / "app" / "agents" / "prompts" / f"{item['organ']}.md"
    template = tpl_path.read_text(encoding="utf-8") if tpl_path.is_file() else ""
    return (
        f"{template}\n\n# 检索到的知识（top 5）\n{item['evidence']}\n\n"
        f"# 用户查询\n{item['clinical_text']}"
    )


async def _call(
    client: httpx.AsyncClient, model: str, prompt: str, *, structured: dict | None
) -> dict:
    payload: dict = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": MAX_TOKENS,
        "temperature": 0,
    }
    if structured:
        payload["response_format"] = structured
    r = await client.post(f"{BASE}/v1/chat/completions", json=payload)
    if r.status_code != 200:
        return {"status": r.status_code, "text": "", "finish_reason": "", "error": r.text[:200]}
    choice = r.json()["choices"][0]
    return {
        "status": r.status_code,
        "text": choice["message"].get("content") or "",
        "finish_reason": choice.get("finish_reason") or "",
    }


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--report", default=str(pathlib.Path.home() / "uai/reports/parse_link.json"))
    args = ap.parse_args()

    try:
        items = await _load_failed()
    except Exception as exc:
        print(f"[FATAL] 取失败样本失败：{type(exc).__name__}: {exc}")
        return 2
    if not items:
        print("没有 parse 环失败的记录（可能已被修复或清理）")
        return 0

    from app.inference.grading_schema import grading_json_schema

    out: list[dict] = []
    async with httpx.AsyncClient(timeout=180.0) as client:
        m = await client.get(f"{BASE}/v1/models")
        model = (m.json().get("data") or [{}])[0].get("id", "") if m.status_code == 200 else ""
        print(f"model={model} 失败样本 {len(items)} 条\n")

        for it in items:
            prompt = _prompt_of(it)
            plain = await _call(client, model, prompt, structured=None)
            schema = grading_json_schema(it["organ"])
            rf = (
                {"type": "json_schema", "json_schema": {"name": f"{it['organ']}_grading", "schema": schema}}
                if schema
                else None
            )
            struct = await _call(client, model, prompt, structured=rf)

            plain_grade = extract_plain(plain["text"], it["organ"])
            struct_data = parse_structured_json(struct["text"])
            struct_grade = grading_of(struct_data)

            row = {
                "request_id": it["request_id"],
                "organ": it["organ"],
                "n_chunks": len(it["knowledge_ids"]),
                "plain": {
                    "finish_reason": plain["finish_reason"],
                    "len": len(plain["text"]),
                    "grade": plain_grade,
                    "head": plain["text"][:180].replace("\n", " / "),
                },
                "structured": {
                    "schema_available": bool(schema),
                    "finish_reason": struct["finish_reason"],
                    "len": len(struct["text"]),
                    "is_json": struct_data is not None,
                    "grade": struct_grade,
                    "head": struct["text"][:180].replace("\n", " / "),
                },
            }
            out.append(row)
            print(
                f"[{it['organ']}] {it['request_id']} chunks={row['n_chunks']}\n"
                f"   plain: finish={row['plain']['finish_reason']} len={row['plain']['len']} "
                f"grade={plain_grade}\n"
                f"   struct: schema={row['structured']['schema_available']} "
                f"finish={row['structured']['finish_reason']} len={row['structured']['len']} "
                f"json={row['structured']['is_json']} grade={struct_grade}\n"
                f"   plain片段: {row['plain']['head'][:120]}\n"
                f"   struct片段: {row['structured']['head'][:120]}\n"
            )

    # 归因：plain 截断 / 结构化不可用 / 结构化能取到但生产没取到（接线问题）
    truncated = sum(1 for r in out if r["plain"]["finish_reason"] == "length")
    struct_ok = sum(1 for r in out if r["structured"]["grade"])
    plain_ok = sum(1 for r in out if r["plain"]["grade"])
    summary = {
        "n": len(out),
        "plain_extractable": plain_ok,
        "structured_extractable": struct_ok,
        "plain_truncated": truncated,
        "verdict": (
            "PRODUCTION_RETRY_INEFFECTIVE" if struct_ok > plain_ok else "MODEL_OR_SCHEMA_PROBLEM"
        ),
    }
    print(f"== 归因 == plain 可抽 {plain_ok}/{len(out)}、结构化可抽 {struct_ok}/{len(out)}、"
          f"plain 被截断 {truncated} → **{summary['verdict']}**")

    report = {"probe": "p56_parse_link_probe", "readonly": True, "model": model,
              "summary": summary, "rows": out}
    path = pathlib.Path(args.report).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"报告：{path}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
