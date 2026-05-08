from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from app.config.validator import validate_config, ConfigValidationError
from app.config.redis import RedisPool
from app.api.v1.health import router as health_router
from app.api.v1.auth import router as auth_router
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
    title="Ultrasound Diagnosis Decision Support AI System",
    description="超声诊疗决策支持AI系统 API",
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

app.include_router(health_router, prefix="/api/v1")
app.include_router(auth_router, prefix="/api/v1/auth")

@app.get("/")
async def root():
    return {"message": "Ultrasound Diagnosis Decision Support AI System"}