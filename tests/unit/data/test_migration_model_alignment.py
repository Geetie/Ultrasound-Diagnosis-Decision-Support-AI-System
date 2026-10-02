"""守卫：迁移链必须能把"空库"演进到与 ORM 模型一致的 schema。

## 为什么需要它（2026-09-28 真机部署暴露）

`alembic upgrade head` **在任何环境都跑不通**，而且失败得很晚：
0001 照的是更早一版的模型（6 张表全链缺失、多张表缺列、`audit_logs` 列名与模型不符、
`findings` 建成 Text），于是 0002 建 GIN 索引时报
`column "target_organs" does not exist`、`relation "safety_events" does not exist`。

为什么单测没抓到：**没有任何测试把迁移链和模型放在一起比**。这个文件补上这一环。

两侧都用**静态解析**（不碰运行期状态）：
* 模型侧：解析 `app/models/database.py` 的 AST，取每个类的 `__tablename__` 与列名。
  **刻意不用** `Base.metadata`：那是全局可变的 —— 别的测试往共享 Base 上挂一张
  测试专用表，就会把本守卫误判成"模型表没被迁移创建"（实测踩到过）。
* 迁移侧：解析 `alembic/versions/*.py`，覆盖本仓库三种写法：
  `op.create_table('x', sa.Column(...))`、`op.create_table(_TABLE, ...)`（表名是模块常量）、
  `op.execute("CREATE TABLE IF NOT EXISTS x (...)"` / `f"ALTER TABLE {_TABLE} ADD COLUMN ..."`。

只断言"表与列的存在性"，不校验类型/可空性（那要真库 introspection，属真机验证范畴：
`scripts/server_migrate_from_scratch.sh` 在空库上跑完整链条并逐列打印）。
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
MODEL_FILE = REPO_ROOT / "app" / "models" / "database.py"
VERSIONS_DIR = REPO_ROOT / "alembic" / "versions"

# `CREATE TABLE [IF NOT EXISTS] [schema.]name ( ... )`
_SQL_CREATE_TABLE = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(?:[a-zA-Z_][a-zA-Z0-9_]*\.)?"
    r"([a-zA-Z_][a-zA-Z0-9_]*)\s*\((?P<body>.*?)\)\s*(?:;|\"\"\")",
    re.IGNORECASE | re.DOTALL,
)
_SQL_ALTER_TABLE = re.compile(
    r"ALTER\s+TABLE\s+(?:[a-zA-Z_][a-zA-Z0-9_]*\.)?([a-zA-Z_][a-zA-Z0-9_]*)",
    re.IGNORECASE,
)
# 一条 ALTER TABLE 里可以有多个 ADD COLUMN（0012 就是 4 个），必须逐个都收。
_SQL_ADD_COLUMN = re.compile(
    r"ADD\s+COLUMN\s+(?:IF\s+NOT\s+EXISTS\s+)?([a-zA-Z_][a-zA-Z0-9_]*)",
    re.IGNORECASE,
)
_CONSTRAINT_KEYWORDS = ("constraint", "primary", "foreign", "unique", "check", "exclude")


# ----------------------------------------------------------------------
# 模型侧（权威）
# ----------------------------------------------------------------------

def _is_column_call(node: ast.AST) -> bool:
    """`Column(...)` 与 `sa.Column(...)` 都要认（迁移里两种写法都有）。"""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Name):
        return func.id == "Column"
    if isinstance(func, ast.Attribute):
        return func.attr == "Column"
    return False


def model_spec() -> dict[str, set[str]]:
    """解析 `app/models/database.py`：表名 → 列名集合（列名取 DB 侧名字）。"""
    tree = ast.parse(MODEL_FILE.read_text(encoding="utf-8"))
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
                # Column('metadata', JSONB, ...) —— 显式列名，可能与属性名不同
                columns.add(str(args[0].value).lower())
            elif targets:
                columns.add(targets[0].lower())
        if table:
            spec[table] = columns
    return spec


# ----------------------------------------------------------------------
# 迁移侧（实际施工）
# ----------------------------------------------------------------------

class _Migration:
    """一个迁移文件：源码 + 模块级字符串常量 + AST。"""

    def __init__(self, path: Path) -> None:
        self.name = path.name
        self.text = path.read_text(encoding="utf-8")
        self.tree = ast.parse(self.text)
        self.consts: dict[str, str] = {}
        for node in self.tree.body:
            if (
                isinstance(node, ast.Assign)
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)
            ):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        self.consts[target.id] = node.value.value

    def resolved_text(self) -> str:
        """把模块级字符串常量内联进源码，便于用正则识别 f-string 里的表名。"""
        text = self.text
        for name, value in self.consts.items():
            text = text.replace("{" + name + "}", value)
            text = re.sub(rf"(?<![\w.]){re.escape(name)}(?![\w])", value, text)
        return text

    def resolve(self, node: ast.expr) -> str | None:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.Name):
            return self.consts.get(node.id)
        return None


def _split_top_level(body: str) -> list[str]:
    """按顶层逗号切分 `CREATE TABLE` 表体（跳过 NUMERIC(10,2) 这类括号内逗号）。"""
    parts: list[str] = []
    depth = 0
    current = ""
    for char in body:
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        if char == "," and depth == 0:
            parts.append(current)
            current = ""
        else:
            current += char
    parts.append(current)
    return parts


def _added_columns_from_sql(text: str) -> dict[str, set[str]]:
    """把每个 `ADD COLUMN` 归属到**最近一次 `ALTER TABLE`** 指定的表。

    不能用 `ALTER TABLE t ADD COLUMN c` 一条正则：一条 ALTER 语句里可以挂多个
    `ADD COLUMN`（0012 一次加 4 列），只匹配第一个会漏掉其余 3 列。
    """
    markers = [(m.start(), m.group(1).lower()) for m in _SQL_ALTER_TABLE.finditer(text)]
    result: dict[str, set[str]] = {}
    for match in _SQL_ADD_COLUMN.finditer(text):
        table = None
        for position, name in markers:
            if position < match.start():
                table = name
            else:
                break
        if table:
            result.setdefault(table, set()).add(match.group(1).lower())
    return result


def migration_spec() -> tuple[set[str], dict[str, set[str]]]:
    """返回 `(建过的表集合, 表 → 列集合)`。"""
    tables: set[str] = set()
    columns: dict[str, set[str]] = {}

    for path in sorted(VERSIONS_DIR.glob("*.py")):
        migration = _Migration(path)

        for match in _SQL_CREATE_TABLE.finditer(migration.resolved_text()):
            table = match.group(1).lower()
            tables.add(table)
            bucket = columns.setdefault(table, set())
            for item in _split_top_level(match.group("body")):
                stripped = item.strip().strip("'\"").strip()
                if not stripped:
                    continue
                first = stripped.split()[0].strip('"').lower()
                if first in _CONSTRAINT_KEYWORDS:
                    continue
                bucket.add(first)

        for table, table_columns in _added_columns_from_sql(migration.resolved_text()).items():
            columns.setdefault(table, set()).update(table_columns)

        for node in ast.walk(migration.tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr == "add_column":
                if len(node.args) < 2:
                    continue
                table = migration.resolve(node.args[0])
                spec = node.args[1]
                if not table or not isinstance(spec, ast.Call) or not spec.args:
                    continue
                column = migration.resolve(spec.args[0])
                if column:
                    columns.setdefault(table.lower(), set()).add(column.lower())
            elif node.func.attr == "create_table" and node.args:
                table = migration.resolve(node.args[0])
                if not table:
                    continue
                table = table.lower()
                tables.add(table)
                bucket = columns.setdefault(table, set())
                for arg in node.args[1:]:
                    if not _is_column_call(arg):
                        continue
                    name = migration.resolve(arg.args[0])
                    if name:
                        bucket.add(name.lower())

    return tables, columns


# ----------------------------------------------------------------------
# 守卫
# ----------------------------------------------------------------------

class TestMigrationTouchesEveryModelTable:
    def test_每张模型表都有建表语句(self):
        created, _ = migration_spec()

        missing = sorted(set(model_spec()) - created)

        assert missing == [], (
            "以下模型表在迁移链里从未被创建 —— 空库 `alembic upgrade head` 之后"
            f"这些表不存在：{missing}"
        )

    def test_扫描不是空的(self):
        model = model_spec()
        created, _ = migration_spec()

        assert len(model) >= 16, f"只解析出 {len(model)} 张模型表，解析逻辑可能已失效"
        assert "sessions" in model, "模型侧解析失败"
        assert len(created) >= 16, f"只解析出 {len(created)} 张迁移表"
        # 两种写法各取一个代表，防止"只认了一种写法"。
        assert "sessions" in created, "op.create_table 字面量表名未被识别"
        assert "knowledge_chunks" in created, "op.create_table 常量表名未被识别"


class TestMigrationCoversEveryModelColumn:
    def test_每个模型列都能在迁移链里找到来源(self):
        created, available_columns = migration_spec()

        missing: list[str] = []
        for table_name, model_columns in model_spec().items():
            if table_name not in created:
                continue  # 缺表由上一个测试专门报错
            available = available_columns.get(table_name, set())
            missing.extend(
                f"{table_name}.{column}"
                for column in sorted(model_columns - available)
            )

        assert missing == [], (
            "以下列在模型里有、迁移链里既没建也没加 —— 空库跑完后这些列不存在"
            f"（真机上正是这类断链炸在 0002 建索引时）：{missing}"
        )
