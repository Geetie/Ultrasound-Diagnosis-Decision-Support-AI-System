#!/usr/bin/env python3
"""实测：真机跑出来的诊断结果里，那批「设计文档说恒为常量」的字段到底是什么值。

依据 .codebuddy/skills/remote-gpu-server-devtest/SKILL.md §12 / §12.7。
红线（§6）：口令只在远端本地读取，不进 argv、不回显、不落仓库。
"""
import asyncio
import json
import logging
import sys
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)

sys.path.insert(0, "/mnt/ee16/home/student03/uai/src")

BASE = "http://127.0.0.1:18000"
PW_FILE = "/mnt/ee16/home/student03/uai/.smoke_pw"
VENV_PY = "/mnt/ee16/home/student03/uai/venv/bin/python"  # noqa: F841 (仅文档说明)

# 设计文档 16-...核定报告 §N-01 声称这些是硬编码常量
TARGETS = [
    "status", "consistency_flag", "fusion_status", "self_rag_passed",
    "ai_diagnosis_failed", "hallucination_severity",
    "requires_manual_review", "desensitization_status", "review",
]


def http(method, path, body=None, token=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        logger.warning("http: 未预期异常，按原逻辑降级", exc_info=True)
        return e.code, e.read().decode()[:300]
    except Exception as e:  # noqa: BLE001
        logger.warning("http: 未预期异常，按原逻辑降级", exc_info=True)
        return 0, str(e)[:300]


def query_db(sql):
    """在远端 venv 里跑 SQL，返回 list[dict]。

    DSN 直接从 ~/uai/.env 读：app.config.settings 没有 get_settings，
    现场导入会失败（技能 §9 的教训：凡是从未在真机跑过的路径都要自己兜住）。
    """
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    dsn = ""
    with open("/mnt/ee16/home/student03/uai/.env", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line.startswith("DATABASE_URL="):
                dsn = line.split("=", 1)[1].strip().strip('"').strip("'")
                break
    if not dsn:
        raise RuntimeError(".env 里没有 DATABASE_URL")
    # 注意：这里**不能**剥掉 `+asyncpg` 后缀。
    # 那是 SQLAlchemy 方言标识，剥了会退回默认 psycopg2 驱动 → ModuleNotFoundError。
    # 只有 asyncpg 直连（app/config/dsn.py）才需要剥。
    dsn = dsn.replace("postgresql://", "postgresql+asyncpg://")

    async def main():
        eng = create_async_engine(dsn)
        out = []
        try:
            async with eng.connect() as c:
                for r in (await c.execute(text(sql))).all():
                    out.append(dict(r._mapping))
        finally:
            await eng.dispose()
        return out

    return asyncio.run(main())


def main():
    try:
        with open(PW_FILE) as f:
            pw = f.read().strip()
    except FileNotFoundError:
        print("!! 冒烟口令文件不存在: " + PW_FILE)
        print("   技能 §12：它由 server_link_smoke.sh 运行时生成（600）")
        return 1

    print("=== 1. 登录（token 不回显） ===")
    st, resp = http("POST", "/api/v1/auth/login",
                    {"username": "smoke-doctor", "password": pw})
    if st != 200:
        print("!! 登录失败 http=%s %s" % (st, str(resp)[:200]))
        return 1
    token = ""
    for k in ("access_token", "token", "accessToken"):
        if isinstance(resp, dict) and resp.get(k):
            token = resp[k]
            break
    if not token:
        print("!! 响应里没找到 token，字段: %s" % list(resp)[:10])
        return 1
    print("OK，token 长度 %d" % len(token))

    print()
    print("=== 2. 库里有哪些相关表（先摸表名，别猜） ===")
    try:
        tbls = query_db(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema='public' AND ("
            "  table_name LIKE '%diagnos%' OR table_name LIKE '%request%' "
            "  OR table_name LIKE '%session%' OR table_name LIKE '%patient%') "
            "ORDER BY table_name")
        for r in tbls:
            print("   -", r["table_name"])
        if not tbls:
            print("   (public schema 下无匹配表)")
    except Exception as e:  # noqa: BLE001
        print("   查询失败: %s" % str(e)[:200])
        tbls = []

    done = []
    try:
        cols = query_db(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name='diagnosis_results' "
            "ORDER BY ordinal_position")
        print()
        print("=== 2b. diagnosis_results 的列 ===")
        print("   " + ", ".join(c["column_name"] for c in cols))
    except Exception as e:  # noqa: BLE001
        print("   取列失败: %s" % str(e)[:200])

    for sql in (
        "SELECT request_id, status, created_at FROM diagnosis_results "
        "ORDER BY created_at DESC LIMIT 5",
        "SELECT id AS request_id, status, created_at FROM diagnosis_results "
        "ORDER BY created_at DESC LIMIT 5",
    ):
        try:
            done = query_db(sql)
            if done:
                print()
                print("=== 2c. diagnosis_results 最近 5 条 ===")
                for r in done:
                    print("   %-40s %-10s %s"
                          % (r.get("request_id"), r.get("status"),
                             r.get("created_at")))
                break
        except Exception as e:  # noqa: BLE001
            print("   查询失败: %s" % str(e)[:160])
            continue

    finished = [r for r in done if r.get("status") == "completed"]
    if not finished:
        print()
        print("!! 没有 status=completed 的行（技能 §12.7 曾达成过）。")
        print("   重造：bash ~/server_run_bootstrap_kb.sh && bash ~/server_link_smoke.sh")
        return 0

    rid = finished[0]["request_id"]
    print()
    print("=== 3. 真实结果的关键字段（request_id=%s） ===" % rid)
    st, res = http("GET", "/api/v1/diagnosis/result/" + rid, token=token)
    if st != 200:
        print("!! 取结果失败 http=%s %s" % (st, str(res)[:200]))
        return 1

    body = res.get("data", res) if isinstance(res, dict) else res
    print("   %-26s %s" % ("字段", "实测值"))
    print("   " + "-" * 56)
    for k in TARGETS:
        mark = ""
        if k in ("consistency_flag", "fusion_status", "self_rag_passed",
                 "ai_diagnosis_failed", "hallucination_severity"):
            mark = "   <-- 设计文档称恒为常量"
        print("   %-26s %s%s" % (k, repr(body.get(k, "<字段缺失>")), mark))

    print()
    print("=== 4. 完整响应（前端接线依据，逐字段看） ===")
    print(json.dumps(body, ensure_ascii=False, indent=2)[:4000])
    return 0


if __name__ == "__main__":
    sys.exit(main())
