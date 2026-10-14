"""零依赖的 MCP Streamable HTTP 客户端。

只用标准库：``urllib.request`` / ``json`` / ``itertools`` / ``urllib.error``。

需要处理的关键点（这些是踩过才知道的地方）：

1. **三步握手**：``initialize`` → ``notifications/initialized`` → ``tools/call``。
   少了第二步，有些服务会拒绝后续调用。
2. **响应双形态**：``Content-Type`` 可能是 ``application/json``（一次性 JSON-RPC
   对象），也可能是 ``text/event-stream``（SSE）。必须分流解析。
3. **``Accept`` 必须同时含** ``application/json`` 与 ``text/event-stream``。
4. **Session 回传**：``initialize`` 响应头可能带 ``Mcp-Session-Id``，
   后续请求必须回传同一个值。
5. **Token 脱敏**：任何异常信息都必须先做掩码，保证 401 报错与 traceback 里
   都不出现明文 Token。
6. **限流**：429 不做重试风暴，直接抛出带说明的异常。
"""

from __future__ import annotations

import itertools
import json
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

from . import __version__
from .constants import (
    DEFAULT_ENDPOINT,
    DEFAULT_TIMEOUT_SECONDS,
    MAX_REQUESTS_PER_MINUTE,
    PROTOCOL_VERSION,
)
from .errors import AuthError, McpError, RateLimitError, ToolError
from .rules import assert_tool_allowed

CLIENT_NAME = "mcd-blind-box"
ACCEPT_HEADER = "application/json, text/event-stream"


def mask_token(token: Optional[str]) -> str:
    """把 Token 掩码成可安全打印的形式。"""
    if not token:
        return "<未提供>"
    if len(token) <= 8:
        return "****"
    return "****" + token[-4:]


def parse_sse(text: str) -> List[Any]:
    """解析 SSE 文本为 JSON 事件列表。

    规则：``data:`` 行累积（多行用 ``\\n`` 连接），空行提交一个事件；
    ``:`` 开头的注释行与 ``event:`` / ``id:`` / ``retry:`` 行不作为数据。
    """
    events: List[Any] = []
    data_lines: List[str] = []

    def flush() -> None:
        if not data_lines:
            return
        payload = "\n".join(data_lines)
        data_lines.clear()
        try:
            events.append(json.loads(payload))
        except json.JSONDecodeError:
            # 非 JSON 的 data 段（例如纯文本心跳）忽略，不影响主流程
            return

    for raw_line in text.splitlines():
        line = raw_line.rstrip("\r")
        if line.startswith(":"):
            continue
        if line == "":
            flush()
            continue
        if line.startswith("data:"):
            data_lines.append(line[len("data:") :].lstrip())
            continue
        # event: / id: / retry: 等字段与数据无关，跳过
    flush()
    return events


def parse_content(result: Dict[str, Any]) -> Dict[str, Any]:
    """解析 ``tools/call`` 的 result。

    优先 ``structuredContent``；否则拼接 ``content`` 里 ``type == "text"`` 的文本
    再尝试 ``json.loads``。解析不出 JSON 时退化成 ``{"text": ...}``，
    让上层至少能拿到原文而不是空对象。
    """
    structured = result.get("structuredContent")
    if isinstance(structured, dict) and structured:
        return structured

    parts = result.get("content") or []
    chunks: List[str] = []
    if isinstance(parts, list):
        for part in parts:
            if isinstance(part, dict) and part.get("type") == "text":
                chunks.append(str(part.get("text", "")))
    text = "\n".join(chunk for chunk in chunks if chunk)

    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {"text": text}
    if isinstance(parsed, dict):
        return parsed
    return {"data": parsed}


class StreamableHttpTransport:
    """最小可用的 Streamable HTTP 传输层。"""

    def __init__(
        self,
        url: str = DEFAULT_ENDPOINT,
        token: Optional[str] = None,
        *,
        timeout: int = DEFAULT_TIMEOUT_SECONDS,
        protocol_version: str = PROTOCOL_VERSION,
    ) -> None:
        self.url = url
        self.token = token
        self.timeout = timeout
        self.protocol_version = protocol_version
        self.session_id: Optional[str] = None
        self._ids = itertools.count(1)
        self._initialized = False

    # -- 内部 ---------------------------------------------------------------
    def _redact(self, text: str) -> str:
        """把可能出现的明文 Token 替换掉。"""
        if not text or not self.token:
            return text
        return text.replace(self.token, mask_token(self.token))

    def _headers(self) -> Dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": ACCEPT_HEADER,
            "MCP-Protocol-Version": self.protocol_version,
            "User-Agent": f"{CLIENT_NAME}/{__version__}",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        return headers

    def _raise_http_error(self, exc: urllib.error.HTTPError) -> McpError:
        status = getattr(exc, "code", None)
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")[:200]
        except Exception:  # pragma: no cover - 读取失败不影响判定
            detail = ""
        detail = self._redact(detail)

        suffix = f"；详情：{detail}" if detail else ""

        if status == 401:
            return AuthError(
                f"MCP 鉴权失败（HTTP 401）：Token 无效、已过期或未提供。"
                f"当前 Token={mask_token(self.token)}，请检查环境变量配置{suffix}"
            )
        if status == 429:
            return RateLimitError(
                f"触发限流（HTTP 429）：每个 Token 每分钟最多 {MAX_REQUESTS_PER_MINUTE} "
                f"次请求，请降低请求频率后重试{suffix}"
            )
        return McpError(f"MCP 请求失败（HTTP {status}）{suffix}")

    def _post(self, payload: Dict[str, Any]) -> List[Any]:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            self.url, data=body, headers=self._headers(), method="POST"
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read()
                content_type = response.headers.get("Content-Type", "") or ""
                session = response.headers.get("Mcp-Session-Id")
                if session:
                    self.session_id = session
        except urllib.error.HTTPError as exc:
            raise self._raise_http_error(exc) from None
        except urllib.error.URLError as exc:
            reason = self._redact(str(getattr(exc, "reason", exc)))
            raise McpError(f"无法连接 {self.url}：{reason}") from None
        except TimeoutError:
            raise McpError(f"连接 {self.url} 超时（{self.timeout}s）") from None

        if not raw:
            return []

        text = raw.decode("utf-8", "replace")
        if "text/event-stream" in content_type.lower():
            return parse_sse(text)
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return parse_sse(text)
        return parsed if isinstance(parsed, list) else [parsed]

    # -- 公开 API -----------------------------------------------------------
    def initialize(self) -> Dict[str, Any]:
        """三步握手的第 1、2 步。"""
        result = self.call(
            "initialize",
            {
                "protocolVersion": self.protocol_version,
                "capabilities": {},
                "clientInfo": {"name": CLIENT_NAME, "version": __version__},
            },
        )
        # 第 2 步：通知（无 id，服务端不返回响应体）
        self.call("notifications/initialized", None, notify=True)
        self._initialized = True
        return result or {}

    def ensure_initialized(self) -> None:
        if not self._initialized:
            self.initialize()

    def call(
        self,
        method: str,
        params: Optional[Dict[str, Any]] = None,
        *,
        notify: bool = False,
    ) -> Optional[Dict[str, Any]]:
        payload: Dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params

        if notify:
            self._post(payload)
            return None

        request_id = next(self._ids)
        payload["id"] = request_id
        messages = self._post(payload)

        for message in messages:
            if not isinstance(message, dict):
                continue
            if message.get("id") != request_id:
                continue
            error = message.get("error")
            if error:
                detail = error.get("message") if isinstance(error, dict) else str(error)
                raise ToolError(f"{method} 返回错误：{self._redact(str(detail))}")
            result = message.get("result")
            return result if isinstance(result, dict) else {"data": result}

        raise McpError(f"{method} 未返回匹配的响应（id={request_id}）")

    def list_tools(self) -> List[Dict[str, Any]]:
        self.ensure_initialized()
        result = self.call("tools/list") or {}
        tools = result.get("tools") or []
        return [t for t in tools if isinstance(t, dict)]

    def call_tool(self, name: str, arguments: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        assert_tool_allowed(name)
        self.ensure_initialized()
        result = self.call("tools/call", {"name": name, "arguments": dict(arguments or {})})
        if result is None:
            return {}
        if result.get("isError"):
            raise ToolError(f"工具 {name} 返回错误：{self._redact(json.dumps(result, ensure_ascii=False)[:300])}")
        return parse_content(result)

    def close(self) -> None:  # pragma: no cover - urllib 无需显式关闭
        self._initialized = False
