"""价格解析。

价格**只有一个来源**：``calculate-price`` 的返回（经 :func:`to_price_result` 解析）。
本项目**不在业务代码里自造价格** —— 离线模式的算价发生在
:class:`~mcd_blind_box.fixtures_client.FixtureBackend` 内部，对上层来说
它和联网走的是同一个接口、同一套字段。

**所有金额单位都是分。** 展示一律走 :func:`~mcd_blind_box.rules.fen_to_yuan`。
"""

from __future__ import annotations

from typing import Any, Dict, Sequence

from .models import CartItem, PriceResult


def to_price_result(raw: Dict[str, Any], items: Sequence[CartItem]) -> PriceResult:
    """把 ``calculate-price`` 的返回解析成 :class:`PriceResult`。

    真实字段是 ``data.originalPrice`` / ``data.price`` / ``data.discount``，单位**分**。

    ``raw`` 若缺少价格字段，则回退到本地按商品单价累加 —— 
    这只是一个「不要显示出 ¥0.00 这种一眼假结果」的兜底，
    兜底发生时价格栏会与真实服务端不一致，所以它同时意味着上游出了问题，
    绝不应当作正常路径。
    """
    result = PriceResult.from_api(raw)
    if result.original_total_fen == 0 and items:
        result.original_total_fen = sum(item.subtotal_fen() for item in items)
    if result.payable_fen == 0 and result.original_total_fen:
        result.payable_fen = result.original_total_fen - result.discount_fen
    return result


def total_calories(items: Sequence[CartItem]) -> int | None:
    """组合总热量。

    任意一项匹配不到营养数据就返回 ``None`` —— 绝不估算、更不编造。
    """
    if not items:
        return 0
    known = [item.calories for item in items]
    if any(value is None for value in known):
        return None
    return sum(int(v) for v in known)  # type: ignore[arg-type]


__all__ = ["to_price_result", "total_calories"]
