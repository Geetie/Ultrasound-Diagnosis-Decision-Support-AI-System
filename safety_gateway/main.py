from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from sqlalchemy.ext.asyncio import create_async_engine
from app.config.validator import validate_config, ConfigValidationError
from app.config.redis import RedisPool
from app.config.settings import settings

@asynccontextmanager
async def lifespan(app: FastAPI):
    errors = validate_config()
    if errors:
        raise ConfigValidationError(errors)
    
    await RedisPool.get_client()
    
    yield
    
    await RedisPool.close()

app = FastAPI(
    title="Safety Gateway",
    description="超声诊疗决策支持AI系统 - 安全网关服务",
    version="1.0.0",
    lifespan=lifespan
)

allowed_origins = [origin.strip() for origin in settings.allowed_origins.split(",")]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-Requested-With"],
)

@app.get("/")
async def root():
    return {"message": "Safety Gateway Service"}

@app.get("/health")
async def health_check():
    redis_healthy = await RedisPool.health_check()
    
    db_healthy = False
    try:
        engine = create_async_engine(settings.database_url)
        async with engine.connect():
            db_healthy = True
        await engine.dispose()
    except Exception as e:
        db_healthy = False
    
    overall_healthy = redis_healthy and db_healthy
    return {
        "status": "healthy" if overall_healthy else "unhealthy", 
        "service": "safety-gateway",
        "redis": "healthy" if redis_healthy else "unhealthy",
        "database": "healthy" if db_healthy else "unhealthy"
    }