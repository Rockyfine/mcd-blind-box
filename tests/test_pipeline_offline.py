"""端到端离线测试：三条链路 × 四种模式，以及安全闸。

这是最有价值的一组测试，因为它验证的是**真实走完的流程**：

- 到店自取 / 得来速 / 麦乐送 三条链路的参数差异
- ``open`` 全程不产生任何写操作
- 没有 ``--confirm`` 时 ``create-order`` **一次都没被调用**
- 券只来自 ``query-store-coupons``
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcd_blind_box import rules  # noqa: E402
from mcd_blind_box.constants import (  # noqa: E402
    ALLOWED_TOOLS,
    CAT_MAIN,
    ORDER_TYPE_DELIVERY,
    ORDER_TYPE_IN_STORE,
    WRITE_TOOLS,
)
from mcd_blind_box.errors import (  # noqa: E402
    ConstraintUnsatisfiable,
    GateRejected,
    InvalidArgument,
    SceneMismatch,
)
from mcd_blind_box.fixtures_client import FixtureBackend  # noqa: E402
from mcd_blind_box.pipeline import BlindBoxPipeline  # noqa: E402
from mcd_blind_box.reveal import render_confirm_panel, render_reveal, render_share_text  # noqa: E402
from mcd_blind_box.rules import be_type_for_scene  # noqa: E402

SEED = 1789000000000
FIXTURES_ROOT = Path(__file__).resolve().parent.parent / "fixtures"
DT_STORE = "100003"


def restricted_fixtures(tmp: str, keep_codes) -> str:
    """复制一份 fixtures，但把菜单裁剪到只剩 ``keep_codes``。

    只动 ``meals.json``，而且**必须同时裁掉分类索引里的引用**：
    ``data.meals`` 是商品本身，``data.categories[].meals[]`` 是「编码 → 分类」
    的索引，两边不一致的话解析出来的分类就是错的。
    """
    import json

    for src in FIXTURES_ROOT.glob("*.json"):
        Path(tmp, src.name).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")

    keep = {str(code) for code in keep_codes}
    menus = json.loads((FIXTURES_ROOT / "meals.json").read_text(encoding="utf-8"))
    for by_order_type in menus.values():
        for recorded in by_order_type.values():
            data = recorded["data"]
            data["meals"] = {c: v for c, v in data["meals"].items() if c in keep}
            for category in data["categories"]:
                category["meals"] = [
                    entry for entry in category["meals"] if entry["code"] in keep
                ]
    Path(tmp, "meals.json").write_text(
        json.dumps(menus, ensure_ascii=False), encoding="utf-8"
    )
    return tmp


def build(
    scene="pickup",
    *,
    timestamp=SEED,
    modes=(),
    budget=None,
    calorie=None,
    taste=None,
    with_mod=False,
    store_code=None,
    be_code=None,
    address_id=None,
    fixtures=None,
):
    backend = FixtureBackend(fixtures)
    pipeline = BlindBoxPipeline(backend, offline=True)
    pipeline.set_scene(be_type_for_scene(scene))
    pipeline.locate_store(
        store_code=store_code, be_code=be_code, address_id=address_id
    )
    pipeline.fetch_menu()
    card = pipeline.open_box(
        modes=list(modes),
        budget_yuan=budget,
        calorie_cap=calorie,
        taste=taste,
        timestamp_ms=timestamp,
        with_modification=with_mod,
    )
    return pipeline, card, backend


class TestThreeChains(unittest.TestCase):
    def test_pickup(self):
        pipeline, card, backend = build("pickup")
        self.assertEqual(pipeline.be_type, 1)
        self.assertEqual(pipeline.order_type, ORDER_TYPE_IN_STORE)
        self.assertIsNone(pipeline.be_code)
        self.assertIsNone(card.store.be_code)
        self.assertIn("query-nearby-stores", backend.calls)

    def test_dt_uses_becode_from_stores(self):
        pipeline, card, backend = build("dt")
        self.assertEqual(pipeline.be_type, 5)
        self.assertEqual(pipeline.order_type, ORDER_TYPE_IN_STORE)
        self.assertTrue(pipeline.be_code)
        self.assertEqual(card.store.be_code, pipeline.be_code)
        self.assertIn("query-nearby-stores", backend.calls)

    def test_delivery_uses_address_and_delivery_stores(self):
        pipeline, card, backend = build("delivery")
        self.assertEqual(pipeline.be_type, 2)
        self.assertEqual(pipeline.order_type, ORDER_TYPE_DELIVERY)
        self.assertTrue(pipeline.address_id)
        self.assertTrue(pipeline.be_code)
        self.assertIn("delivery-query-addresses", backend.calls)
        self.assertIn("delivery-query-stores", backend.calls)
        self.assertNotIn("query-nearby-stores", backend.calls)

    def test_pickup_never_sends_becode_in_any_call(self):
        _, _, backend = build("pickup")
        for entry in backend.call_args:
            with self.subTest(tool=entry["name"]):
                self.assertNotIn("beCode", entry["arguments"])

    def test_dt_and_delivery_always_send_becode_to_order_tools(self):
        for scene in ("dt", "delivery"):
            with self.subTest(scene=scene):
                _, _, backend = build(scene)
                order_tools = [
                    e
                    for e in backend.call_args
                    if e["name"]
                    in ("query-meals", "query-store-coupons", "calculate-price", "query-meal-detail")
                ]
                self.assertTrue(order_tools)
                for entry in order_tools:
                    self.assertTrue(entry["arguments"].get("beCode"))

    def test_order_type_is_always_consistent(self):
        for scene, expected in (("pickup", 1), ("dt", 1), ("delivery", 2)):
            with self.subTest(scene=scene):
                _, _, backend = build(scene)
                for entry in backend.call_args:
                    if "orderType" in entry["arguments"]:
                        self.assertEqual(entry["arguments"]["orderType"], expected)


class TestNoWriteDuringOpen(unittest.TestCase):
    def test_no_write_tool_is_ever_called_by_open(self):
        for scene in ("pickup", "dt", "delivery"):
            with self.subTest(scene=scene):
                _, _, backend = build(scene)
                self.assertEqual(set(backend.calls) & WRITE_TOOLS, set())

    def test_only_whitelisted_tools_are_used(self):
        _, _, backend = build("pickup")
        for name in backend.calls:
            with self.subTest(tool=name):
                self.assertIn(name, ALLOWED_TOOLS)

    def test_coupons_come_only_from_store_coupons(self):
        _, _, backend = build("pickup")
        self.assertIn("query-store-coupons", backend.calls)
        self.assertNotIn("query-my-coupons", backend.calls)
        self.assertNotIn("available-coupons", backend.calls)

    def test_price_is_always_computed(self):
        _, _, backend = build("pickup")
        self.assertIn("calculate-price", backend.calls)

    def test_write_calls_audit_is_empty(self):
        """``Backend.write_calls`` 是给「开盒只读」这句话做机械审计用的。

        离线与联网共用同一份记录，所以这条断言在两种模式下含义完全相同。
        """
        for scene in ("pickup", "dt", "delivery"):
            with self.subTest(scene=scene):
                _, _, backend = build(scene)
                self.assertEqual(backend.write_calls, [])

    def test_write_calls_audit_sees_a_confirmed_order(self):
        """审计不是永远返回空 —— 真下单它必须看得见。"""
        pipeline, _, backend = build("pickup")
        pipeline.order(confirm=True)
        self.assertEqual(backend.write_calls, ["create-order"])


class TestAllModes(unittest.TestCase):
    def test_blind(self):
        _, card, _ = build("pickup", modes=["blind"])
        self.assertEqual(tuple(card.modes), ("blind",))
        self.assertTrue(card.challenge_ok)

    def test_budget_either_fits_or_says_it_did_not(self):
        """契约是这个：要么真的在预算内，要么如实说明已放宽。二者必居其一。"""
        _, card, _ = build("pickup", modes=["budget"], budget=30)
        if card.challenge_ok:
            self.assertLessEqual(card.price.payable_fen, 3000)
        else:
            self.assertTrue(any("最接近上限" in n for n in card.notes))

    def test_budget_is_satisfied_thanks_to_pre_draw_pruning(self):
        """抽取前淘汰生效后，常见预算区间不该动不动就「已放宽」。"""
        for cap in (20, 30, 40, 50):
            hits = 0
            for seed in range(0, 40):
                with self.subTest(cap=cap, seed=seed):
                    _, card, _ = build("pickup", modes=["budget"], budget=cap, timestamp=seed)
                    hits += 1 if card.challenge_ok else 0
            self.assertEqual(hits, 40, f"预算 {cap} 元下应有 40/40 达标，实际 {hits}")

    def test_calorie(self):
        _, card, _ = build("delivery", modes=["calorie"], calorie=600)
        total = card.combo.total_calories()
        self.assertIsNotNone(total)
        self.assertLessEqual(total, 600)

    def test_taste(self):
        _, card, _ = build("dt", modes=["taste"], taste="辣")
        for item in card.combo.items:
            self.assertTrue(rules.build_items_payload([item]))

    def test_stacked_modes(self):
        _, card, _ = build("pickup", modes=["budget", "calorie"], budget=40, calorie=800)
        if card.challenge_ok:
            self.assertLessEqual(card.price.payable_fen, 4000)
            self.assertLessEqual(card.combo.total_calories(), 800)

    def test_every_mode_produces_at_least_one_item(self):
        for modes, kwargs in (
            (["blind"], {}),
            (["budget"], {"budget": 30}),
            (["calorie"], {"calorie": 600}),
            (["taste"], {"taste": "鸡"}),
        ):
            with self.subTest(modes=modes):
                _, card, _ = build("pickup", modes=modes, **kwargs)
                self.assertGreaterEqual(len(card.combo.items), 1)


class TestComboComposition(unittest.TestCase):
    def test_only_one_main(self):
        for seed in range(0, 40):
            with self.subTest(seed=seed):
                _, card, _ = build("pickup", timestamp=seed)
                self.assertEqual(card.combo.items[0].category, CAT_MAIN)

    def test_second_item_is_not_a_main_when_sides_exist(self):
        for seed in range(0, 40):
            with self.subTest(seed=seed):
                _, card, _ = build("pickup", timestamp=seed)
                if len(card.combo.items) > 1:
                    # 分类来自 query-meals 的 data.categories，不是编码猜出来的
                    self.assertNotEqual(card.combo.items[1].category, CAT_MAIN)

    def test_all_items_come_from_the_menu(self):
        """productCode 只能来自 query-meals 的返回，一个都不能是自己编的。"""
        pipeline, card, backend = build("pickup")
        menu_call = [e for e in backend.call_args if e["name"] == "query-meals"][0]
        self.assertTrue(menu_call["arguments"]["storeCode"])
        on_sale = {meal.product_code for meal in pipeline.meals}
        self.assertTrue(on_sale)
        for item in card.combo.items:
            self.assertIn(item.product_code, on_sale)

    def test_category_never_leaks_into_the_payload(self):
        """category 只是展示用的，接口没有这个字段 —— 发出去就是个未定义参数。"""
        pipeline, card, _ = build("pickup")
        for item in pipeline.build_order_payload()["items"]:
            self.assertNotIn("category", item)

    def test_no_duplicate_items(self):
        _, card, _ = build("pickup")
        codes = [i.product_code for i in card.combo.items]
        self.assertEqual(len(codes), len(set(codes)))


class TestComboDedup(unittest.TestCase):
    """默认盲盒是「单人餐」：配品不得与主品（含套餐默认子项）样式重复。"""

    def test_combo_main_suppresses_side_of_same_style(self):
        """主品是「鳕鱼堡套餐」（内含中薯条）时，配品不得再抽薯条样式。"""
        from mcd_blind_box.challenge import style_key

        for seed in range(0, 60):
            with self.subTest(seed=seed):
                _, card, _ = build("pickup", timestamp=seed)
                head = card.combo.items[0]
                if head.name != "鳕鱼堡套餐":
                    continue
                # 套餐内含「中薯条」（side:薯条）—— 配品绝不能是薯条样式
                for item in card.combo.items[1:]:
                    self.assertNotEqual(
                        style_key(item), "side:薯条",
                        f"seed={seed} 配品 {item.name} 与套餐内中薯条样式重复",
                    )

    def test_combo_sub_items_are_parsed_from_rounds(self):
        """MealDetail 能从 rounds[].choices[] 解析出默认子项名称与品类。"""
        from mcd_blind_box.models import MealDetail
        import json

        details = json.loads((FIXTURES_ROOT / "meal_details.json").read_text(encoding="utf-8"))
        detail = MealDetail.from_api(details["1005"])
        self.assertTrue(detail.has_rounds)
        # 鳕鱼堡套餐 = 鳕鱼堡（主食） + 中薯条（小食）
        self.assertEqual(detail.round_products, ("鳕鱼堡", "中薯条"))
        self.assertEqual(detail.round_categories, ("main", "side"))

    def test_plain_main_triggers_no_detail_lookup(self):
        """菜单里没有套餐时，普通单品主品不因去重产生 query-meal-detail 调用。"""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            fixtures = restricted_fixtures(tmp, {"1001", "2001"})  # 巨无霸 + 中薯条
            _, card, backend = build("pickup", timestamp=0, with_mod=False, fixtures=fixtures)
            self.assertEqual(card.combo.items[0].name, "巨无霸")
            self.assertNotIn("query-meal-detail", backend.calls)


class _BrokenProbeBackend(FixtureBackend):
    """单件算价探测一律抛错，用来验证「探测失败保守保留挂牌价」。"""

    def call_tool(self, name, args):
        items = args.get("items") or []
        if name == "calculate-price" and len(items) == 1:
            raise RuntimeError("server down")
        return super().call_tool(name, args)


class TestComboUnitPriceFillBack(unittest.TestCase):
    """套餐主品单价回填：单项价、原价合计、券后价三者口径一致。

    真实服务端对套餐（rounds 结构）的 calculate-price 按展开默认子项计价，
    高于菜单挂牌价（火腿扒麦满分套餐实测 10.20 挂牌 vs 17.00 展开，见
    docs/live-verification.md）。离线用 combo_prices.json 的录制复现该口径差。
    """

    def test_combo_main_unit_price_is_filled_back(self):
        """timestamp=4 主品为鳕鱼堡套餐：单项价回填为展开价 24.00。"""
        pipeline, card, backend = build("pickup", timestamp=4)
        head = card.combo.items[0]
        self.assertEqual(head.product_code, "1005")
        self.assertEqual(head.unit_price_fen, 2400)
        # 口径一致：原价 == 各项单价 × 数量之和
        self.assertEqual(
            card.price.original_total_fen,
            sum(i.subtotal_fen() for i in card.combo.items),
        )
        # 探测恰好发生一次（缓存），且 payload 只有主品一件、不带券
        probes = [
            e for e in backend.call_args
            if e["name"] == "calculate-price"
            and len(e["arguments"].get("items") or []) == 1
            and e["arguments"]["items"][0].get("productCode") == "1005"
        ]
        self.assertEqual(len(probes), 1)
        self.assertNotIn("couponId", probes[0]["arguments"]["items"][0])

    def test_probe_is_cached_across_calls(self):
        """同一套餐商品重复取价不再发第二次探测调用。"""
        backend = FixtureBackend()
        pipeline = BlindBoxPipeline(backend, offline=True)
        pipeline.set_scene(be_type_for_scene("pickup"))
        pipeline.locate_store()
        pipeline.fetch_menu()
        first = pipeline._combo_unit_price("1005")
        second = pipeline._combo_unit_price("1005")
        self.assertEqual(first, 2400)
        self.assertEqual(second, 2400)
        probes = [
            e for e in backend.call_args
            if e["name"] == "calculate-price"
            and len(e["arguments"].get("items") or []) == 1
            and e["arguments"]["items"][0].get("productCode") == "1005"
        ]
        self.assertEqual(len(probes), 1)

    def test_plain_main_never_probed(self):
        """普通单品（巨无霸）主品单价不被改动；单件探测只针对套餐商品。

        探测发生在候选评估阶段：8 个候选的主品若命中套餐信号词就会各探测
        一次（同一商品有缓存），与「最终选中的主品是不是套餐」无关。
        """
        pipeline, card, backend = build("pickup", timestamp=0)
        head = card.combo.items[0]
        self.assertEqual(head.name, "巨无霸")
        # 主品单价 = 菜单挂牌价（巨无霸 1001 → 28.50），未被回填改动
        self.assertEqual(head.unit_price_fen, 2850)
        # 所有单件探测的 productCode 都是套餐商品，绝无普通单品
        for e in backend.call_args:
            if e["name"] != "calculate-price":
                continue
            items = e["arguments"].get("items") or []
            if len(items) == 1:
                self.assertEqual(
                    items[0].get("productCode"),
                    "1005",
                    f"普通单品不应触发单件探测：{e['arguments']}",
                )

    def test_probe_failure_keeps_menu_price(self):
        """探测失败（服务端异常）时保守保留挂牌价，开盒流程不失败。"""
        backend = _BrokenProbeBackend()
        pipeline = BlindBoxPipeline(backend, offline=True)
        pipeline.set_scene(be_type_for_scene("pickup"))
        pipeline.locate_store()
        pipeline.fetch_menu()
        self.assertIsNone(pipeline._combo_unit_price("1005"))
        # 全流程仍能开出盒（回填静默降级，不抛错）
        card = pipeline.open_box(timestamp_ms=4)
        self.assertTrue(card.combo.items)


class TestDeterminism(unittest.TestCase):
    def test_same_seed_same_combo(self):
        _, first, _ = build("pickup", timestamp=987654321)
        _, second, _ = build("pickup", timestamp=987654321)
        self.assertEqual(
            [i.product_code for i in first.combo.items],
            [i.product_code for i in second.combo.items],
        )
        self.assertEqual(first.price.payable_fen, second.price.payable_fen)

    def test_seed_line_is_verifiable(self):
        _, card, _ = build("pickup", timestamp=987654321)
        combo = card.combo
        self.assertEqual(combo.main_index, combo.seed % combo.main_pool_size)

    def test_different_seeds_can_differ(self):
        seen = set()
        for seed in range(0, 30):
            _, card, _ = build("pickup", timestamp=seed)
            seen.add(tuple(i.product_code for i in card.combo.items))
        self.assertGreater(len(seen), 1)

    def test_card_pool_sizes_match_fixture(self):
        _, card, _ = build("pickup")
        self.assertEqual(card.combo.main_pool_size, 11)
        self.assertEqual(card.combo.candidate_size, 25)


class TestServerSeed(unittest.TestCase):
    """不传 --seed 时，种子必须来自服务的毫秒时间戳。

    这是「随机可复核」那条承诺的起点：种子不是本地随机的，
    而是 now-time-info 返回的 data.timestamp。
    """

    def test_default_seed_comes_from_the_server_timestamp(self):
        backend = FixtureBackend()
        pipeline = BlindBoxPipeline(backend, offline=True)
        pipeline.set_scene(1)
        pipeline.locate_store()
        card = pipeline.open_box()
        self.assertIn("now-time-info", backend.calls)
        # fixtures/time.json 录制的 data.timestamp
        self.assertEqual(card.combo.seed, 1789000000000)

    def test_explicit_seed_wins_and_skips_the_time_call(self):
        _, card, backend = build("pickup", timestamp=987654321)
        self.assertEqual(card.combo.seed, 987654321)
        self.assertNotIn("now-time-info", backend.calls)


class TestConstraintDegradation(unittest.TestCase):
    def test_taste_without_match_raises(self):
        # 这家门店只剩巨无霸，菜单里没有任何「鱼」
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            fixtures = restricted_fixtures(tmp, {"1001"})
            with self.assertRaises(ConstraintUnsatisfiable):
                build("pickup", modes=["taste"], taste="鱼", fixtures=fixtures)

    def test_taste_match_still_works_on_restricted_menu(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            fixtures = restricted_fixtures(tmp, {"1005"})  # 只留鳕鱼堡套餐
            _, card, _ = build("pickup", modes=["taste"], taste="鱼", fixtures=fixtures)
            self.assertEqual(card.combo.items[0].name, "鳕鱼堡套餐")

    def test_impossible_calorie_cap_raises(self):
        with self.assertRaises(ConstraintUnsatisfiable):
            build("pickup", modes=["calorie"], calorie=5)

    def test_impossible_budget_relaxes_and_says_so(self):
        _, card, _ = build("pickup", modes=["budget"], budget=5)
        self.assertFalse(card.challenge_ok)
        self.assertTrue(card.notes)
        self.assertTrue(any("最接近上限" in n for n in card.notes))

    def test_relaxation_is_never_silent(self):
        _, card, _ = build("pickup", modes=["budget"], budget=5)
        self.assertIn("未达标（已放宽）", render_reveal(card))

    def test_shortfall_note_never_contradicts_the_card(self):
        """备注说的件数必须和卡上实际列出的件数一致。

        踩过的坑：budget=30 的骨架是「1 主品 + 2 配品」，可能只放得进 1 件；
        这时备注若照抄「没有任何配品放得进去」，卡上却明明列着一件配品，
        用户一眼就看得出是在胡说。
        """
        for kwargs in ({"budget": 20}, {"budget": 30}, {"budget": 50}, {"calorie": 800}):
            modes = ["budget"] if "budget" in kwargs else ["calorie"]
            for seed in range(20):
                with self.subTest(kwargs=kwargs, seed=seed):
                    _, card, _ = build("pickup", modes=modes, timestamp=seed, **kwargs)
                    items = len(card.combo.items)
                    if any("没有任何配品" in n for n in card.notes):
                        self.assertEqual(items, 1)
                    if any("只放得进" in n for n in card.notes):
                        self.assertGreater(items, 1)

    def test_shifted_candidate_is_disclosed(self):
        _, card, _ = build("pickup", modes=["budget"], budget=5)
        self.assertGreater(card.combo.rank, 0)
        self.assertTrue(any("顺延" in n for n in card.notes))


class TestCalorieHonesty(unittest.TestCase):
    def test_calories_only_reported_inside_calorie_mode(self):
        _, card, _ = build("pickup", modes=["blind"])
        self.assertIsNone(card.combo.total_calories())

    def test_calorie_mode_reports_a_number(self):
        _, card, _ = build("pickup", modes=["calorie"], calorie=900)
        self.assertIsNotNone(card.combo.total_calories())

    def test_nutrition_lookup_uses_the_official_tool(self):
        _, _, backend = build("pickup", modes=["calorie"], calorie=900)
        self.assertIn("list-nutrition-foods", backend.calls)

    def test_nutrition_is_not_looked_up_when_not_needed(self):
        _, _, backend = build("pickup", modes=["blind"])
        self.assertNotIn("list-nutrition-foods", backend.calls)


class TestModification(unittest.TestCase):
    def test_pickup_tool_is_called_when_requested(self):
        _, _, backend = build("pickup", timestamp=11, with_mod=True)
        self.assertIn("query-meal-detail", backend.calls)

    def test_keys_follow_the_iron_rule(self):
        """特调 key 铁律：选中用 selectedKey，未选中用 **unselectedKey** 且仍要传。

        fixture 里 1001 的那一组两个值都带 unselectedKey，且默认一选一未选 ——
        如果代码把未选中项也按 selectedKey 发出去，这里的 "0-0" 会变成 "0-1"。
        """
        _, card, _ = build("pickup", timestamp=11, with_mod=True)
        head = card.combo.items[0]
        self.assertEqual(head.product_code, "1001")
        values = {v["code"]: (v["key"], v["quantity"]) for v in head.modification_values}
        self.assertEqual(values["V1001"], ("0-1", 1))  # 默认选中
        self.assertEqual(values["V1002"], ("0-0", 0))  # 默认未选中，但**仍然要传**

    def test_rounds_only_detail_contributes_no_modification(self):
        """纯 rounds 结构的套餐详情不生成特调 —— 不猜一个没验证过的载荷。"""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            fixtures = restricted_fixtures(tmp, {"1005"})  # 鳕鱼堡，详情只有 rounds
            _, card, _ = build("pickup", timestamp=0, with_mod=True, fixtures=fixtures)
            head = card.combo.items[0]
            self.assertEqual(head.product_code, "1005")
            self.assertEqual(head.modification_values, [])

    def test_no_modification_when_not_requested(self):
        """不请求特调时不生成特调载荷；详情查询只服务于套餐去重，非套餐主品零调用。

        去重是默认基础行为，主品若疑似套餐（名含「套餐/三件套」等信号词）会查一次
        详情展开其内部默认子项，避免配品与套餐内含商品样式重复 —— 这是合理且必要的。
        但**普通单品主品**（如巨无霸）绝不因此产生任何 query-meal-detail 调用。
        """
        from mcd_blind_box.constants import COMBO_NAME_HINTS

        pipeline, card, backend = build("pickup", timestamp=11, with_mod=False)
        # 核心契约：不请求特调就不生成特调载荷
        self.assertEqual(card.combo.items[0].modification_values, [])
        # 详情查询（若有）只可能来自「疑似套餐」的主品，不来自普通单品
        name_of = {meal.product_code: meal.name for meal in pipeline.meals}
        queried_codes = {
            e["arguments"].get("code") for e in backend.call_args
            if e["name"] == "query-meal-detail"
        }
        for code in queried_codes:
            name = name_of.get(code, "")
            self.assertTrue(
                any(hint in name for hint in COMBO_NAME_HINTS),
                f"普通单品「{name}」不该触发详情查询",
            )

    def test_modification_is_carried_into_the_payload(self):
        pipeline, card, _ = build("pickup", timestamp=11, with_mod=True)
        payload = pipeline.build_order_payload()
        head = payload["items"][0]
        self.assertIn("modification", head)
        self.assertTrue(head["modification"]["values"])


class TestOrderGate(unittest.TestCase):
    def test_order_without_confirm_is_rejected(self):
        pipeline, _, backend = build("pickup")
        with self.assertRaises(GateRejected):
            pipeline.order(confirm=False)
        self.assertNotIn("create-order", backend.calls)

    def test_order_without_confirm_never_calls_create_order(self):
        pipeline, _, backend = build("pickup")
        for text in (None, "", "好的", "确认下单吗？", "先别下单"):
            with self.subTest(text=text):
                with self.assertRaises(GateRejected):
                    pipeline.order(confirm=False, user_text=text)
        self.assertNotIn("create-order", backend.calls)

    def test_confirmed_order_succeeds(self):
        pipeline, _, backend = build("pickup")
        result = pipeline.order(confirm=True)
        self.assertEqual(backend.calls.count("create-order"), 1)
        self.assertEqual(result["data"]["orderId"], "OFFLINE0001")
        self.assertTrue(result["data"]["offline"])
        self.assertIn("离线假订单", result["data"]["source"])

    def test_user_text_can_confirm(self):
        pipeline, _, backend = build("pickup")
        pipeline.order(confirm=False, user_text="确认下单")
        self.assertEqual(backend.calls.count("create-order"), 1)

    def test_order_before_open_is_rejected(self):
        pipeline = BlindBoxPipeline(FixtureBackend(), offline=True)
        pipeline.set_scene(1)
        with self.assertRaises(GateRejected):
            pipeline.order(confirm=True)


class TestOrderPayload(unittest.TestCase):
    def test_pickup_payload_shape(self):
        pipeline, card, _ = build("pickup")
        payload = pipeline.build_order_payload()
        self.assertEqual(payload["beType"], 1)
        self.assertEqual(payload["orderType"], 1)
        self.assertNotIn("beCode", payload)
        self.assertNotIn("addressId", payload)
        self.assertNotIn("remark", payload)
        self.assertEqual(payload["takeWayCode"], card.price.first_take_way_code())
        self.assertTrue(payload["items"])

    def test_dt_payload_has_becode_and_take_way(self):
        pipeline, _, _ = build("dt")
        payload = pipeline.build_order_payload()
        self.assertTrue(payload["beCode"])
        self.assertTrue(payload["takeWayCode"])
        self.assertNotIn("addressId", payload)

    def test_take_way_code_is_never_invented(self):
        """takeWayCode 只能取自算价返回的 takeWayList —— 每个场景的 code 都不一样。

        实战确认：到店是 eat-in/take-in-store，得来速是 take-dt-quick。
        硬编码任何一个都会在另一个场景下发出非法参数。
        """
        expected = {"pickup": "eat-in", "dt": "take-dt-quick"}
        for scene, code in expected.items():
            with self.subTest(scene=scene):
                pipeline, _, _ = build(scene)
                self.assertEqual(pipeline.build_order_payload()["takeWayCode"], code)

    def test_delivery_payload_has_address_but_no_take_way(self):
        pipeline, _, _ = build("delivery")
        payload = pipeline.build_order_payload()
        self.assertEqual(payload["addressId"], "A1001")
        self.assertNotIn("takeWayCode", payload)
        self.assertTrue(payload["beCode"])

    def test_delivery_remark_is_included(self):
        pipeline, _, _ = build("delivery")
        payload = pipeline.build_order_payload(remark="少冰，谢谢")
        self.assertEqual(payload["remark"], "少冰，谢谢")

    def test_pickup_remark_is_rejected(self):
        pipeline, _, _ = build("pickup")
        with self.assertRaises(InvalidArgument):
            pipeline.build_order_payload(remark="放前台")

    def test_explicit_take_way_code_wins(self):
        pipeline, _, _ = build("pickup")
        payload = pipeline.build_order_payload(take_way_code="TW999")
        self.assertEqual(payload["takeWayCode"], "TW999")

    def test_no_reservation_date_is_invented(self):
        for scene in ("pickup", "dt", "delivery"):
            with self.subTest(scene=scene):
                pipeline, _, _ = build(scene)
                payload = pipeline.build_order_payload()
                self.assertNotIn("reservationDate", payload)

    def test_items_carry_only_real_codes(self):
        pipeline, _, _ = build("pickup")
        payload = pipeline.build_order_payload()
        for item in payload["items"]:
            self.assertTrue(item["productCode"])
            self.assertGreaterEqual(item["quantity"], 1)


class TestSceneSafety(unittest.TestCase):
    def test_pickup_with_becode_is_rejected(self):
        with self.assertRaises(SceneMismatch):
            build("pickup", be_code="B999")

    def test_switching_scene_resets_becode(self):
        backend = FixtureBackend()
        pipeline = BlindBoxPipeline(backend, offline=True)
        pipeline.set_scene(5)
        pipeline.locate_store()
        self.assertTrue(pipeline.be_code)
        pipeline.set_scene(1)
        self.assertIsNone(pipeline.be_code)
        self.assertIsNone(pipeline.store)

    def test_dt_with_explicit_store_code_without_becode_is_rejected(self):
        """指定门店时必须同时给 beCode —— beType=5 的 beCode 不能凭空造。"""
        with self.assertRaises(SceneMismatch):
            build("dt", store_code=DT_STORE, be_code=None)

    def test_dt_store_without_becode_in_response_is_rejected(self):
        """门店是查出来的、但服务端没回 beCode，也要提前拦住，不能等到算价才炸。"""
        import json
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            for src in FIXTURES_ROOT.glob("*.json"):
                Path(tmp, src.name).write_text(
                    src.read_text(encoding="utf-8"), encoding="utf-8"
                )
            stores = json.loads((FIXTURES_ROOT / "stores.json").read_text(encoding="utf-8"))
            for row in stores["5"]["data"]:
                row["beCode"] = None
            Path(tmp, "stores.json").write_text(
                json.dumps(stores, ensure_ascii=False), encoding="utf-8"
            )
            with self.assertRaises(ConstraintUnsatisfiable):
                build("dt", fixtures=tmp)

    def test_explicit_store_code_with_becode_works(self):
        pipeline, card, _ = build("dt", store_code=DT_STORE, be_code="DT9")
        self.assertEqual(pipeline.be_code, "DT9")
        self.assertEqual(card.store.store_code, DT_STORE)

    def test_delivery_without_addresses_raises(self):
        import json
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            for src in FIXTURES_ROOT.glob("*.json"):
                Path(tmp, src.name).write_text(
                    src.read_text(encoding="utf-8"), encoding="utf-8"
                )
            Path(tmp, "addresses.json").write_text(
                json.dumps({"success": True, "code": 200, "data": {"addresses": []}}),
                encoding="utf-8",
            )
            with self.assertRaises(ConstraintUnsatisfiable):
                build("delivery", fixtures=tmp)


class TestRendering(unittest.TestCase):
    def test_reveal_has_main_and_side_labels(self):
        _, card, _ = build("pickup")
        text = render_reveal(card)
        self.assertIn("主品", text)
        self.assertIn("门店", text)
        self.assertIn("确认下单", text)

    def test_reveal_shows_verifiable_seed_line(self):
        _, card, _ = build("pickup", timestamp=987654321)
        text = render_reveal(card)
        self.assertIn(str(card.combo.seed), text)
        self.assertIn(f"mod {card.combo.main_pool_size}", text)

    def test_taste_suffix_does_not_leak_onto_other_modes(self):
        _, card, _ = build("pickup", modes=["budget", "taste"], budget=40, taste="辣")
        title = render_reveal(card).splitlines()[0]
        self.assertIn("口味挑战（辣）", title)
        self.assertIn("预算挑战", title)
        self.assertNotIn("预算挑战（辣）", title)

    def test_reveal_never_promises_an_order(self):
        _, card, _ = build("pickup")
        self.assertIn("才会真实下单", render_reveal(card))

    def test_confirm_panel_lists_items(self):
        pipeline, card, _ = build("pickup")
        text = render_confirm_panel(card, take_way_code="eat-in")
        for item in card.combo.items:
            self.assertIn(item.name, text)

    def test_share_text_is_plain(self):
        _, card, _ = build("pickup")
        text = render_share_text(card)
        self.assertIn("盲盒", text)
        self.assertIn(str(card.combo.seed), text)

    def test_server_side_discount_is_not_called_no_coupon(self):
        """服务端自己减了钱时，不能写「无可用券」——那和上面的价格对不上。

        实测真的会这样：麦乐送算价返回了 30 分的优惠，但没有任何券被选中。
        """
        case = _card_without_coupon_but_discounted()
        for text in (render_reveal(case), render_confirm_panel(case)):
            with self.subTest(renderer=text[:8]):
                self.assertNotIn("无可用券", text)
                self.assertIn("服务端优惠", text)

    def test_no_discount_still_says_no_coupon(self):
        case = _card_without_coupon_but_discounted(discount_fen=0)
        self.assertIn("无可用券", render_reveal(case))

    def test_items_show_their_category(self):
        """配品要标明是「小食/饮品/甜品」，而不是笼统叫配品。"""
        _, card, _ = build("pickup", timestamp=0)
        text = render_reveal(card)
        for item in card.combo.items[1:]:
            self.assertIn(item.name, text)
        self.assertTrue(any(label in text for label in ("小食", "饮品", "甜品")))


def _card_without_coupon_but_discounted(discount_fen: int = 30):
    """构造一张「没有选到券、但服务端还是减了钱」的卡。"""
    from mcd_blind_box.models import CartItem, Combo, PriceResult, RevealCard, Store

    items = [CartItem(product_code="1001", name="巨无霸", unit_price_fen=2850, category=CAT_MAIN)]
    return RevealCard(
        combo=Combo(items=items, seed=1, main_index=0, main_pool_size=11, candidate_size=25),
        price=PriceResult(
            original_total_fen=2850,
            discount_fen=discount_fen,
            payable_fen=2850 - discount_fen,
        ),
        store=Store("100001", "示例门店", 1),
        modes=("blind",),
    )


if __name__ == "__main__":
    unittest.main(verbosity=2)
