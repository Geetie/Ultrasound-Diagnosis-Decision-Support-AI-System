"""语音识别API路由模块。

提供医疗语音转写服务的RESTful接口，支持音频上传和实时语音识别。

核心端点：
- POST /api/v1/speech/recognize - 语音转写
- GET /api/v1/speech/health - 健康检查
- GET /api/v1/speech/info - 获取引擎信息
"""

import base64

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.dependencies import get_speech_engine
from app.speech.protocols import (
    SpeechEngine,
    SpeechError,
    SpeechRequest,
)

router = APIRouter(tags=["speech"])

logger = structlog.get_logger()


class SpeechRecognitionRequest(BaseModel):
    """语音识别请求模型。

    Attributes:
        audio_data: Base64编码的音频数据（PCM格式，16kHz采样率）
        sample_rate: 采样率（默认16000Hz，仅支持16000）
        language: 语言（默认中文zh）
        request_id: 请求唯一标识（可选）
    """
    audio_data: str = Field(
        ..., description="Base64编码的PCM音频数据"
    )
    sample_rate: int = Field(
        16000, description="采样率，仅支持16000Hz", ge=1
    )
    language: str = Field(
        "zh", description="语言，支持zh/en"
    )
    request_id: str | None = Field(
        None, description="请求唯一标识"
    )


class SpeechRecognitionResponse(BaseModel):
    """语音识别响应模型。

    Attributes:
        text: 识别出的文本结果
        language: 语言标识
        confidence: 识别置信度（0.0-1.0）
        latency_ms: 识别耗时（毫秒）
        request_id: 请求标识
        terminology_matches: 识别到的医疗术语列表
    """
    text: str = Field(..., description="识别文本")
    language: str = Field(..., description="语言标识")
    confidence: float = Field(..., description="置信度(0.0-1.0)")
    latency_ms: float = Field(..., description="耗时(毫秒)")
    request_id: str | None = Field(None, description="请求标识")
    terminology_matches: list[dict[str, object]] = Field(
        default_factory=list, description="医疗术语匹配列表"
    )


class SpeechEngineInfoResponse(BaseModel):
    """语音引擎信息响应模型。"""
    engine_name: str = Field(..., description="引擎名称")
    model_name: str = Field(..., description="模型名称")
    model_version: str = Field(..., description="模型版本")
    sample_rate: int = Field(..., description="采样率")
    language: str = Field(..., description="语言")


class HealthResponse(BaseModel):
    """健康检查响应模型。"""
    status: str = Field(..., description="状态")
    engine_ready: bool = Field(..., description="引擎是否就绪")


@router.post(
    "/recognize",
    response_model=SpeechRecognitionResponse,
    summary="语音识别",
    description="将音频数据转换为文本，并识别医疗术语"
)
async def recognize_speech(
    request: SpeechRecognitionRequest,
    engine: SpeechEngine = Depends(get_speech_engine),
) -> SpeechRecognitionResponse:
    """执行语音识别。

    Args:
        request: 语音识别请求，包含Base64编码的音频数据
        engine: 语音引擎实例

    Returns:
        SpeechRecognitionResponse: 识别结果
    """
    try:
        audio_bytes = base64.b64decode(request.audio_data)

        speech_request = SpeechRequest(
            audio_data=audio_bytes,
            sample_rate=request.sample_rate,
            language=request.language,
            request_id=request.request_id,
        )

        response = await engine.recognize(speech_request)

        return SpeechRecognitionResponse(
            text=response.text,
            language=response.language,
            confidence=response.confidence,
            latency_ms=response.latency_ms,
            request_id=response.request_id,
            terminology_matches=response.terminology_matches,
        )

    except base64.binascii.Error:
        raise HTTPException(
            status_code=400,
            detail="音频数据 base64 编码无效"
        )
    except SpeechError as e:
        raise HTTPException(
            status_code=500 if e.retryable else 400,
            detail=f"语音识别失败：{e.message}"
        )
    except Exception as e:
        logger.error("speech_recognition_unexpected_error", error=str(e))
        raise HTTPException(
            status_code=500,
            detail="语音识别因内部错误失败"
        )


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="健康检查",
    description="检查语音引擎的健康状态"
)
async def check_health(
    engine: SpeechEngine = Depends(get_speech_engine),
) -> HealthResponse:
    """检查语音引擎健康状态。"""
    try:
        is_ready = await engine.health_check()
        return HealthResponse(
            status="healthy" if is_ready else "unhealthy",
            engine_ready=is_ready,
        )
    except Exception:
        return HealthResponse(
            status="error",
            engine_ready=False,
        )


@router.get(
    "/info",
    response_model=SpeechEngineInfoResponse,
    summary="引擎信息",
    description="获取当前语音引擎的详细信息"
)
async def get_engine_info(
    engine: SpeechEngine = Depends(get_speech_engine),
) -> SpeechEngineInfoResponse:
    """获取语音引擎信息。"""
    try:
        info = await engine.get_engine_info()
        return SpeechEngineInfoResponse(
            engine_name=info.engine_name,
            model_name=info.model_name,
            model_version=info.model_version,
            sample_rate=info.sample_rate,
            language=info.language,
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"获取语音引擎信息失败：{e!s}"
        )
