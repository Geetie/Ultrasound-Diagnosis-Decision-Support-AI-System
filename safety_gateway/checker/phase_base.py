"""Safety Gateway校验阶段基模块

定义CheckContext上下文对象和CheckPhase抽象基类。
CheckContext携带跨阶段共享状态和各阶段所需参数，
使check(context)成为真正的多态入口。
"""

from typing import Any

from pydantic import BaseModel, ConfigDict


class CheckContext(BaseModel):
    """跨阶段共享上下文

    携带所有Phase所需的参数和跨阶段共享状态。
    新增参数直接在此处声明，避免每个Phase自定义check_with_params签名。
    """
    patient_id: str = ""
    output_data: dict[str, Any] = {}
    fusion_available: bool = True
    edge_connected: bool = True
    desensitization_degraded: bool = False
    conclusion_suppressed: bool = False
    consent_status_at_input: str | None = None
    elapsed_ms: float = 0.0

    consent_status: Any = None
    is_timeout: bool = False
    is_db_error: bool = False
    threshold_results: list[dict[str, Any]] = []
    contraindication_matches: list[dict[str, Any]] = []

    model_config = ConfigDict(arbitrary_types_allowed=True)


class CheckPhase:
    """校验阶段抽象基类

    子类实现check(context)方法，编排器通过基类协议多态调用。
    """

    async def check(self, context: CheckContext) -> Any | None:
        """执行校验，返回CheckResult或None（继续下一阶段）"""
        return await self.check_with_params(context)

    async def check_with_params(self, context: CheckContext) -> Any | None:
        raise NotImplementedError
