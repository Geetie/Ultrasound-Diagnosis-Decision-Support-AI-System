"""脱敏还原服务的 DB 路径补测（quality-debt，核心模块 app/security）。

覆盖 `store_mapping` / `restore_value` / `re_encrypt_with_new_key` 三条落库主路径。
这三条决定的是**数据可恢复性**，最不能只看"没抛异常"：
- 存映射失败必须**向上抛**（静默失败＝原始值永久丢失，脱敏后的值再也还原不回来）；
- 还原时找不到记录必须抛 `ValueError` 而不是返回空串（空串会被上层当成真实值）；
- 密钥轮换要支持**断点续跑**，且失败率超阈值时熔断（否则会在大规模解密失败中静默刷数据）。

放在 tests/unit/data/ 而非 tests/unit/sec/：后者有 session 级 autouse fixture 把本模块的
密钥与限流方法打在类上替换成固定值；换目录后这些方法会执行真实实现（与本会话前几轮
同一类坑，详见 test_desensitization_restore_service.py 的说明）。
"""
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.security.desensitization_restore import (
    DesensitizationRestoreService,
    ReEncryptionError,
)


class _Result:
    def __init__(self, rows):
        self._rows = list(rows)

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)


class _FakeSession:
    """按批次返回查询结果；不传批次则返回空（用于结束分页循环）。"""

    def __init__(self, batches=None):
        self.batches = [list(batch) for batch in (batches or [])]
        self.added = []
        self.commits = 0
        self.queries = []

    async def execute(self, statement):
        self.queries.append(statement)
        return _Result(self.batches.pop(0) if self.batches else [])

    def add(self, instance):
        self.added.append(instance)

    async def commit(self):
        self.commits += 1


def _provider(session, error=None):
    async def _generate():
        if error is not None:
            raise error
        yield session

    return _generate


def _mapping(record_id="rec_1", key_version=1):
    return SimpleNamespace(
        id=record_id,
        encrypted_original=b"ciphertext",
        iv=b"iv-12-bytes!",
        key_version=key_version,
        desensitized_value="张*",
        session_id="sess_1",
    )


@pytest.fixture
def service():
    return DesensitizationRestoreService()


@pytest.fixture
def audit(monkeypatch):
    mock = MagicMock()
    mock.log_action = AsyncMock()
    monkeypatch.setattr("app.services.audit_service.AuditService", lambda: mock)
    return mock


@pytest.fixture
def session(monkeypatch):
    instance = _FakeSession()
    monkeypatch.setattr(
        "app.config.database.get_session", _provider(instance)
    )
    return instance


@pytest.fixture
def stub_crypto(service, monkeypatch):
    """把加解密替换成可预测结果，聚焦"落库与编排"本身。"""
    monkeypatch.setattr(
        service, "_encrypt_value", AsyncMock(return_value=(b"new-ct", b"new-iv", 2))
    )
    monkeypatch.setattr(service, "_decrypt_value", AsyncMock(return_value="张三"))
    return service


class TestStoreMapping:
    async def test_returns_uuid_and_commits(self, service, session, audit):
        record_id = await service.store_mapping(
            session_id="sess_1",
            field_type="patient_name",
            original_value="张三",
            desensitized_value="张*",
        )

        uuid.UUID(record_id)  # 必须是合法 UUID，后续按 id 还原
        assert session.commits == 1
        stored = session.added[0]
        assert stored.session_id == "sess_1"
        assert stored.desensitized_value == "张*"

    async def test_stored_ciphertext_is_not_plaintext(self, service, session, audit):
        """落库字段必须是密文——否则"脱敏"只是把明文换个位置存。"""
        await service.store_mapping(
            session_id="sess_1",
            field_type="patient_name",
            original_value="张三",
            desensitized_value="张*",
        )

        assert session.added[0].encrypted_original != "张三".encode()

    async def test_db_failure_propagates(self, service, monkeypatch, audit):
        """写库失败必须向上抛：静默失败＝原始值永久丢失，之后再也还原不回来。"""
        broken = _FakeSession()
        broken.commit = AsyncMock(side_effect=RuntimeError("deadlock"))
        monkeypatch.setattr("app.config.database.get_session", _provider(broken))

        with pytest.raises(RuntimeError):
            await service.store_mapping(
                session_id="sess_1",
                field_type="patient_name",
                original_value="张三",
                desensitized_value="张*",
            )

    async def test_session_error_propagates(self, service, monkeypatch, audit):
        monkeypatch.setattr(
            "app.config.database.get_session",
            _provider(None, error=RuntimeError("db down")),
        )

        with pytest.raises(RuntimeError):
            await service.store_mapping(
                session_id="sess_1",
                field_type="patient_name",
                original_value="张三",
                desensitized_value="张*",
            )

    async def test_audit_failure_is_swallowed(self, service, session, monkeypatch):
        """审计失败不能把"已经写成功"的映射变成失败（否则会重复写入）。"""
        broken_audit = MagicMock()
        broken_audit.log_action = AsyncMock(side_effect=RuntimeError("audit down"))
        monkeypatch.setattr(
            "app.services.audit_service.AuditService", lambda: broken_audit
        )

        record_id = await service.store_mapping(
            session_id="sess_1",
            field_type="patient_name",
            original_value="张三",
            desensitized_value="张*",
        )

        assert session.commits == 1
        assert record_id


class TestRestoreValue:
    async def test_returns_decrypted_value(self, service, session, stub_crypto, monkeypatch, audit):
        monkeypatch.setattr(
            service, "_check_rate_limit_persistent", AsyncMock(return_value=True)
        )
        session.batches = [[_mapping()]]

        assert await service.restore_value("rec_1") == "张三"
        assert audit.log_action.await_count == 1

    async def test_rate_limit_blocks_before_db(self, service, monkeypatch):
        """限流命中时不得触碰数据库（否则限流形同虚设）。"""
        monkeypatch.setattr(
            service, "_check_rate_limit_persistent", AsyncMock(return_value=False)
        )
        get_session = MagicMock(side_effect=AssertionError("不应访问 DB"))
        monkeypatch.setattr("app.config.database.get_session", get_session)

        with pytest.raises(RuntimeError, match="rate limit"):
            await service.restore_value("rec_1")

        get_session.assert_not_called()

    async def test_missing_record_raises_value_error(self, service, session, monkeypatch):
        """找不到记录必须抛错：返回空串会被上层当成真实原始值。"""
        monkeypatch.setattr(
            service, "_check_rate_limit_persistent", AsyncMock(return_value=True)
        )
        session.batches = [[]]

        with pytest.raises(ValueError, match="not found"):
            await service.restore_value("rec_missing")

    async def test_decrypt_failure_propagates(self, service, session, monkeypatch):
        """解密失败（如密钥不可用）必须上抛，不能返回乱码。"""
        monkeypatch.setattr(
            service, "_check_rate_limit_persistent", AsyncMock(return_value=True)
        )
        monkeypatch.setattr(
            service, "_decrypt_value", AsyncMock(side_effect=RuntimeError("no key"))
        )
        session.batches = [[_mapping()]]

        with pytest.raises(RuntimeError):
            await service.restore_value("rec_1")

    async def test_audit_failure_is_swallowed(self, service, session, stub_crypto, monkeypatch):
        monkeypatch.setattr(
            service, "_check_rate_limit_persistent", AsyncMock(return_value=True)
        )
        session.batches = [[_mapping()]]
        broken_audit = MagicMock()
        broken_audit.log_action = AsyncMock(side_effect=RuntimeError("audit down"))
        monkeypatch.setattr(
            "app.services.audit_service.AuditService", lambda: broken_audit
        )

        assert await service.restore_value("rec_1") == "张三"


class TestReEncryptWithNewKey:
    async def _service(self, service, monkeypatch, decrypt=None):
        monkeypatch.setattr(
            service, "_decrypt_value", AsyncMock(return_value="张三")
        )
        monkeypatch.setattr(
            service, "_encrypt_value", AsyncMock(return_value=(b"new", b"iv", 2))
        )
        if decrypt is not None:
            monkeypatch.setattr(service, "_decrypt_value", decrypt)

    async def test_rewrites_all_records(self, service, session, monkeypatch):
        await self._service(service, monkeypatch)
        first, second = _mapping("rec_1"), _mapping("rec_2")
        session.batches = [[first, second], []]

        total = await service.re_encrypt_with_new_key(2)

        assert total == 2
        assert session.commits == 1
        assert first.key_version == 2 and first.encrypted_original == b"new"

    async def test_empty_result_returns_zero(self, service, session, monkeypatch):
        await self._service(service, monkeypatch)
        session.batches = [[]]

        assert await service.re_encrypt_with_new_key(2) == 0
        assert session.commits == 0, "没有记录就不该提交事务"

    async def test_paginates_until_exhausted(self, service, session, monkeypatch):
        """分页续查：一次只处理 batch_size 条，直到查不到为止。"""
        await self._service(service, monkeypatch)
        session.batches = [[_mapping("rec_1")], [_mapping("rec_2")], []]

        total = await service.re_encrypt_with_new_key(2)

        assert total == 2
        assert len(session.queries) == 3, "每次提交后都要续查下一批"

    async def test_resume_cursor_is_applied(self, service, session, monkeypatch):
        """断点续跑必须真的把游标加进查询条件（否则重跑会从头开始）。"""
        await self._service(service, monkeypatch)
        session.batches = [[]]

        await service.re_encrypt_with_new_key(2, resume_from_id="rec_500")

        sql = str(
            session.queries[0].compile(compile_kwargs={"literal_binds": True})
        )
        assert "rec_500" in sql

    async def test_failure_rate_above_threshold_aborts(self, service, session, monkeypatch):
        """单条解密失败只计数；失败率超阈值必须熔断并带上断点，供人工介入后续跑。"""
        failing = AsyncMock(side_effect=[RuntimeError("bad ciphertext"), "张三"])
        await self._service(service, monkeypatch, decrypt=failing)
        session.batches = [[_mapping("rec_1"), _mapping("rec_2")], []]

        with pytest.raises(ReEncryptionError) as excinfo:
            await service.re_encrypt_with_new_key(2)

        assert excinfo.value.processed_count == 1
        assert excinfo.value.resume_from_id == "rec_2"

    async def test_threshold_denominator_is_success_count(self, service, session, monkeypatch):
        """把判据钉死：分母是**成功数**，不是记录总数。

        实测语义是 ``failed > total_encrypted * 0.1``。于是 9 成功 + 1 失败
        （按记录算是 10% 失败率）也会熔断，因为 1 > 9 x 0.1 = 0.9。
        这不是随手写的边界，而是刻意保守：旋转密钥时宁可早停，也不要在大批解密
        失败中继续把数据刷一遍。**写死在这里，避免以后被"顺手改成 失败/总数"而无人察觉。**
        """
        failing = AsyncMock(side_effect=["张三"] * 9 + [RuntimeError("bad")])
        await self._service(service, monkeypatch, decrypt=failing)
        session.batches = [[_mapping(f"rec_{index}") for index in range(10)], []]

        with pytest.raises(ReEncryptionError):
            await service.re_encrypt_with_new_key(2)

    async def test_few_failures_do_not_abort(self, service, session, monkeypatch):
        """失败数低于"成功数 x 10%"时继续跑完，以计数返回（不能一条坏记录就全停）。"""
        failing = AsyncMock(side_effect=["张三"] * 100 + [RuntimeError("bad")])
        await self._service(service, monkeypatch, decrypt=failing)
        session.batches = [[_mapping(f"rec_{index}") for index in range(101)], []]

        assert await service.re_encrypt_with_new_key(2) == 100

    async def test_unexpected_error_wrapped_with_zero_progress(self, service, monkeypatch):
        """外层异常统一包装成 ReEncryptionError，并声明"本次没有推进"。"""
        monkeypatch.setattr(
            "app.config.database.get_session",
            _provider(None, error=RuntimeError("db down")),
        )

        with pytest.raises(ReEncryptionError) as excinfo:
            await service.re_encrypt_with_new_key(2, resume_from_id="rec_9")

        assert excinfo.value.processed_count == 0
        assert excinfo.value.resume_from_id == "rec_9"
        assert "db down" in str(excinfo.value)

    async def test_reencryption_error_is_not_double_wrapped(self, service, session, monkeypatch):
        """已经是 ReEncryptionError 的要原样抛出，否则断点信息会被擦掉。"""
        failing = AsyncMock(side_effect=[RuntimeError("bad"), "张三"])
        await self._service(service, monkeypatch, decrypt=failing)
        session.batches = [[_mapping("rec_1"), _mapping("rec_2")], []]

        with pytest.raises(ReEncryptionError) as excinfo:
            await service.re_encrypt_with_new_key(2)

        assert excinfo.value.resume_from_id, "断点信息必须保留给运维续跑"
