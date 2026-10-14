"""fixtures 自身的契约测试。

这一组测的不是业务逻辑，而是**离线数据本身**是否还立得住：

- fixtures 必须与生成脚本同步（有人手改 fixture 会在这里被拦住）
- 每个文件都必须是「服务端原样响应的录制」，即带完整信封
- 回放层不得对录制内容做任何变形
- 本地复算的 ``calculate-price`` 必须用真实字段名与单位
- 单位/编码这类「约定」必须与代码里的常量一致

fixtures 一旦漂移，离线测试就会开始验证一个不存在的世界，
所以这组测试是其他所有测试的地基。
"""

from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from mcd_blind_box.constants import (  # noqa: E402
    CAT_MAIN,
    EXPECTED_FIXTURES,
    PRICE_UNIT_DIVISOR,
)
from mcd_blind_box.fixtures_client import FixtureBackend  # noqa: E402

FIXTURES_ROOT = REPO_ROOT / "fixtures"
GENERATOR = REPO_ROOT / "scripts" / "make_fixtures.py"


def load_generator():
    """把生成脚本当模块加载（它在 scripts/ 下，不是包的一部分）。"""
    spec = importlib.util.spec_from_file_location("make_fixtures", GENERATOR)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load(name: str):
    return json.loads((FIXTURES_ROOT / name).read_text(encoding="utf-8"))


class TestFixturesAreGenerated(unittest.TestCase):
    def test_every_expected_fixture_exists(self):
        for name in EXPECTED_FIXTURES:
            with self.subTest(name=name):
                self.assertTrue((FIXTURES_ROOT / name).exists(), f"{name} 缺失")

    def test_no_unexpected_extra_files(self):
        """多出来的文件容易是手改残留，让它显式失败一次。"""
        on_disk = {p.name for p in FIXTURES_ROOT.glob("*.json")}
        self.assertEqual(on_disk, set(EXPECTED_FIXTURES))

    def test_fixtures_match_the_generator(self):
        """手改 fixtures 会在这一步被拦住 —— 改数据请改生成脚本。"""
        module = load_generator()
        generated = module.build_fixtures()
        for name, payload in generated.items():
            with self.subTest(name=name):
                expected = module.render(payload)
                actual = (FIXTURES_ROOT / name).read_text(encoding="utf-8")
                self.assertEqual(
                    actual,
                    expected,
                    f"{name} 与 scripts/make_fixtures.py 不一致，请重新运行生成脚本",
                )

    def test_generation_is_reproducible(self):
        """同一个脚本跑两次必须逐字节一致，否则 --check 没有意义。"""
        module = load_generator()
        self.assertEqual(
            module.render(module.build_fixtures()),
            module.render(module.build_fixtures()),
        )


class TestRecordedEnvelope(unittest.TestCase):
    """每个 fixture 里存的都必须是带信封的服务端响应。"""

    @staticmethod
    def _envelopes(blob):
        """把 fixture 里所有「响应」的位置挖出来（支持按键路由的一层/两层）。"""
        found = []
        if isinstance(blob, dict) and "success" in blob and "data" in blob:
            return [blob]
        if isinstance(blob, dict):
            for value in blob.values():
                found.extend(TestRecordedEnvelope._envelopes(value))
        return found

    def test_every_response_carries_the_envelope(self):
        for name in EXPECTED_FIXTURES:
            with self.subTest(name=name):
                blob = load(name)
                if name == "price.json":
                    # price.json 只提供取餐方式，不是某次调用的完整响应
                    continue
                envelopes = self._envelopes(blob)
                self.assertTrue(envelopes, f"{name} 里找不到任何带信封的响应")
                for entry in envelopes:
                    self.assertIs(entry["success"], True)
                    self.assertEqual(entry["code"], 200)
                    self.assertIn("data", entry)

    def test_menu_is_keyed_by_product_code(self):
        """真实 ``data.meals`` 是**以编码为键的字典**，条目里没有 productCode。"""
        menus = load("meals.json")
        for store_code, by_order_type in menus.items():
            for order_type, recorded in by_order_type.items():
                with self.subTest(store=store_code, orderType=order_type):
                    data = recorded["data"]
                    self.assertIsInstance(data["meals"], dict)
                    self.assertTrue(data["meals"])
                    for code, entry in data["meals"].items():
                        self.assertNotIn("productCode", entry, "真实条目里没有这个字段")
                        # 价格是**元的字符串**
                        self.assertIsInstance(entry["currentPrice"], str)
                        self.assertRegex(entry["currentPrice"], r"^\d+(\.\d+)?$")

    def test_categories_index_matches_the_menu(self):
        """``data.categories`` 是分类的唯一来源，索引必须和菜单对得上。"""
        menus = load("meals.json")
        for by_order_type in menus.values():
            for recorded in by_order_type.values():
                data = recorded["data"]
                indexed = {
                    entry["code"]
                    for category in data["categories"]
                    for entry in category["meals"]
                }
                self.assertEqual(indexed, set(data["meals"]))

    def test_time_uses_the_real_field_name(self):
        self.assertIn("timestamp", load("time.json")["data"])

    def test_nutrition_is_a_plain_text_table(self):
        """真实返回的 ``data`` 是纯文本，不是 JSON 数组。"""
        data = load("nutrition.json")["data"]
        self.assertIsInstance(data, str)
        self.assertIn("energyKcal", data)


class TestFixtureConventions(unittest.TestCase):
    """fixtures 自带的约定（编码分类位、取餐 code）必须与代码一致。"""

    def test_product_code_prefix_still_encodes_the_category(self):
        """这条只是 fixtures 自己的可读性约定，方便肉眼核对。

        生产代码**不依赖**它（分类一律走 data.categories），
        所以这里断言的是「fixtures 没被人乱改」，不是业务逻辑。
        """
        prefix_to_category = {"1": CAT_MAIN, "2": "side", "3": "drink", "4": "dessert"}
        menus = load("meals.json")
        for by_order_type in menus.values():
            for recorded in by_order_type.values():
                data = recorded["data"]
                for category in data["categories"]:
                    for entry in category["meals"]:
                        with self.subTest(code=entry["code"]):
                            self.assertIn(entry["code"][0], prefix_to_category)

    def test_take_way_codes_are_the_real_ones(self):
        """三种真实 code 都要录到 —— 包括实战里才发现的得来速 ``take-dt-quick``。"""
        take_ways = load("price.json")["takeWayList"]
        self.assertEqual(
            [t["code"] for t in take_ways["1"]], ["eat-in", "take-in-store"]
        )
        self.assertEqual([t["code"] for t in take_ways["5"]], ["take-dt-quick"])
        self.assertEqual(take_ways["2"], [])
        self.assertIn("title", take_ways["1"][0])

    def test_take_way_codes_never_contain_an_invented_one(self):
        """TW001 这类自造编码一旦出现在 fixtures 里，就说明有人开始编了。"""
        text = (FIXTURES_ROOT / "price.json").read_text(encoding="utf-8")
        self.assertNotIn("TW0", text)

    def test_dessert_calories_are_deliberately_absent(self):
        """留一条匹配不到热量的商品，才能一直验证「匹配不到就不给总量」。"""
        data = load("nutrition.json")["data"]
        self.assertNotIn("圆筒冰淇淋", data)
        self.assertIn("圆筒冰淇淋", load("meals.json")["100001"]["1"]["data"]["meals"]["4003"]["name"])


class TestReplayFidelity(unittest.TestCase):
    """回放层不许改写录制内容。"""

    def test_query_meals_returns_the_recorded_bytes(self):
        backend = FixtureBackend()
        result = backend.call_tool("query-meals", {"storeCode": "100001", "orderType": 1})
        self.assertEqual(result, load("meals.json")["100001"]["1"])

    def test_coupons_are_replayed_verbatim(self):
        backend = FixtureBackend()
        result = backend.call_tool(
            "query-store-coupons", {"storeCode": "100001", "orderType": 1}
        )
        self.assertEqual(result, load("coupons.json")["100001"]["1"])

    def test_addresses_are_replayed_verbatim(self):
        backend = FixtureBackend()
        self.assertEqual(
            backend.call_tool("delivery-query-addresses", {}), load("addresses.json")
        )

    def test_time_is_replayed_verbatim(self):
        backend = FixtureBackend()
        self.assertEqual(backend.call_tool("now-time-info", {}), load("time.json"))

    def test_stores_are_routed_by_be_type(self):
        backend = FixtureBackend()
        for be_type in (1, 5, 2):
            with self.subTest(beType=be_type):
                self.assertEqual(
                    backend.call_tool("query-nearby-stores", {"beType": be_type}),
                    load("stores.json")[str(be_type)],
                )


class TestCalculatedPriceShape(unittest.TestCase):
    """唯一被本地复算的工具，必须用真实的字段名与单位。"""

    def test_fields_are_the_real_ones_and_in_fen(self):
        backend = FixtureBackend()
        raw = backend.call_tool(
            "calculate-price",
            {
                "storeCode": "100001",
                "orderType": 1,
                "items": [
                    {"productCode": "1001", "quantity": 1, "couponId": "CP01"},
                ],
            },
        )
        data = raw["data"]
        for key in ("originalPrice", "price", "discount"):
            self.assertIsInstance(data[key], int, f"{key} 必须是整数分")
        # 巨无霸 28.5 元 → 2850 分，满 25 减 5
        self.assertEqual(data["originalPrice"], 2850)
        self.assertEqual(data["discount"], 500)
        self.assertEqual(data["price"], 2350)
        self.assertEqual(PRICE_UNIT_DIVISOR, 100)
        self.assertEqual(
            [t["code"] for t in data["takeWayList"]], ["eat-in", "take-in-store"]
        )

    def test_product_coupon_is_not_given_a_made_up_discount(self):
        """商品券的抵扣额由服务端决定，本地保守按 0 处理，绝不编一个数出来。"""
        backend = FixtureBackend()
        data = backend.call_tool(
            "calculate-price",
            {
                "storeCode": "100001",
                "orderType": 1,
                "items": [{"productCode": "2001", "quantity": 1, "couponId": "CP03"}],
            },
        )["data"]
        self.assertEqual(data["discount"], 0)
        self.assertEqual(data["price"], data["originalPrice"])

    def test_threshold_is_not_applied_below_the_threshold(self):
        backend = FixtureBackend()
        data = backend.call_tool(
            "calculate-price",
            {
                "storeCode": "100001",
                "orderType": 1,
                "items": [{"productCode": "1004", "quantity": 1, "couponId": "CP01"}],
            },
        )["data"]
        # 麦香鸡 14.5 元 < 满 25 门槛
        self.assertEqual(data["originalPrice"], 1450)
        self.assertEqual(data["discount"], 0)


class TestFixtureSafety(unittest.TestCase):
    def test_no_credential_like_strings(self):
        """fixtures 是要提交进仓库的，不能夹带任何真实凭证。"""
        import re

        suspicious = re.compile(r"(MCD_MCP_TOKEN|Bearer\s+\S{12,}|[A-Za-z0-9]{28,})")
        for path in sorted(FIXTURES_ROOT.glob("*.json")):
            with self.subTest(file=path.name):
                text = path.read_text(encoding="utf-8")
                self.assertIsNone(suspicious.search(text))

    def test_phone_like_fields_are_masked(self):
        """联系方式字段必须是掩码形式 —— 真实返回也是掩码的，不能拿真号码当样例。

        只查 ``phone`` / ``mobile`` / ``tel`` 这类字段的值，不做全文正则：
        毫秒时间戳之类的长数字串会被朴素正则误伤。
        """
        import re

        eleven_digit = re.compile(r"^1[3-9]\d{9}$")

        def walk(node, key=""):
            if isinstance(node, dict):
                for sub_key, value in node.items():
                    yield from walk(value, sub_key)
            elif isinstance(node, list):
                for value in node:
                    yield from walk(value, key)
            else:
                yield key, node

        for path in sorted(FIXTURES_ROOT.glob("*.json")):
            blob = json.loads(path.read_text(encoding="utf-8"))
            for key, value in walk(blob):
                if re.search(r"phone|mobile|tel", key, re.IGNORECASE) and isinstance(
                    value, str
                ):
                    with self.subTest(file=path.name, key=key):
                        self.assertIsNone(
                            eleven_digit.match(value), f"{key} 里像是真实手机号：{value}"
                        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
