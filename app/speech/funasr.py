"""FunASR语音识别引擎适配器模块。

本模块实现了基于ONNX Runtime的FunASR语音识别适配器，支持医疗领域的语音转写需求。

核心功能：
- FunASROnnxEngine: FunASR ONNX模型的异步推理引擎
- find_terminology: 医疗术语识别函数，支持甲状腺、肝脏、心脏等器官术语
- 支持CPU和CUDA两种推理模式
- 自动置信度估计

术语识别覆盖范围：
- thyroid: 甲状腺相关术语（TI-RADS分级、结节类型等）
- liver: 肝脏相关术语（LI-RADS分级、病变类型等）
- cardiac: 心脏相关术语（LVEF、瓣膜、功能描述等）
- general: 通用超声术语（探头类型、多普勒等）

适用场景：
- 医疗语音记录转写
- 超声检查报告语音输入
- 医疗术语实时识别和标记
"""

import re
import time
from typing import Any

import structlog

from app.speech.protocols import (
    SpeechEngine,
    SpeechEngineInfo,
    SpeechError,
    SpeechErrorCode,
    SpeechRequest,
    SpeechResponse,
)

_ULTRASOUND_TERMS: dict[str, list[str]] = {
    "thyroid": [
        "甲状腺", "结节", "囊肿", "钙化", "微钙化", "粗钙化",
        "TI-RADS", "滤泡", "乳头状", "腺瘤", "回声",
        "高回声", "低回声", "等回声", "无回声", "混合回声",
        "边界清晰", "边界模糊", "形态规则", "形态不规则",
        "纵横比", "血流", "丰富血流", "少血流",
    ],
    "liver": [
        "肝脏", "肝内胆管", "门静脉", "肝静脉", "肝动脉",
        "LI-RADS", "占位", "血管瘤", "局灶性结节增生",
        "肝囊肿", "肝硬化", "脂肪肝", "回声增粗",
    ],
    "cardiac": [
        "心脏", "左心室", "右心室", "左心房", "右心房",
        "LVEF", "射血分数", "室间隔", "左室后壁",
        "二尖瓣", "三尖瓣", "主动脉瓣", "肺动脉瓣",
        "舒张功能", "收缩功能", "心包积液",
    ],
    "general": [
        "超声", "多普勒", "彩色多普勒", "频谱",
        "探头", "线阵", "凸阵", "相控阵",
        "增益", "深度", "聚焦", "帧率",
    ],
}

_TERM_PATTERN_CACHE: dict[str, re.Pattern] | None = None


def _build_term_pattern() -> re.Pattern:
    """构建术语匹配正则表达式。

    使用缓存机制避免重复编译，支持最长匹配优先（按术语长度降序排序）。

    Returns:
        re.Pattern: 包含所有医疗术语的正则表达式对象
    """
    global _TERM_PATTERN_CACHE
    if _TERM_PATTERN_CACHE is not None:
        return _TERM_PATTERN_CACHE
    all_terms: list[str] = []
    for terms in _ULTRASOUND_TERMS.values():
        all_terms.extend(terms)
    all_terms.sort(key=len, reverse=True)
    escaped = [re.escape(t) for t in all_terms]
    _TERM_PATTERN_CACHE = re.compile("|".join(escaped))
    return _TERM_PATTERN_CACHE


def find_terminology(text: str) -> list[dict[str, Any]]:
    """在文本中查找医疗术语。

    使用预定义的超声术语词典进行匹配，返回匹配到的术语及其位置信息。

    Args:
        text: 待分析的文本

    Returns:
        list: 匹配到的术语列表，每个元素包含term、category、start、end字段
    """
    pattern = _build_term_pattern()
    matches: list[dict[str, Any]] = []
    seen: set[str] = set()
    for m in pattern.finditer(text):
        term = m.group()
        if term not in seen:
            seen.add(term)
            category = "general"
            for cat, terms in _ULTRASOUND_TERMS.items():
                if term in terms and cat != "general":
                    category = cat
                    break
            matches.append({
                "term": term,
                "category": category,
                "start": m.start(),
                "end": m.end(),
            })
    return matches


class FunASROnnxEngine(SpeechEngine):
    """FunASR ONNX Runtime语音识别引擎。

    实现了基于ONNX Runtime的FunASR语音识别适配器，支持CPU和CUDA推理。

    Args:
        model_dir: 模型文件目录（默认models/funasr）
        device: 推理设备（cpu或cuda，默认cpu）
        num_threads: 线程数（默认4）
        language: 语言（默认中文zh）

    Note:
        需要预先下载FunASR ONNX模型：
        - model.onnx: ONNX格式的语音识别模型
        - tokens.txt: 词表文件（可选）

        compact模式下强制使用CPU，避免与llama.cpp竞争GPU资源。
    """

    def __init__(
        self,
        model_dir: str = "models/funasr",
        device: str = "cpu",
        num_threads: int = 4,
        language: str = "zh",
    ):
        self._model_dir = model_dir
        self._device = device
        self._num_threads = num_threads
        self._language = language
        self._model = None
        self._tokenizer = None
        self._frontend = None  # Paraformer 前端（A.50）；None = 旧直喂波形路径
        self._initialized = False
        self._log = structlog.get_logger("app.speech.funasr")

    async def initialize(self) -> None:
        """初始化FunASR引擎。

        加载ONNX模型和词表文件，配置推理会话。模型目录同时存在
        ``model.int8.onnx`` 与 ``model.onnx`` 时优先 int8（CPU 推理快 ~3x，
        paraformer int8 精度损失可忽略，sherpa-onnx 同款缺省）。

        Raises:
            SpeechError: 如果模型文件不存在或初始化失败
        """
        if self._initialized:
            return
        try:
            import onnxruntime as ort

            sess_opts = ort.SessionOptions()
            sess_opts.intra_op_num_threads = self._num_threads
            sess_opts.inter_op_num_threads = 2
            sess_opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL

            providers = ["CPUExecutionProvider"]
            if self._device == "cuda":
                providers.insert(0, "CUDAExecutionProvider")

            import os
            int8_path = os.path.join(self._model_dir, "model.int8.onnx")
            fp32_path = os.path.join(self._model_dir, "model.onnx")
            model_path = int8_path if os.path.exists(int8_path) else fp32_path
            if not os.path.exists(model_path):
                raise SpeechError(
                    error_code=SpeechErrorCode.E3001_MODEL_LOAD_FAILED,
                    message=f"FunASR model not found: {fp32_path}",
                    detail="Run model download script first",
                    retryable=False,
                )

            self._model = ort.InferenceSession(
                model_path,
                sess_options=sess_opts,
                providers=providers,
            )

            tokenizer_path = os.path.join(self._model_dir, "tokens.txt")
            if os.path.exists(tokenizer_path):
                self._tokenizer = self._load_tokenizer(tokenizer_path)

            # Paraformer 前端探测：CMVN/LFR 参数内嵌于 ONNX metadata
            # （键名与 sherpa-onnx offline-paraformer-model.cc 同源）。
            meta = getattr(self._model, "get_modelmeta", lambda: None)()
            meta_map = dict(getattr(meta, "custom_metadata_map", {}) or {})
            try:
                from app.speech.features import ParaformerFrontend

                self._frontend = ParaformerFrontend.from_metadata(meta_map)
                self._log.info(
                    "paraformer_frontend_ready",
                    lfr_window_size=self._frontend.lfr_window_size,
                    lfr_window_shift=self._frontend.lfr_window_shift,
                    feat_dim=int(self._frontend.neg_mean.shape[0]),
                )
            except (ValueError, KeyError):
                self._frontend = None
                self._log.warning(
                    "paraformer_frontend_absent_fallback_raw_audio",
                    detail="metadata 缺 LFR/CMVN 键，退回旧原始波形路径（非 paraformer 导出）",
                )

            self._initialized = True
            self._log.info(
                "funasr_initialized",
                device=self._device,
                threads=self._num_threads,
                model=os.path.basename(model_path),
                providers=providers,
            )

        except ImportError as exc:
            raise SpeechError(
                error_code=SpeechErrorCode.E3001_MODEL_LOAD_FAILED,
                message="onnxruntime not installed",
                detail=str(exc),
                retryable=False,
            ) from exc
        except SpeechError:
            raise
        except Exception as exc:
            raise SpeechError(
                error_code=SpeechErrorCode.E3001_MODEL_LOAD_FAILED,
                message="FunASR model initialization failed",
                detail=str(exc),
                retryable=False,
            ) from exc

    async def recognize(self, request: SpeechRequest) -> SpeechResponse:
        """执行语音识别。

        将音频数据转换为文本，并进行医疗术语识别。

        Args:
            request: 语音识别请求对象，包含音频数据和参数

        Returns:
            SpeechResponse: 语音识别响应对象，包含文本结果和术语匹配

        Raises:
            SpeechError: 如果识别过程中发生错误
        """
        if not self._initialized:
            await self.initialize()

        start_time = time.monotonic()

        try:
            import numpy as np

            audio_int16 = np.frombuffer(request.audio_data, dtype=np.int16)

            if request.sample_rate != 16000:
                self._log.warning(
                    "resampling_not_implemented",
                    requested_rate=request.sample_rate,
                    model_rate=16000,
                )

            if self._frontend is not None:
                # Paraformer 路径：波形保持 int16 量纲（normalize_samples=false）
                result_text = await self._run_inference_paraformer(audio_int16.astype(np.float32))
            else:
                # 旧路径：直喂归一化波形（非 paraformer 导出）
                result_text = await self._run_inference(audio_int16.astype(np.float32) / 32768.0)

            latency_ms = (time.monotonic() - start_time) * 1000

            term_matches = find_terminology(result_text)

            response = SpeechResponse(
                text=result_text,
                language=self._language,
                confidence=self._estimate_confidence(result_text, term_matches),
                latency_ms=round(latency_ms, 2),
                request_id=request.request_id,
                terminology_matches=term_matches,
            )

            self._log.info(
                "recognition_completed",
                request_id=request.request_id,
                latency_ms=response.latency_ms,
                text_length=len(result_text),
                term_matches=len(term_matches),
            )

            return response

        except SpeechError:
            raise
        except Exception as exc:
            raise SpeechError(
                error_code=SpeechErrorCode.E3001_RECOGNITION_FAILED,
                message="speech recognition failed",
                detail=str(exc),
                retryable=True,
            ) from exc

    async def _run_inference(self, audio_array: Any) -> str:
        """执行ONNX推理。

        将音频数组输入到ONNX模型进行推理，返回识别结果。

        Args:
            audio_array: 预处理后的音频数组

        Returns:
            str: 识别出的文本

        Raises:
            SpeechError: 如果模型未初始化
        """
        if self._model is None:
            raise SpeechError(
                error_code=SpeechErrorCode.E3001_MODEL_LOAD_FAILED,
                message="model not initialized",
                retryable=False,
            )

        import numpy as np

        audio_length = np.array([len(audio_array)], dtype=np.int64)
        input_names = [inp.name for inp in self._model.get_inputs()]

        feeds: dict[str, Any] = {}
        for name in input_names:
            if "audio" in name.lower() or "waveform" in name.lower() or "speech" in name.lower():
                feeds[name] = audio_array.reshape(1, -1).astype(np.float32)
            elif "length" in name.lower() or "len" in name.lower():
                feeds[name] = audio_length
            else:
                feeds[name] = audio_array.reshape(1, -1).astype(np.float32)

        # 并发审查 P0-2：onnxruntime InferenceSession.run 是同步 C++ 调用，
        # 数秒音频典型 100~800ms 纯 CPU。放在事件循环里 = 单人语音识别时
        # 全站请求一起卡。offload 到线程池（onnxruntime session 的 Run 是
        # 线程安全的，多线程并发推理由 ORT 内部调度）。
        import asyncio as _asyncio

        outputs = await _asyncio.to_thread(self._model.run, None, feeds)

        if outputs and len(outputs) > 0 and isinstance(outputs[0], np.ndarray):
            if outputs[0].ndim == 0:
                return str(outputs[0].item())
            if self._tokenizer:
                return self._decode_tokens(outputs[0])
            return "".join(str(t) for t in outputs[0].flatten())

        return ""

    async def _run_inference_paraformer(self, audio_int16_scale: Any) -> str:
        """Paraformer 路径：特征管线 → ONNX → CTC 贪心解码 → 文本。

        前置：``self._frontend`` 已就绪（initialize 时由模型 metadata 探测）。
        输入名按 sherpa-onnx 导出惯例匹配（``speech`` / ``speech_lengths``），
        其余输入（如姿量分支）一律报错而非静默喂错数据。
        """
        if self._model is None or self._frontend is None:
            raise SpeechError(
                error_code=SpeechErrorCode.E3001_MODEL_LOAD_FAILED,
                message="paraformer model/frontend not initialized",
                retryable=False,
            )

        import numpy as np

        # Paraformer 内部 Loop/ConstantOfShape 对超短输入会产生负 shape
        # （真机实测：0.1s 静音 → onnxruntime FAIL）。统一零填充到 ≥1s。
        _MIN_SAMPLES = 16000
        if audio_int16_scale.shape[0] < _MIN_SAMPLES:
            audio_int16_scale = np.pad(
                audio_int16_scale, (0, _MIN_SAMPLES - audio_int16_scale.shape[0])
            )

        feats = self._frontend(audio_int16_scale)  # [T, 560] float32
        if feats.shape[0] == 0:
            return ""

        input_names = [inp.name for inp in self._model.get_inputs()]
        feeds: dict[str, Any] = {}
        for name in input_names:
            if name in ("speech", "x", "feats"):
                feeds[name] = feats[None, :, :]  # [1, T, 560]
            elif "length" in name.lower():
                feeds[name] = np.array([feats.shape[0]], dtype=np.int32)
            else:
                raise SpeechError(
                    error_code=SpeechErrorCode.E3001_RECOGNITION_FAILED,
                    message=f"unexpected model input: {name}",
                    detail="paraformer 导出出现未知输入，请核对模型版本",
                    retryable=False,
                )

        import asyncio as _asyncio

        outputs = await _asyncio.to_thread(self._model.run, None, feeds)

        logits = outputs[0]
        if not isinstance(logits, np.ndarray) or logits.ndim != 3:
            raise SpeechError(
                error_code=SpeechErrorCode.E3001_RECOGNITION_FAILED,
                message="unexpected paraformer output shape",
                detail=f"shape={getattr(logits, 'shape', None)}",
                retryable=False,
            )

        from app.speech.features import ctc_greedy_decode

        token_ids = ctc_greedy_decode(logits[0], blank_id=0)
        if self._tokenizer is None:
            return ""
        # 剥掉句子边界符（vocab 含 <s>/</s>，CTC 解码不区分）
        special = {"<s>", "</s>"}
        return "".join(
            tok for tid in token_ids if (tok := self._tokenizer.get(tid, "")) not in special
        )

    def _decode_tokens(self, tokens: Any) -> str:
        """解码Token数组为文本。

        使用词表将Token ID数组转换为可读文本。

        Args:
            tokens: Token ID数组

        Returns:
            str: 解码后的文本
        """
        if self._tokenizer is None:
            return str(tokens)
        token_ids = tokens.flatten().tolist()
        return "".join(
            self._tokenizer.get(tid, "")
            for tid in token_ids
            if tid >= 0
        )

    @staticmethod
    def _load_tokenizer(path: str) -> dict[int, str]:
        """加载词表文件。

        从tokens.txt文件加载词表，格式为每行一个token。

        Args:
            path: 词表文件路径

        Returns:
            dict: Token ID到token字符串的映射
        """
        tokenizer: dict[int, str] = {}
        with open(path, encoding="utf-8") as f:
            for idx, line in enumerate(f):
                token = line.strip()
                if token:
                    tokenizer[idx] = token
        return tokenizer

    @staticmethod
    def _estimate_confidence(text: str, term_matches: list[dict[str, Any]]) -> float:
        """估计识别置信度。

        根据识别文本长度和术语匹配数量估计置信度。

        Args:
            text: 识别文本
            term_matches: 术语匹配列表

        Returns:
            float: 置信度值（0.0-1.0）
        """
        if not text:
            return 0.0
        base_confidence = 0.85
        term_ratio = len(term_matches) / max(len(text) / 10, 1)
        bonus = min(term_ratio * 0.05, 0.10)
        return min(base_confidence + bonus, 1.0)

    async def health_check(self) -> bool:
        """检查语音引擎健康状态。

        Returns:
            bool: True表示健康（已初始化且模型存在），False表示不健康
        """
        return self._initialized and self._model is not None

    async def get_engine_info(self) -> SpeechEngineInfo:
        """获取引擎信息。

        Returns:
            SpeechEngineInfo: 引擎信息对象
        """
        return SpeechEngineInfo(
            engine_name="funasr_onnx",
            model_name="paraformer-zh",
            model_version="1.0",
            sample_rate=16000,
            language=self._language,
        )

    async def close(self) -> None:
        """关闭语音引擎。

        释放ONNX模型和词表资源。
        """
        if self._model is not None:
            del self._model
            self._model = None
        self._tokenizer = None
        self._initialized = False
        self._log.info("funasr_closed")
