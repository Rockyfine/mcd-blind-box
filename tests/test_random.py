"""随机抽取的测试。

核心承诺是**可复现**：同样的种子必然得到同样的组合，所以揭晓卡上那句
「种子 → 第 N 项」是可以被用户复核的。这里的测试就是替用户先复核一遍。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcd_blind_box.challenge import (  # noqa: E402
    Candidate,
    candidate_for_seed,
    generate_candidates,
    sorted_pool,
)
from mcd_blind_box.constants import SEED_STRIDE  # noqa: E402
from mcd_blind_box.errors import ConstraintUnsatisfiable, InvalidArgument  # noqa: E402
from mcd_blind_box.models import Meal  # noqa: E402


def m(code: str, name: str, category: str, fen: int = 1000) -> Meal:
    return Meal(product_code=code, name=name, price_fen=fen, category=category)


POOL = [
    m("A", "甲汉堡", "main"),
    m("B", "乙汉堡", "main"),
    m("C", "丙薯条", "side"),
    m("D", "丁可乐", "drink"),
    m("E", "戊甜筒", "dessert"),
]
MAINS = [x for x in POOL if x.category == "main"]


class TestSortedPool(unittest.TestCase):
    def test_sorted_by_product_code(self):
        shuffled = [POOL[3], POOL[0], POOL[4], POOL[1], POOL[2]]
        self.assertEqual([x.product_code for x in sorted_pool(shuffled)], list("ABCDE"))

    def test_order_is_independent_of_input_order(self):
        one = sorted_pool(POOL)
        two = sorted_pool(list(reversed(POOL)))
        self.assertEqual([x.product_code for x in one], [x.product_code for x in two])


class TestMainSelection(unittest.TestCase):
    def test_main_index_is_seed_mod_main_count(self):
        for seed in range(0, 40):
            with self.subTest(seed=seed):
                cand = candidate_for_seed(POOL, seed, main_pool=MAINS, extras=1)
                self.assertEqual(cand.main_index, seed % len(MAINS))
                self.assertEqual(cand.main, MAINS[seed % len(MAINS)])

    def test_main_pool_size_is_recorded(self):
        cand = candidate_for_seed(POOL, 5, main_pool=MAINS, extras=1)
        self.assertEqual(cand.main_pool_size, len(MAINS))
        self.assertEqual(cand.pool_size, len(POOL))

    def test_main_is_a_main_category_item(self):
        """绝不能抽出两道主食当一套组合。"""
        for seed in range(0, 60):
            cand = candidate_for_seed(POOL, seed, main_pool=MAINS, extras=1)
            self.assertEqual(cand.main.category, "main")
            self.assertEqual(cand.picks[0], cand.main)

    def test_extras_are_not_mains_when_sides_exist(self):
        for seed in range(0, 60):
            with self.subTest(seed=seed):
                cand = candidate_for_seed(POOL, seed, main_pool=MAINS, extras=1)
                second = cand.picks[1]
                self.assertNotEqual(second.category, "main")

    def test_missing_main_pool_falls_back_to_full_pool(self):
        cand = candidate_for_seed(POOL, 5, main_pool=[], extras=1)
        self.assertEqual(cand.main_pool_size, len(POOL))
        self.assertEqual(cand.main, sorted_pool(POOL)[5 % len(POOL)])


class TestExtraDrawArithmetic(unittest.TestCase):
    """把「配品种子 = seed // N^level」这条规则钉死。"""

    #: 手工验算过的样例：(seed, extras) -> picks
    #: 配品池 = 非主食（[C 小食, E 甜点, D 饮料]，pairing 顺序），长度 3
    PINNED = {
        (5, 1): ["B", "D"],
        (11, 1): ["B", "D"],
        (0, 1): ["A", "C"],
        (1, 1): ["B", "C"],
        (5, 2): ["B", "E", "D"],
    }

    def test_pinned_cases(self):
        for (seed, extras), expected in self.PINNED.items():
            with self.subTest(seed=seed, extras=extras):
                cand = candidate_for_seed(POOL, seed, main_pool=MAINS, extras=extras)
                self.assertEqual([p.product_code for p in cand.picks], expected)

    def test_first_extra_uses_seed_over_n(self):
        # seed=11, N=2 -> 11//2=5；配品池 [C,E,D] -> 5%3=2 -> D
        cand = candidate_for_seed(POOL, 11, main_pool=MAINS, extras=1)
        self.assertEqual(cand.picks[1].product_code, "D")

    def test_zero_extras_yields_main_only(self):
        cand = candidate_for_seed(POOL, 5, main_pool=MAINS, extras=0)
        self.assertEqual([p.product_code for p in cand.picks], ["B"])

    def test_negative_extras_is_treated_as_zero(self):
        cand = candidate_for_seed(POOL, 5, main_pool=MAINS, extras=-3)
        self.assertEqual([p.product_code for p in cand.picks], ["B"])

    def test_no_duplicates(self):
        for seed in range(0, 80):
            with self.subTest(seed=seed):
                cand = candidate_for_seed(POOL, seed, main_pool=MAINS, extras=4)
                codes = [p.product_code for p in cand.picks]
                self.assertEqual(len(codes), len(set(codes)))

    def test_never_exceeds_available_items(self):
        cand = candidate_for_seed(POOL, 7, main_pool=MAINS, extras=50)
        self.assertLessEqual(len(cand.picks), len(POOL))

    def test_tail_is_ordered_by_pairing_priority(self):
        cand = candidate_for_seed(POOL, 5, main_pool=MAINS, extras=2)
        # picks[1:] 应按 小食 → 甜点 → 饮料 → 主食 排序
        self.assertEqual([p.product_code for p in cand.picks[1:]], ["E", "D"])


class TestExtraFilterRunsBeforeDraw(unittest.TestCase):
    def test_filtered_item_is_never_picked(self):
        blocked = lambda meal, picked: meal.product_code != "C"  # noqa: E731
        for seed in range(0, 60):
            with self.subTest(seed=seed):
                cand = candidate_for_seed(
                    POOL, seed, main_pool=MAINS, extras=2, extra_filter=blocked
                )
                self.assertNotIn("C", [p.product_code for p in cand.picks])

    def test_filter_changes_which_item_wins(self):
        # 屏蔽 C（小食）后，配品池缩成 [E, D] -> 5//2=2, 2%2=0 -> E
        blocked = lambda meal, picked: meal.product_code != "C"  # noqa: E731
        cand = candidate_for_seed(POOL, 5, main_pool=MAINS, extras=2, extra_filter=blocked)
        self.assertEqual([p.product_code for p in cand.picks], ["B", "E", "D"])

    def test_filter_receives_already_picked(self):
        seen = []

        def spy(meal, picked):
            seen.append(tuple(p.product_code for p in picked))
            return True

        candidate_for_seed(POOL, 5, main_pool=MAINS, extras=2, extra_filter=spy)
        # 第一层只看到主品；第二层看到主品 + 已抽出的第一个配品
        self.assertEqual(seen[0], ("B",))
        self.assertEqual(seen[-1], ("B", "D"))
        self.assertTrue(all(s in (("B",), ("B", "D")) for s in seen))

    def test_all_filtered_out_leaves_main_only(self):
        cand = candidate_for_seed(
            POOL, 5, main_pool=MAINS, extras=2, extra_filter=lambda meal, picked: False
        )
        self.assertEqual([p.product_code for p in cand.picks], ["B"])


class TestExtrasFallBackWhenNoNonMain(unittest.TestCase):
    """整本菜单都是主食这类极端门店，仍要开得出盒，不能直接报「无解」。"""

    ALL_MAIN = [m("A", "甲堡", "main"), m("B", "乙堡", "main"), m("C", "丙堡", "main")]

    def test_falls_back_to_full_pool(self):
        cand = candidate_for_seed(self.ALL_MAIN, 5, main_pool=self.ALL_MAIN, extras=1)
        self.assertEqual(len(cand.picks), 2)
        self.assertEqual(cand.picks[0], cand.main)

    def test_still_deterministic(self):
        first = candidate_for_seed(self.ALL_MAIN, 5, main_pool=self.ALL_MAIN, extras=1)
        second = candidate_for_seed(self.ALL_MAIN, 5, main_pool=self.ALL_MAIN, extras=1)
        self.assertEqual(first, second)


class TestDeterminism(unittest.TestCase):
    def test_same_input_same_output(self):
        first = candidate_for_seed(POOL, 123456789, main_pool=MAINS, extras=2)
        second = candidate_for_seed(POOL, 123456789, main_pool=MAINS, extras=2)
        self.assertEqual(first, second)

    def test_different_seed_can_change_result(self):
        results = {
            tuple(p.product_code for p in candidate_for_seed(POOL, s, main_pool=MAINS).picks)
            for s in range(0, 30)
        }
        self.assertGreater(len(results), 1)


class TestEmptyPool(unittest.TestCase):
    def test_empty_pool_raises(self):
        with self.assertRaises(ConstraintUnsatisfiable):
            candidate_for_seed([], 5)


class TestGenerateCandidates(unittest.TestCase):
    def test_first_candidate_uses_the_timestamp(self):
        cands = generate_candidates(POOL, 1000, count=3, main_pool=MAINS, extras=1)
        self.assertEqual(cands[0].seed, 1000)
        self.assertEqual(cands[0].rank, 0)

    def test_seeds_step_by_prime_stride(self):
        cands = generate_candidates(POOL, 1000, count=4, main_pool=MAINS, extras=1)
        self.assertEqual(
            [c.seed for c in cands],
            [1000, 1000 + SEED_STRIDE, 1000 + 2 * SEED_STRIDE, 1000 + 3 * SEED_STRIDE],
        )

    def test_ranks_are_sequential(self):
        cands = generate_candidates(POOL, 1000, count=5, main_pool=MAINS, extras=1)
        self.assertEqual([c.rank for c in cands], [0, 1, 2, 3, 4])

    def test_default_count(self):
        cands = generate_candidates(POOL, 1000, main_pool=MAINS, extras=1)
        self.assertEqual(len(cands), 8)

    def test_count_must_be_positive(self):
        with self.assertRaises(InvalidArgument):
            generate_candidates(POOL, 1000, count=0, main_pool=MAINS)

    def test_extra_filter_is_forwarded(self):
        blocked = lambda meal, picked: meal.product_code != "C"  # noqa: E731
        cands = generate_candidates(
            POOL, 1000, count=3, main_pool=MAINS, extras=2, extra_filter=blocked
        )
        for cand in cands:
            self.assertNotIn("C", [p.product_code for p in cand.picks])


class TestCandidateShape(unittest.TestCase):
    def test_second_property(self):
        cand = candidate_for_seed(POOL, 5, main_pool=MAINS, extras=1)
        self.assertEqual(cand.second, cand.picks[1])

    def test_second_is_none_without_extras(self):
        cand = candidate_for_seed(POOL, 5, main_pool=MAINS, extras=0)
        self.assertIsNone(cand.second)

    def test_defaults(self):
        cand = Candidate(seed=1, main=POOL[0])
        self.assertEqual(cand.picks, ())
        self.assertEqual(cand.rank, 0)
        self.assertEqual(cand.main_pool_size, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
