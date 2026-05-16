"""设备管理Repository"""
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import Optional
from datetime import datetime, timezone
import uuid
import secrets
import hashlib
import structlog

from app.models.database import Device
from app.config.redis_keys import RedisKey

logger = structlog.get_logger()


def _hash_api_key(api_key: str, salt: Optional[str] = None) -> str:
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
    
    async def get_device(self, device_id: str) -> Optional[Device]:
        result = await self.db.execute(
            select(Device).where(Device.device_id == device_id)
        )
        return result.scalar_one_or_none()
    
    async def get_device_by_name(self, device_name: str) -> Optional[Device]:
        result = await self.db.execute(
            select(Device).where(Device.device_name == device_name)
        )
        return result.scalar_one_or_none()
    
    async def authenticate_device(
        self,
        device_id: str,
        api_key: str,
    ) -> Optional[Device]:
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
    ) -> Optional[Device]:
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
