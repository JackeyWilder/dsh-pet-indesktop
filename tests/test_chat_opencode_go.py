# -*- coding: utf-8 -*-
"""OpenCode Go（opencode.ai）端点支持回归（issue #368）。

Go 端点要求每个请求带稳定的 ``x-opencode-session``：缺了就在鉴权之前直接
400 ``MissingSessionID``（"Request is missing x-opencode-session and cannot
be routed efficiently"），带有效 Key 也过不去。另外它的 Model ID 是**裸 id**
（``deepseek-v4.1-flash``），配置里沿用 opencode 自身写法
``opencode-go/<id>`` 会被当成不存在的模型。

本文件锁住三件事：

1. 会话头只发给 ``opencode.ai`` 域名下的端点（含文档里的
   ``/inference/go/openai/v1`` 与 issue 里的 ``/zen/go/v1``），进程内稳定；
2. 其它 provider（含形近域名）一个字节都不变——这是"零影响"那条红线；
3. 模型前缀只在 opencode.ai 上剥离，且聊天/流式/视觉/翻译四条真实发包路径
   都走同一套端点头与模型解析（opencode 文档点名的问题客户端症状就是
   "session information arrives on some model paths, but is missing on others"）。

全部离线：``urllib.request.urlopen`` 一律被替换成记录请求的假实现。
"""

from __future__ import annotations

import http.client
import io
import json
import threading
import urllib.error

import pytest

from pet.chat.models import ProviderConfig
from pet.chat.providers import (
    OpenAICompatibleProvider,
    endpoint_headers,
    endpoint_model,
)
# 断言名不能以 test_ 开头：pytest 会把模块级同名对象当成测试函数收集
from pet.chat.providers import test_connection as probe_connection

GO_BASE = "https://opencode.ai/zen/go/v1"
ZEN_BASE = "https://opencode.ai/zen/v1"
DOC_BASE = "https://opencode.ai/inference/go/openai/v1"
SESSION_HEADER = "x-opencode-session"

# 官方 Endpoints 表里的直连写法（裸 model id），可直接当断言目标
GO_MODEL = "deepseek-v4.1-flash"
GO_MODEL_CONFIGURED = "opencode-go/deepseek-v4.1-flash"


class _FakeResponse:
    """够用的 urlopen 返回值：上下文管理器 + read + status。"""

    def __init__(self, body: bytes = b"{}", status: int = 200):
        self._body = body
        self.status = status

    def read(self, amount: int | None = None) -> bytes:
        if amount is None or amount >= len(self._body):
            body, self._body = self._body, b""
            return body
        chunk, self._body = self._body[:amount], self._body[amount:]
        return chunk

    def close(self) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


def _header_map(request) -> dict[str, str]:
    """urllib 会把头名按 .capitalize() 规整，断言前统一转小写。"""
    return {key.lower(): value for key, value in request.header_items()}


def _capture_requests(monkeypatch, response_factory=None):
    """替换 urlopen，返回 (已发请求列表, 恢复函数)；response_factory(req) 可定制响应。"""
    sent: list = []

    def fake_urlopen(request, timeout=None, context=None):
        sent.append(request)
        if response_factory is not None:
            return response_factory(request)
        return _FakeResponse()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    return sent


def _json_body(request) -> dict:
    return json.loads(request.data.decode("utf-8"))


def _chat_completion(content: str) -> bytes:
    return json.dumps(
        {"choices": [{"message": {"role": "assistant", "content": content}}]},
        ensure_ascii=False,
    ).encode("utf-8")


# --------------------------------------------------------------------------
# 端点头：只发 opencode.ai，且进程内稳定
# --------------------------------------------------------------------------


@pytest.mark.parametrize("base_url", [GO_BASE, ZEN_BASE, DOC_BASE])
def test_opencode_endpoints_carry_session_header(base_url):
    headers = endpoint_headers(base_url, {"Content-Type": "application/json"})
    session = headers.get(SESSION_HEADER, "")
    assert session, "opencode.ai 端点必须带 x-opencode-session"
    assert session == endpoint_headers(base_url)[SESSION_HEADER], (
        "同一进程内会话 ID 必须稳定：每次请求换 ID 等于把每个请求都当成新会话"
    )


def test_session_header_token_is_header_safe():
    """会话 ID 直接进 HTTP 头，必须是可见 ASCII（uuid4().hex 的形状）。"""
    session = endpoint_headers(GO_BASE)[SESSION_HEADER]
    assert len(session) >= 16
    assert all(ch in "0123456789abcdef" for ch in session), session


@pytest.mark.parametrize(
    "base_url",
    [
        "https://api.deepseek.com",
        "https://api.openai.com/v1",
        "https://open.bigmodel.cn/api/paas/v4",
        # 形近域名：suffix 匹配写成裸 endswith('opencode.ai') 会误伤这两个
        "https://notopencode.ai/v1",
        "https://opencode.ai.example.com/v1",
        "",
        None,
    ],
)
def test_non_opencode_endpoints_get_no_session_header(base_url):
    assert SESSION_HEADER not in {key.lower() for key in endpoint_headers(base_url)}


def test_endpoint_headers_keeps_call_site_overrides():
    """调用点自己的头（Accept/Content-Type）不能被端点头盖掉。"""
    headers = endpoint_headers(
        GO_BASE, {"Content-Type": "application/json", "Accept": "text/event-stream"}
    )
    assert headers["Accept"] == "text/event-stream"
    assert headers["Content-Type"] == "application/json"
    assert headers["User-Agent"].startswith("Mozilla/5.0")


# --------------------------------------------------------------------------
# 模型 ID：opencode 配置写法 → HTTP 裸 id
# --------------------------------------------------------------------------


@pytest.mark.parametrize("base_url", [GO_BASE, ZEN_BASE, DOC_BASE])
def test_go_model_prefix_stripped_for_opencode(base_url):
    assert endpoint_model(base_url, GO_MODEL_CONFIGURED) == GO_MODEL
    assert endpoint_model(base_url, GO_MODEL) == GO_MODEL


@pytest.mark.parametrize("base_url", ["https://api.deepseek.com", "https://notopencode.ai/v1"])
def test_model_untouched_for_other_providers(base_url):
    """其它 provider 的 model 原样发出去（红线：零影响）。"""
    assert endpoint_model(base_url, GO_MODEL_CONFIGURED) == GO_MODEL_CONFIGURED
    assert endpoint_model(base_url, "deepseek-v4-flash") == "deepseek-v4-flash"


# --------------------------------------------------------------------------
# 真实发包路径 1/4：test_connection（设置页"测试连接"）
# --------------------------------------------------------------------------


def test_test_connection_sends_session_header_and_bare_model(monkeypatch):
    sent = _capture_requests(monkeypatch)
    config = ProviderConfig("go", base_url=GO_BASE, model=GO_MODEL_CONFIGURED, api_key="sk-test")

    ok, message = probe_connection(config)

    assert ok is True, message
    assert len(sent) == 1
    headers = _header_map(sent[0])
    assert headers[SESSION_HEADER]
    assert headers["authorization"] == "Bearer sk-test"
    body = _json_body(sent[0])
    assert body["model"] == GO_MODEL
    assert body["stream"] is False


def test_test_connection_leaves_other_providers_untouched(monkeypatch):
    sent = _capture_requests(monkeypatch)
    config = ProviderConfig("ds", base_url="https://api.deepseek.com", model="deepseek-v4-flash", api_key="sk-test")

    probe_connection(config)

    headers = _header_map(sent[0])
    assert SESSION_HEADER not in headers
    assert _json_body(sent[0])["model"] == "deepseek-v4-flash"


def test_test_connection_reports_402_style_go_auth_failure(monkeypatch):
    """带齐头之后 Go 端点的无效 Key 是 401 Invalid credential，要报成认证失败。"""
    detail = b'{"error":{"type":"authentication_error","message":"Invalid credential"}}'

    def fake_urlopen(request, timeout=None, context=None):
        raise urllib.error.HTTPError(
            request.full_url, 401, "Unauthorized", http.client.HTTPMessage(), io.BytesIO(detail)
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    config = ProviderConfig("go", base_url=GO_BASE, model=GO_MODEL_CONFIGURED, api_key="sk-bad")

    ok, message = probe_connection(config)

    assert ok is False
    assert message == "认证失败（HTTP 401）：Invalid credential"


# --------------------------------------------------------------------------
# 真实发包路径 2/4：OpenAICompatibleProvider.stream（聊天回答）
# --------------------------------------------------------------------------


def test_stream_answer_sends_session_header_and_bare_model(monkeypatch):
    """真实流式路径：_open 自己还原模型名，头也在这里带上。"""
    sse = (
        b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n'
        b"data: [DONE]\n\n"
    )
    sent = _capture_requests(monkeypatch, lambda request: _FakeResponse(sse))
    provider = OpenAICompatibleProvider()
    config = ProviderConfig("go", base_url=GO_BASE, model=GO_MODEL_CONFIGURED, api_key="sk-test")

    events = list(
        provider._stream_answer(
            [{"role": "user", "content": "ping"}], config, threading.Event(), None
        )
    )

    assert "hi" in events
    headers = _header_map(sent[0])
    assert headers[SESSION_HEADER]
    assert headers["accept"] == "text/event-stream"
    assert _json_body(sent[0])["model"] == GO_MODEL


def test_open_sends_session_header_for_go(monkeypatch):
    """_open 是流式/非流式共同出口：头必须在这里就带上。"""
    sent = _capture_requests(monkeypatch)
    provider = OpenAICompatibleProvider()
    config = ProviderConfig("go", base_url=GO_BASE, model=GO_MODEL_CONFIGURED, api_key="sk-test")

    response = provider._open({"model": GO_MODEL, "stream": True}, config, "text/event-stream")

    response.close()
    headers = _header_map(sent[0])
    assert headers[SESSION_HEADER]
    assert headers["accept"] == "text/event-stream"
    assert headers["authorization"] == "Bearer sk-test"


def test_open_leaves_other_providers_untouched(monkeypatch):
    sent = _capture_requests(monkeypatch)
    provider = OpenAICompatibleProvider()
    config = ProviderConfig("ds", base_url="https://api.deepseek.com", model="deepseek-v4-flash")

    provider._open({"model": "deepseek-v4-flash", "stream": True}, config, "text/event-stream").close()

    assert SESSION_HEADER not in _header_map(sent[0])


# --------------------------------------------------------------------------
# 真实发包路径 3/4：截图问答（pet/vision.py）
# --------------------------------------------------------------------------


def test_vision_request_sends_session_header_for_go(monkeypatch):
    from pet import vision

    sent = _capture_requests(
        monkeypatch, lambda request: _FakeResponse(_chat_completion("看到了"))
    )
    config = ProviderConfig("go", base_url=GO_BASE, model=GO_MODEL_CONFIGURED, api_key="sk-test")

    answer = vision._post_vision_request(b"\xff\xd8jpeg", "记事本", "你是桌宠", config)

    assert answer == "看到了"
    headers = _header_map(sent[0])
    assert headers[SESSION_HEADER]
    assert headers["authorization"] == "Bearer sk-test"
    assert _json_body(sent[0])["model"] == GO_MODEL


# --------------------------------------------------------------------------
# 真实发包路径 4/4：截图翻译（pet/screen_capture/translate.py）
# --------------------------------------------------------------------------


def test_translate_request_sends_session_header_for_go(monkeypatch):
    from pet.screen_capture import translate, vision_bridge

    sent = _capture_requests(
        monkeypatch, lambda request: _FakeResponse(_chat_completion("hello"))
    )
    config = ProviderConfig("go", base_url=GO_BASE, model=GO_MODEL_CONFIGURED, api_key="sk-test")
    monkeypatch.setattr(
        vision_bridge, "resolve_chat_context", lambda cfg: (config, "system prompt")
    )

    translated = translate.translate_text("你好", object())

    assert translated == "hello"
    headers = _header_map(sent[0])
    assert headers[SESSION_HEADER]
    assert _json_body(sent[0])["model"] == GO_MODEL
