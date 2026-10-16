"""编排层：把三层串成三条链路。

这里是「取数 → 规则 → 展示」的中枢。它只依赖 :class:`~mcd_blind_box.backend.Backend`
接口，所以离线与联网走完全相同的一套代码。

对应 Skill 里的三条链路：

- A 到店自取（beType=1, orderType=1，全程不带 beCode）
- B 得来速（beType=5, orderType=1，全程带 beCode）
- C 麦乐送（beType=2, orderType=2，addressId 必填，remark 仅此链）
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Sequence

from . import rules
from .challenge import (
    MODE_BUDGET,
    MODE_CALORIE,
    MODE_TASTE,
    Candidate,
    build_combo_meals,
    budget_extras_filter,
    calorie_extras_filter,
    candidate_for_seed,
    combine_filters,
    describe_modes,
    extra_item_count,
    filter_by_calories,
    filter_by_taste,
    generate_candidates,
    has_mode,
    normalize_modes,
    pick_main_categories,
    relax_budget,
    relax_calorie,
    style_key,
    style_key_for,
)
from .constants import (
    CANDIDATE_COUNT,
    CAT_MAIN,
    COMBO_NAME_HINTS,
    ORDER_TYPE_DELIVERY,
    ORDER_TYPE_IN_STORE,
    SEED_STRIDE,
)
from .coupon import assert_coupon_source, attach_coupon, select_coupon
from .errors import ConstraintUnsatisfiable, GateRejected, InvalidArgument
from .models import CartItem, Combo, Coupon, Meal, MealDetail, PriceResult, RevealCard, Store
from .pricing import to_price_result, total_calories

_COUPON_SOURCE = "query-store-coupons"


def _looks_like_combo(name: str) -> bool:
    """餐品名是否命中套餐信号词 —— 决定是否展开 rounds 默认子项做去重。

    只是「要不要查详情」的判据，不是套餐定义。普通单品名不含这些词，
    于是零额外调用；命中（如「鳕鱼堡套餐」）才查 ``query-meal-detail``。
    """
    return any(hint in name for hint in COMBO_NAME_HINTS)


class BlindBoxPipeline:
    """一次开盒会话的状态机。"""

    def __init__(self, backend: Any, *, offline: bool = True) -> None:
        self.backend = backend
        self.offline = offline

        self.be_type: Optional[int] = None
        self.order_type: Optional[int] = None
        self.store: Optional[Store] = None
        self.be_code: Optional[str] = None
        self.address_id: Optional[str] = None

        self.meals: List[Meal] = []
        self.card: Optional[RevealCard] = None

        self._kcal: Optional[Dict[str, int]] = None
        self._details: Dict[str, MealDetail] = {}

    # ------------------------------------------------------------------
    # 场景
    # ------------------------------------------------------------------
    def reset_scene(self) -> None:
        """切换取餐场景时必须调用。

        ``beCode`` 与门店 + 取餐方式绑定，**不能沿用** —— 换场景后旧的一律作废。
        """
        self.store = None
        self.be_code = None
        self.address_id = None
        self.meals = []
        self.card = None
        self._details.clear()

    def set_scene(self, be_type: int) -> None:
        if self.be_type is not None and self.be_type != be_type:
            self.reset_scene()
        self.be_type = be_type
        self.order_type = rules.order_type_for(be_type)

    def locate_store(
        self,
        *,
        store_code: Optional[str] = None,
        be_code: Optional[str] = None,
        search_type: Optional[int] = None,
        city: Optional[str] = None,
        keyword: Optional[str] = None,
        address_id: Optional[str] = None,
    ) -> Store:
        """定位门店（链路 A/B/C 的起点）。"""
        if self.be_type is None:
            raise InvalidArgument("请先调用 set_scene()")

        # beType=1 携带 beCode 是硬性禁令（服务端会直接拒绝）。
        # 这里提前拦下来并报错，而不是把用户传的 beCode 静默丢掉 ——
        # 静默忽略会让人以为自己指定的门店生效了，实际没有。
        rules.assert_no_be_code_for_pickup(self.be_type, {"beCode": be_code})

        if store_code:
            # 显式指定门店：仍需按规则校验 beCode
            args = rules.build_scene_args(self.be_type, be_code=be_code, order_type=self.order_type)
            self.store = Store(
                store_code=str(store_code),
                store_name=f"门店 {store_code}（调用方指定）",
                be_type=self.be_type,
                be_code=args.get("beCode"),
            )
            self.be_code = self.store.be_code
            return self.store

        if self.be_type == 2:
            addresses = self._fetch_addresses()
            if not addresses:
                raise ConstraintUnsatisfiable(
                    "没有可用的配送地址。请先用 delivery-create-address 新建地址（写操作，需确认）。"
                )
            chosen = address_id or str(addresses[0].get("addressId") or "")
            if not chosen:
                raise ConstraintUnsatisfiable("配送地址缺少 addressId")
            self.address_id = chosen
            args = rules.build_store_locator_args(self.be_type, address_id=chosen)
            raw = self.backend.call_tool("delivery-query-stores", args)
        else:
            args = rules.build_store_locator_args(
                self.be_type,
                search_type=search_type,
                city=city,
                keyword=keyword,
            )
            raw = self.backend.call_tool("query-nearby-stores", args)

        stores = self._extract_list(raw, ("stores", "data", "list"))
        if not stores:
            raise ConstraintUnsatisfiable(
                "该位置没有查到可点餐门店。可以换个更宽泛的关键词，或改用其他取餐方式。"
            )

        first = stores[0]
        store = Store.from_api(first, self.be_type)
        # 调用方显式传了 beCode 就以它为准（只可能出现在 beType=5/2，上面已经拦过 beType=1）
        if be_code and rules.requires_be_code(self.be_type):
            store = Store(
                store_code=store.store_code,
                store_name=store.store_name,
                be_type=store.be_type,
                be_code=str(be_code),
                reservation=store.reservation,
                today=store.today,
            )
        # beType=5/2 必须有 beCode，否则后续链路一定失败 —— 提前拦住
        if rules.requires_be_code(self.be_type) and not store.be_code:
            raise ConstraintUnsatisfiable(
                f"门店 {store.store_code} 未返回 beCode，无法在 beType={self.be_type} 下点餐"
            )
        if self.be_type == 1:
            store = Store(
                store_code=store.store_code,
                store_name=store.store_name,
                be_type=store.be_type,
                be_code=None,
                reservation=store.reservation,
                today=store.today,
            )

        self.store = store
        self.be_code = store.be_code
        return store

    def _fetch_addresses(self) -> List[Dict[str, Any]]:
        raw = self.backend.call_tool("delivery-query-addresses", {})
        return self._extract_list(raw, ("addresses", "data", "list"))

    def fetch_menu(self) -> List[Meal]:
        """抓真实在售菜单 —— productCode 的唯一入口。"""
        if self.store is None:
            raise InvalidArgument("请先调用 locate_store()")

        args = rules.build_scene_args(
            self.be_type, be_code=self.be_code, order_type=self.order_type
        )
        args["storeCode"] = self.store.store_code

        raw = self.backend.call_tool("query-meals", args)
        meals = self._extract_products(raw)
        meals = [m for m in meals if m.product_code]
        if not meals:
            raise ConstraintUnsatisfiable(
                f"门店 {self.store.store_code} 当前没有可取到的在售餐品，建议换门店或换取餐方式。"
            )
        self.meals = meals
        return meals

    # ------------------------------------------------------------------
    # 开盒
    # ------------------------------------------------------------------
    def open_box(
        self,
        *,
        modes: Sequence[str] = (),
        budget_yuan: Optional[float] = None,
        calorie_cap: Optional[int] = None,
        taste: Optional[str] = None,
        timestamp_ms: Optional[int] = None,
        with_modification: bool = False,
    ) -> RevealCard:
        """抽一次盲盒，算价用券，产出揭晓卡。**不产生任何写操作。**"""
        if not self.meals:
            self.fetch_menu()

        normalized = normalize_modes(modes)

        # 1) 候选池
        pool = filter_by_taste(self.meals, taste if has_mode(normalized, MODE_TASTE) else None)
        if not pool:
            raise ConstraintUnsatisfiable(
                f"在售清单里没有匹配到「{taste}」的餐品。可以换个口味，或改成纯盲盒。"
            )
        pool = [m for m in pool if m]

        # 2) 随机种子（规则本来的那个：服务端毫秒时间戳）
        seed_ts = timestamp_ms
        if seed_ts is None:
            seed_ts = self._server_timestamp_ms()

        # 3) 热量粗筛（热量挑战时；匹配不到热量的商品在这里被排除，而不是当 0 卡放行）
        by_code: Dict[str, int] = {}
        if has_mode(normalized, MODE_CALORIE):
            kcal_map = self._calorie_map()
            for meal in pool:
                value = self._calories_for(meal, kcal_map)
                if value is not None:
                    by_code[meal.product_code] = value
            pool = filter_by_calories(pool, by_code, calorie_cap)
            if not pool:
                raise ConstraintUnsatisfiable(
                    f"没有单品热量低于 {calorie_cap} 大卡的餐品（或营养表里匹配不到）。"
                    "可以放宽热量上限，或换成不设热量限制的盲盒。"
                )

        # 4) 生成候选：主品取自主食类，配品优先小食/饮料，
        #    且**抽取前**就淘汰违反热量/预算约束的配品
        #    （券表要先拿到，才能准确估算券后价）
        coupons = self._fetch_coupons()
        extras = extra_item_count(budget_yuan)
        main_pool = pick_main_categories(pool)

        # 4.5) 套餐子项去重：主品若是 rounds 套餐，其内部默认选中的子商品
        #    （如「鳕鱼堡套餐」内含的中薯条）也算「已占用样式」，
        #    配品抽取时必须避开，避免默认盲盒出现样式重复。
        #    主品样式 = 主品自身 + 套餐默认子项（预取主品详情，复用特调缓存）。
        dedup_filter = self._build_dedup_filter(main_pool)

        candidates = generate_candidates(
            pool,
            seed_ts,
            CANDIDATE_COUNT,
            main_pool=main_pool,
            extras=extras,
            extra_filter=combine_filters(
                dedup_filter,
                calorie_extras_filter(calorie_cap, by_code)
                if has_mode(normalized, MODE_CALORIE)
                else None,
                budget_extras_filter(
                    rules.yuan_to_fen(budget_yuan) if budget_yuan is not None else None,
                    lambda subtotal, picked: self._payable_with_best_coupon(
                        coupons,
                        subtotal,
                        [meal.product_code for meal in picked],
                    ),
                )
                if has_mode(normalized, MODE_BUDGET)
                else None,
            ),
        )

        # 5) 逐候选评估
        evaluated = []
        for candidate in candidates:
            picks = build_combo_meals(candidate, pool, extras=extras)
            items = self._fill_combo_unit_price(
                self._to_items(picks, with_modification=with_modification)
            )
            subtotal = sum(i.subtotal_fen() for i in items)
            coupon = select_coupon(
                coupons, subtotal, [i.product_code for i in items]
            )
            items_with_coupon = attach_coupon(items, coupon)
            price = self._price(items_with_coupon)
            calories = total_calories(items_with_coupon) if has_mode(normalized, MODE_CALORIE) else None
            evaluated.append(
                {
                    "candidate": candidate,
                    "items": items_with_coupon,
                    "price": price,
                    "calories": calories,
                    "coupon": coupon,
                }
            )

        chosen = self._select(evaluated, normalized, budget_yuan, calorie_cap)
        card = self._build_card(
            chosen, normalized, budget_yuan, calorie_cap, taste, extras
        )
        self.card = card
        return card

    @staticmethod
    def _payable_with_best_coupon(
        coupons: Sequence[Coupon], subtotal_fen: int, item_codes: Sequence[str]
    ) -> int:
        """给定小计与商品，算出「用最优券之后」的应付金额。

        抽取前淘汰候选时用它做估算；与最终算价走同一套 ``select_coupon``，
        所以估算和真实券后价不会打架。商品券的实际优惠由服务端算，
        这里只能保守地按「不抵扣」估，宁可估高（更容易判为超预算），
        也不要估低导致卡上价超预算。
        """
        coupon = select_coupon(coupons, subtotal_fen, item_codes)
        if coupon is None or not coupon.discount_fen:
            return subtotal_fen
        return subtotal_fen - min(coupon.discount_fen, subtotal_fen)

    def _select(
        self,
        evaluated: List[Dict[str, Any]],
        modes: Sequence[str],
        budget_yuan: Optional[float],
        calorie_cap: Optional[int],
    ) -> Dict[str, Any]:
        """按约束筛选；全部不满足时逐级放宽（并如实标注）。"""
        def satisfies(entry: Dict[str, Any]) -> bool:
            if has_mode(modes, MODE_BUDGET) and budget_yuan is not None:
                if entry["price"].payable_fen > rules.yuan_to_fen(budget_yuan):
                    return False
            if has_mode(modes, MODE_CALORIE) and calorie_cap is not None:
                calories = entry["calories"]
                if calories is None or calories > calorie_cap:
                    return False
            return True

        for entry in evaluated:
            if satisfies(entry):
                return entry

        # 逐级放宽：预算取最接近上限，热量取最低热量
        if has_mode(modes, MODE_CALORIE) and calorie_cap is not None:
            usable = [e for e in evaluated if e["calories"] is not None]
            if not usable:
                raise ConstraintUnsatisfiable(
                    "组合热量均无法计算（营养表匹配不到），无法判定是否满足热量挑战。"
                    "已按合规要求停止，而不是估算热量。"
                )
            entry = relax_calorie([(e, e["calories"]) for e in usable])[0]
            entry["relaxed"] = True
            entry["relaxation_note"] = (
                f"没能在 {calorie_cap} 大卡内凑出组合，这是热量最低的一套（{entry['calories']} 大卡）"
            )
            return entry

        if has_mode(modes, MODE_BUDGET) and budget_yuan is not None:
            cap_fen = rules.yuan_to_fen(budget_yuan)
            entry = relax_budget(
                [(e, e["price"].payable_fen) for e in evaluated], cap_fen
            )[0]
            entry["relaxed"] = True
            entry["relaxation_note"] = (
                f"没能在 {budget_yuan:.2f} 元内凑出组合，这是最接近上限的一套"
                f"（券后 ¥{entry['price'].payable_fen / 100:.2f}）"
            )
            return entry

        return evaluated[0]

    def _build_card(
        self,
        chosen: Dict[str, Any],
        modes: Sequence[str],
        budget_yuan: Optional[float],
        calorie_cap: Optional[int],
        taste: Optional[str],
        skeleton_extras: int = 0,
    ) -> RevealCard:
        candidate: Candidate = chosen["candidate"]
        combo = Combo(
            items=chosen["items"],
            seed=candidate.seed,
            main_index=candidate.main_index,
            main_pool_size=candidate.main_pool_size,
            candidate_size=candidate.pool_size,
            rank=candidate.rank,
            relaxed=bool(chosen.get("relaxed")),
            relaxation_note=chosen.get("relaxation_note", ""),
        )

        challenge_ok = not combo.relaxed
        notes: List[str] = []
        # 约束把原始种子那一个候选筛掉了 —— 如实说明，别让用户以为是原样抽出来的
        if candidate.rank > 0:
            notes.append(
                f"原始种子抽到的组合不满足当前约束，已按步长 {SEED_STRIDE} 顺延到第 "
                f"{candidate.rank + 1} 个候选（种子 {candidate.seed}）。"
            )
        # 抽取前淘汰把配品筛掉了 —— 凑不齐骨架也要说清楚为什么，
        # 而不是让用户以为「盲盒就长这样」。
        #
        # 这里必须按**实际抽到的配品件数**措辞：budget=30 的骨架是
        # 「1 主品 + 2 配品」，可能只放得进 1 件配品 —— 那时若照抄
        # 「没有任何配品放得进去」，卡上却明明列着一件配品，就是自相矛盾。
        if (
            skeleton_extras > 0
            and (has_mode(modes, MODE_BUDGET) or has_mode(modes, MODE_CALORIE))
        ):
            drawn_extras = max(len(combo.items) - 1, 0)
            if drawn_extras == 0:
                notes.append(
                    "本次约束下没有任何配品放得进去，所以只开了一件主品。"
                    f"组合骨架（1 主品 + {skeleton_extras} 件配品）是参考区间而不是硬规则，"
                    "这里宁可少一件，也不把预算或热量撑破。"
                )
            elif drawn_extras < skeleton_extras:
                notes.append(
                    f"本次约束下只放得进 {drawn_extras} 件配品，骨架想要 {skeleton_extras} 件。"
                    "组合骨架是参考区间而不是硬规则 —— 这里不为了凑数把预算或热量撑破。"
                )
        if combo.relaxation_note:
            notes.append(combo.relaxation_note)

        return RevealCard(
            combo=combo,
            price=chosen["price"],
            store=self.store,
            modes=modes,
            budget_yuan=budget_yuan,
            calorie_cap=calorie_cap,
            taste=taste,
            coupon=chosen["coupon"],
            challenge_ok=challenge_ok,
            notes=notes,
            address_id=self.address_id,
        )

    # ------------------------------------------------------------------
    # 下单（写操作，必须显式确认）
    # ------------------------------------------------------------------
    def build_order_payload(
        self,
        *,
        take_way_code: Optional[str] = None,
        remark: Optional[str] = None,
    ) -> Dict[str, Any]:
        """构造 create-order 的参数。**构造本身无副作用。**"""
        if self.card is None:
            raise GateRejected("还没有揭晓卡，请先执行 open 流程。")
        if self.store is None:
            raise InvalidArgument("缺少门店信息")

        args = rules.build_scene_args(
            self.be_type, be_code=self.be_code, order_type=self.order_type
        )
        args["storeCode"] = self.store.store_code
        args["items"] = rules.build_items_payload(self.card.combo.items)

        # 先校验 remark：自取单传了备注要直接报错，而不是悄悄丢掉 ——
        # 静默丢弃会让用户以为备注已经写进去了。
        validated_remark = rules.validate_remark(remark, self.order_type)

        if self.order_type == ORDER_TYPE_IN_STORE:
            args["takeWayCode"] = take_way_code or rules.pick_take_way(self.card.price)
        else:
            args["addressId"] = rules.require_address_id(self.order_type, self.address_id)
            if validated_remark:
                args["remark"] = validated_remark

        return args

    def order(
        self,
        *,
        confirm: bool = False,
        user_text: Optional[str] = None,
        take_way_code: Optional[str] = None,
        remark: Optional[str] = None,
    ) -> Dict[str, Any]:
        """真实下单。

        双重保险：这里再判一次确认，即使 CLI 层被绕过也不会误下单。
        """
        from .confirmation import require_order_confirmation

        verdict = require_order_confirmation(confirm, user_text)
        if not verdict.confirmed:
            raise GateRejected(
                f"确认门禁拦截：{verdict.reason}。"
                "请先查看揭晓卡，确认无误后明确回复「确认下单」。"
            )

        payload = self.build_order_payload(take_way_code=take_way_code, remark=remark)
        return self.backend.call_tool("create-order", payload)

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------
    @staticmethod
    def _extract_list(raw: Any, keys: Sequence[str]) -> List[Dict[str, Any]]:
        """从 MCP 返回里挖出一个「对象列表」。

        真实返回统一包了一层 ``{"success":…, "data": …}``，而 ``data`` 有时是列表
        （门店、券），有时是字典（``data.addresses``、``data.meals``）。
        所以这里要按 keys 逐层找，不能只看顶层。
        """
        if isinstance(raw, list):
            return [item for item in raw if isinstance(item, dict)]
        if not isinstance(raw, dict):
            return []

        # 1) 直接按候选键找
        for key in keys:
            value = raw.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]

        # 2) 钻进 data 里再找一轮（真实返回的通用形态）
        data = raw.get("data")
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        if isinstance(data, dict):
            for key in keys:
                value = data.get(key)
                if isinstance(value, list):
                    return [item for item in value if isinstance(item, dict)]
        return []

    @staticmethod
    def _as_mapping(value: Any) -> Dict[str, Any]:
        """把 ``data`` 规范成 dict（真实返回里它偶尔是列表或缺失）。"""
        if isinstance(value, dict):
            return value
        return {}

    def _extract_products(self, raw: Any) -> List[Meal]:
        """解析 ``query-meals`` 的返回。

        真实形状::

            {"data": {
                "categories": [{"name": "饮品", "meals": [{"code": "6352"}]}, ...],
                "meals": {"6352": {"name": "…", "currentPrice": "13.5"}}
            }}

        商品编码是 ``data.meals`` 这个**字典的键**，条目里没有 ``productCode``；
        分类信息只在 ``data.categories[].name`` 上。所以要先建
        ``产品码 → 分类名`` 的索引，再把字典展开成列表。

        同时兼容「``products`` 是对象列表」这种更直白的形状（离线 fixtures 用它）。
        """
        data = self._as_mapping(raw.get("data") if isinstance(raw, dict) else None)

        # 兼容形状：products / meals 直接是列表
        for key in ("products", "meals"):
            value = data.get(key)
            if isinstance(value, list) and value:
                return [Meal.from_api(p) for p in value if isinstance(p, dict)]

        meal_map = data.get("meals")
        if not isinstance(meal_map, dict):
            return []

        # 产品码 → 分类名
        category_of: Dict[str, str] = {}
        categories = data.get("categories")
        if isinstance(categories, list):
            for block in categories:
                if not isinstance(block, dict):
                    continue
                name = str(block.get("name") or "")
                for entry in block.get("meals") or []:
                    if isinstance(entry, dict):
                        code = str(entry.get("code") or entry.get("productCode") or "")
                        if code and code not in category_of:
                            category_of[code] = name

        meals: List[Meal] = []
        for code, entry in meal_map.items():
            if not isinstance(entry, dict):
                continue
            meals.append(
                Meal.from_api(
                    entry,
                    product_code=str(code),
                    category_name=category_of.get(str(code)),
                )
            )
        return meals

    def _fetch_coupons(self) -> List[Coupon]:
        """券只从 query-store-coupons 来 —— 唯一正确来源。"""
        assert_coupon_source(_COUPON_SOURCE)
        if self.store is None:
            return []
        args = rules.build_scene_args(
            self.be_type, be_code=self.be_code, order_type=self.order_type
        )
        args["storeCode"] = self.store.store_code
        raw = self.backend.call_tool(_COUPON_SOURCE, args)
        rows = self._extract_list(raw, ("coupons", "data", "list"))
        return [Coupon.from_api(row) for row in rows]

    def _price(self, items: Sequence[CartItem]) -> PriceResult:
        args = rules.build_scene_args(
            self.be_type, be_code=self.be_code, order_type=self.order_type
        )
        args["storeCode"] = self.store.store_code if self.store else ""
        args["items"] = rules.build_items_payload(items)
        raw = self.backend.call_tool("calculate-price", args)
        return to_price_result(raw, items)

    def _fill_combo_unit_price(self, items: List[CartItem]) -> List[CartItem]:
        """把套餐主品的单价回填为服务端算价口径。

        ``query-meals.currentPrice`` 对套餐只返回**主品挂牌价**，而
        ``calculate-price`` 会把套餐的默认子项展开计价 —— 两者混排在揭晓卡上
        就出现「单项之和 ≠ 原价」。这里对疑似套餐（名含套餐信号词）的主品
        **单独调一次单件算价**（无券、无特调），用返回的原价回填
        ``unit_price_fen``，让单项价、原价合计、券后价三者口径一致。

        结果按 ``product_code`` 缓存：候选生成的 8 个主品里套餐至多占几席，
        同一商品绝不做第二次探测。普通单品零额外调用；探测失败（网络/服务端
        异常）时保守保留挂牌价，绝不让开盒流程失败。
        """
        if not items:
            return items
        head = items[0]
        if not _looks_like_combo(head.name):
            return items
        real_fen = self._combo_unit_price(head.product_code)
        if real_fen is None or real_fen == head.unit_price_fen:
            return items
        updated = list(items)
        updated[0] = CartItem(
            product_code=head.product_code,
            name=head.name,
            quantity=head.quantity,
            unit_price_fen=real_fen,
            modification_values=head.modification_values,
            coupon_id=head.coupon_id,
            coupon_code=head.coupon_code,
            calories=head.calories,
            category=head.category,
        )
        return updated

    def _combo_unit_price(self, product_code: str) -> Optional[int]:
        """单件探测套餐的真实原价（分）；失败返回 None。带缓存。"""
        if not hasattr(self, "_combo_price_cache"):
            self._combo_price_cache: Dict[str, Optional[int]] = {}
        cache = self._combo_price_cache
        if product_code in cache:
            return cache[product_code]
        probe = CartItem(
            product_code=product_code,
            name="",
            quantity=1,
            unit_price_fen=0,
        )
        try:
            original = self._price([probe]).original_total_fen
        except Exception:
            original = 0
        cache[product_code] = original or None
        return cache[product_code]

    def _server_timestamp_ms(self) -> int:
        """取服务端毫秒时间戳作种子。

        真实 ``now-time-info`` 返回 ``data.timestamp``（毫秒）。
        另外两个候选键名只是解析上的宽容（万一上游改名），
        **不表示它们真实存在** —— 取字段名错了不会报错，只会让种子静默变成 0，
        所以这里刻意多认几个名字，并且一个都取不到时直接抛错而不是返回 0。
        """
        raw = self.backend.call_tool("now-time-info", {})
        data = self._as_mapping(raw.get("data") if isinstance(raw, dict) else None)
        for source in (data, raw if isinstance(raw, dict) else {}):
            for key in ("timestamp", "timestampMs", "timestamp_ms"):
                value = source.get(key)
                if value is not None:
                    try:
                        return int(value)
                    except (TypeError, ValueError):
                        continue
        raise InvalidArgument("now-time-info 没有返回可用的毫秒时间戳")

    @staticmethod
    def _calorie_map_from_text(text: str) -> Dict[str, int]:
        """解析 ``list-nutrition-foods`` 返回的文本表。

        真实返回的 ``data`` 是一段**纯文本**，不是 JSON::

            [160]{productName,nutritionDescription,energyKj,energyKcal,protein,fat,carbohydrate,sodium,calcium}:
              猪柳麦满分,null,1288,308,16,16,24,781,213
              中薯条,null,1210,289,4,12,38,165,18

        第一行是 `[行数]{列名}:`，其余是缩进的逗号分隔行；``energyKcal`` 是第 4 列。
        """
        mapping: Dict[str, int] = {}
        header: List[str] = []
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith("[") and "{" in line:
                start, end = line.find("{"), line.rfind("}")
                if 0 <= start < end:
                    header = [c.strip() for c in line[start + 1 : end].split(",")]
                continue
            if not header:
                continue
            cells = [c.strip() for c in line.split(",")]
            if len(cells) < 2:
                continue
            name = cells[0]
            try:
                index = header.index("energyKcal")
            except ValueError:
                continue
            if index >= len(cells) or not name:
                continue
            try:
                mapping[name] = int(float(cells[index]))
            except (TypeError, ValueError):
                continue
        return mapping

    def _calorie_map(self) -> Dict[str, int]:
        if self._kcal is None:
            raw = self.backend.call_tool("list-nutrition-foods", {})
            data = raw.get("data") if isinstance(raw, dict) else None

            # 形态一：纯文本表（真实返回）
            if isinstance(data, str) and data.strip():
                self._kcal = self._calorie_map_from_text(data)
                return self._kcal

            # 形态二：对象列表（更直白的形状）
            mapping: Dict[str, int] = {}
            for food in self._extract_list(raw, ("foods", "data", "list")):
                name = str(
                    food.get("name") or food.get("productName") or ""
                ).strip()
                if not name:
                    continue
                for key in ("energyKcal", "energy", "calories", "kcal"):
                    if food.get(key) is not None:
                        try:
                            mapping[name] = int(float(food[key]))
                        except (TypeError, ValueError):
                            pass
                        break
            self._kcal = mapping
        return self._kcal

    @staticmethod
    def _calories_for(meal: Meal, kcal_map: Dict[str, int]) -> Optional[int]:
        if meal.name in kcal_map:
            return kcal_map[meal.name]
        for name, value in kcal_map.items():
            if name and (name in meal.name or meal.name in name):
                return value
        # 匹配不上就返回 None —— 绝不估算
        return None

    def _modification_values(self, meal: Meal) -> List[Dict[str, Any]]:
        """取特调并构造 modification.values（走同一个铁律函数）。

        **多轮套餐特调不生成。** 真实 ``query-meal-detail`` 对套餐返回的是
        ``rounds[].choices[]`` 结构，特调挂在「轮次 → 候选项」上，需要发
        ``roundList``；而 ``roundList`` 的载荷结构我们实测**没有被算价接口接受**
        （调用不报错但拿不到价格）。既然没有验证通过的形状，就不猜——
        宁可少带一项特调，也不要发出一个可能被静默丢弃的载荷。
        """
        if self.store is None:
            return []
        if meal.product_code not in self._details:
            args = rules.build_scene_args(
                self.be_type, be_code=self.be_code, order_type=self.order_type
            )
            args["storeCode"] = self.store.store_code
            args["code"] = meal.product_code
            raw = self.backend.call_tool("query-meal-detail", args)
            self._details[meal.product_code] = MealDetail.from_api(raw)
        detail = self._details[meal.product_code]
        if detail.has_rounds and not detail.groups:
            # 只在「有顶层特调组」时才继续；纯 rounds 结构交给上层跳过并说明
            return []
        if not detail.support_modify or not detail.groups:
            return []
        return rules.build_modification_values(detail.groups)

    def _detail_for(self, meal: Meal) -> MealDetail:
        """获取餐品详情并缓存（与特调共用同一份 ``_details`` 缓存）。

        去重和特调都需要 ``query-meal-detail`` 的返回；这里只做「取并缓存」，
        不判断用途 —— 避免两处各自写一套 fetch 逻辑导致缓存不一致。
        """
        if meal.product_code not in self._details:
            args = rules.build_scene_args(
                self.be_type, be_code=self.be_code, order_type=self.order_type
            )
            args["storeCode"] = self.store.store_code if self.store else ""
            args["code"] = meal.product_code
            raw = self.backend.call_tool("query-meal-detail", args)
            self._details[meal.product_code] = MealDetail.from_api(raw)
        return self._details[meal.product_code]

    def _build_dedup_filter(
        self, main_pool: Sequence[Meal]
    ) -> Optional[Callable[[Meal, Sequence[Meal]], bool]]:
        """构造「配品不与主品（含套餐默认子项）样式重复」的抽取前过滤器。

        ``extra_filter(meal, picked)`` 的 ``picked[0]`` 就是主品。这里把
        「主品自身样式」列入已占用集；若主品名命中套餐信号词（:data:`COMBO_NAME_HINTS`），
        再惰性取它的详情、展开默认子项样式一并占用。已抽配品的样式也累加进去。

        关键取舍：只有「疑似套餐」的主品才查 ``query-meal-detail``，普通单品
        （如巨无霸）零额外调用 —— 保住「不请求特调就不查详情」的最小请求契约。
        """
        if not main_pool:
            return None

        def _ok(meal: Meal, picked: Sequence[Meal]) -> bool:
            main_meal = picked[0] if picked else None
            occupied: List[str] = []
            if main_meal is not None:
                occupied.append(style_key(main_meal))
                if _looks_like_combo(main_meal.name):
                    detail = self._detail_for(main_meal)
                    for name, category in zip(
                        detail.round_products, detail.round_categories
                    ):
                        occupied.append(style_key_for(name, category))
            for already in picked[1:]:
                occupied.append(style_key(already))
            return style_key(meal) not in frozenset(occupied)

        return _ok

    def _to_items(self, meals: Sequence[Meal], *, with_modification: bool = False) -> List[CartItem]:
        kcal_map = self._calorie_map() if self._kcal is not None else None
        items: List[CartItem] = []
        for meal in meals:
            mods = self._modification_values(meal) if with_modification else []
            calories = None
            if kcal_map is not None:
                calories = self._calories_for(meal, kcal_map)
            items.append(
                CartItem(
                    product_code=meal.product_code,
                    name=meal.name,
                    quantity=1,
                    unit_price_fen=meal.price_fen,
                    modification_values=mods,
                    calories=calories,
                    category=meal.category,
                )
            )
        return items


__all__ = ["BlindBoxPipeline", "MODE_BUDGET", "MODE_CALORIE", "MODE_TASTE", "describe_modes"]
