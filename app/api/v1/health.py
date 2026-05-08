from fastapi import APIRouter, HTTPException
import redis.asyncio as redis
from sqlalchemy.ext.asyncio import create_async_engine
from app.config.settings import settings

router = APIRouter()


@router.get("/health")
async def health_check():
    redis_healthy = False
    redis_error = ""
    try:
        client = redis.from_url(settings.redis_url)
        pong = await client.ping()
        redis_healthy = pong is True
        await client.close()
    except Exception as e:
        redis_error = f"{type(e).__name__}: {str(e)}"
        redis_healthy = False
    
    db_healthy = False
    db_error = ""
    try:
        engine = create_async_engine(settings.database_url)
        async with engine.connect():
            db_healthy = True
        await engine.dispose()
    except Exception as e:
        db_error = f"{type(e).__name__}: {str(e)}"
        db_healthy = False
    
    if redis_healthy and db_healthy:
        return {"status": "healthy", "services": {"redis": "healthy", "database": "healthy"}}
    elif redis_healthy:
        raise HTTPException(status_code=503, detail={"status": "unhealthy", "services": {"redis": "healthy", "database": "unhealthy", "db_error": db_error}})
    elif db_healthy:
        raise HTTPException(status_code=503, detail={"status": "unhealthy", "services": {"redis": "unhealthy", "database": "healthy", "redis_error": redis_error}})
    else:
        raise HTTPException(status_code=503, detail={"status": "unhealthy", "services": {"redis": "unhealthy", "database": "unhealthy", "redis_error": redis_error, "db_error": db_error}})