"""P9 辅助管线探针（A.49）：健康/分割/属性提示/语音/反馈/会话。

依赖：P8 已落 audit_ctx.json（report_probe_session/report_probe_request）。
speech/recognize 输入 0.1s 静音 PCM 16kHz（探针只验链路，不验识别质量）。
feedback 幂等：同 idempotency_key 二次提交应返回同一 feedback_id。
"""
from __future__ import annotations

import base64
import struct
import sys
import time

from _audit_common import check, http, load_ctx, login, pw, summary

DOCTOR = "smoke-doctor"


def silence_pcm_b64(n_samples: int = 1600) -> str:
    raw = struct.pack(f"<{n_samples}h", *([0] * n_samples))
    return base64.b64encode(raw).decode()


def main() -> int:
    doctor = login(DOCTOR, pw("smoke_pw"))
    dtok = doctor["access_token"]
    ctx = load_ctx()
    sid = ctx.get("report_probe_session")
    rid = ctx.get("report_probe_request")
    if not sid or not rid:
        check("audit_ctx 前置", False, "缺 report_probe_session/request（先跑 p8）")
        return summary("P9 辅助管线")

    # 1) 健康三件套
    for path, name in [("/api/v1/health", "health（redis/db/hsm）"), ("/api/v1/health/hsm", "health/hsm"), ("/api/v1/health/cache", "health/cache")]:
        code, body = http("GET", path)
        check(f"GET {name} 200", code == 200, f"http={code} {str(body)[:80]}")

    # 2) 分割
    code, _ = http("GET", "/api/v1/segmentation/health?organ=thyroid")
    check("GET segmentation/health 200", code == 200, f"http={code}")
    code, body = http("GET", "/api/v1/segmentation/organs")
    ok = code == 200 and isinstance(body, (dict, list))
    check("GET segmentation/organs 200", ok, f"http={code}")

    # 3) 属性提示（8003 引擎懒加载）。**语言护栏是设计**（attribute_hints.py
    #    L349-369）：ULC 文本塔是英文 PubMedBERT，中文文本直接 503 拒绝
    #    （临床安全：中文进塔近全 [UNK] 会制造假冲突警报）——探针发英文。
    code, body = http("POST", "/api/v1/attributes/hints",
                      body={"text": "Thyroid right lobe hypoechoic nodule with well-defined margins, no calcification."})
    check("POST attributes/hints 200（英文文本）", code == 200, f"http={code} {str(body)[:60]}")
    code, body = http("POST", "/api/v1/attributes/hints", body={"text": "甲状腺右叶低回声结节"})
    check("POST attributes/hints 中文 -> 503（语言护栏设计）", code == 503, f"http={code}")

    # 4) 语音：health → recognize（静音）。引擎未就绪属**已知部署缺口**（P2 登记）：
    #    health 如实报 engine_ready=false；recognize 此时按 SpeechError 语义 400。
    code, body = http("GET", "/api/v1/speech/health")
    ready = isinstance(body, dict) and body.get("engine_ready") is True
    check("GET speech/health 200", code == 200, f"http={code} engine_ready={body.get('engine_ready') if isinstance(body, dict) else body}")
    code, body = http("POST", "/api/v1/speech/recognize",
                      body={"audio_data": silence_pcm_b64(), "sample_rate": 16000, "language": "zh", "request_id": "audit-p9-speech"})
    if ready:
        ok = code == 200 and isinstance(body, dict) and "text" in body
        check("POST speech/recognize 200（静音 PCM）", ok, f"http={code}")
    else:
        check("speech/recognize 引擎未就绪 -> 400（P2 部署缺口，如实记录）", code == 400,
              f"http={code} {str(body)[:60]}")

    # 5) 语音确认（diagnosis_trigger 必带 organ）
    code, body = http(
        "POST", "/api/v1/voice/confirm",
        body={"session_id": sid, "transcript": "开始甲状腺判读", "command_type": "diagnosis_trigger",
              "confirmed": True, "timestamp": int(time.time() * 1000), "organ": "thyroid"},
        token=dtok,
    )
    ok = code == 200 and isinstance(body, dict) and body.get("executed") is True
    check("POST voice/confirm 200 executed", ok, f"http={code} action={body.get('action_taken') if isinstance(body, dict) else body}")

    # 6) 反馈 + 幂等
    fb = {
        "request_id": rid, "session_id": sid,
        "feedback_type": "correction", "feedback_level": "L2",
        "content": {"field": "diagnosis", "original_value": "原结论", "corrected_value": "更正结论（审计探针）", "reason": "A.49 摸排"},
        "idempotency_key": f"audit-p9-{int(time.time())}",
    }
    code, body = http("POST", "/api/v1/feedback", body=fb, token=dtok)
    fid = body.get("feedback_id") if isinstance(body, dict) else None
    check("POST feedback 200", code == 200 and bool(fid), f"http={code} feedback_id={fid}")
    code, body2 = http("POST", "/api/v1/feedback", body=fb, token=dtok)
    fid2 = body2.get("feedback_id") if isinstance(body2, dict) else None
    check("feedback 幂等（同 key 同 id）", fid2 == fid, f"id2={fid2}")

    # 7) 会话列表 + PATCH 终态（探针会话，不动真实数据）
    code, body = http("GET", "/api/v1/sessions?limit=5", token=dtok)
    ok = code == 200 and isinstance(body, dict) and "total_pending" in body
    check("GET sessions 200", ok, f"http={code}")
    code, _ = http("PATCH", f"/api/v1/sessions/{sid}", body={"status": "completed"}, token=dtok)
    check("PATCH session -> completed", code == 200, f"http={code}")

    return summary("P9 辅助管线")


if __name__ == "__main__":
    sys.exit(main())
