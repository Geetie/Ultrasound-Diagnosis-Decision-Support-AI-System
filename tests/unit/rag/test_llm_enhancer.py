"""Unit tests for app.rag.llm_enhancer (T-GA-RAG-01 optional LLM layer).

Covers:
- AgnesLLMEnhancer._has_credentials() (empty / whitespace / valid)
- enhance() with various HTTP responses (success, missing key, timeout, etc.)
- _extract_rewritten_query() static method (covers both response shapes)
- health_check() (missing key / reachable)
- close() (idempotent)

The HTTP layer is mocked with httpx.MockTransport, consistent with
tests/unit/emb/test_embedding.py.
"""
from __future__ import annotations

import json
from typing import Any, Optional

import httpx
import pytest

pytestmark = [pytest.mark.track_rag]

from app.rag.llm_enhancer import AgnesLLMEnhancer


# ----------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------


def _make_mock_handler(
    response: Optional[httpx.Response] = None,
    exc: Optional[Exception] = None,
):
    async def handler(request: httpx.Request) -> httpx.Response:
        if exc is not None:
            raise exc
        return response
    return handler


def _inject_client(svc: AgnesLLMEnhancer, handler) -> None:
    """Replace the underlying AsyncClient with a MockTransport-backed one."""
    svc._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url=svc._api_base,
    )


def _ok_response(payload: dict[str, Any]) -> httpx.Response:
    return httpx.Response(
        200,
        json=payload,
        request=httpx.Request("POST", "http://test/chat/completions"),
    )


def _error_response(status: int, body: str = "error") -> httpx.Response:
    return httpx.Response(
        status,
        text=body,
        request=httpx.Request("POST", "http://test/chat/completions"),
    )


# ----------------------------------------------------------------------
# credentials
# ----------------------------------------------------------------------


class TestCredentials:
    def test_no_key(self):
        svc = AgnesLLMEnhancer(api_key="", api_base="https://x", model="m")
        assert svc._has_credentials() is False

    def test_whitespace_key(self):
        svc = AgnesLLMEnhancer(api_key="   ", api_base="https://x", model="m")
        assert svc._has_credentials() is False

    def test_valid_key(self):
        svc = AgnesLLMEnhancer(api_key="abc-123", api_base="https://x", model="m")
        assert svc._has_credentials() is True


# ----------------------------------------------------------------------
# enhance() — return value paths
# ----------------------------------------------------------------------


class TestEnhanceNoKey:
    async def test_returns_none_when_no_key(self):
        svc = AgnesLLMEnhancer(api_key="", api_base="https://x", model="agnes-flash")
        result = await svc.enhance("甲状腺结节", ["hypoechoic"], organ="thyroid")
        assert result is None
        # Client should not even be created
        assert svc._client is None


class TestEnhanceSuccess:
    async def test_rewritten_query_field(self):
        svc = AgnesLLMEnhancer(api_key="k", api_base="http://test", model="agnes-flash")
        payload = {"rewritten_query": "thyroid hypoechoic nodule"}
        _inject_client(svc, _make_mock_handler(_ok_response(payload)))

        out = await svc.enhance("甲状腺结节", ["hypoechoic"], organ="thyroid")
        assert out == "thyroid hypoechoic nodule"

    async def test_choices_message_json(self):
        svc = AgnesLLMEnhancer(api_key="k", api_base="http://test", model="agnes-flash")
        content = json.dumps({"rewritten_query": "liver mass"})
        payload = {
            "choices": [
                {"message": {"role": "assistant", "content": content}}
            ]
        }
        _inject_client(svc, _make_mock_handler(_ok_response(payload)))

        out = await svc.enhance("肝脏肿块", [], organ="liver")
        assert out == "liver mass"

    async def test_choices_message_plain_text(self):
        """If the LLM ignores the JSON instruction, treat content as plain text."""
        svc = AgnesLLMEnhancer(api_key="k", api_base="http://test", model="agnes-flash")
        payload = {
            "choices": [
                {"message": {"role": "assistant", "content": "liver lesion query"}}
            ]
        }
        _inject_client(svc, _make_mock_handler(_ok_response(payload)))

        out = await svc.enhance("肝脏病变", [], organ="liver")
        assert out == "liver lesion query"

    async def test_strips_whitespace(self):
        svc = AgnesLLMEnhancer(api_key="k", api_base="http://test", model="agnes-flash")
        payload = {"rewritten_query": "  spaced  "}
        _inject_client(svc, _make_mock_handler(_ok_response(payload)))

        out = await svc.enhance("x", [], organ=None)
        assert out == "spaced"

    async def test_empty_rewritten_returns_none(self):
        svc = AgnesLLMEnhancer(api_key="k", api_base="http://test", model="agnes-flash")
        payload = {"rewritten_query": "   "}
        _inject_client(svc, _make_mock_handler(_ok_response(payload)))

        out = await svc.enhance("x", [], organ=None)
        assert out is None


class TestEnhanceMissingFields:
    async def test_no_choices_no_rewritten_query(self):
        svc = AgnesLLMEnhancer(api_key="k", api_base="http://test", model="agnes-flash")
        payload = {"foo": "bar"}  # neither field present
        _inject_client(svc, _make_mock_handler(_ok_response(payload)))

        out = await svc.enhance("x", [], organ=None)
        assert out is None

    async def test_empty_choices_list(self):
        svc = AgnesLLMEnhancer(api_key="k", api_base="http://test", model="agnes-flash")
        payload = {"choices": []}
        _inject_client(svc, _make_mock_handler(_ok_response(payload)))

        out = await svc.enhance("x", [], organ=None)
        assert out is None


# ----------------------------------------------------------------------
# enhance() — failure paths (must return None, never raise)
# ----------------------------------------------------------------------


class TestEnhanceFailures:
    async def test_http_500(self):
        svc = AgnesLLMEnhancer(api_key="k", api_base="http://test", model="agnes-flash")
        _inject_client(svc, _make_mock_handler(_error_response(500, "internal error")))

        out = await svc.enhance("x", [], organ=None)
        assert out is None

    async def test_http_400(self):
        svc = AgnesLLMEnhancer(api_key="k", api_base="http://test", model="agnes-flash")
        _inject_client(svc, _make_mock_handler(_error_response(400, "bad")))

        out = await svc.enhance("x", [], organ=None)
        assert out is None

    async def test_timeout(self):
        svc = AgnesLLMEnhancer(api_key="k", api_base="http://test", model="agnes-flash")
        _inject_client(svc, _make_mock_handler(exc=httpx.TimeoutException("t/o")))

        out = await svc.enhance("x", [], organ=None)
        assert out is None

    async def test_connect_error(self):
        svc = AgnesLLMEnhancer(api_key="k", api_base="http://test", model="agnes-flash")
        _inject_client(svc, _make_mock_handler(exc=httpx.ConnectError("refused")))

        out = await svc.enhance("x", [], organ=None)
        assert out is None

    async def test_invalid_json(self):
        svc = AgnesLLMEnhancer(api_key="k", api_base="http://test", model="agnes-flash")
        bad = httpx.Response(
            200,
            content=b"<<not json>>",
            request=httpx.Request("POST", "http://test/chat/completions"),
        )
        _inject_client(svc, _make_mock_handler(bad))

        out = await svc.enhance("x", [], organ=None)
        assert out is None

    async def test_choices_not_dict(self):
        svc = AgnesLLMEnhancer(api_key="k", api_base="http://test", model="agnes-flash")
        payload = {"choices": ["just a string"]}  # not a dict list
        _inject_client(svc, _make_mock_handler(_ok_response(payload)))

        out = await svc.enhance("x", [], organ=None)
        assert out is None

    async def test_message_content_not_string(self):
        svc = AgnesLLMEnhancer(api_key="k", api_base="http://test", model="agnes-flash")
        payload = {"choices": [{"message": {"content": 12345}}]}
        _inject_client(svc, _make_mock_handler(_ok_response(payload)))

        out = await svc.enhance("x", [], organ=None)
        assert out is None

    async def test_parsed_dict_missing_rewritten_query(self):
        svc = AgnesLLMEnhancer(api_key="k", api_base="http://test", model="agnes-flash")
        content = json.dumps({"keywords": ["a", "b"]})  # missing rewritten_query
        payload = {"choices": [{"message": {"content": content}}]}
        _inject_client(svc, _make_mock_handler(_ok_response(payload)))

        out = await svc.enhance("x", [], organ=None)
        assert out is None


# ----------------------------------------------------------------------
# health_check
# ----------------------------------------------------------------------


class TestHealthCheck:
    async def test_no_key_returns_false(self):
        svc = AgnesLLMEnhancer(api_key="", api_base="http://test", model="m")
        assert await svc.health_check() is False

    async def test_whitespace_key_returns_false(self):
        svc = AgnesLLMEnhancer(api_key="   ", api_base="http://test", model="m")
        assert await svc.health_check() is False

    async def test_reachable(self):
        svc = AgnesLLMEnhancer(api_key="k", api_base="http://test", model="m")
        resp = httpx.Response(
            200,
            request=httpx.Request("GET", "http://test/models"),
        )
        _inject_client(svc, _make_mock_handler(resp))
        assert await svc.health_check() is True

    async def test_unreachable(self):
        svc = AgnesLLMEnhancer(api_key="k", api_base="http://test", model="m")
        _inject_client(
            svc, _make_mock_handler(exc=httpx.ConnectError("refused"))
        )
        assert await svc.health_check() is False

    async def test_timeout(self):
        svc = AgnesLLMEnhancer(api_key="k", api_base="http://test", model="m")
        _inject_client(
            svc, _make_mock_handler(exc=httpx.TimeoutException("t/o"))
        )
        assert await svc.health_check() is False

    async def test_500_returns_false(self):
        svc = AgnesLLMEnhancer(api_key="k", api_base="http://test", model="m")
        resp = httpx.Response(500, request=httpx.Request("GET", "http://test/models"))
        _inject_client(svc, _make_mock_handler(resp))
        assert await svc.health_check() is False


# ----------------------------------------------------------------------
# close / lifecycle
# ----------------------------------------------------------------------


class TestClose:
    async def test_close_idempotent(self):
        svc = AgnesLLMEnhancer(api_key="k", api_base="http://test", model="m")
        # Build client first by calling health_check
        resp = httpx.Response(200, request=httpx.Request("GET", "http://test/models"))
        _inject_client(svc, _make_mock_handler(resp))
        assert await svc.health_check() is True

        await svc.close()
        # Second close is a no-op (does not raise, even though client is None)
        await svc.close()
        assert svc._closed is True

    async def test_ensure_client_after_close_raises(self):
        svc = AgnesLLMEnhancer(api_key="k", api_base="http://test", model="m")
        await svc.close()
        with pytest.raises(RuntimeError, match="closed"):
            svc._ensure_client()


# ----------------------------------------------------------------------
# _extract_rewritten_query (static method)
# ----------------------------------------------------------------------


class TestExtractRewrittenQuery:
    def test_top_level_field(self):
        data = {"rewritten_query": "abc"}
        assert AgnesLLMEnhancer._extract_rewritten_query(data) == "abc"

    def test_top_level_stripped(self):
        data = {"rewritten_query": "  abc  "}
        assert AgnesLLMEnhancer._extract_rewritten_query(data) == "abc"

    def test_top_level_empty_returns_none(self):
        data = {"rewritten_query": ""}
        assert AgnesLLMEnhancer._extract_rewritten_query(data) is None

    def test_choices_json(self):
        content = json.dumps({"rewritten_query": "y"})
        data = {"choices": [{"message": {"content": content}}]}
        assert AgnesLLMEnhancer._extract_rewritten_query(data) == "y"

    def test_choices_plain_text(self):
        data = {"choices": [{"message": {"content": "plain"}}]}
        assert AgnesLLMEnhancer._extract_rewritten_query(data) == "plain"

    def test_no_choices(self):
        data: dict = {}
        assert AgnesLLMEnhancer._extract_rewritten_query(data) is None

    def test_choices_not_list(self):
        data = {"choices": "not a list"}
        assert AgnesLLMEnhancer._extract_rewritten_query(data) is None

    def test_choices_first_not_dict(self):
        data = {"choices": ["string"]}
        assert AgnesLLMEnhancer._extract_rewritten_query(data) is None

    def test_message_field_missing(self):
        # Falls back to "text" key on the choice
        data = {"choices": [{"text": "fallback text"}]}
        assert AgnesLLMEnhancer._extract_rewritten_query(data) == "fallback text"

    def test_choices_dict_without_message(self):
        data = {"choices": [{"text": "t"}]}
        assert AgnesLLMEnhancer._extract_rewritten_query(data) == "t"

    def test_rewritten_query_in_choices_dict_but_not_string(self):
        content = json.dumps({"rewritten_query": 123})
        data = {"choices": [{"message": {"content": content}}]}
        # parsed["rewritten_query"] is int, not str
        assert AgnesLLMEnhancer._extract_rewritten_query(data) is None
