"""A.88 真机探针：既往诊断 SQL 是否正确、是否会取到"没有结论"的行。

只读。用途：
1. 验证 `get_previous_result_same_organ` 的 join/过滤在真实库上跑得通；
2. 量化一个风险：`TERMINAL_RESULT_STATUSES` 含 failed/timeout/cancelled，
   这些行**没有可比对的结论**，若它们占据 lookback 名额，会把"有既往"误判成
   "取不到分级"。用真数据回答，不靠猜。
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.expanduser("~/uai/src"))

from sqlalchemy import text

from app.config.database import AsyncSessionLocal
from app.services.sg_output_data import extract_findings_text


async def main():
    async with AsyncSessionLocal() as db:
        # 1) 按患者×器官统计：有多少"同患者同器官"的可比历史
        rows = (await db.execute(text("""
            SELECT s.patient_id, r.organ, r.status, count(*) AS n
            FROM diagnosis_results r
            JOIN sessions s ON s.session_id = r.session_id
            GROUP BY s.patient_id, r.organ, r.status
            ORDER BY n DESC
            LIMIT 40
        """))).fetchall()
        print("== 患者×器官×状态（top40）==")
        for pid, organ, status, n in rows:
            print(f"  {pid} {organ} {status} n={n}")

        # 2) 关键问题：每个 (患者,器官) 里，"已终态但正文抽不出结论"的行
        #    会不会挡在真正有结论的行前面（按 created_at 倒序前 5 条内）
        pairs = (await db.execute(text("""
            SELECT s.patient_id, r.organ, count(*) AS n
            FROM diagnosis_results r
            JOIN sessions s ON s.session_id = r.session_id
            GROUP BY s.patient_id, r.organ
            HAVING count(*) >= 2
            ORDER BY n DESC
            LIMIT 10
        """))).fetchall()
        print("\n== 同患者同器官 >=2 次的组合 ==", len(pairs))

        shapes = {"content": 0, "final_response": 0, "empty": 0}
        for pid, organ, n in pairs:
            detail = (await db.execute(text("""
                SELECT r.request_id, r.status, r.findings
                FROM diagnosis_results r
                JOIN sessions s ON s.session_id = r.session_id
                WHERE s.patient_id = :pid AND r.organ = :organ
                ORDER BY r.created_at DESC
            """), {"pid": pid, "organ": organ})).fetchall()
            print(f"\n  {pid} / {organ}（{n} 条，按时间倒序）")
            for rid, status, findings in detail:
                fr = (findings or {}).get("fusion_result") if isinstance(findings, dict) else None
                if isinstance(fr, dict) and isinstance(fr.get("content"), str) and fr["content"]:
                    shapes["content"] += 1
                    got = extract_findings_text(fr)
                    print(f"    {rid} {status} flat_content=True extract_len={len(got)}")
                elif isinstance(fr, dict) and isinstance(fr.get("final_response"), dict):
                    shapes["final_response"] += 1
                    print(f"    {rid} {status} nested=True")
                else:
                    shapes["empty"] += 1
                    print(f"    {rid} {status} 无可抽正文")

        print("\n== 形状统计 ==", shapes)


asyncio.run(main())
