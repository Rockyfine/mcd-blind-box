"""离线数据源：把 ``fixtures/`` 当作一台**录制好的 mcd-mcp**。

这是「一键复现」的关键 —— 任何人在任何机器上 clone 下来就能跑完整流程，
不联网、不申请 Token、不消耗额度。

设计上只有一条规则：**原样回放**。
fixtures 里存的就是服务端返回的完整信封
（``{"success":…, "code":…, "message":…, "data":…}``），
本类只按参数把它取出来返回，**不做任何字段变形**。
于是 :mod:`mcd_blind_box.pipeline` 里那套解析代码在离线和联网下走的是同一条路径，
任何响应形状漂移都会先在离线测试里炸出来，而不是等到联网时才悄悄算错。

两个例外，它们**无法录制**（结果取决于真实购物车与真实服务端）：

- ``calculate-price`` —— 价格是按真实会返回的字段
  （``data.originalPrice`` / ``data.price`` / ``data.discount``，单位**分**）
  在本地复算的。商品券的优惠金额由服务端决定，本地**保守地按不抵扣处理**，
  宁可把价估高，也不虚报一个自己算不出来的折扣。
- ``create-order`` —— 只返回一张带 ``offline`` 标记的假订单，
  并在返回里写明来源，**永远不会产生真实交易**。

它同时**复现真实服务端的关键校验**（例如 ``beType=1`` 携带 ``beCode`` 时报错），
所以离线跑通的链路，在联网模式下也不会因为同一个坑而失败。

:attr:`FixtureBackend.calls` 会记录每一次工具调用，测试可以用它断言
「没有 ``--confirm`` 时 ``create-order`` 从未被调用」。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import __version__
from .backend import Backend
from .constants import (
    ALLOWED_TOOLS,
    BE_TYPE_DELIVERY,
    BE_TYPE_DT,
    BE_TYPE_PICKUP,
    ORDER_TYPE_DELIVERY,
    ORDER_TYPE_IN_STORE,
)
from .errors import FixturesMissingError, InvalidArgument, SceneMismatch
from .rules import assert_tool_allowed

DEFAULT_FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"


class FixtureBackend(Backend):
    """把 fixtures 目录当作一个已录制的 MCP Server。"""

    name = "fixtures"

    def __init__(self, fixtures_dir: Optional[str] = None) -> None:
        super().__init__()
        self.dir = Path(fixtures_dir).resolve() if fixtures_dir else DEFAULT_FIXTURES_DIR
        self._cache: Dict[str, Any] = {}

    # -- 基础 ---------------------------------------------------------------
    def _load(self, filename: str) -> Any:
        if filename in self._cache:
            return self._cache[filename]
        path = self.dir / filename
        if not path.exists():
            raise FixturesMissingError(
                f"离线模式缺少 fixtures 文件：{path}。"
                "请确认在仓库根目录运行，或用 --fixtures 指定目录。"
            )
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise FixturesMissingError(f"{path} 不是合法 JSON：{exc}") from None
        self._cache[filename] = data
        return data

    @staticmethod
    def _check_scene(arguments: Dict[str, Any]) -> None:
        """模拟服务端行为：到店自取不能携带 beCode。"""
        if arguments.get("beType") == BE_TYPE_PICKUP and arguments.get("beCode"):
            raise SceneMismatch(
                "beType=1（到店自取）不能传 beCode —— fixtures 复现了服务端的拒绝行为"
            )

    # -- 公开 API -----------------------------------------------------------
    def list_tools(self) -> List[Dict[str, Any]]:
        return [{"name": name, "description": "(offline fixture)"} for name in sorted(ALLOWED_TOOLS)]

    def call_tool(
        self, name: str, arguments: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        assert_tool_allowed(name)
        args = dict(arguments or {})
        self._record(name, args)
        self._check_scene(args)

        handler = getattr(self, f"_tool_{name.replace('-', '_')}", None)
        if handler is None:
            raise FixturesMissingError(
                f"离线模式尚未实现工具 {name}（本项目的离线范围见 README「离线能跑到哪一步」）"
            )
        return handler(args)

    # -- 路由辅助 -----------------------------------------------------------
    def _by_store_and_order_type(self, filename: str, args: Dict[str, Any]) -> Dict[str, Any]:
        """取出 ``{storeCode: {orderType: 录制响应}}`` 里对应的一份。"""
        store_code = str(args.get("storeCode") or "")
        order_type = int(args.get("orderType") or ORDER_TYPE_IN_STORE)

        table = self._load(filename)
        store_block = table.get(store_code)
        if store_block is None:
            raise FixturesMissingError(
                f"fixtures 里没有门店 {store_code} 的记录；可用门店：{sorted(table)}"
            )
        block = store_block.get(str(order_type))
        if block is None:
            raise FixturesMissingError(
                f"门店 {store_code} 在 orderType={order_type} 下没有记录"
            )
        return block

    def _stores_for(self, be_type: int) -> Dict[str, Any]:
        if be_type not in (BE_TYPE_PICKUP, BE_TYPE_DT, BE_TYPE_DELIVERY):
            raise InvalidArgument(f"不支持的 beType={be_type!r}")
        table = self._load("stores.json")
        block = table.get(str(be_type))
        if block is None:
            raise FixturesMissingError(f"stores.json 里没有 beType={be_type} 的记录")
        return block

    # -- 纯回放的工具 -------------------------------------------------------
    def _tool_now_time_info(self, args: Dict[str, Any]) -> Dict[str, Any]:
        return self._load("time.json")

    def _tool_query_nearby_stores(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """回放门店列表，并按真实接口行为做城市/关键词过滤。

        真实 ``query-nearby-stores`` 在 ``searchType=2`` 时会按 ``city`` /
        ``keyword`` 匹配门店；``searchType=1``（附近）则按距离返回。
        这里复现同样的行为，避免离线模式下「按城市搜」永远返回全部门店，
        掩盖了前端搜索链路的问题。
        """
        block = self._stores_for(int(args.get("beType")))
        rows = block.get("data")
        if not isinstance(rows, list):
            return block

        search_type = int(args.get("searchType") or 1)
        if search_type == 2:
            city = str(args.get("city") or "").strip()
            keyword = str(args.get("keyword") or "").strip()
            filtered = []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                row_city = str(row.get("city") or "").strip()
                haystack = " ".join(
                    str(row.get(k) or "")
                    for k in ("storeName", "city", "district", "address")
                )
                # 城市做双向包含匹配（用户可能填「南京」或「南京市」，
                # 门店可能记「南京」），只要一方包含另一方即视为命中。
                if city and row_city:
                    if city not in row_city and row_city not in city:
                        continue
                elif city and not row_city:
                    # 门店没记城市时，退化为看关键词是否命中店名/地址
                    if keyword and keyword not in haystack:
                        continue
                if keyword and keyword not in haystack:
                    continue
                filtered.append(row)
            rows = filtered

        # 原样回放：保持信封结构不变，只替换 data 里的列表
        return {
            k: (rows if k == "data" else v)
            for k, v in block.items()
        }

    def _tool_delivery_query_stores(self, args: Dict[str, Any]) -> Dict[str, Any]:
        if not args.get("addressId"):
            raise InvalidArgument("delivery-query-stores 必填 addressId")
        return self._stores_for(BE_TYPE_DELIVERY)

    def _tool_delivery_query_addresses(self, args: Dict[str, Any]) -> Dict[str, Any]:
        return self._load("addresses.json")

    def _tool_query_meals(self, args: Dict[str, Any]) -> Dict[str, Any]:
        return self._by_store_and_order_type("meals.json", args)

    def _tool_query_store_coupons(self, args: Dict[str, Any]) -> Dict[str, Any]:
        return self._by_store_and_order_type("coupons.json", args)

    def _tool_list_nutrition_foods(self, args: Dict[str, Any]) -> Dict[str, Any]:
        return self._load("nutrition.json")

    def _tool_query_meal_detail(self, args: Dict[str, Any]) -> Dict[str, Any]:
        code = str(args.get("code") or "")
        details = self._load("meal_details.json")
        recorded = details.get(code)
        if recorded is not None:
            return recorded
        # 真实服务端对不可特调的商品也会返回一份结构完整的响应，这里照此模拟
        return _ok_envelope({"productCode": code, "supportModify": False, "rounds": [], "modification": {"items": []}})

    # -- 需要本地复算的两个工具 ---------------------------------------------
    def _tool_calculate_price(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """本地复算价格。**返回字段与真实响应一致**（单位：分）。"""
        store_code = str(args.get("storeCode") or "")
        order_type = int(args.get("orderType") or ORDER_TYPE_IN_STORE)
        items = args.get("items") or []
        if not isinstance(items, list):
            raise InvalidArgument("calculate-price 的 items 必须是数组")

        prices = self._unit_prices(store_code, order_type)
        combo_prices = self._combo_expanded_prices()
        original = 0
        coupon_id: Optional[str] = None
        item_codes: List[str] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            code = str(item.get("productCode") or "")
            quantity = int(item.get("quantity") or 0)
            # 套餐（rounds 结构）按录制的「单件算价」展开价计——
            # 与真实服务端口径一致：currentPrice 只是主品挂牌价。
            unit = combo_prices.get(code, prices.get(code, 0))
            original += unit * quantity
            item_codes.append(code)
            if coupon_id is None and item.get("couponId"):
                coupon_id = str(item["couponId"])

        discount = self._discount_for(store_code, order_type, coupon_id, original, item_codes)
        take_ways = self._take_ways(args)

        return _ok_envelope(
            {
                "storeCode": store_code,
                "orderType": order_type,
                "beType": args.get("beType"),
                # 真实字段：三个金额都是**分**
                "originalPrice": original,
                "discount": discount,
                "price": original - discount,
                "takeWayList": take_ways,
                "offlineRecomputed": True,
            }
        )

    def _take_ways(self, args: Dict[str, Any]) -> List[Dict[str, Any]]:
        """回放录制的取餐方式。

        **按 beType 取**：实战确认到店与得来速虽然同属 orderType=1，
        返回的 code 却不同（``eat-in``/``take-in-store`` vs ``take-dt-quick``）。
        """
        table = self._load("price.json").get("takeWayList") or {}
        be_type = args.get("beType")
        order_type = args.get("orderType")
        for key in (be_type, order_type):
            if key is None:
                continue
            block = table.get(str(key))
            if block is not None:
                return block
        return []

    def _discount_for(
        self,
        store_code: str,
        order_type: int,
        coupon_id: Optional[str],
        original_fen: int,
        item_codes: List[str],
    ) -> int:
        """只对**门槛券**算出确切的抵扣额。

        商品券的优惠金额由服务端根据它的规则决定（「买一送一」到底减多少、
        和购物车数量怎么联动），本地无法复现，所以返回 0 —— 保守估高，
        不虚报一个自己算不出来的折扣。
        """
        if not coupon_id:
            return 0
        for coupon in self._recorded_coupons(store_code, order_type):
            if str(coupon.get("couponId")) != coupon_id:
                continue
            if coupon.get("products"):
                return 0
            threshold = int(coupon.get("thresholdFen", 0) or 0)
            if original_fen < threshold:
                return 0
            return min(int(coupon.get("discountFen", 0) or 0), original_fen)
        return 0

    def _recorded_coupons(self, store_code: str, order_type: int) -> List[Dict[str, Any]]:
        table = self._load("coupons.json")
        block = (table.get(store_code) or {}).get(str(order_type)) or {}
        rows = block.get("data") if isinstance(block, dict) else None
        return [row for row in (rows or []) if isinstance(row, dict)]

    def _tool_create_order(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """离线下单：只返回一张假订单，**绝不产生真实订单**。"""
        order_type = int(args.get("orderType") or ORDER_TYPE_IN_STORE)
        if order_type == ORDER_TYPE_DELIVERY and not args.get("addressId"):
            raise InvalidArgument("orderType=2（外送）必填 addressId")
        if order_type == ORDER_TYPE_IN_STORE and not args.get("takeWayCode"):
            raise InvalidArgument("orderType=1（到店）必填 takeWayCode")

        template = self._load("orders.json").get("created") or {}
        data = template.get("data") or {}
        items = args.get("items") or []
        quantity = sum(int(i.get("quantity") or 0) for i in items if isinstance(i, dict))
        return _ok_envelope(
            {
                "orderId": data.get("orderId", "OFFLINE0001"),
                "status": data.get("status", "待支付"),
                "orderType": order_type,
                "itemCount": quantity,
                "offline": True,
                "source": "fixtures（离线假订单，未产生真实交易）",
            }
        )

    def _tool_query_order(self, args: Dict[str, Any]) -> Dict[str, Any]:
        return _ok_envelope(
            {
                "orderId": str(args.get("orderId") or ""),
                "status": "待支付",
                "offline": True,
                "note": "离线模式仅回显订单号，不查询真实订单状态",
            }
        )

    # -- 本地价格表 ---------------------------------------------------------
    def _combo_expanded_prices(self) -> Dict[str, int]:
        """读取「套餐单件算价」录制表：code → 服务端展开子项后的原价（分）。

        ``combo_prices.json`` 缺失时返回空表（老 fixtures 兼容）——
        此时全部按菜单挂牌价复算，行为与旧版一致。
        """
        try:
            raw = self._load("combo_prices.json")
        except FixturesMissingError:
            return {}
        if not isinstance(raw, dict):
            return {}
        out: Dict[str, int] = {}
        for code, entry in raw.items():
            if code == "note" or not isinstance(entry, dict):
                continue
            # 信封录制：业务数据在 data 层；兼容直接放 data 的写法
            data = entry.get("data") if isinstance(entry.get("data"), dict) else entry
            try:
                value = int(data.get("originalPrice") or 0)
            except (TypeError, ValueError):
                continue
            if value:
                out[str(code)] = value
        return out

    def _unit_prices(self, store_code: str, order_type: int) -> Dict[str, int]:
        """从录制菜单里读单价（真实字段 ``currentPrice`` 是**元的字符串**）。"""
        block = self._by_store_and_order_type("meals.json", {"storeCode": store_code, "orderType": order_type})
        meals = ((block or {}).get("data") or {}).get("meals") or {}
        prices: Dict[str, int] = {}
        for code, entry in meals.items():
            if not isinstance(entry, dict):
                continue
            raw = entry.get("currentPrice")
            try:
                prices[str(code)] = int(round(float(str(raw)) * 100))
            except (TypeError, ValueError):
                prices[str(code)] = 0
        return prices


def _ok_envelope(data: Any) -> Dict[str, Any]:
    """构造一个与真实服务端同形的成功信封。"""
    return {
        "success": True,
        "code": 200,
        "message": "请求成功",
        "traceId": "offline-fixture",
        "data": data,
    }


__all__ = ["FixtureBackend", "DEFAULT_FIXTURES_DIR", "__version__"]
