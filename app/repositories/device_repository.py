"""设备管理Repository"""
import hashlib
import secrets
import uuid
from datetime import datetime, timezone

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.redis_keys import RedisKey
from app.models.database import Device

logger = structlog.get_logger()


def _hash_api_key(api_key: str, salt: str | None = None) -> str:
    if salt is None:
        salt = secrets.token_hex(16)
    return f"{salt}${hashlib.sha256((salt + api_key).encode()).hexdigest()}"


def _verify_api_key(hashed: str, api_key: str) -> bool:
    if "$" not in hashed:
        return hashlib.sha256(api_key.encode()).hexdigest() == hashed
    salt, stored_hash = hashed.split("$", 1)
    computed = hashlib.sha256((salt + api_key).encode()).hexdigest()
    return computed == stored_hash


class DeviceRepository:

    def __init__(self, db: AsyncSession):
        self.db = db

    def _generate_device_id(self) -> str:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d")
        return f"dev_{timestamp}_{uuid.uuid4().hex[:8]}"

    def _generate_api_key(self) -> str:
        return f"ak_{secrets.token_urlsafe(32)}"

    async def get_device(self, device_id: str) -> Device | None:
        result = await self.db.execute(
            select(Device).where(Device.device_id == device_id)
        )
        return result.scalar_one_or_none()

    async def list_devices(
        self,
        *,
        status: str | None = None,
        device_type: str | None = None,
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[list[Device], int]:
        """设备台账分页查询（A.51 设备列表端点）。

        为什么此前没有：走查实证「设备/患者列表端点不存在，前端用校验+录入补位」——
        管理面无法看到已注册设备全貌。返回 (rows, total) 供分页响应。
        limit 上限 200（防大页拖库）；排序按 id desc（最新注册在前）。
        """
        from sqlalchemy import func

        conds = []
        if status:
            conds.append(Device.status == status)
        if device_type:
            conds.append(Device.device_type == device_type)
        base = select(Device)
        count_stmt = select(func.count()).select_from(Device)
        if conds:
            base = base.where(*conds)
            count_stmt = count_stmt.where(*conds)
        total = int((await self.db.execute(count_stmt)).scalar_one())
        rows = (
            await self.db.execute(
                base.order_by(Device.id.desc())
                .offset(max(0, int(offset)))
                .limit(min(200, max(1, int(limit))))
            )
        ).scalars().all()
        return list(rows), total

    async def get_device_by_name(self, device_name: str) -> Device | None:
        # 按名取**最新一条**：device_name 无 DB 唯一约束（database.py Devices 表），
        # 历史脏数据/并发窗口可能留下同名多行 —— scalar_one_or_none() 会
        # MultipleResultsFound → 注册/恢复端点 500（A.49 真机探针实测）。
        result = await self.db.execute(
            select(Device)
            .where(Device.device_name == device_name)
            .order_by(Device.id.desc())
            .limit(1)
        )
        return result.scalars().first()

    async def authenticate_device(
        self,
        device_id: str,
        api_key: str,
    ) -> Device | None:
        device = await self.get_device(device_id)
        if not device:
            logger.warning("device_not_found", device_id=device_id)
            return None

        if not _verify_api_key(device.api_key, api_key):
            logger.warning("invalid_api_key", device_id=device_id)
            return None

        if not device.status or device.status != "active":
            logger.warning("device_not_active", device_id=device_id, status=device.status)
            return None

        await self._touch_device(device)

        logger.info("device_authenticated", device_id=device_id)
        return device

    async def register_device(
        self,
        device_name: str,
        device_type: str,
        location: str,
        jetpack_version: str,
    ) -> tuple[Device, str]:
        device_id = self._generate_device_id()
        raw_api_key = self._generate_api_key()
        hashed_api_key = _hash_api_key(raw_api_key)
        now = datetime.now(timezone.utc)

        device = Device(
            device_id=device_id,
            device_name=device_name,
            device_type=device_type,
            location=location,
            jetpack_version=jetpack_version,
            api_key=hashed_api_key,
            status="active",
            created_at=now,
            updated_at=now,
        )

        self.db.add(device)
        await self.db.flush()

        logger.info(
            "device_registered",
            device_id=device_id,
            device_name=device_name,
        )

        return device, raw_api_key

    async def update_device_status(
        self,
        device_id: str,
        status: str,
    ) -> Device | None:
        device = await self.get_device(device_id)
        if not device:
            return None

        device.status = status
        device.updated_at = datetime.now(timezone.utc)

        await self.db.flush()

        logger.info(
            "device_status_updated",
            device_id=device_id,
            status=status,
        )

        return device

    async def _touch_device(self, device: Device) -> None:
        device.updated_at = datetime.now(timezone.utc)
        await self.db.flush()

    async def store_recovery_token(self, device_id: str, token: str, expires_at: datetime) -> None:
        from app.config.redis import RedisPool
        redis_client = await RedisPool.get_client()
        key = RedisKey.device_recovery_token(device_id)
        ttl = int((expires_at - datetime.now(timezone.utc)).total_seconds())
        if ttl > 0:
            await redis_client.setex(key, ttl, token)

    async def verify_recovery_token(self, device_id: str, token: str) -> bool:
        from app.config.redis import RedisPool
        redis_client = await RedisPool.get_client()
        key = RedisKey.device_recovery_token(device_id)
        stored = await redis_client.get(key)
        if stored and stored.decode() == token:
            await redis_client.delete(key)
            return True
        return False

    async def store_new_api_key(self, device_id: str, api_key: str) -> None:
        hashed = _hash_api_key(api_key)
        device = await self.get_device(device_id)
        if device:
            device.api_key = hashed
            await self.db.flush()
