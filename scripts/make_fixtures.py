#!/usr/bin/env python
"""生成 ``fixtures/`` 下的离线数据（零依赖、可复现）。

**设计原则：fixture 就是服务端响应的原样录制。**

每个文件里放的都是完整信封::

    {"success": true, "code": 200, "message": "请求成功",
     "datetime": "...", "traceId": "...", "data": ...}

按工具需要的路由键分层（``storeCode`` / ``orderType`` / ``beType`` / ``code``）。
``FixtureBackend`` 只做「按键取出、原样返回」，**不做任何字段变形** ——
于是离线跑的就是线上同一套解析代码，任何形状漂移都会在离线测试里先暴露出来。

这些形状不是猜的：:mod:`mcd_blind_box.pipeline` 里的解析分支、
``docs/live-verification.md`` 里的探测记录，都是对着真实端点核对过的。

两个无法录制的例外（价格/订单依赖真实会话，只能在本地复算）：

- ``calculate-price`` —— 按菜单价与券规则复算，但**返回字段与真实响应一致**
  （``data.originalPrice`` / ``data.price`` / ``data.discount``，单位都是**分**）。
- ``create-order`` —— 只返回一张带 ``offline`` 标记的假订单，绝不产生真实交易。

编码约定：fixtures 里商品编码的首位是分类位（1 主食 / 2 小食 / 3 饮品 / 4 甜品），
只为肉眼核对方便。真实编码**不保证**这个规律，所以生产代码只依赖
``data.categories[].name`` 做分类，从不依赖编码本身（见 tests/test_fixtures.py）。

用法::

    python scripts/make_fixtures.py                 # 写入仓库 fixtures/
    python scripts/make_fixtures.py --out /tmp/fx   # 写到别处
    python scripts/make_fixtures.py --check         # 只校验现有 fixtures 是否为最新
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = REPO_ROOT / "fixtures"

# 录制时间与种子时间戳：写死，保证每次生成的文件逐字节一致（--check 才有意义）
RECORDED_AT = "2026-10-14T17:13:43.704"
FIXTURE_TIMESTAMP_MS = 1789000000000

STORE_PICKUP_1 = "100001"
STORE_PICKUP_2 = "100002"
STORE_DT = "100003"
STORE_DELIVERY = "100004"


def envelope(data: Any) -> Dict[str, Any]:
    """把 ``data`` 包成 mcd-mcp 的统一信封。"""
    return {
        "success": True,
        "code": 200,
        "message": "请求成功",
        "datetime": RECORDED_AT,
        "traceId": "offline-fixture",
        "data": data,
    }


# ---------------------------------------------------------------------------
# 菜单：11 主食 + 7 小食 + 4 饮品 + 3 甜品 = 25 件
# 价格是**元的字符串**，与真实 ``currentPrice`` 一致
# ---------------------------------------------------------------------------
MAINS: List[Tuple[str, str, str]] = [
    ("1001", "巨无霸", "28.5"),
    ("1002", "双层吉士汉堡", "22.0"),
    ("1003", "香辣鸡腿堡", "20.5"),
    ("1004", "麦香鸡", "14.5"),
    ("1005", "鳕鱼堡套餐", "18.0"),
    ("1006", "麻辣鸡腿堡", "21.0"),
    ("1007", "田园脆鸡堡", "16.5"),
    ("1008", "双层牛肉堡", "25.0"),
    ("1009", "蔬菜沙拉卷", "13.0"),
    ("1010", "板烧鸡腿堡", "23.0"),
    ("1011", "藤椒鸡腿堡", "21.5"),
]
SIDES: List[Tuple[str, str, str]] = [
    ("2001", "中薯条", "11.0"),
    ("2002", "大薯条", "15.5"),
    ("2003", "麦乐鸡5块", "13.5"),
    ("2004", "麦辣鸡翅-2块", "14.5"),
    ("2005", "玉米杯", "9.5"),
    ("2006", "田园沙拉", "12.0"),
    ("2007", "藤椒风味鸡块", "15.0"),
]
DRINKS: List[Tuple[str, str, str]] = [
    ("3001", "可口可乐中杯", "9.5"),
    ("3002", "雪碧中杯", "9.5"),
    ("3003", "麦咖啡拿铁", "19.0"),
    ("3004", "麦咖啡美式", "11.9"),
]
DESSERTS: List[Tuple[str, str, str]] = [
    ("4001", "香芋派", "8.0"),
    ("4002", "奥利奥麦旋风", "13.5"),
    ("4003", "圆筒冰淇淋", "5.0"),
]
ALL_PRODUCTS = MAINS + SIDES + DRINKS + DESSERTS

# 分类名刻意**只按类归拢**（主食类名下不放配品），这样「分类名 → 四桶」的映射
# 不存在歧义，测试断言才有意义。真实菜单的「人气热卖」是混装的，
# 那正是 pipeline 拿第一处出现的分类名做索引、且认不出就算主品的原因。
CATEGORY_BLOCKS: Tuple[Tuple[str, List[str]], ...] = (
    ("鸡肉汉堡/卷", [c for c, _, _ in MAINS[:4]] + [MAINS[6][0]]),
    ("人气热卖", [MAINS[0][0], MAINS[2][0], MAINS[7][0], MAINS[10][0]]),
    ("巨无霸牛鱼肉堡", [MAINS[1][0], MAINS[4][0], MAINS[8][0]]),
    ("大堡口福单人餐", [MAINS[3][0], MAINS[5][0], MAINS[9][0]]),
    ("小食", [c for c, _, _ in SIDES]),
    ("饮品", [DRINKS[0][0], DRINKS[1][0]]),
    ("麦咖啡™", [DRINKS[2][0], DRINKS[3][0]]),
    ("甜品", [c for c, _, _ in DESSERTS]),
)

# 用真实门店的券面额（阈值券与商品券两种形态都要有）
COUPONS_PICKUP_1 = [
    {
        "couponId": "CP01",
        "couponCode": "CC01",
        "title": "满 25 减 5",
        "thresholdFen": 2500,
        "discountFen": 500,
        "products": [],
    },
    {
        "couponId": "CP02",
        "couponCode": "CC02",
        "title": "满 40 减 10",
        "thresholdFen": 4000,
        "discountFen": 1000,
        "products": [],
    },
    {
        # 商品券：没有「满 X 减 Y」的数字，只有关联商品。
        # 只有当购物车里真有 2001 中薯条时才可能抵扣。
        "couponId": "CP03",
        "couponCode": "CC03",
        "title": "中薯条买一送一",
        "products": [{"productCode": "2001", "productName": "中薯条"}],
    },
]
COUPONS_BY_STORE: Dict[str, List[Dict[str, Any]]] = {
    STORE_PICKUP_1: COUPONS_PICKUP_1,
    STORE_PICKUP_2: [],  # 故意留一家没有可用券的门店
    STORE_DT: [
        {
            "couponId": "CP04",
            "couponCode": "CC04",
            "title": "满 30 减 6",
            "thresholdFen": 3000,
            "discountFen": 600,
            "products": [],
        }
    ],
    STORE_DELIVERY: [
        {
            "couponId": "CP05",
            "couponCode": "CC05",
            "title": "外送满 35 减 8",
            "thresholdFen": 3500,
            "discountFen": 800,
            "products": [],
        }
    ],
}

# 特调：真实形状是 ``data.modification.items[].values[]``，
# 每个值带 ``selectedQuantity``（默认是否选中）与 ``selectedKey`` / ``unselectedKey``。
#
# 三个样例刻意覆盖铁律的三条分支：
#   1001 组内两个值都带 unselectedKey，且默认状态一选一未选 → 整组都要传
#   1003 组内两个值的 key **互不相同**（"0-*" 与 "1-*"）→ 用错 key 一定能被测出来
#   1006 组内没有任何 unselectedKey → 只传选中项
#   1005 是「轮次套餐」，特调挂在 rounds[].choices[] 上 → 本项目不生成这类特调
MEAL_DETAILS: Dict[str, Dict[str, Any]] = {
    "1001": {
        "productCode": "1001",
        "name": "巨无霸",
        "supportModify": True,
        "rounds": [],
        "modification": {
            "items": [
                {
                    "maxValues": 2,
                    "minValues": 0,
                    "values": [
                        {
                            "code": "V1001",
                            "name": "麦香鸡酱",
                            "selectedQuantity": 1,
                            "selectedKey": "0-1",
                            "unselectedKey": "0-0",
                        },
                        {
                            "code": "V1002",
                            "name": "切块生菜",
                            "selectedQuantity": 0,
                            "selectedKey": "0-1",
                            "unselectedKey": "0-0",
                        },
                    ],
                }
            ]
        },
    },
    "1003": {
        "productCode": "1003",
        "name": "香辣鸡腿堡",
        "supportModify": True,
        "rounds": [],
        "modification": {
            "items": [
                {
                    "maxValues": 1,
                    "minValues": 1,
                    "values": [
                        {
                            "code": "V3001",
                            "name": "标准辣",
                            "selectedQuantity": 1,
                            "selectedKey": "0-1",
                            "unselectedKey": "0-0",
                        },
                        {
                            "code": "V3002",
                            "name": "加辣",
                            "selectedQuantity": 0,
                            "selectedKey": "1-1",
                            "unselectedKey": "1-0",
                        },
                    ],
                }
            ]
        },
    },
    "1006": {
        "productCode": "1006",
        "name": "麻辣鸡腿堡",
        "supportModify": True,
        "rounds": [],
        "modification": {
            "items": [
                {
                    "maxValues": 1,
                    "minValues": 1,
                    "values": [
                        {
                            "code": "V6001",
                            "name": "正常辣",
                            "selectedQuantity": 1,
                            "selectedKey": "0-1",
                        },
                        {
                            "code": "V6002",
                            "name": "少辣",
                            "selectedQuantity": 0,
                            "selectedKey": "0-1",
                        },
                    ],
                }
            ]
        },
    },
    "1005": {
        "productCode": "1005",
        "name": "鳕鱼堡套餐",
        "supportModify": True,
        "modification": {"items": []},
        "rounds": [
            {
                "id": 1,
                "name": "鳕鱼堡",
                "quantity": 1,
                "choices": [
                    {
                        "code": "1600",
                        "name": "鳕鱼堡",
                        "quantity": 1,
                        "isDefault": 1,
                        "supportModify": True,
                        "modification": {
                            "items": [
                                {
                                    "maxValues": 1,
                                    "minValues": 0,
                                    "values": [
                                        {
                                            "code": "V9001",
                                            "name": "多加酱",
                                            "selectedQuantity": 1,
                                            "selectedKey": "0-1",
                                            "unselectedKey": "0-0",
                                        }
                                    ],
                                }
                            ]
                        },
                    }
                ],
            },
            {
                "id": 2,
                "name": "小食",
                "quantity": 1,
                "choices": [
                    {
                        "code": "2001",
                        "name": "中薯条",
                        "quantity": 1,
                        "isDefault": 1,
                        "supportModify": False,
                    }
                ],
            },
        ],
    },
}

# 营养：真实返回是**纯文本表**。
# 故意不给「圆筒冰淇淋」——用来验证「匹配不到热量就不显示总量」这条诚实性规则。
NUTRITION_ROWS: List[Tuple[str, int]] = [
    ("巨无霸", 550),
    ("双层吉士汉堡", 460),
    ("香辣鸡腿堡", 520),
    ("麦香鸡", 400),
    ("鳕鱼堡", 390),
    ("麻辣鸡腿堡", 530),
    ("田园脆鸡堡", 370),
    ("双层牛肉堡", 620),
    ("蔬菜沙拉卷", 320),
    ("板烧鸡腿堡", 430),
    ("藤椒鸡腿堡", 540),
    ("中薯条", 289),
    ("大薯条", 379),
    ("麦乐鸡5块", 213),
    ("麦辣鸡翅-2块", 224),
    ("玉米杯", 87),
    ("田园沙拉", 110),
    ("藤椒风味鸡块", 250),
    ("可口可乐中杯", 150),
    ("雪碧中杯", 140),
    ("麦咖啡拿铁", 130),
    ("麦咖啡美式", 10),
    ("香芋派", 232),
    ("奥利奥麦旋风", 266),
]


def menu_data() -> Dict[str, Any]:
    """构造 ``query-meals`` 的 ``data``：编码为键的字典 + 分类索引。"""
    meals: Dict[str, Any] = {}
    for code, name, price in ALL_PRODUCTS:
        meals[code] = {
            "name": name,
            "image": f"https://example.invalid/fixture/{code}.png",
            "currentPrice": price,
            "originalPrice": price,
            "canWithOrder": False,
        }
    categories = [
        {"name": name, "meals": [{"code": code, "tags": []} for code in codes]}
        for name, codes in CATEGORY_BLOCKS
    ]
    return {"categories": categories, "meals": meals, "frequent": []}


def nutrition_text() -> str:
    """真实 ``list-nutrition-foods`` 的 ``data`` 是一段纯文本，不是 JSON。"""
    lines = [
        f"[{len(NUTRITION_ROWS)}]{{productName,nutritionDescription,energyKj,"
        "energyKcal,protein,fat,carbohydrate,sodium,calcium}:"
    ]
    for name, kcal in NUTRITION_ROWS:
        kj = int(round(kcal * 4.184))
        lines.append(f"  {name},null,{kj},{kcal},0,0,0,0,0")
    return "\n".join(lines)


def build_fixtures() -> Dict[str, Any]:
    """返回 ``{文件名: 内容}``。内容全部是**可原样回放的响应**。"""
    menus = {
        STORE_PICKUP_1: {"1": envelope(menu_data())},
        STORE_PICKUP_2: {"1": envelope(menu_data())},
        STORE_DT: {"1": envelope(menu_data())},
        STORE_DELIVERY: {"2": envelope(menu_data())},
    }

    stores = {
        # beType=1 的行**没有** beCode；beType=5/2 的行**一定有** —— 这是硬规则
        # 每条门店带 city / district，供 searchType=2 的城市/关键词过滤复现真实接口行为
        "1": envelope(
            [
                {
                    "storeCode": STORE_PICKUP_1,
                    "storeName": "南京新街口餐厅",
                    "city": "南京",
                    "district": "秦淮区",
                    "address": "中山南路 1 号",
                    "distance": 182,
                    "businessStatus": True,
                    "reservation": True,
                },
                {
                    "storeCode": STORE_PICKUP_2,
                    "storeName": "南京鼓楼餐厅",
                    "city": "南京",
                    "district": "鼓楼区",
                    "address": "中央路 1 号",
                    "distance": 860,
                    "businessStatus": True,
                    "reservation": False,
                },
                {
                    "storeCode": "100005",
                    "storeName": "南京河西万达餐厅",
                    "city": "南京",
                    "district": "建邺区",
                    "address": "江东中路 98 号",
                    "distance": 1240,
                    "businessStatus": True,
                    "reservation": True,
                },
                {
                    "storeCode": "100006",
                    "storeName": "南京江宁百家湖餐厅",
                    "city": "南京",
                    "district": "江宁区",
                    "address": "双龙大道 1698 号",
                    "distance": 2310,
                    "businessStatus": True,
                    "reservation": False,
                },
                {
                    "storeCode": "100007",
                    "storeName": "苏州观前街餐厅",
                    "city": "苏州",
                    "district": "姑苏区",
                    "address": "观前街 1 号",
                    "distance": 215000,
                    "businessStatus": True,
                    "reservation": True,
                },
            ]
        ),
        "5": envelope(
            [
                {
                    "storeCode": STORE_DT,
                    "beCode": "DT9001",
                    "storeName": "南京卡子门得来速店",
                    "city": "南京",
                    "district": "秦淮区",
                    "address": "卡子门大街 1 号",
                    "distance": 7841,
                    "businessStatus": True,
                    "reservation": True,
                }
            ]
        ),
        "2": envelope(
            [
                {
                    "storeCode": STORE_DELIVERY,
                    "beCode": "DL8001",
                    "storeName": "南京鼓楼麦乐送店",
                    "city": "南京",
                    "district": "鼓楼区",
                    "address": "中央路 1 号",
                    "businessStatus": True,
                    "reservation": True,
                }
            ]
        ),
    }

    addresses = envelope(
        {
            "addresses": [
                {
                    "addressId": "A1001",
                    "contactName": "示例联系人",
                    "phone": "138****0000",  # 真实返回是掩码手机号
                    "fullAddress": "示例省示例市示例区示例路 1 号",
                }
            ]
        }
    )

    coupons = {
        store: {order_type: envelope(rows) for order_type, rows in per_type.items()}
        for store, per_type in _coupons_by_store_and_type().items()
    }

    return {
        "meals.json": menus,
        "stores.json": stores,
        "addresses.json": addresses,
        "meal_details.json": {
            code: envelope(detail) for code, detail in MEAL_DETAILS.items()
        },
        "coupons.json": coupons,
        "nutrition.json": envelope(nutrition_text()),
        "time.json": envelope(
            {
                "timestamp": FIXTURE_TIMESTAMP_MS,
                "datetime": RECORDED_AT,
                "date": RECORDED_AT[:10],
                "time": RECORDED_AT[11:19],
                "timezone": "GMT+08:00",
                "offset": "+08:00",
            }
        ),
        # 取餐方式来自 calculate-price 的 takeWayList。
        # **按 beType 键控**：实战确认同一 orderType 下 beType 不同，返回的 code 也不同 ——
        # 到店是 eat-in / take-in-store，而来得速是 take-dt-quick。
        # （都不是 TW001 这类自造编码；真实 code 一律以服务端返回为准，代码里绝不硬编码。）
        "price.json": {
            "note": (
                "取餐方式取自真实 calculate-price 返回的 takeWayList[].code，按 beType 分档；"
                "离线价格由 FixtureBackend 依菜单价与券规则复算。"
            ),
            "takeWayList": {
                "1": [
                    {"code": "eat-in", "title": "堂食", "subtitle": "店内用餐"},
                    {"code": "take-in-store", "title": "外带", "subtitle": "店内自提"},
                ],
                "5": [{"code": "take-dt-quick", "title": "得来速", "subtitle": "车道取餐"}],
                "2": [],
            },
        },
        "orders.json": {
            "note": "离线假订单模板；create-order 不会产生任何真实交易。",
            "created": envelope(
                {"orderId": "OFFLINE0001", "status": "待支付", "offline": True}
            ),
        },
        # 套餐单件算价录制：真实服务端对 rounds 套餐的 calculate-price 按
        # 「展开默认子项」计价，原价高于 query-meals.currentPrice（那只是
        # 主品挂牌价）。没有这条录制，离线复算会结构性掩盖两套口径的差异
        # （单项之和恒等于原价），联调真店时才暴露 —— 实测见
        # docs/live-verification.md（火腿扒麦满分套餐 10.20 vs 展开价 17.00）。
        # 形状 = 单件 calculate-price 响应的 data（按 productCode 键控）。
        "combo_prices.json": {
            "note": (
                "套餐单件 calculate-price 录制（data 层）；"
                "1005 鳕鱼堡套餐展开默认子项（鳕鱼堡+中薯条）后原价 2400 分，"
                "高于菜单挂牌价 1800 分。"
            ),
            "1005": envelope(
                {
                    "storeCode": STORE_PICKUP_1,
                    "orderType": 1,
                    "beType": 1,
                    "originalPrice": 2400,
                    "discount": 0,
                    "price": 2400,
                }
            ),
        },
    }


def _coupons_by_store_and_type() -> Dict[str, Dict[str, List[Dict[str, Any]]]]:
    return {
        STORE_PICKUP_1: {"1": COUPONS_PICKUP_1},
        STORE_PICKUP_2: {"1": []},
        STORE_DT: {"1": COUPONS_BY_STORE[STORE_DT]},
        STORE_DELIVERY: {"2": COUPONS_BY_STORE[STORE_DELIVERY]},
    }


def render(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成离线 fixtures（零依赖）")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="输出目录，默认仓库 fixtures/")
    parser.add_argument("--check", action="store_true", help="只校验现有文件是否为最新，不写入")
    args = parser.parse_args(argv)

    out = Path(args.out).resolve()
    fixtures = build_fixtures()

    if args.check:
        stale: List[str] = []
        missing: List[str] = []
        for name, payload in fixtures.items():
            path = out / name
            if not path.exists():
                missing.append(name)
            elif path.read_text(encoding="utf-8") != render(payload):
                stale.append(name)
        if stale or missing:
            print(f"fixtures 需要重新生成：缺失 {missing}，过期 {stale}")
            return 1
        print(f"fixtures 已是最新（{len(fixtures)} 个文件）")
        return 0

    out.mkdir(parents=True, exist_ok=True)
    for name, payload in fixtures.items():
        (out / name).write_text(render(payload), encoding="utf-8")
    print(f"已写入 {len(fixtures)} 个 fixture 到 {out}")
    for name in sorted(fixtures):
        print(f"  - {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
