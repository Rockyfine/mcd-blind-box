"""数据源抽象 —— 整个项目最关键的接缝。

离线（fixtures）与联网（mcd-mcp）实现**同一个**接口::

    call_tool(name, arguments) -> dict

算法层（rules / challenge / coupon / pricing / pipeline）完全不感知数据从哪来，
所以「零依赖离线复现」和「真连 MCP」可以共用同一套业务代码，
不需要任何分支判断。
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from .constants import DEFAULT_ENDPOINT, DEFAULT_TIMEOUT_SECONDS, TOKEN_ENV_VAR
from .errors import InvalidArgument


class Backend:
    """数据源协议（鸭子类型，不强制继承）。

    基类顺带提供**调用审计**：每次 ``call_tool`` 都把工具名与参数记下来。
    两个实现共用同一份记录，于是「开盒全程只读、没有 --confirm 就绝不下单」
    这句话在离线和联网下都能被机械地验证，而不是靠人盯代码。
    """

    name = "backend"

    def __init__(self) -> None:
        self.calls: List[str] = []
        self.call_args: List[Dict[str, Any]] = []

    def _record(self, name: str, arguments: Optional[Dict[str, Any]] = None) -> None:
        self.calls.append(name)
        self.call_args.append({"name": name, "arguments": dict(arguments or {})})

    @property
    def write_calls(self) -> List[str]:
        """本次会话里真正调用过的**写工具**。

        正常情况下永远是空的 —— 只有用户在确认门禁之后显式下单，
        才会出现 ``create-order``。
        """
        from .constants import WRITE_TOOLS

        return [name for name in self.calls if name in WRITE_TOOLS]

    def call_tool(self, name: str, arguments: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        raise NotImplementedError

    def list_tools(self) -> List[Dict[str, Any]]:
        raise NotImplementedError

    def close(self) -> None:  # pragma: no cover - 默认无需清理
        return None


def resolve_token(explicit: Optional[str] = None) -> Optional[str]:
    """按优先级解析 MCP Token：``--token`` > 环境变量 ``MCD_MCP_TOKEN``。

    **刻意不读取 ~/.workbuddy/mcp.json** —— 那会把宿主凭证耦合进项目代码，
    也不满足参赛声明的信息安全要求。
    """
    if explicit:
        return explicit.strip()
    value = os.environ.get(TOKEN_ENV_VAR, "").strip()
    return value or None


def make_backend(
    *,
    offline: bool = True,
    fixtures_dir: Optional[str] = None,
    token: Optional[str] = None,
    endpoint: str = DEFAULT_ENDPOINT,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> Backend:
    """按模式构造数据源。

    ``offline=True``（默认）读本地 fixtures，不联网、不消耗 Token；
    ``offline=False`` 需要显式提供 Token，否则直接报错而不是静默失败。

    随机种子**不在这里**：种子是 ``now-time-info`` 的返回值，
    由 :meth:`~mcd_blind_box.pipeline.BlindBoxPipeline.open_box` 自己取，
    或者由调用方显式传入 ``timestamp_ms``。数据源不该管随机性。
    """
    if offline:
        from .fixtures_client import FixtureBackend

        return FixtureBackend(fixtures_dir=fixtures_dir)

    from .mcp_client import McpBackend

    resolved = resolve_token(token)
    if not resolved:
        raise InvalidArgument(
            "联网模式需要 MCP Token：请设置环境变量 "
            f"{TOKEN_ENV_VAR}，或传入 --token。"
            "（申请地址见官方文档 https://open.mcd.cn/mcp）"
        )
    return McpBackend(endpoint=endpoint, token=resolved, timeout=timeout)
