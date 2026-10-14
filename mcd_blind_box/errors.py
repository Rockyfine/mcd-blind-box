"""异常层级。

每个异常对应 Skill 的 troubleshooting.md 里的一类降级分支，
让上层可以据此决定「如实说明 + 给可执行的下一步」。
"""

from __future__ import annotations


class BlindBoxError(Exception):
    """本项目所有异常的基类。"""


class McpError(BlindBoxError):
    """MCP 调用失败（HTTP 层或 JSON-RPC 层）。"""


class AuthError(McpError):
    """鉴权失败。对应 HTTP 401：Token 无效、已过期或未提供。"""


class RateLimitError(McpError):
    """触发限流。对应 HTTP 429：每 Token 每分钟最多 600 次请求。"""


class ToolError(McpError):
    """工具返回业务错误（isError=true 或 JSON-RPC error）。"""


class FixturesMissingError(BlindBoxError):
    """离线模式所需的 fixtures 缺失或结构不符。"""


class ConstraintUnsatisfiable(BlindBoxError):
    """约束无法满足。

    抛出这个异常是刻意的：Skill 明确禁止在候选集为空时编造「经典套餐」，
    所以宁可失败，也不许凭空造一套出来。
    """


class GateRejected(BlindBoxError):
    """写操作被确认门禁拦截。"""


class SceneMismatch(BlindBoxError):
    """取餐场景与传入参数不匹配（例如给到店自取传了 beCode）。"""


class UnsupportedBeType(BlindBoxError):
    """beType 不在本项目支持范围内。

    本项目支持 1（到店自取）/ 5（得来速）/ 2（麦乐送），
    不实现 6（企业团餐）——遇到就显式报错，绝不猜测。
    """


class InvalidArgument(BlindBoxError):
    """参数不合法（例如 remark 超过 50 字）。"""
