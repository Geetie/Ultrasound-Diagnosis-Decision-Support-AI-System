"""T-G1-INT-06：在途诊断登记表——同意撤回时取消同一患者的在途诊断。

为什么需要它
------------
同意撤回此前的处理是"下一次检查时拦截"：管线在安全网关返回 withdrawn 时抛
异常强阻断。但**已经在跑**的那次诊断不受影响——它会继续占用算力、写库、推送
结果，医生在撤回后仍可能看到一份基于已撤回同意的结论。验收要求是"撤回后
<500ms 生效 + 在途请求取消"，所以必须能主动打断在途任务。

事件循环纪律
------------
历史上本项目已修过 3 处"模块级 asyncio 单例绑错事件循环导致死锁"。这里持的是
``asyncio.Task`` 引用，不做跨循环调度：取消前先校验登记时的事件循环与当前循环
一致，不一致就跳过并记日志——宁可漏取消（有 SG 兜底强阻断），也不要跨循环
cancel 造成难以定位的挂死。
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import structlog

logger = structlog.get_logger()


@dataclass
class _Entry:
    request_id: str
    patient_id: str
    task: asyncio.Task
    loop: asyncio.AbstractEventLoop


@dataclass
class InFlightDiagnosisRegistry:
    """按 ``request_id`` 登记在途诊断任务，并维护 ``patient_id -> request_id`` 索引。"""

    _entries: dict[str, _Entry] = field(default_factory=dict)
    _by_patient: dict[str, set] = field(default_factory=dict)

    def register(
        self,
        patient_id: str,
        request_id: str,
        task: asyncio.Task | None = None,
    ) -> None:
        """登记当前（或指定）任务。无事件循环时静默跳过（同步上下文调用）。

        .. warning::
           不传 ``task`` 时捕获的是**调用者自己**。因此必须在被取消的那个协程
           内部调用（管线就是这么做的）；若在"调度方"里替别人登记，撤回时会
           把调度方自己取消掉——测试里踩过一次，症状是测试任务被莫名取消。
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:  # silent-ok: 无运行事件循环属同步调用场景（改走 asyncio.run/直连路径）
            return

        current = task or asyncio.current_task()
        if current is None:
            return

        self._entries[request_id] = _Entry(
            request_id=request_id,
            patient_id=patient_id,
            task=current,
            loop=loop,
        )
        self._by_patient.setdefault(patient_id, set()).add(request_id)

    def unregister(self, request_id: str) -> None:
        entry = self._entries.pop(request_id, None)
        if entry is None:
            return
        patient_set = self._by_patient.get(entry.patient_id)
        if patient_set is not None:
            patient_set.discard(request_id)
            if not patient_set:
                self._by_patient.pop(entry.patient_id, None)

    def cancel_for_patient(self, patient_id: str) -> list[str]:
        """取消该患者全部在途诊断，返回被取消的 request_id 列表。"""
        request_ids = sorted(self._by_patient.get(patient_id, set()))
        if not request_ids:
            return []

        try:
            running_loop = asyncio.get_running_loop()
        except RuntimeError:  # silent-ok: 无运行事件循环属同步调用场景（改走 asyncio.run/直连路径）
            running_loop = None

        cancelled: list[str] = []
        for request_id in request_ids:
            entry = self._entries.get(request_id)
            if entry is None:
                continue
            if running_loop is not None and entry.loop is not running_loop:
                logger.warning(
                    "consent_withdrawal_cancel_skipped_loop_mismatch",
                    request_id=request_id,
                    patient_id=patient_id,
                )
                continue
            if entry.task.done():
                self.unregister(request_id)
                continue
            entry.task.cancel()
            cancelled.append(request_id)
            self.unregister(request_id)

        if cancelled:
            logger.info(
                "consent_withdrawal_cancelled_inflight_diagnosis",
                patient_id=patient_id,
                request_ids=cancelled,
            )
        return cancelled

    def in_flight_request_ids(self) -> list[str]:
        return sorted(self._entries.keys())

    def in_flight_count(self) -> int:
        return len(self._entries)

    def reset(self) -> None:
        """清空登记（仅用于测试与进程重启）。"""
        self._entries.clear()
        self._by_patient.clear()


_registry = InFlightDiagnosisRegistry()


def get_inflight_registry() -> InFlightDiagnosisRegistry:
    return _registry


def reset_inflight_registry() -> None:
    _registry.reset()
