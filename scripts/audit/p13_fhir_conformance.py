#!/usr/bin/env python3
"""A.50.10 E11（第一层）：FHIR R4 资源合规裁决 —— 用标准实现校验 bridge 产出。

fhir.resources 是 FHIR R4 规范（HL7）的 Pydantic 严格映射（行业维护包），
比自造断言硬：字段名/类型/基数/枚举/必填全按规范校验。
链路：真实判读报告 → fhir_bridge 转换 → DiagnosticReport → 标准模型解析。

运行（真机）：~/uai/venv/bin/python scripts/audit/p13_fhir_conformance.py
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import httpx
from _audit_common import BASE, check, load_ctx, login, pw, summary

logger = logging.getLogger(__name__)


async def main() -> int:
    ctx = load_ctx() or {}
    rid = ctx.get("report_probe_request") or ctx.get("e2rag_probe_request")
    if not rid:
        check("audit_ctx 前置", False, "缺报告探针 request（先跑 p8/p11）")
        return summary("E11 FHIR 合规裁决")

    doctor = login("smoke-doctor", pw("smoke_pw"))
    tok = doctor["access_token"]
    async with httpx.AsyncClient() as c:
        r = await c.get(f"{BASE}/api/v1/report/by-request/{rid}",
                        headers={"Authorization": f"Bearer {tok}"}, timeout=30.0)
        rep = r.json()
    check("取真实报告（p8 探针产物）", r.status_code == 200, f"http={r.status_code} rid={rid}")

    # fhir_bridge 转换（进程内直调，与 8091 服务同函数）
    sys.path.insert(0, str(Path.home() / "uai" / "src"))
    from scripts.fhir_bridge import _build_diagnostic_report  # type: ignore

    raw = rep.get("content") or rep.get("text") or rep
    text = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False)
    resource = _build_diagnostic_report(
        payload=text.encode("utf-8"),
        report_id=rep.get("report_id", ""),
        patient_id=rep.get("patient_id"),
        organ=rep.get("organ"),
    )
    check("bridge 产出 DiagnosticReport（dict）", isinstance(resource, dict)
          and resource.get("resourceType") == "DiagnosticReport",
          f"keys={list(resource)[:6]}")

    # 标准实现裁决：fhir.resources 严格解析
    try:
        from fhir.resources.diagnosticreport import DiagnosticReport
        from fhir.resources.patient import Patient
    except ImportError:
        logger.warning("main: 未预期异常，按原逻辑降级", exc_info=True)
        check("fhir.resources 可用", False, "pip install fhir.resources 缺失")
        return summary("E11 FHIR 合规裁决")

    try:
        dr = DiagnosticReport.model_validate(resource)
        check("fhir.resources 解析通过（R4 规范）", True,
              f"status={dr.status} code.text={getattr(dr.code, 'text', None)}")
        check("必填/基数合规（subject/identifier/status）",
              dr.subject is not None and bool(dr.identifier) and dr.status is not None,
              f"identifier={dr.identifier[0].value if dr.identifier else None}")
        check("LOINC 编码合规",
              any(c.system == "http://loinc.org" for c in (dr.code.coding or [])),
              f"{[c.code for c in (dr.code.coding or [])]}")
    except Exception as exc:
        logger.warning("main: 未预期异常，按原逻辑降级", exc_info=True)
        check("fhir.resources 解析通过（R4 规范）", False, str(exc)[:160])

    return summary("E11 FHIR 合规裁决")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
