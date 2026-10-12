from typing import Any

from fastapi import APIRouter, Depends

from app.api.v1.errors import BadRequestException, NotFoundException
from app.config.logging import LOG_LEVELS, get_log_level_info, set_log_level
from app.security.auth import RoleChecker, get_current_active_user
from app.security.desensitization import DesensitizationRule, desensitization_engine

router = APIRouter(tags=["security"])

_admin_only = Depends(RoleChecker(["SYSTEM_ADMIN"]))
_admin_or_head = Depends(RoleChecker(["SYSTEM_ADMIN", "DEPARTMENT_HEAD"]))
_any_authenticated = Depends(get_current_active_user)


@router.get("/desensitization/status")
async def get_desensitization_status(current_user: dict[str, Any] = _any_authenticated):
    return desensitization_engine.get_status()


@router.get("/desensitization/rules")
async def get_desensitization_rules(current_user: dict[str, Any] = _any_authenticated):
    rules = desensitization_engine.get_rules()
    return {"rules": [rule.model_dump() for rule in rules]}


@router.post("/desensitization/rules/reload")
async def reload_desensitization_rules(current_user: dict[str, Any] = _admin_only):
    try:
        result = desensitization_engine.reload_rules()
        return result
    except Exception as e:
        raise BadRequestException(error_message=f"规则重载失败：{e!s}")


@router.post("/desensitization/rules/check")
async def check_desensitization_rules(current_user: dict[str, Any] = _admin_or_head):
    changed = desensitization_engine.check_and_reload_rules()
    if changed:
        return {"status": "reloaded", "message": "规则文件已修改，已自动重新加载"}
    else:
        return {"status": "unchanged", "message": "规则文件未修改"}


@router.post("/desensitization/rules")
async def add_desensitization_rule(rule: DesensitizationRule, current_user: dict[str, Any] = _admin_only):
    existing_rules = desensitization_engine.get_rules()
    if any(r.name == rule.name for r in existing_rules):
        raise BadRequestException(error_message=f"规则 {rule.name} 已存在")

    desensitization_engine.add_rule(rule)
    return {"status": "success", "message": f"规则 {rule.name} 添加成功"}


@router.put("/desensitization/rules/{rule_name}")
async def update_desensitization_rule(rule_name: str, rule: DesensitizationRule, current_user: dict[str, Any] = _admin_only):
    existing_rules = desensitization_engine.get_rules()
    if not any(r.name == rule_name for r in existing_rules):
        raise NotFoundException(error_message=f"规则 {rule_name} 不存在")

    desensitization_engine.update_rule(rule_name, rule)
    return {"status": "success", "message": f"规则 {rule_name} 更新成功"}


@router.delete("/desensitization/rules/{rule_name}")
async def delete_desensitization_rule(rule_name: str, current_user: dict[str, Any] = _admin_only):
    existing_rules = desensitization_engine.get_rules()
    if not any(r.name == rule_name for r in existing_rules):
        raise NotFoundException(error_message=f"规则 {rule_name} 不存在")

    desensitization_engine.remove_rule(rule_name)
    return {"status": "success", "message": f"规则 {rule_name} 删除成功"}


@router.post("/desensitization/test")
async def test_desensitization(text: str, current_user: dict[str, Any] = _admin_or_head):
    result = desensitization_engine.desensitize(text)
    modified = text != result

    return {
        "desensitized": result,
        "modified": modified
    }


@router.get("/logging/level")
async def get_log_level(current_user: dict[str, Any] = _any_authenticated):
    return get_log_level_info()


@router.put("/logging/level")
async def update_log_level(level: str, current_user: dict[str, Any] = _admin_only):
    if level.upper() not in LOG_LEVELS:
        raise BadRequestException(
            error_message=f"日志级别非法：{level}，必须是 {LOG_LEVELS} 之一"
        )

    try:
        result = set_log_level(level)
        return result
    except ValueError as e:
        raise BadRequestException(error_message=str(e))
