"""数据载体。

刻意只用 dataclasses，不引入 pydantic —— 这是「零依赖」的前提。
每个类都带一个 ``from_api`` 类方法，负责把 MCP 返回的原始 dict 转成对象；
所有解析都做宽容处理（字段缺失就给默认值），因为 MCP 返回结构可能演进。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .constants import PRICE_UNIT_DIVISOR
from .errors import FixturesMissingError


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_str(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value)


def _first(mapping: Dict[str, Any], *keys: str, default: Any = None) -> Any:
    """从 dict 里按候选键名取第一个非 None 的值。

    兼容命名差异（priceFen / price / currentPrice 之类），
    避免因为上游换字段名就整个崩掉。
    """
    for key in keys:
        if key in mapping and mapping[key] is not None:
            return mapping[key]
    return default


def yuan_to_fen(value: Any) -> int:
    """把「元」形式的金额转成分。

    真实菜单里 ``currentPrice`` / ``originalPrice`` 是**元的字符串**（``"33.5"``），
    而 ``calculate-price`` 返回的是**分**（``3350``）。两者混用会把 33.5 元
    显示成 3350 元，所以这里必须严格按字段语义分开处理。
    """
    if value is None:
        return 0
    try:
        return int(round(float(str(value).strip()) * PRICE_UNIT_DIVISOR))
    except (TypeError, ValueError):
        return 0


def _first_fen(mapping: Dict[str, Any], *fen_keys: str) -> Optional[int]:
    """取一个**已经是分**的字段。"""
    value = _first(mapping, *fen_keys)
    if value is None:
        return None
    return _as_int(value)


def _first_yuan_as_fen(mapping: Dict[str, Any], *yuan_keys: str) -> Optional[int]:
    """取一个**以元为单位**的字段并换算成分。"""
    value = _first(mapping, *yuan_keys)
    if value is None:
        return None
    return yuan_to_fen(value)


def classify_category(raw_name: Any, explicit: Any = None) -> str:
    """把菜单里的分类名映射到 ``main`` / ``side`` / ``drink`` / ``dessert``。

    优先用显式给出的 ``category`` 字段（离线 fixtures 用它把意图写清楚）；
    没有就按分类名做关键词匹配；**都认不出来时算主品** —— 这是刻意的保守兜底。
    """
    from .constants import CAT_MAIN, MEAL_CATEGORY_KEYWORDS

    if explicit:
        text = str(explicit).strip()
        if text in (CAT_MAIN, "side", "drink", "dessert"):
            return text

    name = str(raw_name or "")
    for bucket, keywords in MEAL_CATEGORY_KEYWORDS:
        if any(keyword in name for keyword in keywords):
            return bucket
    return CAT_MAIN


@dataclass(frozen=True)
class Store:
    """门店。be_code 在 beType=1 时永远是 None。"""

    store_code: str
    store_name: str
    be_type: int
    be_code: Optional[str] = None
    reservation: bool = False
    today: bool = False

    @classmethod
    def from_api(cls, raw: Dict[str, Any], be_type: int) -> "Store":
        return cls(
            store_code=_as_str(_first(raw, "storeCode", "store_code", "code")),
            store_name=_as_str(_first(raw, "storeName", "store_name", "name")),
            be_type=be_type,
            be_code=(_as_str(_first(raw, "beCode", "be_code")) or None),
            reservation=bool(_first(raw, "reservation", default=False)),
            today=bool(_first(raw, "today", default=False)),
        )


@dataclass(frozen=True)
class Meal:
    """在售餐品。product_code 只能来自 query-meals。"""

    product_code: str
    name: str
    price_fen: int
    category: str = "main"

    @classmethod
    def from_api(
        cls,
        raw: Dict[str, Any],
        *,
        product_code: Optional[str] = None,
        category_name: Optional[str] = None,
    ) -> "Meal":
        """解析一条菜单条目。

        ``query-meals`` 的真实返回是 ``data.meals`` 这个**以编码为键的字典**，
        商品自己的字段里并没有 ``productCode``，所以编码要靠 ``product_code``
        参数从字典键传进来。

        价格有两套语义，必须分清：
        - ``priceFen`` / ``price_fen`` → 已经是**分**；
        - ``currentPrice`` / ``originalPrice`` / ``price`` → 是**元的字符串**（``"33.5"``）。
        """
        fen = _first_fen(raw, "priceFen", "price_fen")
        if fen is None:
            fen = _first_yuan_as_fen(raw, "currentPrice", "price", "originalPrice")
        if fen is None:
            fen = 0

        return cls(
            product_code=_as_str(
                _first(raw, "productCode", "product_code", "code", default=product_code)
            ),
            name=_as_str(_first(raw, "name", "productName", "product_name")),
            price_fen=fen,
            category=classify_category(
                category_name or _first(raw, "categoryName", "category"),
                _first(raw, "category"),
            ),
        )

    def price_yuan(self) -> float:
        return self.price_fen / PRICE_UNIT_DIVISOR


@dataclass(frozen=True)
class ModValue:
    """一个特调选项。selected_key / unselected_key 是传参的唯一依据。"""

    code: str
    name: str
    selected_key: str = ""
    unselected_key: str = ""
    selected: bool = False

    @classmethod
    def from_api(cls, raw: Dict[str, Any]) -> "ModValue":
        # 真实数据用 selectedQuantity 表示「这一项是否被选中」，
        # 没有 selected 布尔字段；两种都要认。
        selected = _first(raw, "selected")
        if selected is None:
            quantity = _first(raw, "selectedQuantity", "defaultQuantity")
            if quantity is not None:
                selected = _as_int(quantity) > 0
            else:
                selected = bool(_first(raw, "isDefault", default=False))
        return cls(
            code=_as_str(_first(raw, "code", "valueCode", "value_code")),
            name=_as_str(_first(raw, "name", "valueName", "value_name")),
            selected_key=_as_str(_first(raw, "selectedKey", "selected_key")),
            unselected_key=_as_str(_first(raw, "unselectedKey", "unselected_key")),
            selected=bool(selected),
        )


@dataclass(frozen=True)
class ModGroup:
    """一个特调组。组内只要有任一项带 unselected_key，整组都要传。

    真实数据里这一层叫 ``modification.items[]``，每组带 ``maxValues`` /
    ``minValues``；fixtures 里叫 ``groups[]``。两种都映射到本类。
    """

    code: str
    name: str
    values: List[ModValue] = field(default_factory=list)
    max_values: int = 0
    min_values: int = 0

    @classmethod
    def from_api(cls, raw: Dict[str, Any], index: int = 0) -> "ModGroup":
        raw_values = _first(raw, "values", "modifications", "options", default=[]) or []
        return cls(
            code=_as_str(_first(raw, "code", "groupCode", "group_code", default=f"G{index}")),
            name=_as_str(_first(raw, "name", "groupName", "group_name")),
            values=[ModValue.from_api(v) for v in raw_values if isinstance(v, dict)],
            max_values=_as_int(_first(raw, "maxValues", "max_values", default=0)),
            min_values=_as_int(_first(raw, "minValues", "min_values", default=0)),
        )

    @property
    def has_unselected_key(self) -> bool:
        """组内是否存在带 unselected_key 的项 —— 决定是否整组传入。"""
        return any(v.unselected_key for v in self.values)

    def default_value(self) -> Optional[ModValue]:
        for value in self.values:
            if value.selected:
                return value
        return self.values[0] if self.values else None


@dataclass(frozen=True)
class MealDetail:
    """餐品详情（含特调）。

    真实 ``query-meal-detail`` 的形状是::

        data.supportModify        是否可特调
        data.modification.items[] 顶层特调组（部分单品有）
        data.rounds[].choices[]   套餐的「轮次 + 候选项」结构

    ``rounds`` 里每个 round 的 choices 是**互斥的候选项**（挑一个），
    而 ``calculate-price`` 的 ``roundList`` 结构我们**没有在真实服务端验证通过**
    （实测不带价格返回），所以本项目对这类多轮套餐**不生成特调**，
    而不是猜一个结构发出去。见 :attr:`has_rounds`。
    """

    product_code: str
    support_modify: bool = False
    groups: List[ModGroup] = field(default_factory=list)
    has_rounds: bool = False

    @classmethod
    def from_api(cls, raw: Dict[str, Any]) -> "MealDetail":
        data = _first(raw, "data", default=raw) or raw
        if not isinstance(data, dict):
            data = {}

        modification = _first(data, "modification", default={}) or {}
        if not isinstance(modification, dict):
            modification = {}

        # 顶层特调组：fixtures 叫 groups，真实数据叫 items
        raw_groups = _first(modification, "groups", "items", "modificationGroups", default=[]) or []
        if not isinstance(raw_groups, list):
            raw_groups = []

        rounds = _first(data, "rounds", default=[]) or []
        has_rounds = isinstance(rounds, list) and any(
            isinstance(round_item, dict) for round_item in rounds
        )

        return cls(
            product_code=_as_str(_first(data, "productCode", "code", default="")),
            support_modify=bool(_first(data, "supportModify", default=False)),
            groups=[
                ModGroup.from_api(g, i)
                for i, g in enumerate(raw_groups)
                if isinstance(g, dict)
            ],
            has_rounds=has_rounds,
        )


@dataclass(frozen=True)
class Coupon:
    """优惠券。只允许来自 query-store-coupons。

    真实返回有两种形态，都支持：

    - **门槛券**：带 ``thresholdFen`` / ``discountFen``，按小计筛门槛即可；
    - **商品券**：带 ``products[].productCode``（例如「中薯条买一送一」「9.9元中杯冰美式」），
      **只有当购物车里确实有它关联的商品时才会真正抵扣**。

    商品券没有「满 X 减 Y」这样的数字，硬按门槛筛会选出用不上的券 ——
    所以 :func:`coupon.select_coupon` 会优先按关联商品匹配。
    """

    coupon_id: str
    coupon_code: str
    title: str = ""
    threshold_fen: int = 0
    discount_fen: int = 0
    product_codes: Tuple[str, ...] = ()

    @classmethod
    def from_api(cls, raw: Dict[str, Any]) -> "Coupon":
        products = _first(raw, "products", "productList", default=[]) or []
        codes: List[str] = []
        if isinstance(products, list):
            for entry in products:
                if isinstance(entry, dict):
                    code = _as_str(_first(entry, "productCode", "product_code", "code"))
                else:
                    code = _as_str(entry)
                if code:
                    codes.append(code)

        threshold = _first_fen(raw, "thresholdFen", "threshold")
        if threshold is None:
            threshold = _first_yuan_as_fen(raw, "thresholdYuan")
        discount = _first_fen(raw, "discountFen", "discount")
        if discount is None:
            discount = _first_yuan_as_fen(raw, "discountYuan")

        return cls(
            coupon_id=_as_str(_first(raw, "couponId", "coupon_id")),
            coupon_code=_as_str(_first(raw, "couponCode", "coupon_code")),
            title=_as_str(_first(raw, "title", "name", "couponName")),
            threshold_fen=threshold or 0,
            discount_fen=discount or 0,
            product_codes=tuple(codes),
        )

    def covers(self, product_codes: Sequence[str]) -> bool:
        """这张券是否和购物车里的某个商品相关？"""
        if not self.product_codes:
            return False
        return bool(set(self.product_codes) & set(product_codes))


@dataclass
class CartItem:
    """购物车项。to_payload() 产出的就是 calculate-price / create-order 的 items[] 结构。"""

    product_code: str
    name: str = ""
    quantity: int = 1
    unit_price_fen: int = 0
    modification_values: List[Dict[str, Any]] = field(default_factory=list)
    coupon_id: Optional[str] = None
    coupon_code: Optional[str] = None
    calories: Optional[int] = None
    # 展示用的分类（main/side/drink/dessert）。**绝不进 payload** ——
    # 接口没有这个字段，发出去只会是个未定义参数。
    category: str = "main"

    def to_payload(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "productCode": self.product_code,
            "quantity": self.quantity,
        }
        if self.modification_values:
            payload["modification"] = {"values": self.modification_values}
        # 未返回的 couponId / couponCode 不生成（SKILL.md 规则 13）
        if self.coupon_id:
            payload["couponId"] = self.coupon_id
        if self.coupon_code:
            payload["couponCode"] = self.coupon_code
        return payload

    def subtotal_fen(self) -> int:
        return self.unit_price_fen * self.quantity


@dataclass(frozen=True)
class TakeWay:
    """取餐方式。code 是 create-order.takeWayCode 的唯一合法来源。

    真实返回的 ``takeWayList[]`` 用 ``title`` / ``subtitle`` 描述（例如
    ``{"code": "eat-in", "title": "堂食"}``、``{"code": "take-in-store", "title": "外带"}``），
    不是 ``name``。
    """

    code: str
    name: str = ""

    @classmethod
    def from_api(cls, raw: Dict[str, Any]) -> "TakeWay":
        return cls(
            code=_as_str(_first(raw, "code", "takeWayCode")),
            name=_as_str(
                _first(raw, "name", "title", "takeWayName", "subtitle")
            ),
        )


@dataclass
class PriceResult:
    """算价结果。所有金额单位都是分。

    ``calculate-price`` 的真实字段是 ``originalPrice`` / ``price`` / ``discount``，
    都是**分**；这里同时兼容 ``originalTotalFen`` 这类带单位后缀的命名。
    """

    original_total_fen: int = 0
    payable_fen: int = 0
    discount_fen: int = 0
    take_ways: List[TakeWay] = field(default_factory=list)

    @classmethod
    def from_api(cls, raw: Dict[str, Any]) -> "PriceResult":
        data = _first(raw, "data", default=raw) or raw
        if not isinstance(data, dict):  # 出错时 data 可能不是 dict
            data = raw if isinstance(raw, dict) else {}
        raw_take_ways = _first(data, "takeWayList", "take_ways", default=[]) or []
        if not isinstance(raw_take_ways, list):
            raw_take_ways = []

        original = _first_fen(
            data, "originalTotalFen", "originalTotal", "totalFen", "originalPrice"
        )
        if original is None:
            original = _first_yuan_as_fen(data, "originalPriceYuan")

        payable = _first_fen(
            data, "payableFen", "payable", "payAmount", "totalAmount", "price"
        )
        if payable is None:
            payable = _first_yuan_as_fen(data, "payableYuan")

        discount = _first_fen(data, "discountFen", "discount", "discountAmount")
        if discount is None:
            discount = 0

        return cls(
            original_total_fen=original or 0,
            payable_fen=payable or 0,
            discount_fen=discount,
            take_ways=[TakeWay.from_api(t) for t in raw_take_ways if isinstance(t, dict)],
        )

    def first_take_way_code(self) -> Optional[str]:
        for take_way in self.take_ways:
            if take_way.code:
                return take_way.code
        return None


@dataclass
class Combo:
    """一次抽出来的盲盒组合。"""

    items: List[CartItem] = field(default_factory=list)
    seed: int = 0
    main_index: int = 0
    main_pool_size: int = 0
    candidate_size: int = 0
    rank: int = 0
    relaxed: bool = False
    relaxation_note: str = ""

    def add(self, meal: Meal, quantity: int = 1, **kwargs: Any) -> CartItem:
        item = CartItem(
            product_code=meal.product_code,
            name=meal.name,
            quantity=quantity,
            unit_price_fen=meal.price_fen,
            category=meal.category,
            **kwargs,
        )
        self.items.append(item)
        return item

    def total_price_fen(self) -> int:
        return sum(item.subtotal_fen() for item in self.items)

    def total_calories(self) -> Optional[int]:
        known = [item.calories for item in self.items if item.calories is not None]
        if not known or len(known) != len(self.items):
            # 有任意一项匹配不到营养数据，就不给总量 —— 绝不估算
            return None
        return sum(known)

    def has_unmatched_nutrition(self) -> bool:
        return any(item.calories is None for item in self.items)


@dataclass
class RevealCard:
    """揭晓卡。这是给用户看的最终产物。"""

    combo: Combo
    price: PriceResult
    store: Optional[Store] = None
    modes: Sequence[str] = ()
    budget_yuan: Optional[float] = None
    calorie_cap: Optional[int] = None
    taste: Optional[str] = None
    coupon: Optional[Coupon] = None
    challenge_ok: bool = True
    notes: List[str] = field(default_factory=list)
    address_id: Optional[str] = None

    def payable_yuan_text(self) -> str:
        return f"{self.price.payable_fen / PRICE_UNIT_DIVISOR:.2f}"

    def original_yuan_text(self) -> str:
        return f"{self.price.original_total_fen / PRICE_UNIT_DIVISOR:.2f}"


@dataclass
class GateVerdict:
    """确认门禁的判定结果。"""

    confirmed: bool
    reason: str = ""
    matched: Optional[str] = None

    def __bool__(self) -> bool:  # pragma: no cover - 便捷判断
        return self.confirmed


def require(condition: bool, message: str) -> None:
    """断言助手，失败时抛 FixturesMissingError（离线数据问题的统一出口）。"""
    if not condition:
        raise FixturesMissingError(message)
