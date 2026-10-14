#!/usr/bin/env python3
"""p19 A.51 批次端到端摸排（真实链路，非 mock；服务器上跑）。

覆盖 A.51 四项交付在**运行态**的表现 + 部署产物一致性：

  S1 运行期配置实测（同 `~/uai/.env`，探针进程内加载 settings）
  S2 qdrant 物理状态 + dense 腿直连（`query_points` 路径必须真返回结果）
  S3 设备列表端点 `GET /api/v1/admin/device`（401/403/分页/过滤/零 api_key）
  S4 患者列表端点 `GET /api/v1/patients`（401/检索命中/字段/边界不虚构）
  S5 端到端判读（真实 CAMUS 3 帧）：timings 键 + validate 时延/降级 + 择优帧
     + 报告帧号 + 知识命中（检索腿在真实判读里生效）
  S6 部署产物一致性（服务器 `app/static/ui` 里含 A.51 新特性字符串）

纪律：
* 只读 + 建探针数据（探针患者/会话），不改任何服务配置；
* 影像一律真实样本（CAMUS 转换，用户制度：禁止合成样本）；
* 口令只从 `~/uai/.smoke_pw` / `~/uai/.smoke_admin_pw` 读，不进 argv。

跑法（服务器）：
    cd ~/uai/src/scripts/audit && ~/uai/venv/bin/python p19_a51_e2e_audit.py
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import pathlib
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
# t1_eval_retrieval / _audit_common 都在 scripts/ 下（p15 同款做法）
sys.path.insert(0, str(HERE.parent))

from _audit_common import BASE, UAI, check, http, login, pw, save_ctx, summary  # noqa: E402
from p11_rag_evidence import make_camus_dicoms, upload_dicom

logger = logging.getLogger(__name__)

BGE_URL = "http://127.0.0.1:8004"
QDRANT_URL = "http://127.0.0.1:6333"
TARGET_VALIDATE_MS = 1500.0
KB_EXPECTED_CHUNKS = 1257  # kb_v1.3.0 生产库（A.50 切换）
OUT_DIR = UAI / "reports"


# ----------------------------------------------------------------------
# S1 运行期配置实测
# ----------------------------------------------------------------------


def _load_dotenv() -> dict[str, str]:
    """把 ~/uai/.env 灌进 os.environ（setdefault：显式环境变量优先）。"""
    kv: dict[str, str] = {}
    env_file = UAI / ".env"
    if not env_file.exists():
        return kv
    for raw in env_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        kv[k] = v
        os.environ.setdefault(k, v)
    return kv


def s1_runtime_settings() -> dict:
    print("\n== S1 运行期配置实测（探针进程加载 app.config.settings）==", flush=True)
    env_kv = _load_dotenv()
    sys.path.insert(0, str(UAI / "src"))
    try:
        from app.config.settings import settings
    except Exception as exc:
        logger.warning("s1_runtime_settings: 未预期异常，按原逻辑降级", exc_info=True)
        check("S1 加载 app.config.settings", False, f"{type(exc).__name__}: {exc}")
        return {}

    snap: dict = {}
    try:
        snap = {
            "pipeline_validate_budget_s": float(getattr(settings, "pipeline_validate_budget_s", -1)),
            "self_rag_max_retries": int(getattr(settings, "self_rag_max_retries", -1)),
            "self_rag_retry_delay_s": float(getattr(settings, "self_rag_retry_delay_s", -1)),
            "vector_index_backend": str(getattr(settings, "vector_index_backend", "?")),
            "rag_kb_version": str(getattr(settings, "rag_kb_version", "?")),
            "rag_collection": str(getattr(settings, "rag_collection", "?")),
            "nodule_contour_in_pipeline_enabled": bool(
                getattr(settings, "nodule_contour_in_pipeline_enabled", False)
            ),
            "qdrant_host": str(getattr(settings, "qdrant_host", "?")),
            "qdrant_port": str(getattr(settings, "qdrant_port", "?")),
        }
    except Exception as exc:
        logger.warning("s1_runtime_settings: 未预期异常，按原逻辑降级", exc_info=True)
        check("S1 读取 settings 字段", False, f"{type(exc).__name__}: {exc}")
        return snap

    for k, v in snap.items():
        print(f"  {k} = {v}", flush=True)

    check(
        "S1 validate 预算已接线且 == 3.0s（> 实测 P99）",
        abs(snap["pipeline_validate_budget_s"] - 3.0) < 1e-6,
        f"值={snap['pipeline_validate_budget_s']}",
    )
    check(
        "S1 Self-RAG 评估重试已关（max_retries=0，不再与 CRAG 重叠）",
        snap["self_rag_max_retries"] == 0 and snap["self_rag_retry_delay_s"] == 0.0,
        f"retries={snap['self_rag_max_retries']} delay={snap['self_rag_retry_delay_s']}",
    )
    check(
        "S1 向量后端 == qdrant（生产向量索引）",
        snap["vector_index_backend"].lower() == "qdrant",
        f"backend={snap['vector_index_backend']}",
    )
    check(
        "S1 kb_version 口径一致（.env 与 settings）",
        not env_kv.get("RAG_KB_VERSION") or env_kv["RAG_KB_VERSION"] == snap["rag_kb_version"],
        f".env={env_kv.get('RAG_KB_VERSION')!r} settings={snap['rag_kb_version']!r}",
    )
    print(
        f"  [INFO] 分割旁路运行期开关 = {snap['nodule_contour_in_pipeline_enabled']}"
        "（注释/文档另有「默认关闭」说法，以运行期实证为准）",
        flush=True,
    )
    return snap


# ----------------------------------------------------------------------
# S2 qdrant 物理状态 + dense 腿直连
# ----------------------------------------------------------------------


async def s2_qdrant_dense(snap: dict) -> dict:
    print("\n== S2 qdrant 物理状态 + dense 腿直连 ==", flush=True)
    import httpx

    out: dict = {}
    coll = f"ultrasound_{snap.get('rag_collection', 'knowledge_chunks')}"
    async with httpx.AsyncClient(timeout=30.0) as c:
        r = await c.get(f"{QDRANT_URL}/collections")
        names = sorted(x["name"] for x in r.json()["result"]["collections"])
        print(f"  collections = {names}", flush=True)
        check(f"S2 collection 存在：{coll}", coll in names, f"names={names}")

        r = await c.get(f"{QDRANT_URL}/collections/{coll}")
        info = r.json().get("result", {}) if r.status_code == 200 else {}
        pts = info.get("points_count")
        out["points_count"] = pts
        check(
            f"S2 生产 collection 点数 == {KB_EXPECTED_CHUNKS}（kb_v1.3.0 全量）",
            pts == KB_EXPECTED_CHUNKS,
            f"points_count={pts}",
        )
        r = await c.post(
            f"{QDRANT_URL}/collections/{coll}/points/scroll",
            json={"limit": 8, "with_payload": True, "with_vector": False},
        )
        kbs = sorted({(p.get("payload") or {}).get("kb_version", "<missing>")
                      for p in r.json()["result"]["points"]})
        check("S2 payload kb_version 单调（抽样 8 点）", len(kbs) == 1, f"kb_versions={kbs}")

    try:
        from t1_eval_retrieval import embed_texts

        from app.config.settings import settings
        from app.embedding.index_factory import create_index_client_from_settings

        raw = json.loads((UAI / "data" / "golden" / "kb_qa_golden_v1.json").read_text(encoding="utf-8"))
        queries = raw if isinstance(raw, list) else raw.get("queries", [])
        q0 = queries[0]["query_text"]
        vec = embed_texts([q0], BGE_URL)[0]
        client = await create_index_client_from_settings()
        try:
            res = await client.search(settings.rag_collection, vec, top_k=5)
        finally:
            closer = getattr(client, "close", None)
            if closer:
                await closer()
        out["dense_hits"] = len(res)
        out["dense_top"] = [
            {"chunk_id": getattr(getattr(h, "chunk", None), "id", None) or getattr(h, "id", "?"),
             "score": round(float(getattr(h, "score", 0.0)), 4)}
            for h in res[:5]
        ]
        check(
            "S2 ⭐ dense 腿直连非空（query_points 修复在生产库生效）",
            len(res) > 0,
            f"n={len(res)} top={out['dense_top'][0] if out['dense_top'] else None}",
        )
    except Exception as exc:
        logger.warning("s2_qdrant_dense: 未预期异常，按原逻辑降级", exc_info=True)
        check("S2 dense 腿直连", False, f"{type(exc).__name__}: {exc}")
    return out


# ----------------------------------------------------------------------
# S3/S4 列表端点
# ----------------------------------------------------------------------


def _walk_keys(obj, keys: set[str]) -> None:
    if isinstance(obj, dict):
        keys.update(str(k) for k in obj)
        for v in obj.values():
            _walk_keys(v, keys)
    elif isinstance(obj, list):
        for v in obj:
            _walk_keys(v, keys)


def s3_device_list(dtok: str, atok: str) -> dict:
    print("\n== S3 设备列表端点 ==", flush=True)
    code, _ = http("GET", "/api/v1/admin/device")
    check("S3 无 token → 401", code == 401, f"http={code}")

    code, _ = http("GET", "/api/v1/admin/device", token=dtok)
    check("S3 医生（非 SYSTEM_ADMIN）→ 403", code == 403, f"http={code}")

    code, body = http("GET", "/api/v1/admin/device", token=atok)
    ok_shape = (code == 200 and isinstance(body, dict)
                and {"items", "total", "offset", "limit"} <= set(body))
    check("S3 管理员 → 200 且结构 {items,total,offset,limit}", ok_shape,
          f"http={code} keys={sorted(body)[:6] if isinstance(body, dict) else body}")
    if not ok_shape:
        return {}
    total0 = body["total"]

    keys: set[str] = set()
    _walk_keys(body, keys)
    check("S3 ⭐ 响应零 api_key（列表是只读视图，不回密钥）", "api_key" not in keys,
          f"api_key 出现={('api_key' in keys)} 字段数={len(keys)}")

    code, b2 = http("GET", "/api/v1/admin/device?limit=500", token=atok)
    check("S3 limit 上夹到 200", code == 200 and b2.get("limit") == 200, f"limit={b2.get('limit')}")

    code, b3 = http("GET", "/api/v1/admin/device?offset=100000", token=atok)
    check("S3 offset 越界 → items 空但 total 不减",
          code == 200 and b3.get("items") == [] and b3.get("total") == total0,
          f"items={len(b3.get('items', []))} total={b3.get('total')} (基准 {total0})")

    if body["items"]:
        st = body["items"][0]["status"]
        code, b4 = http("GET", f"/api/v1/admin/device?status={st}", token=atok)
        all_match = all(i["status"] == st for i in b4.get("items", []))
        check("S3 status 过滤生效", code == 200 and all_match,
              f"status={st} n={len(b4.get('items', []))}")
    return {"total": total0, "first_item": body["items"][0] if body["items"] else None}


def s4_patient_list(dtok: str) -> dict:
    print("\n== S4 患者列表端点 ==", flush=True)
    code, _ = http("GET", "/api/v1/patients")
    check("S4 无 token → 401", code == 401, f"http={code}")

    marker = f"probe-p19-{int(time.time())}"
    code, _ = http("POST", "/fhir/Patient", token=dtok,
                   body={"patient_id": marker, "name": f"P19 摸排 {marker}", "gender": "unknown"})
    check("S4 建探针患者（FHIR）", code in (200, 201), f"http={code}")

    code, body = http("GET", f"/api/v1/patients?q={marker}", token=dtok)
    ok = code == 200 and isinstance(body, dict) and {"items", "total", "offset", "limit"} <= set(body)
    check("S4 模糊检索 200 + 结构完整", ok, f"http={code}")
    if not ok:
        return {}
    ids = [i.get("patient_id") for i in body["items"]]
    check("S4 ⭐ 新建患者可被 q 命中", marker in ids, f"items={ids[:5]} total={body['total']}")

    fields = set(body["items"][0].keys()) if body["items"] else set()
    check("S4 条目字段齐全（patient_id/name/gender/consent_status/last_session_at）",
          {"patient_id", "name", "gender", "consent_status", "last_session_at"} <= fields,
          f"fields={sorted(fields)}")

    code, b0 = http("GET", f"/api/v1/patients?q=zzz-no-such-{marker}", token=dtok)
    check("S4 无命中时 total=0 且 items 空（不虚构）",
          code == 200 and b0.get("total") == 0 and b0.get("items") == [],
          f"total={b0.get('total')} items={len(b0.get('items', []))}")

    code, _ = http("GET", "/api/v1/patients?limit=101", token=dtok)
    check("S4 limit 上界约束（>100 → 422）", code == 422, f"http={code}")

    code, b1 = http("GET", "/api/v1/patients?limit=5&offset=0", token=dtok)
    check("S4 分页回显 limit/offset",
          code == 200 and b1.get("limit") == 5 and b1.get("offset") == 0,
          f"limit={b1.get('limit')} offset={b1.get('offset')}")
    return {"total": body["total"], "probe_patient": marker}


# ----------------------------------------------------------------------
# S5 端到端判读（真实 CAMUS 3 帧）
# ----------------------------------------------------------------------


async def s5_e2e_diagnosis(dtok: str, atok: str, snap: dict | None = None) -> dict:
    print("\n== S5 端到端判读（真实 CAMUS 3 帧，完整链路）==", flush=True)
    import httpx

    pid = f"probe-p19-e2e-{int(time.time())}"
    out: dict = {}
    async with httpx.AsyncClient(timeout=60.0) as c:
        await c.post(f"{BASE}/fhir/Patient",
                     json={"patient_id": pid, "name": "P19 E2E PROBE", "gender": "unknown"},
                     headers={"Authorization": f"Bearer {dtok}"})
        await c.post(f"{BASE}/api/v1/consent",
                     json={"patient_id": pid, "action": "grant", "operator_id": "smoke-admin"},
                     headers={"Authorization": f"Bearer {atok}"})
        r = await c.post(f"{BASE}/api/v1/sessions",
                         json={"patient_id": pid, "device_id": "probe-p19",
                               "target_organs": ["cardiac"]},
                         headers={"Authorization": f"Bearer {dtok}"})
        sid = r.json().get("session_id", "")
        check("S5 建会话", r.status_code == 200 and bool(sid), f"http={r.status_code} sid={sid}")
        if not sid:
            return out

    img_ids: list[str] = []
    try:
        for p in make_camus_dicoms(3):  # 真实 CAMUS 帧（禁合成样本）
            code, body = upload_dicom(dtok, sid, p)
            iid = body.get("image_id") if code == 200 else None
            if iid:
                img_ids.append(iid)
    except Exception as exc:
        logger.warning("s5_e2e_diagnosis: 未预期异常，按原逻辑降级", exc_info=True)
        check("S5 CAMUS 帧转换/上传", False, f"{type(exc).__name__}: {exc}")
    check("S5 上传 3 张真实帧", len(img_ids) == 3, f"n={len(img_ids)}")
    if len(img_ids) < 2:
        return out
    out["session_id"] = sid
    out["image_ids"] = img_ids

    t0 = time.monotonic()
    code, body = http("POST", "/api/v1/diagnosis/request", token=dtok,
                      body={"session_id": sid, "target_organ": "cardiac", "image_ids": img_ids,
                            "clinical_text": "A.51 端到端摸排：心尖四腔，活动后气促，室间隔运动减弱。"},
                      timeout=60.0)
    rid = body.get("request_id") if isinstance(body, dict) else None
    check("S5 发起判读", code == 200 and bool(rid), f"http={code} rid={rid}")
    if not rid:
        return out
    out["request_id"] = rid

    result: dict | None = None
    while time.monotonic() - t0 < 300:
        await asyncio.sleep(2.0)
        code, body = http("GET", f"/api/v1/diagnosis/result/{rid}", token=dtok, timeout=30.0)
        if code == 200 and isinstance(body, dict) and body.get("status") in (
            "completed", "failed", "needs_review", "cancelled"
        ):
            result = body
            break
    wall = round(time.monotonic() - t0, 1)
    out["wall_s"] = wall
    check("S5 判读到达终态", result is not None,
          f"status={(result or {}).get('status')} wall={wall}s")
    if result is None:
        return out
    out["status"] = result.get("status")
    check("S5 ⭐ 判读 completed（端到端无失败）", result.get("status") == "completed",
          f"status={result.get('status')}")

    diag = result.get("diagnosis") if isinstance(result.get("diagnosis"), dict) else {}
    tm = diag.get("timings_ms") or result.get("timings_ms") or {}
    out["timings_ms"] = {k: v for k, v in tm.items()
                         if k.startswith(("fusion.", "segmentation.", "pipeline.", "dispatcher."))}
    need_keys = [
        "fusion.fuse_ms", "fusion.validate_ms", "fusion.self_rag_skipped",
        "fusion.validate_degraded", "segmentation.bypass_ms", "pipeline.total_ms",
    ]
    missing = [k for k in need_keys if k not in tm]
    check("S5 ⭐ timing 观测键齐全（A.51 可观测性接线）", not missing, f"missing={missing}")

    v = tm.get("fusion.validate_ms")
    deg = tm.get("fusion.validate_degraded")
    check(f"S5 ⭐ validate 时延 < {TARGET_VALIDATE_MS:.0f}ms（降载后达标）",
          isinstance(v, (int, float)) and float(v) < TARGET_VALIDATE_MS,
          f"validate_ms={v} degraded={deg}")
    check("S5 validate 未降级（常态不打降级标记）", deg in (False, 0), f"degraded={deg!r}")
    check("S5 Self-RAG 在 FULL 档执行（未被跳过）",
          tm.get("fusion.self_rag_skipped") in (False, 0),
          f"skipped={tm.get('fusion.self_rag_skipped')!r}")

    seg = result.get("nodule_segmentation") or diag.get("nodule_segmentation") or {}
    keep = ("available", "frame_index", "total_frames", "selection_policy",
            "candidates_evaluated", "frames_evaluated", "mean_prob",
            "infer_latency_ms", "bypass_latency_ms", "reason")
    out["segmentation"] = {k: v for k, v in seg.items() if k in keep}
    check("S5 分割旁路可用（真实影像 + 权重就绪）", seg.get("available") is True,
          f"available={seg.get('available')} reason={seg.get('reason')}")
    if seg.get("available") is True:
        frames = seg.get("frames_evaluated") or []
        check("S5 ⭐ 择优语义生效（policy=best_mean_prob）",
              seg.get("selection_policy") == "best_mean_prob",
              f"policy={seg.get('selection_policy')} candidates={seg.get('candidates_evaluated')}")
        check("S5 评估帧数 ≤3 且与逐帧表一致",
              isinstance(seg.get("candidates_evaluated"), int)
              and seg["candidates_evaluated"] == len(frames) and len(frames) <= 3,
              f"candidates={seg.get('candidates_evaluated')} len(frames)={len(frames)}")
        if frames:
            best = max(frames, key=lambda f: f.get("mean_prob") or -1)
            check("S5 ⭐ 择优帧 == 逐帧 max(mean_prob)（帧号与影像都对上）",
                  seg.get("frame_index") == best.get("frame_index")
                  and seg.get("image_id") == best.get("image_id"),
                  f"chosen=({seg.get('image_id')},{seg.get('frame_index')}) "
                  f"argmax=({best.get('image_id')},{best.get('frame_index')})")

    ev = result.get("evidence") or diag.get("evidence") or {}
    kids = (ev or {}).get("knowledge_ids") or []
    out["knowledge_ids_n"] = len(kids)
    check("S5 ⭐ 检索腿在真实判读中生效（knowledge_ids 非空）", len(kids) > 0,
          f"n={len(kids)} kb_version={result.get('knowledge_base_version')}")

    code, rep = http("POST", "/api/v1/report", token=dtok,
                     body={"request_id": rid, "operator_id": "smoke-doctor"}, timeout=60.0)
    content = ""
    if code == 200 and isinstance(rep, dict):
        content = str(rep.get("content") or "")
        rid_rep = rep.get("report_id")
        if not content and rid_rep:
            c2, rep2 = http("GET", f"/api/v1/report/{rid_rep}", token=dtok)
            if c2 == 200 and isinstance(rep2, dict):
                content = str(rep2.get("content") or "")
    out["report_len"] = len(content)

    # ---- A.52 口径修复验证：会话/结果/报告声明的知识库版本必须 == 运行期真值
    expected_kb = (snap or {}).get("rag_kb_version")
    if expected_kb:
        res_kb = result.get("knowledge_base_version")
        out["result_kb_version"] = res_kb
        check(f"S5 ⭐ 结果知识库版本 == 运行期 {expected_kb}（A.52 修复）",
              res_kb == expected_kb, f"result={res_kb} expected={expected_kb}")
        _code_s, sess = http("GET", f"/api/v1/sessions/{sid}", token=dtok)
        sess_kb = sess.get("knowledge_base_version") if isinstance(sess, dict) else None
        out["session_kb_version"] = sess_kb
        check(f"S5 ⭐ 会话记录知识库版本 == 运行期 {expected_kb}",
              sess_kb == expected_kb, f"session={sess_kb} expected={expected_kb}")
        if content and expected_kb != "kb_v1.0.0":
            check("S5 ⭐ 报告标注不再写旧的 kb_v1.0.0",
                  "kb_v1.0.0" not in content,
                  f"报告含旧版本={('kb_v1.0.0' in content)} len={len(content)}")
            check(f"S5 报告标注含 {expected_kb}",
                  expected_kb in content, f"hit={expected_kb in content}")

    tf = seg.get("total_frames") or 1
    if tf > 1 and seg.get("available"):
        expect = f"第 {int(seg.get('frame_index', 0)) + 1}/{tf} 帧"
        check("S5 ⭐ 报告正文含帧归属（第 X/Y 帧）", expect in content,
              f"expect={expect!r} hit={expect in content} len={len(content)}")
        out["report_has_frame_text"] = expect in content
    return out


# ----------------------------------------------------------------------
# S6 部署产物一致性
# ----------------------------------------------------------------------


def s6_built_artifact() -> dict:
    print("\n== S6 部署前端产物一致性（服务器 app/static/ui）==", flush=True)
    ui = UAI / "src" / "app" / "static" / "ui"
    assets = sorted((ui / "assets").glob("*.js")) if (ui / "assets").exists() else []
    blob = "".join(p.read_text(encoding="utf-8", errors="replace") for p in assets)
    needles = {
        "患者候选调用 /patients": '"/patients"',
        "跨帧提示 data-region": "segmentation-off-frame",
        "跨帧文案「分割结果在第」": "分割结果在第",
        "分割字段 selection_policy": "selection_policy",
        "帧字段 total_frames": "total_frames",
    }
    out = {"assets": len(assets), "chars": len(blob), "hits": {}}
    for label, needle in needles.items():
        n = blob.count(needle)
        out["hits"][label] = n
        check(f"S6 产物含「{label}」", n > 0, f"出现 {n} 次")

    code, _ = http("GET", "/ui/")
    check("S6 /ui/ 可访问（200）", code == 200, f"http={code}")
    # index.html 从磁盘读（http helper 对非 JSON 会截断到 200 字符，不能用来判内容）
    idx = ui / "index.html"
    html = idx.read_text(encoding="utf-8", errors="replace") if idx.exists() else ""
    check("S6 index.html 指向已构建 assets", "/assets/" in html or "assets/" in html,
          f"len={len(html)}")
    check("S6 index.html 资源前缀走 /ui/（VITE_BASE 正确）",
          "/ui/assets/" in html or 'src="./assets/' in html,
          f"head={html[:120]!r}")
    return out


# ----------------------------------------------------------------------


async def main() -> int:
    t_start = time.time()
    print("== p19 A.51 批次端到端摸排 ==", flush=True)
    doctor = login("smoke-doctor", pw("smoke_pw"))
    admin = login("smoke-admin", pw("smoke_admin_pw"))
    dtok, atok = doctor["access_token"], admin["access_token"]

    report: dict = {"generated_at": time.strftime("%Y-%m-%d %H:%M:%S")}
    try:
        report["S1_runtime"] = s1_runtime_settings()
    except Exception as exc:
        logger.warning("main: 未预期异常，按原逻辑降级", exc_info=True)
        check("S1 段执行", False, f"{type(exc).__name__}: {exc}")
    try:
        report["S2_qdrant"] = await s2_qdrant_dense(report.get("S1_runtime") or {})
    except Exception as exc:
        logger.warning("main: 未预期异常，按原逻辑降级", exc_info=True)
        check("S2 段执行", False, f"{type(exc).__name__}: {exc}")
    try:
        report["S3_device"] = s3_device_list(dtok, atok)
    except Exception as exc:
        logger.warning("main: 未预期异常，按原逻辑降级", exc_info=True)
        check("S3 段执行", False, f"{type(exc).__name__}: {exc}")
    try:
        report["S4_patients"] = s4_patient_list(dtok)
    except Exception as exc:
        logger.warning("main: 未预期异常，按原逻辑降级", exc_info=True)
        check("S4 段执行", False, f"{type(exc).__name__}: {exc}")
    try:
        report["S5_e2e"] = await s5_e2e_diagnosis(dtok, atok, report.get("S1_runtime") or {})
    except Exception as exc:
        logger.warning("main: 未预期异常，按原逻辑降级", exc_info=True)
        check("S5 段执行", False, f"{type(exc).__name__}: {exc}")
    try:
        report["S6_built"] = s6_built_artifact()
    except Exception as exc:
        logger.warning("main: 未预期异常，按原逻辑降级", exc_info=True)
        check("S6 段执行", False, f"{type(exc).__name__}: {exc}")

    report["elapsed_s"] = round(time.time() - t_start, 1)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / f"a51_e2e_audit_{time.strftime('%Y%m%d_%H%M%S')}.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  -> {out_path}", flush=True)
    save_ctx(p19_audit=str(out_path))
    return summary("A.51 批次端到端摸排")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
