"""揭晓卡与分享文案渲染。

纯字符串拼接，没有 IO，方便单测。所有数字都来自 :class:`RevealCard`，
渲染层不做任何再加工 —— 尤其**不做四舍五入之外的修饰**，避免把数据讲得比实际好。
"""

from __future__ import annotations

from typing import Any, Dict, List

from .challenge import MODE_BUDGET, MODE_CALORIE, MODE_TASTE, has_mode
from .constants import CATEGORY_LABELS, MODE_LABELS
from .coupon import describe_coupon
from .models import RevealCard
from .rules import fen_to_yuan

# 彩蛋文案池。用种子取模选择 —— 同样的种子得到同样的点评，可复现。
_QUIPS = (
    "服务器在 {seed} 毫秒时点了点头，于是有了这顿。",
    "盲盒官只负责抽，好不好吃是玄学的事。",
    "这份是算出来的，不是想出来的 —— 价格来自真实菜单。",
    "随机来自时间戳，不来自心情。所以你可以复核它。",
    "开盒完成。不满意可以重开，但换的是种子，不是人品。",
)

_MODE_TITLES = {
    "blind": "纯盲盒",
    "budget": "预算挑战",
    "calorie": "热量挑战",
    "taste": "口味挑战",
}


def _scene_label(card: RevealCard) -> str:
    if card.store is None:
        return "未知取餐方式"
    from .constants import BE_TYPE_LABELS

    return f"{card.store.store_name}（{BE_TYPE_LABELS.get(card.store.be_type, '未知')}）"


def _title(card: RevealCard) -> str:
    labels: List[str] = []
    for mode in card.modes:
        label = _MODE_TITLES.get(mode, MODE_LABELS.get(mode, mode))
        # 口味只标在「口味挑战」上；否则会变成「预算挑战（辣）+ 口味挑战（辣）」
        if mode == MODE_TASTE and card.taste:
            label = f"{label}（{card.taste}）"
        labels.append(label)
    return " + ".join(labels) or _MODE_TITLES["blind"]


def _quip(card: RevealCard) -> str:
    template = _QUIPS[card.combo.seed % len(_QUIPS)]
    return template.format(seed=card.combo.seed)


def _item_lines(card: RevealCard) -> List[str]:
    lines: List[str] = []
    if not card.combo.items:
        return ["（空）"]
    head = card.combo.items[0]
    lines.append(f"主品  {head.name} × {head.quantity}")
    for item in card.combo.items[1:]:
        label = CATEGORY_LABELS.get(item.category, "配品")
        lines.append(f"配品  {item.name} × {item.quantity}（{label}）")
    return lines


def render_reveal(card: RevealCard) -> str:
    """渲染盲盒揭晓卡。"""
    out: List[str] = []
    out.append(f"【麦门盲盒 · {_title(card)}】")
    out.append("")

    # 挑战进度块
    if has_mode(card.modes, MODE_BUDGET) and card.budget_yuan is not None:
        payable = card.price.payable_fen / 100
        remaining = card.budget_yuan - payable
        out.append(f"预算      {card.budget_yuan:.2f} 元")
        out.append(f"实际      券后 ¥{card.payable_yuan_text()}  →  "
                   f"{'余额' if remaining >= 0 else '超出'} {abs(remaining):.2f} 元")
        out.append(f"状态      挑战{'成功' if card.challenge_ok else '未达标（已放宽）'}")
        out.append("")

    if has_mode(card.modes, MODE_CALORIE) and card.calorie_cap is not None:
        calories = card.combo.total_calories()
        calories_text = f"{calories} 大卡" if calories is not None else "无法计算"
        out.append(f"上限      {card.calorie_cap} 大卡")
        if calories is not None:
            remaining = card.calorie_cap - calories
            out.append(f"实际      {calories_text}  →  "
                       f"{'剩余' if remaining >= 0 else '超出'} {abs(remaining)} 大卡")
        else:
            out.append(f"实际      {calories_text}")
        out.append(f"状态      挑战{'成功' if card.challenge_ok else '未达标（已放宽）'}")
        out.append("")

    out.extend(_item_lines(card))
    out.append("")
    out.append(f"门店      {_scene_label(card)}")
    if card.store is not None and card.store.be_code:
        out.append(f"beCode    {card.store.be_code}")
    if card.address_id:
        out.append(f"配送地址  {card.address_id}")
    out.append(f"原价      ¥{card.original_yuan_text()}")
    # 判据是「有没有选到券」，不是「减了多少钱」——
    # 商品券（如「中薯条买一送一」）的实际抵扣由服务端决定，本地算不出来，
    # 用它当判据会显示成「无可用券」，那就是在说谎。
    if card.coupon is not None:
        out.append(
            f"券后价    ¥{card.payable_yuan_text()}     使用券：{describe_coupon(card.coupon)}"
        )
    elif card.price.discount_fen:
        # 服务端自己减了钱，但不是我们选的那张券（实测真的会这样）。
        # 这时写「无可用券」会和上面的价格对不上，所以要如实说是服务端优惠。
        out.append(
            f"券后价    ¥{card.payable_yuan_text()}     服务端优惠 "
            f"¥{fen_to_yuan(card.price.discount_fen)}（非券来源）"
        )
    else:
        out.append(f"券后价    ¥{card.payable_yuan_text()}     {describe_coupon(None)}")
    if card.price.take_ways:
        names = " / ".join(f"{t.name or t.code}" for t in card.price.take_ways)
        out.append(f"取餐方式  {names}（takeWayCode={card.price.take_ways[0].code}）")
    out.append("")

    out.append(f"一句话点评：{_quip(card)}")
    out.append("")
    out.append("—— 回复「确认下单」才会真实下单。")
    combo = card.combo
    if combo.main_pool_size:
        rank_text = f"，为第 {combo.rank + 1} 个候选" if combo.rank else ""
        out.append(
            f"   种子 {combo.seed} → 第 {combo.main_index + 1} 项"
            f"（主品候选池 {combo.main_pool_size} 件；"
            f"{combo.seed} mod {combo.main_pool_size} = {combo.main_index}{rank_text}）"
        )
    else:
        out.append(f"   种子 {combo.seed} → 第 {combo.main_index + 1} 项")

    if card.notes:
        out.append("")
        for note in card.notes:
            out.append(f"注意：{note}")

    if has_mode(card.modes, MODE_CALORIE):
        out.append("")
        out.append("营养数据仅供参考，非医疗或健康建议。")

    return "\n".join(out)


def render_confirm_panel(card: RevealCard, *, take_way_code: str = "", remark: str = "") -> str:
    """渲染「待确认」明细。写操作之前必须先输出这个并停下来等回复。"""
    from .constants import BE_TYPE_LABELS

    out: List[str] = ["【待确认】真实下单"]
    if card.store is not None:
        out.append(f"门店        {card.store.store_name}")
        out.append(f"取餐方式    {BE_TYPE_LABELS.get(card.store.be_type, '未知')}")
        if card.store.be_code:
            out.append(f"beCode      {card.store.be_code}")
    if card.address_id:
        out.append(f"配送地址    {card.address_id}")
    if take_way_code:
        out.append(f"取餐编码    {take_way_code}")
    if remark:
        out.append(f"备注        {remark}")
    out.append("品项        " + " / ".join(f"{i.name} × {i.quantity}" for i in card.combo.items))
    out.append(f"原价        ¥{card.original_yuan_text()}")
    if card.coupon is not None:
        suffix = f"     使用券：{describe_coupon(card.coupon)}"
    elif card.price.discount_fen:
        suffix = f"     服务端优惠 ¥{fen_to_yuan(card.price.discount_fen)}（非券来源）"
    else:
        suffix = "     （无可用券）"
    out.append(f"券后价      ¥{card.payable_yuan_text()}{suffix}")
    out.append("—— 回复「确认下单」才会真实下单，回复其他内容则不下单。")
    return "\n".join(out)


def render_share_text(card: RevealCard) -> str:
    """生成一段可复制的分享文案（纯文本）。"""
    items = " + ".join(i.name for i in card.combo.items)
    return (
        f"今天把点餐交给了服务器。它给我开了个盲盒：\n"
        f"{items}，券后 ¥{card.payable_yuan_text()}。\n"
        f"随机种子 {card.combo.seed} → 第 {card.combo.main_index + 1} 项"
        f"（主品候选池 {card.combo.main_pool_size} 件），"
        f"你可以自己去复核它有没有作弊。"
    )


def card_to_dict(card: RevealCard) -> Dict[str, Any]:
    """给 ``--json`` 用的结构化输出。"""
    return {
        "modes": list(card.modes),
        "taste": card.taste,
        "budgetYuan": card.budget_yuan,
        "calorieCap": card.calorie_cap,
        "challengeOk": card.challenge_ok,
        "notes": list(card.notes),
        "store": {
            "storeCode": card.store.store_code if card.store else None,
            "storeName": card.store.store_name if card.store else None,
            "beType": card.store.be_type if card.store else None,
            "beCode": card.store.be_code if card.store else None,
        },
        "addressId": card.address_id,
        "items": [
            {
                "productCode": item.product_code,
                "name": item.name,
                "quantity": item.quantity,
                "unitPriceYuan": fen_to_yuan(item.unit_price_fen),
                "category": item.category,
                "calories": item.calories,
                "modification": item.modification_values,
            }
            for item in card.combo.items
        ],
        "price": {
            "originalTotalFen": card.price.original_total_fen,
            "discountFen": card.price.discount_fen,
            "payableFen": card.price.payable_fen,
            "originalYuan": card.original_yuan_text(),
            "payableYuan": card.payable_yuan_text(),
            "takeWayList": [{"code": t.code, "name": t.name} for t in card.price.take_ways],
        },
        "coupon": (
            {
                "couponId": card.coupon.coupon_id,
                "couponCode": card.coupon.coupon_code,
                "title": card.coupon.title,
            }
            if card.coupon
            else None
        ),
        "seed": card.combo.seed,
        "mainIndex": card.combo.main_index,
        "mainPoolSize": card.combo.main_pool_size,
        "candidateSize": card.combo.candidate_size,
        "candidateRank": card.combo.rank,
        "relaxed": card.combo.relaxed,
        "calories": card.combo.total_calories(),
    }
