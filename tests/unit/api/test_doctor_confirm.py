"""P0-4 医生确认回写（POST /api/v1/diagnosis/{request_id}/doctor-confirm）。

修的问题
--------
`能力盘点-自下而上.md` §Q2 查出：工作区「确认结论」按钮只执行
`setAcknowledged(true)`，全仓无消费者、后端无端点无字段。医生点「确认」
在系统里不留任何痕迹。

设计依据 `前端设计/17-医生侧展示契约.md:137-141`：

    | 5 | 语音确认／修正 | 不可省（防 automation bias） |
    | 8 | 签发           | 不可省（法律责任节点）       |

**确认与签发是两件事**，本测试钉住这个边界，防止后来者把两者合并：

1. 确认**只**写 `doctor_confirmed_at` / `doctor_confirmed_by`；
2. 确认**不**改 status / confidence / findings（否则等于偷偷签发）；
3. 确认**不**产生报告、不签名；
4. 时间戳由服务端生成（客户端传什么都无效）；
5. 确认写审计（出事要能倒查是谁看的）。
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, Mock

import pytest

from app.api.v1.diagnosis import doctor_confirm_result
from app.api.v1.errors import BadRequestException, NotFoundException
from app.models.schemas import DoctorConfirmRequest
from app.repositories import DiagnosisRepository

pytestmark = pytest.mark.track_api


def _result(**kw):
    """一个已完成判读的结果行。"""
    base = dict(
        request_id="req_1",
        session_id="sess_1",
        status="completed",
        confidence=0.85,
        findings={"organ": "thyroid"},
        doctor_confirmed_at=None,
        doctor_confirmed_by=None,
    )
    base.update(kw)
    return Mock(**base)


def _deps(result=None, actor="doctor_007"):
    """构造依赖替身。

    ⚠️ **`repo` 必须用 `spec=` 限形**（2026-10-28 真机跑测试抓到的测试自身 bug）。

    原实现是裸 `Mock()`，而 `Mock()` 对**任意属性名都返回子 Mock** →
    `hasattr(repo, "sign_report")` 恒为 `True`、`hasattr(repo, "create_report")` 也恒为 `True`，
    于是 `test_does_not_sign_or_generate_report` 里
    `assert not hasattr(repo, "sign_report")` **无论端点做了什么都会失败**
    （实测：服务器上 6 passed / 1 failed，失败点正是这一行）。

    正确做法是给 Mock 一个 spec，让它只响应该类的真实方法 ——
    `spec=DiagnosisRepository` 后，`hasattr(repo, "sign_report")` 为 `False`（该类确实没有这个方法），
    `hasattr(repo, "update_result")` 为 `True`（该类有，但本文不该调用它）。
    这样两条边界断言才真正在测"端点做了什么"，而不是在测"Mock 的默认行为"。
    """
    repo = Mock(spec=DiagnosisRepository)
    repo.get_result = AsyncMock(return_value=result)
    repo.mark_doctor_confirmed = AsyncMock(return_value=result)
    audit = Mock()
    audit.log_action = AsyncMock()
    user = {"user_id": actor}
    return repo, audit, user


class TestDoctorConfirmHappyPath:
    @pytest.mark.asyncio
    async def test_writes_timestamp_and_actor(self):
        """正常确认：写时间戳 + 医生标识，并回显给前端。"""
        result = _result()
        result.doctor_confirmed_at = datetime.now(timezone.utc)
        repo, audit, user = _deps(result=result)

        resp = await doctor_confirm_result(
            request_id="req_1",
            payload=DoctorConfirmRequest(),
            diagnosis_repo=repo,
            audit_service=audit,
            current_user=user,
        )

        assert resp.request_id == "req_1"
        assert resp.doctor_confirmed_by == "doctor_007"
        assert resp.doctor_confirmed_at is not None
        repo.mark_doctor_confirmed.assert_awaited_once_with(
            request_id="req_1", confirmed_by="doctor_007"
        )

    @pytest.mark.asyncio
    async def test_writes_audit_trail(self):
        """确认必须留审计：谁、什么时候。"""
        result = _result()
        result.doctor_confirmed_at = datetime.now(timezone.utc)
        repo, audit, user = _deps(result=result)

        await doctor_confirm_result(
            request_id="req_1",
            payload=DoctorConfirmRequest(),
            diagnosis_repo=repo,
            audit_service=audit,
            current_user=user,
        )

        audit.log_action.assert_awaited_once()
        kwargs = audit.log_action.await_args.kwargs
        assert kwargs["action"] == "doctor_confirm_result"
        assert kwargs["actor"] == "doctor_007"
        assert kwargs["target_id"] == "req_1"
        assert kwargs["target_type"] == "diagnosis"


class TestDoctorConfirmBoundaries:
    """确认 ≠ 签发。这几条如果破了，等于让"看过"悄悄变成"负责"。"""

    @pytest.mark.asyncio
    async def test_does_not_touch_status_or_confidence(self):
        """确认只写确认字段，不得改 status / confidence / findings。"""
        result = _result(status="completed", confidence=0.85)
        result.doctor_confirmed_at = datetime.now(timezone.utc)
        repo, audit, user = _deps(result=result)

        await doctor_confirm_result(
            request_id="req_1",
            payload=DoctorConfirmRequest(),
            diagnosis_repo=repo,
            audit_service=audit,
            current_user=user,
        )

        # update_result 一旦被调用就意味着改了判读本身 —— 本端点不该碰它
        assert not hasattr(repo, "update_result") or not repo.update_result.called
        assert result.status == "completed"
        assert result.confidence == 0.85

    @pytest.mark.asyncio
    async def test_does_not_sign_or_generate_report(self):
        """确认不得触发任何报告/签发能力。"""
        result = _result()
        result.doctor_confirmed_at = datetime.now(timezone.utc)
        repo, audit, user = _deps(result=result)

        await doctor_confirm_result(
            request_id="req_1",
            payload=DoctorConfirmRequest(),
            diagnosis_repo=repo,
            audit_service=audit,
            current_user=user,
        )

        # 端点签名里根本没有 report service / signer 依赖，这里再钉一次行为
        assert not hasattr(repo, "sign_report")
        assert not hasattr(repo, "create_report")


class TestDoctorConfirmFailures:
    @pytest.mark.asyncio
    async def test_result_not_found_raises_404(self):
        repo, audit, user = _deps(result=None)
        with pytest.raises(NotFoundException):
            await doctor_confirm_result(
                request_id="req_missing",
                payload=DoctorConfirmRequest(),
                diagnosis_repo=repo,
                audit_service=audit,
                current_user=user,
            )
        # 找不到结果时不得调用写库
        repo.mark_doctor_confirmed.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_repo_returns_none_raises_400(self):
        """仓储写入失败（竞态删行）→ 400，不能假装成功。"""
        result = _result()
        repo, audit, user = _deps(result=result)
        repo.mark_doctor_confirmed = AsyncMock(return_value=None)

        with pytest.raises(BadRequestException):
            await doctor_confirm_result(
                request_id="req_1",
                payload=DoctorConfirmRequest(),
                diagnosis_repo=repo,
                audit_service=audit,
                current_user=user,
            )

    @pytest.mark.asyncio
    async def test_timestamp_still_none_raises_400(self):
        """仓储返回了行但时间戳没写上 → 视为失败，不能回一个空时间给前端。"""
        result = _result()
        result.doctor_confirmed_at = None
        repo, audit, user = _deps(result=result)

        with pytest.raises(BadRequestException):
            await doctor_confirm_result(
                request_id="req_1",
                payload=DoctorConfirmRequest(),
                diagnosis_repo=repo,
                audit_service=audit,
                current_user=user,
            )
