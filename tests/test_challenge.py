"""挑战模式、约束筛选与逐级放宽的测试。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcd_blind_box.challenge import (  # noqa: E402
    budget_cap_fen,
    calorie_extras_filter,
    describe_modes,
    extra_item_count,
    filter_by_calories,
    filter_by_taste,
    has_mode,
    match_taste,
    normalize_modes,
    pick_main_categories,
    relax_budget,
    relax_calorie,
    requires_pricing,
    skeleton_for_budget,
)
from mcd_blind_box.constants import TASTE_KEYWORDS  # noqa: E402
from mcd_blind_box.errors import ConstraintUnsatisfiable, InvalidArgument  # noqa: E402
from mcd_blind_box.models import Meal  # noqa: E402


def m(code: str, name: str, category: str = "main", fen: int = 1000) -> Meal:
    return Meal(product_code=code, name=name, price_fen=fen, category=category)


class TestNormalizeModes(unittest.TestCase):
    def test_empty_defaults_to_blind(self):
        self.assertEqual(normalize_modes([]), ("blind",))
        self.assertEqual(normalize_modes(()), ("blind",))
        self.assertEqual(normalize_modes(None), ("blind",))

    def test_dedup_keeps_order(self):
        self.assertEqual(
            normalize_modes(["taste", "budget", "taste"]), ("taste", "budget")
        )

    def test_unknown_mode_rejected(self):
        with self.assertRaises(InvalidArgument):
            normalize_modes(["random"])

    def test_all_four_are_allowed(self):
        self.assertEqual(
            normalize_modes(["blind", "budget", "calorie", "taste"]),
            ("blind", "budget", "calorie", "taste"),
        )

    def test_has_mode(self):
        self.assertTrue(has_mode(("blind", "budget"), "budget"))
        self.assertFalse(has_mode(("blind",), "calorie"))


class TestTaste(unittest.TestCase):
    def test_every_configured_taste_is_recognised(self):
        for taste in TASTE_KEYWORDS:
            with self.subTest(taste=taste):
                # 不该抛异常
                match_taste("随便什么", taste)

    def test_spicy(self):
        for name in ("香辣鸡腿堡", "麻辣鸡腿堡", "藤椒鸡腿堡", "麦辣鸡翅 2对"):
            self.assertTrue(match_taste(name, "辣"), name)

    def test_spicy_does_not_match_plain(self):
        self.assertFalse(match_taste("原味甜筒", "辣"))

    def test_coffee(self):
        for name in ("麦咖啡拿铁", "麦咖啡美式"):
            self.assertTrue(match_taste(name, "咖啡"), name)

    def test_fish(self):
        self.assertTrue(match_taste("鳕鱼堡", "鱼"))

    def test_unknown_taste_raises(self):
        with self.assertRaises(InvalidArgument):
            match_taste("巨无霸", "意面")

    def test_filter_by_taste(self):
        meals = [m("P1", "巨无霸"), m("P2", "鳕鱼堡"), m("P3", "香辣鸡腿堡")]
        self.assertEqual([x.product_code for x in filter_by_taste(meals, "鱼")], ["P2"])

    def test_filter_none_returns_all(self):
        meals = [m("P1", "巨无霸"), m("P2", "鳕鱼堡")]
        self.assertEqual(len(filter_by_taste(meals, None)), 2)
        self.assertEqual(len(filter_by_taste(meals, "")), 2)

    def test_filter_can_return_empty(self):
        meals = [m("P1", "巨无霸")]
        self.assertEqual(filter_by_taste(meals, "鱼"), [])


class TestCalorieFilter(unittest.TestCase):
    MEALS = [m("P1", "甲"), m("P2", "乙"), m("P3", "丙")]
    KCAL = {"P1": 300, "P2": 700}  # P3 匹配不到营养数据

    def test_none_cap_passes_everything(self):
        self.assertEqual(len(filter_by_calories(self.MEALS, self.KCAL, None)), 3)

    def test_over_cap_is_dropped(self):
        kept = filter_by_calories(self.MEALS, self.KCAL, 600)
        self.assertEqual([x.product_code for x in kept], ["P1"])

    def test_unknown_calories_are_dropped_not_assumed_zero(self):
        """匹配不到热量就必须排除，不能当 0 卡放行。"""
        kept = filter_by_calories(self.MEALS, self.KCAL, 9999)
        self.assertEqual([x.product_code for x in kept], ["P1", "P2"])

    def test_boundary_is_inclusive(self):
        kept = filter_by_calories(self.MEALS, self.KCAL, 300)
        self.assertEqual([x.product_code for x in kept], ["P1"])


class TestSkeleton(unittest.TestCase):
    def test_none_budget(self):
        self.assertEqual(skeleton_for_budget(None), (1, 1, 0))

    def test_bands(self):
        self.assertEqual(skeleton_for_budget(15), (1, 1, 0))
        self.assertEqual(skeleton_for_budget(20), (1, 1, 0))
        self.assertEqual(skeleton_for_budget(21), (1, 1, 1))
        self.assertEqual(skeleton_for_budget(30), (1, 1, 1))
        self.assertEqual(skeleton_for_budget(45), (1, 1, 1))
        self.assertEqual(skeleton_for_budget(500), (1, 1, 1))

    def test_extra_item_count(self):
        self.assertEqual(extra_item_count(None), 1)
        self.assertEqual(extra_item_count(15), 1)
        self.assertEqual(extra_item_count(30), 2)

    def test_budget_cap_fen(self):
        self.assertIsNone(budget_cap_fen(None))
        self.assertEqual(budget_cap_fen(30), 3000)
        self.assertEqual(budget_cap_fen(29.99), 2999)


class TestMainPool(unittest.TestCase):
    POOL = [m("P1", "堡", "main"), m("P2", "薯条", "side"), m("P3", "可乐", "drink")]

    def test_only_mains(self):
        self.assertEqual(
            [x.product_code for x in pick_main_categories(self.POOL)], ["P1"]
        )

    def test_falls_back_when_no_main(self):
        no_main = [m("P2", "薯条", "side"), m("P3", "可乐", "drink")]
        self.assertEqual(len(pick_main_categories(no_main)), 2)


class TestCalorieExtrasFilter(unittest.TestCase):
    KCAL = {"P1": 300, "P2": 200, "P3": 900}

    def test_no_cap_returns_none(self):
        self.assertIsNone(calorie_extras_filter(None, self.KCAL))

    def test_single_item_over_cap_rejected(self):
        f = calorie_extras_filter(400, self.KCAL)
        self.assertFalse(f(m("P3", "丙"), [m("P1", "甲")]))

    def test_cumulative_over_cap_rejected(self):
        f = calorie_extras_filter(400, self.KCAL)
        self.assertFalse(f(m("P2", "乙"), [m("P1", "甲")]))  # 300 + 200 = 500 > 400

    def test_within_cap_accepted(self):
        f = calorie_extras_filter(500, self.KCAL)
        self.assertTrue(f(m("P2", "乙"), [m("P1", "甲")]))

    def test_unknown_calories_rejected(self):
        f = calorie_extras_filter(500, self.KCAL)
        self.assertFalse(f(m("P9", "未知"), [m("P1", "甲")]))

    def test_unknown_already_picked_rejected(self):
        f = calorie_extras_filter(500, self.KCAL)
        self.assertFalse(f(m("P2", "乙"), [m("P9", "未知")]))

    def test_empty_picked(self):
        f = calorie_extras_filter(300, self.KCAL)
        self.assertTrue(f(m("P1", "甲"), []))


class TestRelaxation(unittest.TestCase):
    def test_budget_picks_closest_below_and_above(self):
        entries = [("a", 1000), ("b", 2900), ("c", 3100)]
        # 上限 3000：|2900-3000|=100 最小
        self.assertEqual(relax_budget(entries, 3000)[0], "b")

    def test_budget_tie_prefers_cheaper(self):
        entries = [("a", 2900), ("b", 3100)]
        self.assertEqual(relax_budget(entries, 3000)[0], "a")

    def test_budget_empty(self):
        with self.assertRaises(ConstraintUnsatisfiable):
            relax_budget([], 3000)

    def test_calorie_picks_minimum(self):
        entries = [("a", 700), ("b", 520), ("c", 900)]
        self.assertEqual(relax_calorie(entries)[0], "b")

    def test_calorie_empty(self):
        with self.assertRaises(ConstraintUnsatisfiable):
            relax_calorie([])


class TestModeHelpers(unittest.TestCase):
    def test_requires_pricing(self):
        self.assertTrue(requires_pricing(["budget"]))
        self.assertTrue(requires_pricing(["blind", "budget"]))
        self.assertFalse(requires_pricing(["blind"]))
        self.assertFalse(requires_pricing(["calorie"]))

    def test_describe_modes(self):
        self.assertEqual(describe_modes(["budget"]), "预算挑战")
        self.assertEqual(describe_modes(["blind", "budget"]), "纯盲盒 + 预算挑战")

    def test_describe_modes_with_taste_suffix(self):
        self.assertEqual(describe_modes(["taste"], taste="辣"), "口味挑战（辣）")

    def test_describe_modes_without_taste_has_no_suffix(self):
        self.assertEqual(describe_modes(["taste"]), "口味挑战")

    def test_taste_suffix_does_not_leak_onto_other_modes(self):
        self.assertEqual(
            describe_modes(["budget", "taste"], taste="辣"),
            "预算挑战 + 口味挑战（辣）",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
