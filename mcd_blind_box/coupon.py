"""优惠券。

**唯一正确的来源是 ``query-store-coupons``**（指定门店 + 订单类型下真正可用的券）。

``query-my-coupons`` 返回的是卡包资产，**不校验门店/渠道/配送方式是否可用**，
拿它算价会导致券不抵扣、揭晓卡的「券后价」直接虚标 —— 所以本模块
只接受显式标注来源正确的券。
"""

from __future__ import annotations

from typing import Iterable, List, Optional, Sequence

from .constants import PRICE_UNIT_DIVISOR
from .errors import InvalidArgument
from .models import CartItem, Coupon

# 允许作为券来源的工具（白名单，只此一个）
COUPON_SOURCE_TOOL = "query-store-coupons"

# 明确禁止用于算券后价的工具
FORBIDDEN_COUPON_SOURCES = ("query-my-coupons", "available-coupons")


def assert_coupon_source(tool_name: str) -> str:
    """校验券的来源工具，防止误用卡包券。"""
    if tool_name in FORBIDDEN_COUPON_SOURCES:
        raise InvalidArgument(
            f"{tool_name} 不能用于计算券后价（不校验门店与渠道可用性）；"
            f"请改用 {COUPON_SOURCE_TOOL}"
        )
    if tool_name != COUPON_SOURCE_TOOL:
        raise InvalidArgument(f"未知的券来源 {tool_name!r}；唯一合法来源是 {COUPON_SOURCE_TOOL}")
    return tool_name


def applicable(
    coupons: Iterable[Coupon],
    subtotal_fen: int,
    item_codes: Sequence[str] = (),
) -> List[Coupon]:
    """筛出**确实能用**的券，按「优惠力度 → 商品券优先 → couponId」排序。

    真实券有两种，判定方式不同：

    - **商品券**（带 ``products``，如「中薯条买一送一」「9.9元中杯冰美式」）：
      只有当购物车里确实有它关联的商品时才可用。这类券没有「满 X 减 Y」的数字，
      硬按门槛筛会选出一张根本用不上的券。
    - **门槛券**（带 ``thresholdFen`` / ``discountFen``）：小计达到门槛即可用。

    完全不带信息的券（既无关联商品、门槛与优惠又都是 0）直接跳过 ——
    挂上去只会让算价结果更难解释。
    """
    usable: List[Coupon] = []
    code_set = set(item_codes)

    for coupon in coupons:
        if coupon.product_codes:
            if code_set & set(coupon.product_codes):
                usable.append(coupon)
            continue
        if coupon.threshold_fen == 0 and coupon.discount_fen == 0:
            continue
        if subtotal_fen >= coupon.threshold_fen:
            usable.append(coupon)

    # 优惠力度降序；同为 0 时商品券优先（它的实际优惠由服务端算，值多少不知道）
    return sorted(
        usable,
        key=lambda c: (-c.discount_fen, 0 if c.product_codes else 1, c.coupon_id),
    )


def select_coupon(
    coupons: Iterable[Coupon],
    subtotal_fen: int,
    item_codes: Sequence[str] = (),
) -> Optional[Coupon]:
    """按「能用 + 优惠最多」选一张券；没有可用券就返回 None（不编造优惠）。

    只挂**一张**：接口允许每件商品各挂一张券，但组合枚举会让调用次数和
    结果解释成本都失控，所以这里保持可解释的单券策略。
    """
    usable = applicable(coupons, subtotal_fen, item_codes)
    return usable[0] if usable else None


def attach_coupon(
    items: Sequence[CartItem],
    coupon: Optional[Coupon],
) -> List[CartItem]:
    """把券挂到第一个商品上（接口的券是挂在 item 上的）。

    ``coupon`` 为 None 时不做任何事 —— 未返回的 couponId/couponCode 绝不生成。
    """
    if coupon is None:
        return list(items)
    if not items:
        return list(items)
    if not coupon.coupon_id and not coupon.coupon_code:
        # 券对象本身没有可用标识，宁可不用，也不传空串
        return list(items)

    updated = list(items)
    head = updated[0]
    updated[0] = CartItem(
        product_code=head.product_code,
        name=head.name,
        quantity=head.quantity,
        unit_price_fen=head.unit_price_fen,
        modification_values=head.modification_values,
        coupon_id=coupon.coupon_id or None,
        coupon_code=coupon.coupon_code or None,
        calories=head.calories,
        category=head.category,
    )
    return updated


def describe_coupon(coupon: Optional[Coupon]) -> str:
    """给揭晓卡用的一句话券描述。

    **商品券要如实标注它算不出金额**：这类券（「中薯条买一送一」）没有
    「满 X 减 Y」的数字，实际抵扣由服务端根据自己的规则决定。
    本地把它当作 0 元优惠是**保守**做法，但不能让用户以为这张券一分钱不值 ——
    所以这里明说优惠金额以服务端算价为准。
    """
    if coupon is None:
        return "无可用券"
    if coupon.product_codes:
        title = coupon.title or "商品券"
        return f"{title}（商品券，实际抵扣以服务端算价为准）"
    if coupon.discount_fen:
        amount = coupon.discount_fen / PRICE_UNIT_DIVISOR
        title = coupon.title or "优惠券"
        return f"{title}（满 {coupon.threshold_fen / PRICE_UNIT_DIVISOR:.2f} 减 {amount:.2f}）"
    return coupon.title or "优惠券"
