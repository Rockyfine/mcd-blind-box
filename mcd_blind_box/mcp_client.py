"""联网数据源：把 ``mcd-mcp`` 接成 Backend。

刻意做得很薄 —— 它只负责「把 ``call_tool(name, args)`` 转发到传输层」。
**工具语义（参数怎么组装、结果怎么解析）统一放在 pipeline 层**，
这样离线与联网两条路走的是同一套业务代码，不存在「离线能跑、联网不能跑」的分裂。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .backend import Backend
from .constants import (
    DEFAULT_ENDPOINT,
    DEFAULT_SERVER_NAME,
    DEFAULT_TIMEOUT_SECONDS,
    EXPECTED_TOOL_COUNT,
)
from .transport import StreamableHttpTransport, mask_token


class McpBackend(Backend):
    """真实 mcd-mcp 端点。"""

    name = DEFAULT_SERVER_NAME

    def __init__(
        self,
        endpoint: str = DEFAULT_ENDPOINT,
        token: Optional[str] = None,
        *,
        timeout: int = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        super().__init__()
        self.endpoint = endpoint
        self.transport = StreamableHttpTransport(endpoint, token, timeout=timeout)

    def call_tool(
        self, name: str, arguments: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        # 与 FixtureBackend 共用同一份审计记录，所以「只读开盒」这条承诺
        # 在联网时同样可以被机械验证（见 Backend.write_calls）
        self._record(name, arguments)
        return self.transport.call_tool(name, arguments)

    def list_tools(self) -> List[Dict[str, Any]]:
        return self.transport.list_tools()

    def close(self) -> None:
        self.transport.close()

    # -- 自检 ---------------------------------------------------------------
    def probe(self) -> Dict[str, Any]:
        """``verify`` 子命令用：握手 + 列工具，报告服务信息。

        返回值里的 token 一定是掩码后的形式。
        """
        info = self.transport.initialize()
        tools = self.transport.list_tools()
        server_info = info.get("serverInfo") or {}
        return {
            "endpoint": self.endpoint,
            "token": mask_token(self.transport.token),
            "serverName": server_info.get("name") or DEFAULT_SERVER_NAME,
            "serverVersion": server_info.get("version") or "unknown",
            "protocolVersion": info.get("protocolVersion"),
            "toolCount": len(tools),
            "expectedToolCount": EXPECTED_TOOL_COUNT,
            "toolCountMatches": len(tools) == EXPECTED_TOOL_COUNT,
            "sessionIdPresent": bool(self.transport.session_id),
            "toolNames": sorted(str(t.get("name")) for t in tools),
        }
