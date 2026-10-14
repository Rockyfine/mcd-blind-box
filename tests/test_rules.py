"""规则铁律的单元测试。

这里测的是「踩过的坑」有没有被真正编码进去 —— 尤其是：
- beType 与 orderType 的固定映射
- beType=1 绝不带 beCode
- 特调 key 铁律
- 分/元换算
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcd_blind_box import rules  # noqa: E402
from mcd_blind_box.constants import (  # noqa: E402
    ALLOWED_TOOLS,
    EXPECTED_TOOL_COUNT,
    REMARK_MAX_LEN,
)
from mcd_blind_box.errors import (  # noqa: E402
    InvalidArgument,
    SceneMismatch,
    UnsupportedBeType,
)
from mcd_blind_box.models import (  # noqa: E402
    CartItem,
    ModGroup,
    ModValue,
    PriceResult,
    TakeWay,
)


def meal(code: str, name: str, fen: int = 1000, category: str = "main"):
    from mcd_blind_box.models import Meal

    return Meal(product_code=code, name=name, price_fen=fen, category=category)


class TestOrderTypeMapping(unittest.TestCase):
    def test_pickup_maps_to_in_store(self):
        self.assertEqual(rules.order_type_for(1), 1)

    def test_dt_maps_to_in_store(self):
        self.assertEqual(rules.order_type_for(5), 1)

    def test_delivery_maps_to_delivery(self):
        self.assertEqual(rules.order_type_for(2), 2)

    def test_group_meal_is_not_implemented(self):
        with self.assertRaises(UnsupportedBeType):
            rules.order_type_for(6)

    def test_unknown_be_type_raises(self):
        for bad in (0, 3, 4, 99, -1, None, "1"):
            with self.subTest(be_type=bad):
                with self.assertRaises(UnsupportedBeType):
                    rules.order_type_for(bad)  # type: ignore[arg-type]

    def test_requires_be_code(self):
        self.assertFalse(rules.requires_be_code(1))
        self.assertTrue(rules.requires_be_code(5))
        self.assertTrue(rules.requires_be_code(2))

    def test_requires_be_code_rejects_unknown(self):
        with self.assertRaises(UnsupportedBeType):
            rules.requires_be_code(6)

    def test_scene_names(self):
        self.assertEqual(rules.be_type_for_scene("pickup"), 1)
        self.assertEqual(rules.be_type_for_scene("dt"), 5)
        self.assertEqual(rules.be_type_for_scene("delivery"), 2)

    def test_unknown_scene_raises(self):
        with self.assertRaises(UnsupportedBeType):
            rules.be_type_for_scene("pick-up")


class TestNoBeCodeForPickup(unittest.TestCase):
    def test_empty_is_fine(self):
        rules.assert_no_be_code_for_pickup(1, {})
        rules.assert_no_be_code_for_pickup(1, {"beCode": None})
        rules.assert_no_be_code_for_pickup(1, {"beCode": ""})

    def test_non_empty_is_rejected(self):
        with self.assertRaises(SceneMismatch):
            rules.assert_no_be_code_for_pickup(1, {"beCode": "B123"})

    def test_dt_and_delivery_unaffected(self):
        rules.assert_no_be_code_for_pickup(5, {"beCode": "B123"})
        rules.assert_no_be_code_for_pickup(2, {"beCode": "B123"})


class TestBuildSceneArgs(unittest.TestCase):
    def test_pickup_has_no_becode_key_at_all(self):
        args = rules.build_scene_args(1)
        self.assertEqual(args, {"orderType": 1, "beType": 1})
        self.assertNotIn("beCode", args)

    def test_pickup_with_becode_is_rejected(self):
        with self.assertRaises(SceneMismatch):
            rules.build_scene_args(1, be_code="B123")

    def test_dt_requires_becode(self):
        with self.assertRaises(SceneMismatch):
            rules.build_scene_args(5)
        args = rules.build_scene_args(5, be_code="DT1")
        self.assertEqual(args["beCode"], "DT1")
        self.assertEqual(args["orderType"], 1)

    def test_delivery_requires_becode(self):
        with self.assertRaises(SceneMismatch):
            rules.build_scene_args(2)
        args = rules.build_scene_args(2, be_code="DL1")
        self.assertEqual(args["beCode"], "DL1")
        self.assertEqual(args["orderType"], 2)

    def test_order_type_mismatch_is_rejected(self):
        with self.assertRaises(SceneMismatch):
            rules.build_scene_args(1, order_type=2)
        with self.assertRaises(SceneMismatch):
            rules.build_scene_args(2, be_code="DL1", order_type=1)

    def test_explicit_matching_order_type_is_accepted(self):
        self.assertEqual(rules.build_scene_args(1, order_type=1)["orderType"], 1)

    def test_unsupported_be_type(self):
        with self.assertRaises(UnsupportedBeType):
            rules.build_scene_args(6)


class TestBuildStoreLocatorArgs(unittest.TestCase):
    def test_nearby_defaults_to_search_type_1(self):
        self.assertEqual(
            rules.build_store_locator_args(1), {"beType": 1, "searchType": 1}
        )

    def test_search_type_2_needs_city_and_keyword(self):
        with self.assertRaises(InvalidArgument):
            rules.build_store_locator_args(1, search_type=2)
        with self.assertRaises(InvalidArgument):
            rules.build_store_locator_args(1, search_type=2, city="南京市")
        with self.assertRaises(InvalidArgument):
            rules.build_store_locator_args(1, search_type=2, keyword="新街口")
        args = rules.build_store_locator_args(
            1, search_type=2, city="南京市", keyword="新街口"
        )
        self.assertEqual(args["city"], "南京市")
        self.assertEqual(args["keyword"], "新街口")

    def test_dt_uses_nearby_shape(self):
        args = rules.build_store_locator_args(5, search_type=1)
        self.assertEqual(args["beType"], 5)

    def test_delivery_needs_address_id(self):
        with self.assertRaises(InvalidArgument):
            rules.build_store_locator_args(2)
        args = rules.build_store_locator_args(2, address_id="A1001")
        self.assertEqual(args, {"beType": 2, "addressId": "A1001"})

    def test_unsupported(self):
        with self.assertRaises(UnsupportedBeType):
            rules.build_store_locator_args(6)


class TestToolWhitelist(unittest.TestCase):
    def test_official_tool_count(self):
        self.assertEqual(len(ALLOWED_TOOLS), EXPECTED_TOOL_COUNT)

    def test_real_names_pass(self):
        for name in ("query-nearby-stores", "delivery-query-addresses", "query-meals"):
            self.assertEqual(rules.assert_tool_allowed(name), name)

    def test_singular_typos_are_rejected(self):
        for bad in ("query-nearby-store", "delivery-query-address", "query-meal"):
            with self.subTest(name=bad):
                with self.assertRaises(InvalidArgument):
                    rules.assert_tool_allowed(bad)

    def test_empty_name_rejected(self):
        with self.assertRaises(InvalidArgument):
            rules.assert_tool_allowed("")


def _group(code, *values):
    return ModGroup(
        code=code,
        name=code,
        values=[
            ModValue(
                code=v[0],
                name=v[0],
                selected_key=v[1],
                unselected_key=v[2],
                selected=v[3],
            )
            for v in values
        ],
    )


class TestModificationIronRule(unittest.TestCase):
    """特调 key 铁律 —— 本文件里最重要的测试。"""

    def test_selected_uses_selected_key_qty_1(self):
        groups = [_group("G1", ("V1", "K_A", "UK_A", True), ("V2", "K_B", "UK_B", False))]
        values = rules.build_modification_values(groups)
        by_code = {v["code"]: v for v in values}
        self.assertEqual(by_code["V1"], {"code": "V1", "key": "K_A", "quantity": 1})

    def test_unselected_with_unselected_key_is_included_qty_0(self):
        groups = [_group("G1", ("V1", "K_A", "UK_A", True), ("V2", "K_B", "UK_B", False))]
        values = rules.build_modification_values(groups)
        by_code = {v["code"]: v for v in values}
        self.assertEqual(by_code["V2"], {"code": "V2", "key": "UK_B", "quantity": 0})

    def test_group_with_unselected_key_passes_everything(self):
        # V2 没有 unselected_key，但同组 V1 有 —— 整组都要传
        groups = [_group("G1", ("V1", "K_A", "UK_A", True), ("V2", "K_B", "", False))]
        values = rules.build_modification_values(groups)
        self.assertEqual(len(values), 2)
        by_code = {v["code"]: v for v in values}
        self.assertEqual(by_code["V2"]["quantity"], 0)
        self.assertEqual(by_code["V2"]["key"], "K_B")

    def test_group_without_any_unselected_key_only_sends_selected(self):
        groups = [_group("G1", ("V1", "K_A", "", True), ("V2", "K_B", "", False))]
        values = rules.build_modification_values(groups)
        self.assertEqual(values, [{"code": "V1", "key": "K_A", "quantity": 1}])

    def test_empty_key_is_skipped(self):
        groups = [_group("G1", ("V1", "", "", False), ("V2", "", "", False))]
        self.assertEqual(rules.build_modification_values(groups), [])

    def test_default_value_falls_back_to_first(self):
        groups = [_group("G1", ("V1", "K_A", "", False), ("V2", "K_B", "", False))]
        values = rules.build_modification_values(groups)
        self.assertEqual(values[0]["code"], "V1")
        self.assertEqual(values[0]["quantity"], 1)

    def test_chosen_override(self):
        groups = [_group("G1", ("V1", "K_A", "", True), ("V2", "K_B", "", False))]
        values = rules.build_modification_values(groups, {"G1": "V2"})
        self.assertEqual(values, [{"code": "V2", "key": "K_B", "quantity": 1}])

    def test_chosen_unknown_code_falls_back_to_default(self):
        groups = [_group("G1", ("V1", "K_A", "", True), ("V2", "K_B", "", False))]
        values = rules.build_modification_values(groups, {"G1": "NOPE"})
        self.assertEqual(values[0]["code"], "V1")

    def test_empty_groups(self):
        self.assertEqual(rules.build_modification_values([]), [])

    def test_group_without_values_is_skipped(self):
        self.assertEqual(rules.build_modification_values([ModGroup("G1", "G1", [])]), [])

    def test_price_and_order_share_the_same_result(self):
        """铁律要求 calculate-price 与 create-order 构造方式完全一致。"""
        groups = [
            _group("G1", ("V1", "K_A", "UK_A", True), ("V2", "K_B", "UK_B", False)),
            _group("G2", ("V3", "K_C", "", True), ("V4", "K_D", "", False)),
        ]
        self.assertEqual(
            rules.build_modification_values(groups),
            rules.build_modification_values(groups),
        )


class TestUnitConversion(unittest.TestCase):
    def test_fen_to_yuan(self):
        self.assertEqual(rules.fen_to_yuan(0), "0.00")
        self.assertEqual(rules.fen_to_yuan(100), "1.00")
        self.assertEqual(rules.fen_to_yuan(2850), "28.50")
        self.assertEqual(rules.fen_to_yuan(1), "0.01")

    def test_fen_to_yuan_tolerates_junk(self):
        self.assertEqual(rules.fen_to_yuan(None), "0.00")
        self.assertEqual(rules.fen_to_yuan("abc"), "0.00")
        self.assertEqual(rules.fen_to_yuan("300"), "3.00")

    def test_yuan_to_fen(self):
        self.assertEqual(rules.yuan_to_fen(30), 3000)
        self.assertEqual(rules.yuan_to_fen(30.5), 3050)
        self.assertEqual(rules.yuan_to_fen(0.01), 1)
        self.assertEqual(rules.yuan_to_fen("28.5"), 2850)

    def test_yuan_to_fen_rejects_junk(self):
        for bad in ("abc", None, {}, []):
            with self.subTest(value=bad):
                with self.assertRaises(InvalidArgument):
                    rules.yuan_to_fen(bad)


class TestRemark(unittest.TestCase):
    def test_none_and_blank(self):
        self.assertIsNone(rules.validate_remark(None, 2))
        self.assertIsNone(rules.validate_remark("", 2))
        self.assertIsNone(rules.validate_remark("   ", 2))

    def test_delivery_accepts(self):
        self.assertEqual(rules.validate_remark("  少冰  ", 2), "少冰")

    def test_in_store_rejects(self):
        with self.assertRaises(InvalidArgument):
            rules.validate_remark("放在前台", 1)

    def test_length_limit(self):
        ok = "菜" * REMARK_MAX_LEN
        self.assertEqual(len(rules.validate_remark(ok, 2)), REMARK_MAX_LEN)
        with self.assertRaises(InvalidArgument):
            rules.validate_remark("菜" * (REMARK_MAX_LEN + 1), 2)


class TestTakeWay(unittest.TestCase):
    def test_picks_first_non_empty_code(self):
        price = PriceResult(take_ways=[TakeWay("", "空"), TakeWay("TW001", "到店取餐")])
        self.assertEqual(rules.pick_take_way(price), "TW001")

    def test_empty_list_is_rejected(self):
        with self.assertRaises(SceneMismatch):
            rules.pick_take_way(PriceResult(take_ways=[]))

    def test_all_blank_codes_are_rejected(self):
        with self.assertRaises(SceneMismatch):
            rules.pick_take_way(PriceResult(take_ways=[TakeWay("", "")]))


class TestAddressId(unittest.TestCase):
    def test_delivery_needs_it(self):
        self.assertEqual(rules.require_address_id(2, "A1001"), "A1001")
        with self.assertRaises(InvalidArgument):
            rules.require_address_id(2, None)

    def test_in_store_ignores_it(self):
        self.assertIsNone(rules.require_address_id(1, None))
        self.assertIsNone(rules.require_address_id(1, "A1001"))

    def test_is_in_store(self):
        self.assertTrue(rules.is_in_store(1))
        self.assertFalse(rules.is_in_store(2))


class TestItemsPayload(unittest.TestCase):
    def test_minimal_item(self):
        payload = rules.build_items_payload(
            [CartItem(product_code="P1", name="巨无霸", quantity=1, unit_price_fen=2850)]
        )
        self.assertEqual(payload, [{"productCode": "P1", "quantity": 1}])

    def test_absent_coupon_ids_are_not_invented(self):
        payload = rules.build_items_payload([CartItem(product_code="P1")])[0]
        self.assertNotIn("couponId", payload)
        self.assertNotIn("couponCode", payload)

    def test_present_coupon_ids_are_kept(self):
        payload = rules.build_items_payload(
            [CartItem(product_code="P1", coupon_id="CP01", coupon_code="CC01")]
        )[0]
        self.assertEqual(payload["couponId"], "CP01")
        self.assertEqual(payload["couponCode"], "CC01")

    def test_modification_is_nested(self):
        payload = rules.build_items_payload(
            [CartItem(product_code="P1", modification_values=[{"code": "V1", "key": "K", "quantity": 1}])]
        )[0]
        self.assertEqual(payload["modification"]["values"][0]["code"], "V1")

    def test_no_modification_key_when_empty(self):
        payload = rules.build_items_payload([CartItem(product_code="P1")])[0]
        self.assertNotIn("modification", payload)


if __name__ == "__main__":
    unittest.main(verbosity=2)
