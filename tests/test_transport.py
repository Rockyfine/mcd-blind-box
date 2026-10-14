"""传输层的测试。

这里用一个假的 ``urlopen`` 顶上，所以测试**完全离线**、不需要 Token、不碰网络。
重点验证四件事：

1. 三步握手真的发了（``initialize`` + ``notifications/initialized``）；
2. ``application/json`` 与 ``text/event-stream`` 两种响应都能解析；
3. 401 / 429 映射成专门的异常类型；
4. **明文 Token 绝不出现在任何异常信息里**。
"""

from __future__ import annotations

import io
import json
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcd_blind_box import transport as transport_mod  # noqa: E402
from mcd_blind_box.errors import (  # noqa: E402
    AuthError,
    InvalidArgument,
    McpError,
    RateLimitError,
    ToolError,
)
from mcd_blind_box.mcp_client import McpBackend  # noqa: E402
from mcd_blind_box.transport import (  # noqa: E402
    ACCEPT_HEADER,
    StreamableHttpTransport,
    mask_token,
    parse_content,
    parse_sse,
)

# 合成占位 Token —— 只为验证掩码逻辑，**绝不是真实凭证**。
# 真实 Token 只从环境变量 MCD_MCP_TOKEN 读取，任何固化的凭证都不该进仓库。
TOKEN = "ZZZ0FakeTestToken000NoRealCredential00"
ENDPOINT = "https://mcp.mcd.cn"


# ---------------------------------------------------------------------------
# 假的 urlopen
# ---------------------------------------------------------------------------
class FakeHeaders:
    def __init__(self, mapping=None):
        self._mapping = dict(mapping or {})

    def get(self, key, default=None):
        for name, value in self._mapping.items():
            if name.lower() == key.lower():
                return value
        return default


class FakeResponse:
    def __init__(self, body: bytes, content_type="application/json", session=None):
        self._body = body
        self.headers = FakeHeaders({"Content-Type": content_type})
        if session:
            self.headers._mapping["Mcp-Session-Id"] = session

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeServer:
    """按 method 返回 JSON-RPC 响应的假服务端。"""

    def __init__(self, results=None, content_type="application/json", session=None):
        self.results = dict(results or {})
        self.content_type = content_type
        self.session = session
        self.requests = []
        self.headers_seen = []

    def __call__(self, request, timeout=None):
        payload = json.loads((request.data or b"{}").decode("utf-8"))
        self.requests.append(payload)
        self.headers_seen.append(dict(request.headers))
        method = payload.get("method")
        if "id" not in payload:  # notification，服务端不回响应体
            return FakeResponse(b"", self.content_type)
        result = self.results.get(method, {})
        body = json.dumps({"jsonrpc": "2.0", "id": payload["id"], "result": result})
        return FakeResponse(body.encode("utf-8"), self.content_type, self.session)

    @property
    def methods(self):
        return [r.get("method") for r in self.requests]


def patch_urlopen(server):
    return mock.patch.object(transport_mod.urllib.request, "urlopen", server)


def sse(events) -> bytes:
    chunks = []
    for event in events:
        chunks.append("data: " + json.dumps(event))
        chunks.append("")
    return ("\n".join(chunks) + "\n").encode("utf-8")


# ---------------------------------------------------------------------------
class TestMaskToken(unittest.TestCase):
    def test_missing(self):
        self.assertEqual(mask_token(None), "<未提供>")
        self.assertEqual(mask_token(""), "<未提供>")

    def test_short_token_fully_masked(self):
        self.assertEqual(mask_token("abc"), "****")
        self.assertEqual(mask_token("12345678"), "****")

    def test_long_token_keeps_last_four(self):
        self.assertEqual(mask_token("123456789"), "****6789")

    def test_real_shape(self):
        masked = mask_token(TOKEN)
        self.assertTrue(masked.startswith("****"))
        self.assertEqual(len(masked), 8)
        self.assertNotIn(TOKEN, masked)


class TestParseSse(unittest.TestCase):
    def test_single_event(self):
        self.assertEqual(parse_sse('data: {"a": 1}\n\n'), [{"a": 1}])

    def test_multiple_events(self):
        text = 'data: {"a": 1}\n\ndata: {"b": 2}\n\n'
        self.assertEqual(parse_sse(text), [{"a": 1}, {"b": 2}])

    def test_comment_lines_are_skipped(self):
        text = ': keep-alive\n\ndata: {"a": 1}\n\n'
        self.assertEqual(parse_sse(text), [{"a": 1}])

    def test_event_and_id_fields_are_skipped(self):
        text = 'event: message\nid: 7\nretry: 1000\ndata: {"a": 1}\n\n'
        self.assertEqual(parse_sse(text), [{"a": 1}])

    def test_multi_line_data_is_joined(self):
        text = 'data: {"a":\ndata: 1}\n\n'
        self.assertEqual(parse_sse(text), [{"a": 1}])

    def test_non_json_data_is_ignored(self):
        self.assertEqual(parse_sse("data: not-json\n\n"), [])

    def test_carriage_returns_are_tolerated(self):
        self.assertEqual(parse_sse('data: {"a": 1}\r\n\r\n'), [{"a": 1}])

    def test_empty_input(self):
        self.assertEqual(parse_sse(""), [])

    def test_data_without_value_is_ignored(self):
        self.assertEqual(parse_sse("data: \n\n"), [])


class TestParseContent(unittest.TestCase):
    def test_structured_content_wins(self):
        result = {
            "structuredContent": {"meals": [1]},
            "content": [{"type": "text", "text": '{"ignored": true}'}],
        }
        self.assertEqual(parse_content(result), {"meals": [1]})

    def test_empty_structured_content_falls_through(self):
        result = {"structuredContent": {}, "content": [{"type": "text", "text": '{"a":1}'}]}
        self.assertEqual(parse_content(result), {"a": 1})

    def test_text_parts_are_parsed(self):
        result = {"content": [{"type": "text", "text": '{"a": 1}'}]}
        self.assertEqual(parse_content(result), {"a": 1})

    def test_multiple_text_parts_are_joined(self):
        result = {"content": [{"type": "text", "text": '{"a":'}, {"type": "text", "text": "1}"}]}
        self.assertEqual(parse_content(result), {"a": 1})

    def test_non_text_parts_are_skipped(self):
        result = {"content": [{"type": "image", "data": "xxx"}]}
        self.assertEqual(parse_content(result), {})

    def test_non_json_text_degrades_to_text(self):
        result = {"content": [{"type": "text", "text": "hello"}]}
        self.assertEqual(parse_content(result), {"text": "hello"})

    def test_json_list_is_wrapped(self):
        result = {"content": [{"type": "text", "text": "[1,2]"}]}
        self.assertEqual(parse_content(result), {"data": [1, 2]})

    def test_empty_result(self):
        self.assertEqual(parse_content({}), {})


class TestHeaders(unittest.TestCase):
    def test_accept_has_both_content_types(self):
        headers = StreamableHttpTransport(ENDPOINT, TOKEN)._headers()
        self.assertEqual(headers["Accept"], ACCEPT_HEADER)
        self.assertIn("application/json", headers["Accept"])
        self.assertIn("text/event-stream", headers["Accept"])

    def test_authorization_uses_bearer(self):
        headers = StreamableHttpTransport(ENDPOINT, TOKEN)._headers()
        self.assertEqual(headers["Authorization"], f"Bearer {TOKEN}")

    def test_no_token_no_authorization_header(self):
        self.assertNotIn("Authorization", StreamableHttpTransport(ENDPOINT)._headers())

    def test_session_id_added_only_after_set(self):
        transport = StreamableHttpTransport(ENDPOINT, TOKEN)
        self.assertNotIn("Mcp-Session-Id", transport._headers())
        transport.session_id = "S123"
        self.assertEqual(transport._headers()["Mcp-Session-Id"], "S123")

    def test_protocol_version_header(self):
        from mcd_blind_box.constants import PROTOCOL_VERSION

        self.assertEqual(
            StreamableHttpTransport(ENDPOINT, TOKEN)._headers()["MCP-Protocol-Version"],
            PROTOCOL_VERSION,
        )


class TestRedaction(unittest.TestCase):
    def test_token_is_replaced(self):
        transport = StreamableHttpTransport(ENDPOINT, TOKEN)
        self.assertNotIn(TOKEN, transport._redact(f"bad {TOKEN} bad"))
        self.assertIn("****", transport._redact(f"bad {TOKEN} bad"))

    def test_no_token_no_change(self):
        transport = StreamableHttpTransport(ENDPOINT)
        self.assertEqual(transport._redact("plain"), "plain")

    def test_empty_text(self):
        transport = StreamableHttpTransport(ENDPOINT, TOKEN)
        self.assertEqual(transport._redact(""), "")


class TestHttpErrorMapping(unittest.TestCase):
    def _error(self, code, body=b'{"message":"boom"}'):
        return urllib.error.HTTPError(ENDPOINT, code, "err", FakeHeaders(), io.BytesIO(body))

    def test_401_is_auth_error(self):
        transport = StreamableHttpTransport(ENDPOINT, TOKEN)
        exc = transport._raise_http_error(self._error(401))
        self.assertIsInstance(exc, AuthError)
        self.assertIn("401", str(exc))

    def test_401_message_masks_token(self):
        transport = StreamableHttpTransport(ENDPOINT, TOKEN)
        exc = transport._raise_http_error(self._error(401, TOKEN.encode()))
        self.assertNotIn(TOKEN, str(exc))
        self.assertIn("****", str(exc))

    def test_429_is_rate_limit_error(self):
        transport = StreamableHttpTransport(ENDPOINT, TOKEN)
        exc = transport._raise_http_error(self._error(429))
        self.assertIsInstance(exc, RateLimitError)
        self.assertIn("600", str(exc))

    def test_other_status_is_generic(self):
        transport = StreamableHttpTransport(ENDPOINT, TOKEN)
        exc = transport._raise_http_error(self._error(500))
        self.assertIsInstance(exc, McpError)
        self.assertNotIsInstance(exc, AuthError)

    def test_body_is_redacted(self):
        transport = StreamableHttpTransport(ENDPOINT, TOKEN)
        exc = transport._raise_http_error(self._error(500, f'{{"t":"{TOKEN}"}}'.encode()))
        self.assertNotIn(TOKEN, str(exc))


class TestCall(unittest.TestCase):
    def test_successful_json_call(self):
        server = FakeServer({"tools/list": {"tools": [{"name": "query-meals"}]}})
        transport = StreamableHttpTransport(ENDPOINT, TOKEN)
        with patch_urlopen(server):
            result = transport.call("tools/list")
        self.assertEqual(result["tools"][0]["name"], "query-meals")

    def test_sse_response_is_parsed(self):
        payload = {"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}
        body = sse([payload])
        transport = StreamableHttpTransport(ENDPOINT, TOKEN)
        with patch_urlopen(lambda req, timeout=None: FakeResponse(body, "text/event-stream")):
            result = transport.call("tools/list")
        self.assertEqual(result, {"ok": True})

    def test_json_response_with_sse_content_type(self):
        payload = {"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}
        body = sse([payload])
        transport = StreamableHttpTransport(ENDPOINT, TOKEN)
        with patch_urlopen(lambda req, timeout=None: FakeResponse(body, "text/event-stream")):
            self.assertEqual(transport.call("x"), {"ok": True})

    def test_jsonrpc_error_becomes_tool_error(self):
        body = json.dumps(
            {"jsonrpc": "2.0", "id": 1, "error": {"code": -32000, "message": "nope"}}
        ).encode()
        transport = StreamableHttpTransport(ENDPOINT, TOKEN)
        with patch_urlopen(lambda req, timeout=None: FakeResponse(body)):
            with self.assertRaises(ToolError) as ctx:
                transport.call("tools/call")
        self.assertIn("nope", str(ctx.exception))

    def test_error_message_is_redacted(self):
        body = json.dumps(
            {"jsonrpc": "2.0", "id": 1, "error": {"message": f"leak {TOKEN}"}}
        ).encode()
        transport = StreamableHttpTransport(ENDPOINT, TOKEN)
        with patch_urlopen(lambda req, timeout=None: FakeResponse(body)):
            with self.assertRaises(ToolError) as ctx:
                transport.call("tools/call")
        self.assertNotIn(TOKEN, str(ctx.exception))

    def test_empty_body_raises(self):
        transport = StreamableHttpTransport(ENDPOINT, TOKEN)
        with patch_urlopen(lambda req, timeout=None: FakeResponse(b"")):
            with self.assertRaises(McpError):
                transport.call("tools/list")

    def test_mismatched_id_raises(self):
        body = json.dumps({"jsonrpc": "2.0", "id": 999, "result": {}}).encode()
        transport = StreamableHttpTransport(ENDPOINT, TOKEN)
        with patch_urlopen(lambda req, timeout=None: FakeResponse(body)):
            with self.assertRaises(McpError):
                transport.call("tools/list")

    def test_http_401_raises_auth_error(self):
        def raise_401(request, timeout=None):
            raise urllib.error.HTTPError(
                ENDPOINT, 401, "unauthorized", FakeHeaders(), io.BytesIO(b"denied")
            )

        transport = StreamableHttpTransport(ENDPOINT, TOKEN)
        with patch_urlopen(raise_401):
            with self.assertRaises(AuthError):
                transport.call("tools/list")

    def test_http_429_raises_rate_limit(self):
        def raise_429(request, timeout=None):
            raise urllib.error.HTTPError(
                ENDPOINT, 429, "slow down", FakeHeaders(), io.BytesIO(b"")
            )

        transport = StreamableHttpTransport(ENDPOINT, TOKEN)
        with patch_urlopen(raise_429):
            with self.assertRaises(RateLimitError):
                transport.call("tools/list")

    def test_url_error_becomes_mcp_error(self):
        def raise_urlerror(request, timeout=None):
            raise urllib.error.URLError("dns fail")

        transport = StreamableHttpTransport(ENDPOINT, TOKEN)
        with patch_urlopen(raise_urlerror):
            with self.assertRaises(McpError):
                transport.call("tools/list")

    def test_timeout_becomes_mcp_error(self):
        def raise_timeout(request, timeout=None):
            raise TimeoutError()

        transport = StreamableHttpTransport(ENDPOINT, TOKEN)
        with patch_urlopen(raise_timeout):
            with self.assertRaises(McpError):
                transport.call("tools/list")


class TestInitialize(unittest.TestCase):
    def test_three_step_handshake(self):
        server = FakeServer({"initialize": {"protocolVersion": "2025-03-26"}})
        transport = StreamableHttpTransport(ENDPOINT, TOKEN)
        with patch_urlopen(server):
            transport.initialize()

        self.assertEqual(server.methods[0], "initialize")
        self.assertEqual(server.methods[1], "notifications/initialized")
        self.assertTrue(transport._initialized)

    def test_notification_has_no_id(self):
        server = FakeServer()
        transport = StreamableHttpTransport(ENDPOINT, TOKEN)
        with patch_urlopen(server):
            transport.initialize()
        self.assertNotIn("id", server.requests[1])

    def test_session_id_is_captured_and_resent(self):
        server = FakeServer({"initialize": {}}, session="SESS-1")
        transport = StreamableHttpTransport(ENDPOINT, TOKEN)
        with patch_urlopen(server):
            transport.initialize()
            transport.call("tools/list")
        self.assertEqual(transport.session_id, "SESS-1")
        self.assertEqual(server.headers_seen[-1].get("Mcp-session-id"), "SESS-1")

    def test_ensure_initialized_only_once(self):
        server = FakeServer({"initialize": {}})
        transport = StreamableHttpTransport(ENDPOINT, TOKEN)
        with patch_urlopen(server):
            transport.ensure_initialized()
            transport.ensure_initialized()
        self.assertEqual(server.methods.count("initialize"), 1)

    def test_list_tools_triggers_initialize(self):
        server = FakeServer({"initialize": {}, "tools/list": {"tools": []}})
        transport = StreamableHttpTransport(ENDPOINT, TOKEN)
        with patch_urlopen(server):
            transport.list_tools()
        self.assertIn("initialize", server.methods)


class TestCallTool(unittest.TestCase):
    def _server(self, result, is_error=False):
        payload = dict(result)
        if is_error:
            payload["isError"] = True
        return FakeServer({"initialize": {}, "tools/call": payload})

    def test_returns_parsed_content(self):
        server = self._server({"structuredContent": {"meals": [1, 2]}})
        transport = StreamableHttpTransport(ENDPOINT, TOKEN)
        with patch_urlopen(server):
            self.assertEqual(transport.call_tool("query-meals", {}), {"meals": [1, 2]})

    def test_tool_error_is_raised(self):
        server = self._server({"content": [{"type": "text", "text": "bad"}]}, is_error=True)
        transport = StreamableHttpTransport(ENDPOINT, TOKEN)
        with patch_urlopen(server):
            with self.assertRaises(ToolError):
                transport.call_tool("query-meals", {})

    def test_disallowed_tool_name_is_rejected_before_any_io(self):
        transport = StreamableHttpTransport(ENDPOINT, TOKEN)
        with patch_urlopen(FakeServer()):
            with self.assertRaises(InvalidArgument):
                transport.call_tool("query-nearby-store", {})

    def test_arguments_are_forwarded(self):
        server = self._server({"structuredContent": {"ok": True}})
        transport = StreamableHttpTransport(ENDPOINT, TOKEN)
        with patch_urlopen(server):
            transport.call_tool("query-meals", {"storeCode": "100001"})
        call = [r for r in server.requests if r.get("method") == "tools/call"][0]
        self.assertEqual(call["params"]["name"], "query-meals")
        self.assertEqual(call["params"]["arguments"]["storeCode"], "100001")


class TestProbe(unittest.TestCase):
    def test_probe_masks_token_and_counts_tools(self):
        tools = [{"name": f"t{i}"} for i in range(35)]
        server = FakeServer(
            {
                "initialize": {
                    "protocolVersion": "2025-03-26",
                    "serverInfo": {"name": "mcd-mcp", "version": "1.0.0"},
                },
                "tools/list": {"tools": tools},
            },
            session="S1",
        )
        backend = McpBackend(ENDPOINT, TOKEN)
        with patch_urlopen(server):
            info = backend.probe()

        self.assertEqual(info["token"], "****" + TOKEN[-4:])
        self.assertNotIn(TOKEN, json.dumps(info))
        self.assertEqual(info["toolCount"], 35)
        self.assertTrue(info["toolCountMatches"])
        self.assertTrue(info["sessionIdPresent"])
        self.assertEqual(info["serverName"], "mcd-mcp")
        self.assertEqual(info["protocolVersion"], "2025-03-26")

    def test_probe_reports_count_mismatch(self):
        server = FakeServer(
            {"initialize": {"serverInfo": {}}, "tools/list": {"tools": [{"name": "x"}]}}
        )
        backend = McpBackend(ENDPOINT, TOKEN)
        with patch_urlopen(server):
            info = backend.probe()
        self.assertFalse(info["toolCountMatches"])
        self.assertEqual(info["serverVersion"], "unknown")


if __name__ == "__main__":
    unittest.main(verbosity=2)
