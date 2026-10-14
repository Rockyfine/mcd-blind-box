"""算价、券与热量的测试。

关注的坑：
- 金额单位是**分**，展示才 /100
- 券只能来自 ``query-store-coupons``
- 匹配不到热量就**不给**总量，绝不估算
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcd_blind_box import coupon as coupon_mod  # noqa: E402
from mcd_blind_box.errors import InvalidArgument  # noqa: E402
from mcd_blind_box.models import CartItem, Coupon, PriceResult, TakeWay  # noqa: E402
from mcd_blind_box.pricing import to_price_result, total_calories  # noqa: E402


def item(code="1001", fen=1000, qty=1, calories=None) -> CartItem:
    return CartItem(
        product_code=code, name=code, quantity=qty, unit_price_fen=fen, calories=calories
    )


class TestToPriceResult(unittest.TestCase):
    def test_parses_the_real_calculate_price_shape(self):
        """真实字段：data.originalPrice / data.price / data.discount，单位**分**。"""
        raw = {
            "success": True,
            "code": 200,
            "message": "请求成功",
            "data": {
                "storeCode": "100001",
                "orderType": 1,
                "originalPrice": 3700,
                "discount": 500,
                "price": 3200,
                "takeWayList": [{"code": "eat-in", "title": "堂食", "subtitle": "店内用餐"}],
            },
        }
        price = to_price_result(raw, [item(fen=3700)])
        self.assertEqual(price.original_total_fen, 3700)
        self.assertEqual(price.discount_fen, 500)
        self.assertEqual(price.payable_fen, 3200)
        self.assertEqual(price.first_take_way_code(), "eat-in")
        self.assertEqual(price.take_ways[0].name, "堂食")

    def test_parses_flat_payload(self):
        raw = {"originalTotalFen": 1000, "payableFen": 900, "discountFen": 100}
        self.assertEqual(to_price_result(raw, []).payable_fen, 900)

    def test_falls_back_to_item_sum_when_missing(self):
        """上游没给价格字段时，用商品单价累加，避免显示成 0 元。"""
        price = to_price_result({}, [item(fen=1500), item("1002", fen=2000)])
        self.assertEqual(price.original_total_fen, 3500)

    def test_tolerates_alternate_field_names(self):
        raw = {"data": {"totalFen": 1000, "payAmount": 800, "discountAmount": 200}}
        price = to_price_result(raw, [])
        self.assertEqual(price.original_total_fen, 1000)
        self.assertEqual(price.payable_fen, 800)
        self.assertEqual(price.discount_fen, 200)

    def test_error_response_does_not_crash(self):
        """出错的响应里 data 可能不是字典，解析层不能因此抛异常。"""
        price = to_price_result({"success": False, "code": 401, "data": None}, [item(fen=1000)])
        self.assertEqual(price.original_total_fen, 1000)


class TestTotalCalories(unittest.TestCase):
    def test_empty_is_zero(self):
        self.assertEqual(total_calories([]), 0)

    def test_all_known(self):
        self.assertEqual(total_calories([item(calories=300), item(calories=250)]), 550)

    def test_any_unknown_returns_none(self):
        """绝不能把缺失当成 0 —— 那等于编造数据。"""
        self.assertIsNone(total_calories([item(calories=300), item()]))

    def test_all_unknown_returns_none(self):
        self.assertIsNone(total_calories([item(), item("P2")]))


class TestCouponSource(unittest.TestCase):
    def test_only_store_coupons_is_allowed(self):
        self.assertEqual(
            coupon_mod.assert_coupon_source("query-store-coupons"), "query-store-coupons"
        )

    def test_my_coupons_is_forbidden(self):
        with self.assertRaises(InvalidArgument):
            coupon_mod.assert_coupon_source("query-my-coupons")

    def test_available_coupons_is_forbidden(self):
        with self.assertRaises(InvalidArgument):
            coupon_mod.assert_coupon_source("available-coupons")

    def test_unknown_source_rejected(self):
        with self.assertRaises(InvalidArgument):
            coupon_mod.assert_coupon_source("guess-coupons")


class TestCouponSelection(unittest.TestCase):
    COUPONS = [
        Coupon("CP03", "CC03", "满 30 减 6", threshold_fen=3000, discount_fen=600),
        Coupon("CP01", "CC01", "满 20 减 3", threshold_fen=2000, discount_fen=300),
        Coupon("CP02", "CC02", "满 25 减 5", threshold_fen=2500, discount_fen=500),
        Coupon("CP09", "CC09", "满 90 减 30", threshold_fen=9000, discount_fen=3000),
    ]

    def test_filters_by_threshold(self):
        # 2600 分只够 CP01(2000) 与 CP02(2500)；按优惠降序 -> CP02(500) 在前
        usable = coupon_mod.applicable(self.COUPONS, 2600)
        self.assertEqual([c.coupon_id for c in usable], ["CP02", "CP01"])

    def test_threshold_boundary_is_inclusive(self):
        usable = coupon_mod.applicable(self.COUPONS, 3000)
        self.assertIn("CP03", [c.coupon_id for c in usable])

    def test_sorted_by_discount_desc(self):
        usable = coupon_mod.applicable(self.COUPONS, 10000)
        self.assertEqual([c.coupon_id for c in usable], ["CP09", "CP03", "CP02", "CP01"])

    def test_tie_broken_by_coupon_id(self):
        tied = [
            Coupon("CPB", "CCB", threshold_fen=0, discount_fen=500),
            Coupon("CPA", "CCA", threshold_fen=0, discount_fen=500),
        ]
        self.assertEqual([c.coupon_id for c in coupon_mod.applicable(tied, 1000)], ["CPA", "CPB"])

    def test_select_picks_best(self):
        self.assertEqual(coupon_mod.select_coupon(self.COUPONS, 10000).coupon_id, "CP09")

    def test_select_returns_none_when_nothing_fits(self):
        self.assertIsNone(coupon_mod.select_coupon(self.COUPONS, 100))

    def test_select_on_empty(self):
        self.assertIsNone(coupon_mod.select_coupon([], 9999))


class TestProductCoupon(unittest.TestCase):
    """商品券（如「中薯条买一送一」）没有「满 X 减 Y」，判定方式完全不同。

    真实返回里这类券只有 ``products[].productCode``，**没有** ``thresholdFen`` /
    ``discountFen``。硬按门槛筛就会选出一张根本用不上的券，
    所以只有购物车里真的出现了它关联的商品，才算可用。
    """

    PRODUCT_COUPON = Coupon(
        "CP03", "CC03", "中薯条买一送一", product_codes=("2001",)
    )
    THRESHOLD_COUPON = Coupon(
        "CP01", "CC01", "满 25 减 5", threshold_fen=2500, discount_fen=500
    )

    def test_requires_an_intersecting_item(self):
        self.assertEqual(coupon_mod.applicable([self.PRODUCT_COUPON], 10000, ["1001"]), [])

    def test_available_when_its_product_is_in_the_cart(self):
        usable = coupon_mod.applicable([self.PRODUCT_COUPON], 1100, ["2001"])
        self.assertEqual([c.coupon_id for c in usable], ["CP03"])

    def test_threshold_coupon_wins_when_it_discounts_more(self):
        """商品券的抵扣额本地算不出来，所以有确切优惠的门槛券要排在前面。"""
        usable = coupon_mod.applicable(
            [self.PRODUCT_COUPON, self.THRESHOLD_COUPON], 3000, ["2001", "1001"]
        )
        self.assertEqual([c.coupon_id for c in usable], ["CP01", "CP03"])
        self.assertEqual(coupon_mod.select_coupon(usable, 3000, ["2001"]).coupon_id, "CP01")

    def test_covers_helper(self):
        self.assertTrue(self.PRODUCT_COUPON.covers(["2001", "1002"]))
        self.assertFalse(self.PRODUCT_COUPON.covers(["1002"]))
        # 门槛券不关联商品，covers 永远是 False —— 它的判定走另一条分支
        self.assertFalse(self.THRESHOLD_COUPON.covers(["2001"]))


class TestAttachCoupon(unittest.TestCase):
    def test_none_coupon_is_noop(self):
        items = [item()]
        self.assertEqual(coupon_mod.attach_coupon(items, None), items)

    def test_attaches_to_first_item_only(self):
        items = [item("P1"), item("P2")]
        c = Coupon("CP01", "CC01")
        updated = coupon_mod.attach_coupon(items, c)
        self.assertEqual(updated[0].coupon_id, "CP01")
        self.assertIsNone(updated[1].coupon_id)

    def test_does_not_mutate_input(self):
        items = [item("P1")]
        coupon_mod.attach_coupon(items, Coupon("CP01", "CC01"))
        self.assertIsNone(items[0].coupon_id)

    def test_coupon_without_any_identifier_is_skipped(self):
        items = [item("P1")]
        updated = coupon_mod.attach_coupon(items, Coupon("", ""))
        self.assertIsNone(updated[0].coupon_id)

    def test_empty_item_list(self):
        self.assertEqual(coupon_mod.attach_coupon([], Coupon("CP01", "CC01")), [])

    def test_payload_omits_none_ids(self):
        updated = coupon_mod.attach_coupon([item("P1")], Coupon("CP01", ""))
        payload = updated[0].to_payload()
        self.assertEqual(payload["couponId"], "CP01")
        self.assertNotIn("couponCode", payload)

    def test_other_fields_preserved(self):
        original = CartItem(
            product_code="P1",
            name="巨无霸",
            quantity=2,
            unit_price_fen=2850,
            modification_values=[{"code": "V1", "key": "K", "quantity": 1}],
            calories=550,
        )
        updated = coupon_mod.attach_coupon([original], Coupon("CP01", "CC01"))[0]
        self.assertEqual(updated.name, "巨无霸")
        self.assertEqual(updated.quantity, 2)
        self.assertEqual(updated.unit_price_fen, 2850)
        self.assertEqual(updated.calories, 550)
        self.assertEqual(updated.modification_values, original.modification_values)


class TestDescribeCoupon(unittest.TestCase):
    def test_none(self):
        self.assertEqual(coupon_mod.describe_coupon(None), "无可用券")

    def test_with_discount(self):
        text = coupon_mod.describe_coupon(
            Coupon("CP01", "CC01", "满 25 减 5", threshold_fen=2500, discount_fen=500)
        )
        self.assertIn("满 25 减 5", text)
        self.assertIn("满 25.00 减 5.00", text)

    def test_without_discount_uses_title(self):
        self.assertEqual(
            coupon_mod.describe_coupon(Coupon("CP01", "CC01", "赠品券")), "赠品券"
        )

    def test_without_title_falls_back(self):
        self.assertEqual(coupon_mod.describe_coupon(Coupon("CP01", "CC01")), "优惠券")

    def test_product_coupon_says_the_amount_is_up_to_the_server(self):
        """不能把「本地算不出抵扣额」说成「这券不值钱」。"""
        text = coupon_mod.describe_coupon(
            Coupon("CP03", "CC03", "中薯条买一送一", product_codes=("2001",))
        )
        self.assertIn("中薯条买一送一", text)
        self.assertIn("服务端", text)


class TestPriceResultHelpers(unittest.TestCase):
    def test_first_take_way_code_skips_blank(self):
        price = PriceResult(take_ways=[TakeWay("", "x"), TakeWay("TW002", "y")])
        self.assertEqual(price.first_take_way_code(), "TW002")

    def test_first_take_way_code_none(self):
        self.assertIsNone(PriceResult().first_take_way_code())


if __name__ == "__main__":
    unittest.main(verbosity=2)
