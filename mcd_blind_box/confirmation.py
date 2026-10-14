"""确认门禁。

写操作（``create-order`` / ``cancel-order`` / ``delivery-create-address`` /
``auto-bind-coupons`` 等）在执行前必须先展示明细、并等用户**明确**确认。

本模块把「什么算确认」编码成可测试的判定函数。
**拿不准的时候一律判为不确认** —— 宁可多问一句，也不要误下单。
"""

from __future__ import annotations

from typing import Optional

from .constants import (
    CONFIRM_BLACKLIST_APPROVAL,
    CONFIRM_BLACKLIST_HESITANT,
    CONFIRM_EXCLAMATION_MARKERS,
    CONFIRM_QUESTION_MARKERS,
    CONFIRM_WHITELIST,
    WRITE_TOOLS,
)
from .models import GateVerdict

# 明确否定词：出现在确认短语之前，就否定掉这次确认
NEGATION_PREFIXES = ("不", "别", "先别", "暂时不", "不想", "取消")


def is_write_tool(tool_name: str) -> bool:
    """这个工具会不会真实改变状态？

    ``calculate-price`` 虽然是「下单前必调」，但它只算价、不下单，
    属于纯查询，**不在**写工具集合里。
    """
    return tool_name in WRITE_TOOLS


def classify(text: Optional[str]) -> GateVerdict:
    """判定一段用户输入是否构成「明确下单」。

    判定顺序（顺序本身就是规则的一部分）：

    1. 空输入 → 不确认
    2. **条件式 / 犹豫式**表达先拦（它们往往也含「下单」字样）
    3. 提问 → 不确认
    4. 仅表示认可（「就这个吧」「行」「嗯」）→ 不确认（要求整句相等）
    5. 命中确认白名单且未被否定 → 确认
    6. 其余 → 不确认（默认不下单）
    """
    if text is None:
        return GateVerdict(False, "空输入不构成确认")

    normalized = str(text).strip()
    if not normalized:
        return GateVerdict(False, "空输入不构成确认")

    compact = normalized.replace(" ", "").replace("\u3000", "")

    # 2) 条件式 / 犹豫式
    for phrase in CONFIRM_BLACKLIST_HESITANT:
        if phrase in compact:
            return GateVerdict(False, f"条件式表达「{phrase}」不构成确认", phrase)

    # 3) 提问
    for marker in CONFIRM_QUESTION_MARKERS:
        if marker in compact:
            return GateVerdict(False, "提问不构成确认", marker)

    # 4) 仅表示认可
    stripped = compact.rstrip("。.!！~～,，")
    for phrase in CONFIRM_BLACKLIST_APPROVAL:
        if stripped == phrase:
            return GateVerdict(False, f"「{phrase}」只是认可内容，不构成下单指令", phrase)

    # 5) 确认白名单（先排除否定表达）
    for phrase in CONFIRM_WHITELIST:
        if phrase in compact:
            for prefix in NEGATION_PREFIXES:
                if (prefix + phrase) in compact:
                    return GateVerdict(
                        False, f"「{prefix}{phrase}」是否定表达，不构成确认", phrase
                    )
            return GateVerdict(True, "明确下单意图", phrase)

    # 6) 兜底
    for marker in CONFIRM_EXCLAMATION_MARKERS:
        if marker in compact:
            return GateVerdict(False, "感叹或复述不构成确认", marker)

    return GateVerdict(False, "无法判定为明确下单意图，默认不下单")


def require_order_confirmation(
    confirm_flag: bool,
    user_text: Optional[str] = None,
) -> GateVerdict:
    """下单前的统一闸门。

    有两种放行方式，二者都要求「明确」：

    - ``confirm_flag=True``（CLI 的 ``--confirm``）
    - ``user_text`` 通过 :func:`classify` 判定为确认

    只给 ``--confirm`` 不给文本时视为显式确认（命令行是显式动作）；
    给了文本则以文本判定为准，文本不构成确认时**即使带了 --confirm 也拒绝**，
    避免「展示明细时顺口说了句『好的』」被误当成下单。
    """
    if user_text is not None and str(user_text).strip():
        verdict = classify(user_text)
        if not verdict.confirmed:
            return verdict
        return GateVerdict(True, "用户输入构成明确确认", verdict.matched)

    if confirm_flag:
        return GateVerdict(True, "显式传入了 --confirm", "--confirm")

    return GateVerdict(False, "未传入 --confirm，且无法判定用户明确表达了下单意图")
