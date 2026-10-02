"""守卫：被**两个服务各自声明**的表，其列必须是两边所需列的**并集**。

## 为什么需要它（2026-09-29 真机第 1 例真实诊断暴露）

`consent_records` 同时出现在：

* `app/models/database.py::ConsentRecord`（本表由 app 的迁移链创建，是"所有者"）；
* `safety_gateway/sg_models.py::ConsentRecord`（SG 用自己的独立 Base 声明同名表）。

SG 侧多声明了一个 `updated_at`，而它做的是 ORM 全列查询，于是真机报：

    UndefinedColumnError: column consent_records.updated_at does not exist
    → 503 consent_check_db_error → 主服务拿不到同意状态 → fail-closed → 诊断恒 failed

这类"两套 DDL 打架"单测永远发现不了（本地没人建这张表），只有真机第一次真查询才现形。
本守卫静态比对两边声明，把"共享表 = 双方列的并集"钉死。
"""
from __future__ import annotations

import ast
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
APP_MODELS = REPO_ROOT / "app" / "models" / "database.py"
SG_MODELS = REPO_ROOT / "safety_gateway" / "sg_models.py"

# 已知的共享表清单：出现新共享表时应显式复核（而不是让它悄悄漏过）
KNOWN_SHARED_TABLES = {"consent_records", "organ_disease_thresholds"}


def _is_column_call(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Name):
        return func.id == "Column"
    if isinstance(func, ast.Attribute):
        return func.attr == "Column"
    return False


def model_spec(path: pathlib.Path) -> dict[str, set[str]]:
    """解析 ORM 文件：表名 → 列名集合（列名取 DB 侧名字）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    spec: dict[str, set[str]] = {}
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        table: str | None = None
        columns: set[str] = set()
        for stmt in node.body:
            if not isinstance(stmt, ast.Assign):
                continue
            targets = [t.id for t in stmt.targets if isinstance(t, ast.Name)]
            if "__tablename__" in targets and isinstance(stmt.value, ast.Constant):
                table = str(stmt.value.value)
                continue
            if not _is_column_call(stmt.value):
                continue
            args = stmt.value.args  # type: ignore[union-attr]
            if args and isinstance(args[0], ast.Constant):
                columns.add(str(args[0].value).lower())
            elif targets:
                columns.add(targets[0].lower())
        if table:
            spec[table] = columns
    return spec


def test_app_侧声明必须覆盖_sg_侧所需列():
    app_spec = model_spec(APP_MODELS)
    sg_spec = model_spec(SG_MODELS)
    shared = set(app_spec) & set(sg_spec)

    assert shared, "两边没有同名表？解析逻辑可能已失效"

    gaps = {
        table: sorted(sg_spec[table] - app_spec[table])
        for table in sorted(shared)
        if sg_spec[table] - app_spec[table]
    }

    assert gaps == {}, (
        "共享表缺列：SG 的 ORM 会 SELECT 这些列，而 app 的迁移没建 → 真机必报 "
        f"UndefinedColumnError（表: 缺失列）：{gaps}"
    )


def test_共享表清单未悄悄扩大():
    app_spec = model_spec(APP_MODELS)
    shared = set(app_spec) & set(model_spec(SG_MODELS))

    assert shared == KNOWN_SHARED_TABLES, (
        "共享表清单发生变化 —— 新增共享表意味着又一个'两套 DDL'风险点，"
        f"请显式复核并更新本清单。当前: {sorted(shared)}"
    )
