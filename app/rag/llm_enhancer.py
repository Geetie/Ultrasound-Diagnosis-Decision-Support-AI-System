"""LLM-based query enhancement (T-GA-RAG-01 optional layer).

This module provides a pluggable LLMEnhancer abstraction and a default
Agnes-backed implementation. The enhancer is OPTIONAL: when the API key
is missing or the upstream call fails, the enhancer returns None and the
caller (QueryEnhancer) falls back to pure UDAF vocabulary expansion.

The Agnes client follows the same httpx-based pattern as the BgeM3MedicalService
and BgeRerankerService for consistency.
"""
from __future__ import annotations

import json
from abc import ABC, abstractmethod

import httpx
import structlog

logger = structlog.get_logger("app.rag.llm_enhancer")


# Prompt template (inlined; not loaded from disk so the prompt is part of the
# deterministic prompt contract used by tests).
_SYSTEM_PROMPT = (
    "你是一名医学超声领域的查询改写助手。"
    "任务：将用户的原始查询改写为更适合知识库检索的形式。"
    "约束：\n"
    "1) 保留器官名和关键特征词\n"
    "2) 补充同义词（如低回声 ↔ hypoechoic）\n"
    "3) 输出 JSON 格式：{\"rewritten_query\": \"...\", \"keywords\": [\"...\"]}"
)

_USER_PROMPT_TEMPLATE = (
    "原始查询：{query}\n"
    "UDAF 扩展词：{udaf_expansion}\n"
    "器官：{organ}\n"
    "请输出 JSON。"
)


class LLMEnhancer(ABC):
    """Abstract LLM-backed query enhancer.

    The enhance() contract returns None on any failure so callers can
    transparently fall back to deterministic UDAF expansion.
    """

    @abstractmethod
    async def enhance(
        self,
        original_query: str,
        udaf_expansion: list[str],
        organ: str | None,
    ) -> str | None:
        """Rewrite the query for retrieval.

        Args:
            original_query: Raw user query.
            udaf_expansion: Synonyms / hypernyms from UDAF (used as context).
            organ: Optional organ hint.

        Returns:
            Rewritten query string, or None on any failure.
        """
        raise NotImplementedError

    @abstractmethod
    async def health_check(self) -> bool:
        """Check LLM service health.

        Returns:
            True if reachable, False otherwise.
        """
        raise NotImplementedError


class AgnesLLMEnhancer(LLMEnhancer):
    """Agnes-flash LLM enhancer (HTTP client).

    Uses httpx.AsyncClient with a small connection pool. All failures
    (timeouts, connection errors, JSON parse errors) are logged at INFO
    level and translated to None so the QueryEnhancer can fall back to
    UDAF-only expansion.

    Args:
        api_key: Agnes API key (empty string disables the enhancer).
        api_base: Base URL, e.g. "https://agnes-api.example.com/v1".
        model: Model identifier, e.g. "agnes-flash".
        timeout_seconds: HTTP request timeout.
        max_connections: httpx connection pool size.
        max_keepalive_connections: httpx keepalive pool size.
    """

    def __init__(
        self,
        api_key: str,
        api_base: str,
        model: str,
        timeout_seconds: float = 3.0,
        max_connections: int = 5,
        max_keepalive_connections: int = 2,
    ):
        self._api_key = api_key
        self._api_base = api_base.rstrip("/")
        self._model = model
        self._timeout = timeout_seconds
        self._client: httpx.AsyncClient | None = None
        self._max_connections = max_connections
        self._max_keepalive_connections = max_keepalive_connections
        self._closed = False

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._closed:
            raise RuntimeError("AgnesLLMEnhancer has been closed")
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self._api_base,
                timeout=self._timeout,
                limits=httpx.Limits(
                    max_connections=self._max_connections,
                    max_keepalive_connections=self._max_keepalive_connections,
                ),
            )
        return self._client

    def _has_credentials(self) -> bool:
        return bool(self._api_key and self._api_key.strip())

    async def enhance(
        self,
        original_query: str,
        udaf_expansion: list[str],
        organ: str | None,
    ) -> str | None:
        """Call Agnes /chat/completions to rewrite the query.

        Returns None (with INFO log) on:
        - missing API key
        - HTTP timeout / connection error
        - non-2xx response
        - malformed JSON / missing rewritten_query
        """
        if not self._has_credentials():
            logger.info("llm_enhance_skipped_no_api_key")
            return None

        udaf_text = " ".join(udaf_expansion) if udaf_expansion else "(none)"
        user_prompt = _USER_PROMPT_TEMPLATE.format(
            query=original_query,
            udaf_expansion=udaf_text,
            organ=organ or "(unknown)",
        )
        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.1,
            "max_tokens": 256,
            "response_format": {"type": "json_object"},
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        try:
            client = self._ensure_client()
            response = await client.post(
                "/chat/completions", json=payload, headers=headers
            )
        except (httpx.TimeoutException, httpx.ConnectError) as exc:
            logger.info(
                "llm_enhance_network_error",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return None
        except Exception as exc:
            logger.info(
                "llm_enhance_unexpected_error",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return None

        if response.status_code != 200:
            logger.info(
                "llm_enhance_non_2xx",
                status_code=response.status_code,
                body_preview=response.text[:200],
            )
            return None

        try:
            data = response.json()
        except json.JSONDecodeError as exc:
            logger.info("llm_enhance_invalid_json", error=str(exc))
            return None

        rewritten = self._extract_rewritten_query(data)
        if not rewritten:
            logger.info("llm_enhance_no_rewritten_query_in_response")
            return None
        return rewritten

    @staticmethod
    def _extract_rewritten_query(data: dict) -> str | None:
        """Extract rewritten query from OpenAI-compatible chat response.

        Supports two response shapes:
        1) Standard chat: data["choices"][0]["message"]["content"] is JSON.
        2) Pre-parsed JSON: data["rewritten_query"] is a string.
        """
        if "rewritten_query" in data and isinstance(data["rewritten_query"], str):
            return data["rewritten_query"].strip() or None

        choices = data.get("choices")
        if not choices or not isinstance(choices, list):
            return None
        first = choices[0] if isinstance(choices[0], dict) else None
        if first is None:
            return None
        message = first.get("message") if isinstance(first.get("message"), dict) else None
        content = message.get("content") if message else first.get("text")
        if not isinstance(content, str):
            return None

        # Try to parse the content as JSON.
        content = content.strip()
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            # If the model ignored the JSON instruction, treat content as plain text.
            return content or None
        if isinstance(parsed, dict):
            value = parsed.get("rewritten_query")
            if isinstance(value, str):
                return value.strip() or None
        return None

    async def health_check(self) -> bool:
        if not self._has_credentials():
            return False
        try:
            client = self._ensure_client()
            response = await client.get("/models")
            return response.status_code == 200
        except (httpx.TimeoutException, httpx.ConnectError):
            return False
        except Exception:
            return False

    async def close(self) -> None:
        self._closed = True
        if self._client is not None:
            await self._client.aclose()
            self._client = None


__all__ = ["AgnesLLMEnhancer", "LLMEnhancer"]
