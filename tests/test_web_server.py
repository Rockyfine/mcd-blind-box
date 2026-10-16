"""网页桥接后端（web/server.py）的契约测试。

全部走离线 fixtures（``offline=True``，无需 Token），直接调用 ``web.server``
暴露出来的业务纯函数，不起真实 HTTP 端口。覆盖：

- 开盒 / 查门店 / 查地址 的 JSON 契约
- 异常 → 统一错误信封（EXIT_* 语义）的映射
- 下单门禁：``confirm=false`` 时 ``create-order`` 从未被调用
- 会话过期 / 缺失 → GATE
- Token 掩码不泄露明文
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import web.server as srv  # noqa: E402

from mcd_blind_box.constants import WRITE_TOOLS  # noqa: E402
from mcd_blind_box.errors import (  # noqa: E402
    ConstraintUnsatisfiable,
    GateRejected,
    InvalidArgument,
)
from mcd_blind_box.transport import mask_token  # noqa: E402

PICKUP_STORE = "100001"
PICKUP_NAME = "南京新街口餐厅"
DT_STORE = "100003"


def _open(store_code=PICKUP_STORE, store_name=PICKUP_NAME, **kw) -> dict:
    sessions = srv.SessionStore()
    body = {
        "scene": "pickup",
        "store": {"storeCode": store_code, "storeName": store_name},
        "modes": [],
        "timestampMs": 1789000000000,
    }
    body.update(kw)
    res = srv.do_open(True, None, body, sessions)
    return res


class TestHealth(unittest.TestCase):
    def test_health_reports_mode(self) -> None:
        res = srv.do_health(True, 0.0)
        self.assertTrue(res["ok"])
        self.assertEqual(res["mode"], "offline")
        self.assertIn("version", res)


class TestOpenContract(unittest.TestCase):
    def test_open_returns_card_and_session(self) -> None:
        res = _open()
        self.assertTrue(res["ok"])
        self.assertIn("sessionId", res)
        self.assertIn("card", res)
        self.assertIn("shareText", res)

    def test_card_has_expected_shape(self) -> None:
        res = _open(modes=["budget"], budgetYuan=30)
        card = res["card"]
        for key in (
            "modes", "taste", "budgetYuan", "calorieCap", "challengeOk",
            "notes", "store", "items", "price", "coupon", "seed",
        ):
            self.assertIn(key, card)
        self.assertEqual(card["store"]["storeName"], PICKUP_NAME)
        self.assertGreater(len(card["items"]), 0)
        self.assertIn("payableYuan", card["price"])

    def test_store_name_is_patched_not_placeholder(self) -> None:
        # locate_store(store_code=...) 会生成「门店 xxx（调用方指定）」占位名，
        # 桥接层必须用前端传来的真实店名覆盖。
        res = _open(store_code=PICKUP_STORE, store_name="真实门店名")
        self.assertEqual(res["card"]["store"]["storeName"], "真实门店名")

    def test_seed_is_reproducible(self) -> None:
        a = _open(timestampMs=1789000000000)["card"]
        b = _open(timestampMs=1789000000000)["card"]
        self.assertEqual(a["seed"], b["seed"])
        self.assertEqual(a["seed"], 1789000000000)

    def test_unknown_taste_raises(self) -> None:
        # 未知口味 → InvalidArgument（映射为 USAGE）
        with self.assertRaises(InvalidArgument):
            _open(modes=["taste"], taste="不存在的口味xyz")


class TestStores(unittest.TestCase):
    def test_stores_returns_list(self) -> None:
        res = srv.do_stores(True, None, {
            "scene": "pickup", "searchType": 2, "city": "南京市", "keyword": "新街口",
        })
        self.assertTrue(res["ok"])
        self.assertGreater(len(res["stores"]), 0)
        s0 = res["stores"][0]
        self.assertIn("storeCode", s0)
        self.assertIn("storeName", s0)
        self.assertIn("beType", s0)

    def test_pickup_store_has_no_becode(self) -> None:
        res = srv.do_stores(True, None, {
            "scene": "pickup", "searchType": 2, "city": "南京市", "keyword": "新街口",
        })
        for s in res["stores"]:
            self.assertIsNone(s["beCode"])

    def test_dt_store_has_becode(self) -> None:
        res = srv.do_stores(True, None, {
            "scene": "dt", "searchType": 2, "city": "南京市", "keyword": "卡子门",
        })
        for s in res["stores"]:
            self.assertTrue(s["beCode"])

    def test_unknown_scene_raises_usage(self) -> None:
        with self.assertRaises(InvalidArgument):
            srv.do_stores(True, None, {"scene": "nope"})

    def test_city_search_requires_keyword(self) -> None:
        with self.assertRaises(InvalidArgument):
            srv.do_stores(True, None, {"scene": "pickup", "searchType": 2, "city": "南京"})

    def test_city_search_filters_by_city(self) -> None:
        # fixtures 里有南京（4 家）和苏州（1 家），按城市「苏州」搜应只剩 1 家
        res = srv.do_stores(True, None, {
            "scene": "pickup", "searchType": 2, "city": "苏州", "keyword": "观前街",
        })
        self.assertTrue(res["ok"])
        self.assertEqual(len(res["stores"]), 1)
        self.assertEqual(res["stores"][0]["storeName"], "苏州观前街餐厅")

    def test_keyword_search_narrows_results(self) -> None:
        # 关键词「新街口」在南京 4 家里只命中 1 家
        res = srv.do_stores(True, None, {
            "scene": "pickup", "searchType": 2, "city": "南京", "keyword": "新街口",
        })
        self.assertTrue(res["ok"])
        self.assertEqual(len(res["stores"]), 1)
        self.assertEqual(res["stores"][0]["storeCode"], "100001")

    def test_nearby_search_returns_all_in_city(self) -> None:
        # searchType=1（附近）不做关键词过滤，返回该 beType 全部门店
        res = srv.do_stores(True, None, {"scene": "pickup", "searchType": 1})
        self.assertTrue(res["ok"])
        self.assertEqual(len(res["stores"]), 5)


class TestAddresses(unittest.TestCase):
    def test_addresses_returns_list(self) -> None:
        res = srv.do_addresses(True, None)
        self.assertTrue(res["ok"])
        self.assertGreater(len(res["addresses"]), 0)
        self.assertIn("addressId", res["addresses"][0])


class TestOrderGate(unittest.TestCase):
    def test_order_without_confirm_is_rejected(self) -> None:
        sessions = srv.SessionStore()
        res = srv.do_open(True, None, {
            "scene": "pickup",
            "store": {"storeCode": PICKUP_STORE, "storeName": PICKUP_NAME},
            "timestampMs": 1789000000000,
        }, sessions)
        sid = res["sessionId"]
        with self.assertRaises(GateRejected):
            srv.do_order({"sessionId": sid, "confirm": False}, sessions)

    def test_order_without_session_is_rejected(self) -> None:
        with self.assertRaises(GateRejected):
            srv.do_order({"sessionId": "nonexistent", "confirm": True}, srv.SessionStore())

    def test_expired_session_is_rejected(self) -> None:
        sessions = srv.SessionStore(ttl_seconds=0)  # 立即过期
        res = srv.do_open(True, None, {
            "scene": "pickup",
            "store": {"storeCode": PICKUP_STORE, "storeName": PICKUP_NAME},
            "timestampMs": 1789000000000,
        }, sessions)
        with self.assertRaises(GateRejected):
            srv.do_order({"sessionId": res["sessionId"], "confirm": True}, sessions)

    def test_confirmed_order_succeeds_offline(self) -> None:
        sessions = srv.SessionStore()
        res = srv.do_open(True, None, {
            "scene": "pickup",
            "store": {"storeCode": PICKUP_STORE, "storeName": PICKUP_NAME},
            "timestampMs": 1789000000000,
        }, sessions)
        sid = res["sessionId"]
        order = srv.do_order({"sessionId": sid, "confirm": True}, sessions)
        self.assertTrue(order["ok"])
        self.assertIn("orderId", order["order"])
        self.assertTrue(order["order"]["offline"])  # 离线假订单要带 offline 标记
        # 一次性：下单成功后再取应失败
        with self.assertRaises(GateRejected):
            srv.do_order({"sessionId": sid, "confirm": True}, sessions)


class TestOrderStatus(unittest.TestCase):
    def test_order_status_returns_status(self) -> None:
        res = srv.do_order_status(True, None, {"orderId": "OFFLINE0001"})
        self.assertTrue(res["ok"])
        self.assertEqual(res["order"]["orderId"], "OFFLINE0001")
        self.assertIn("status", res["order"])

    def test_order_status_requires_order_id(self) -> None:
        with self.assertRaises(InvalidArgument):
            srv.do_order_status(True, None, {"orderId": ""})


class TestNoWriteDuringOpen(unittest.TestCase):
    def test_open_is_read_only(self) -> None:
        """开盒全程不得调用任何写工具（create-order 等）。"""
        sessions = srv.SessionStore()
        body = {
            "scene": "pickup",
            "store": {"storeCode": PICKUP_STORE, "storeName": PICKUP_NAME},
            "timestampMs": 1789000000000,
        }
        # 直接在 do_open 内部构建 backend，无法从外部审计；这里改为
        # 用 make_backend(offline=True) 手动走一遍，审计 write_calls 为空。
        from mcd_blind_box.backend import make_backend
        from mcd_blind_box.pipeline import BlindBoxPipeline

        backend = make_backend(offline=True)
        pipeline = BlindBoxPipeline(backend, offline=True)
        pipeline.set_scene(1)
        pipeline.locate_store(store_code=PICKUP_STORE)
        pipeline.fetch_menu()
        pipeline.open_box(timestamp_ms=1789000000000)
        self.assertEqual(backend.write_calls, [])

    def test_order_records_a_write_call(self) -> None:
        """真下单时审计必须看得见 —— 证明审计不是永远返回空的摆设。"""
        from mcd_blind_box.backend import make_backend
        from mcd_blind_box.pipeline import BlindBoxPipeline

        backend = make_backend(offline=True)
        pipeline = BlindBoxPipeline(backend, offline=True)
        pipeline.set_scene(1)
        pipeline.locate_store(store_code=PICKUP_STORE)
        pipeline.fetch_menu()
        pipeline.open_box(timestamp_ms=1789000000000)
        pipeline.order(confirm=True)
        self.assertIn("create-order", backend.write_calls)


class TestErrorMapping(unittest.TestCase):
    def test_maps_constraint_to_unsatisfiable(self) -> None:
        err = srv._map_exception(ConstraintUnsatisfiable("候选集为空"))
        self.assertEqual(err["error"]["code"], "UNSATISFIABLE")
        self.assertEqual(err["error"]["exit"], 6)

    def test_maps_invalid_argument_to_usage(self) -> None:
        err = srv._map_exception(InvalidArgument("参数不合法"))
        self.assertEqual(err["error"]["code"], "USAGE")
        self.assertEqual(err["error"]["exit"], 2)

    def test_maps_gate_to_gate(self) -> None:
        err = srv._map_exception(GateRejected("确认门禁拦截"))
        self.assertEqual(err["error"]["code"], "GATE")
        self.assertEqual(err["error"]["exit"], 3)

    def test_http_status_mapping(self) -> None:
        self.assertEqual(srv._http_status("AUTH"), 401)
        self.assertEqual(srv._http_status("RATE_LIMIT"), 429)
        self.assertEqual(srv._http_status("GATE"), 403)


class TestTokenSafety(unittest.TestCase):
    def test_mask_token_never_leaks_plaintext(self) -> None:
        secret = "sk-secret-token-abcdef123456"
        masked = mask_token(secret)
        self.assertNotIn(secret, masked)
        self.assertTrue(masked.startswith("****"))

    def test_live_requires_token(self) -> None:
        with self.assertRaises(InvalidArgument):
            srv.build_backend(False, None)


class TestModeSwitching(unittest.TestCase):
    """修复「提供 Token 后仍显示离线」的核心：请求体 offline 字段必须能覆盖启动默认值。"""

    def test_resolve_offline_prefers_body(self) -> None:
        # 启动时 --offline（handler_offline=True），但请求体显式 offline=false → 联网
        self.assertFalse(srv._resolve_offline(True, {"offline": False}))
        # 启动时默认联网（handler_offline=False），请求体 offline=true → 离线
        self.assertTrue(srv._resolve_offline(False, {"offline": True}))

    def test_resolve_offline_falls_back_to_handler(self) -> None:
        # 请求体不带 offline → 回落到启动参数
        self.assertTrue(srv._resolve_offline(True, {}))
        self.assertFalse(srv._resolve_offline(False, {}))

    def test_offline_false_without_token_raises(self) -> None:
        # 联网（offline=False）但没 token → InvalidArgument（映射 USAGE），而非静默回退离线
        with self.assertRaises(InvalidArgument):
            srv.build_backend(False, None)

    def test_offline_false_with_token_builds_live_backend(self) -> None:
        # 有 token → 真正构造联网 McpBackend（不实际发请求，只验证构造成功）
        backend = srv.build_backend(False, "test-token-abc")
        self.assertEqual(backend.name, "mcd-mcp")


class TestStaticServing(unittest.TestCase):
    def test_index_served(self) -> None:
        h = srv.Handler
        # 直接调 _serve_static（不经过 socket）
        inst = h.__new__(h)
        content, ctype, _ = inst._serve_static("/")
        self.assertIn("麦门盲盒", content.decode("utf-8"))
        self.assertIn("text/html", ctype)

    def test_static_assets_served(self) -> None:
        h = srv.Handler
        inst = h.__new__(h)
        for path, suffix in (("/style.css", "css"), ("/app.js", "javascript")):
            content, ctype, _ = inst._serve_static(path)
            self.assertGreater(len(content), 0)
            self.assertIn(suffix, ctype)

    def test_directory_traversal_blocked(self) -> None:
        h = srv.Handler
        inst = h.__new__(h)
        with self.assertRaises(InvalidArgument):
            inst._serve_static("/../README.md")
        with self.assertRaises(InvalidArgument):
            inst._serve_static("/../../etc/passwd")


if __name__ == "__main__":
    unittest.main()
