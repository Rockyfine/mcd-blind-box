"""枚举与映射的唯一真相源。

这里的每一个常量都逐字对应 `skill/mcd-blind-box-challenge/references/` 里的规则，
代码不做任何「合理推断」。要改规则，先改 Skill，再改这里。
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# 取餐方式：beType
# ---------------------------------------------------------------------------
# Skill 里只用到 1 / 5 / 2 三种；6（企业团餐）属于其他玩法方向，本项目不实现。
BE_TYPE_PICKUP = 1  # 到店自取
BE_TYPE_DT = 5  # 得来速车道取餐
BE_TYPE_DELIVERY = 2  # 麦乐送到家

BE_TYPE_LABELS = {
    BE_TYPE_PICKUP: "到店自取",
    BE_TYPE_DT: "得来速车道取餐",
    BE_TYPE_DELIVERY: "麦乐送到家",
}

# 本项目支持的 beType（刻意不含 6）
SUPPORTED_BE_TYPES = (BE_TYPE_PICKUP, BE_TYPE_DT, BE_TYPE_DELIVERY)

# CLI 的 --scene 取值 → beType
SCENE_TO_BE_TYPE = {
    "pickup": BE_TYPE_PICKUP,
    "dt": BE_TYPE_DT,
    "delivery": BE_TYPE_DELIVERY,
}

# ---------------------------------------------------------------------------
# orderType 固定映射（SKILL.md 规则 3）
# ---------------------------------------------------------------------------
ORDER_TYPE_IN_STORE = 1  # 到店（含自取 + 得来速）
ORDER_TYPE_DELIVERY = 2  # 外送（含麦乐送）

ORDER_TYPE_BY_BE_TYPE = {
    BE_TYPE_PICKUP: ORDER_TYPE_IN_STORE,
    BE_TYPE_DT: ORDER_TYPE_IN_STORE,
    BE_TYPE_DELIVERY: ORDER_TYPE_DELIVERY,
}

# beType=1 绝不传 beCode；beType=5/2 必传（SKILL.md 规则 1、2）
REQUIRES_BE_CODE = {
    BE_TYPE_PICKUP: False,
    BE_TYPE_DT: True,
    BE_TYPE_DELIVERY: True,
}

# ---------------------------------------------------------------------------
# 价格与格式
# ---------------------------------------------------------------------------
# calculate-price / query-meals 返回的价格单位是「分」，展示前必须 /100
PRICE_UNIT_DIVISOR = 100

# reservationDate 格式，仅预约场景传；本项目非预约模式根本不构造该字段
RESERVATION_DATE_FORMAT = "yyyy-MM-dd HH:mm"

# remark 最多 50 字，且仅外送场景引导填写
REMARK_MAX_LEN = 50

# ---------------------------------------------------------------------------
# MCP 服务事实（来自官方 mcd-mcp-server 文档与端点探测）
# ---------------------------------------------------------------------------
DEFAULT_ENDPOINT = "https://mcp.mcd.cn"
DEFAULT_SERVER_NAME = "mcd-mcp"
DEFAULT_SERVER_VERSION = "1.0.0"
PROTOCOL_VERSION = "2025-03-26"
EXPECTED_TOOL_COUNT = 35
MAX_REQUESTS_PER_MINUTE = 600
DEFAULT_TIMEOUT_SECONDS = 30
TOKEN_ENV_VAR = "MCD_MCP_TOKEN"

# ---------------------------------------------------------------------------
# 工具名白名单：杜绝把 query-nearby-stores 写成单数（SKILL.md 规则 14）
# ---------------------------------------------------------------------------
ALLOWED_TOOLS = frozenset(
    {
        "list-nutrition-foods",
        "delivery-query-addresses",
        "delivery-create-address",
        "delivery-query-stores",
        "query-meal-assistance",
        "query-nearby-stores",
        "query-store-coupons",
        "query-meals",
        "query-meal-detail",
        "calculate-price",
        "create-order",
        "cancel-order",
        "query-order",
        "order-list",
        "campaign-calendar",
        "available-coupons",
        "auto-bind-coupons",
        "query-my-coupons",
        "query-my-account",
        "mall-points-products",
        "mall-product-detail",
        "mall-create-order",
        "now-time-info",
        "query-lottery-info",
        "draw-lottery",
        "query-my-prizes",
        "mall-order-list",
        "mall-order-detail",
        "query-party-city",
        "query-party-store",
        "query-party-store-date",
        "query-party-store-session",
        "party-order-create",
        "query-promotions",
        "query-survey-coupon",
    }
)

# 本项目实际会用到的工具
USED_TOOLS = (
    "query-nearby-stores",
    "delivery-query-addresses",
    "delivery-query-stores",
    "query-meals",
    "query-meal-detail",
    "query-store-coupons",
    "calculate-price",
    "list-nutrition-foods",
    "now-time-info",
    "create-order",
    "query-order",
)

# 写操作（会真实改变状态）——必须走确认门禁
WRITE_TOOLS = frozenset(
    {
        "create-order",
        "cancel-order",
        "delivery-create-address",
        "auto-bind-coupons",
        "draw-lottery",
        "mall-create-order",
        "party-order-create",
    }
)

# 纯查询（无副作用）——可以直接调用，不需要门禁。
# 注意 calculate-price 只算价、不下单，属于这一组（confirmation-gate.md 第一节明确）。
READ_ONLY_TOOLS = frozenset(
    {
        "query-nearby-stores",
        "delivery-query-addresses",
        "delivery-query-stores",
        "query-meals",
        "query-meal-detail",
        "query-store-coupons",
        "calculate-price",
        "list-nutrition-foods",
        "query-order",
        "order-list",
        "now-time-info",
        "query-meal-assistance",
        "campaign-calendar",
        "available-coupons",
        "query-my-coupons",
        "query-my-account",
        "query-lottery-info",
        "query-my-prizes",
        "mall-points-products",
        "mall-product-detail",
        "mall-order-list",
        "mall-order-detail",
        "query-party-city",
        "query-party-store",
        "query-party-store-date",
        "query-party-store-session",
        "query-promotions",
        "query-survey-coupon",
    }
)

# ---------------------------------------------------------------------------
# 挑战模式
# ---------------------------------------------------------------------------
MODE_BLIND = "blind"  # 纯盲盒
MODE_BUDGET = "budget"  # 预算挑战
MODE_CALORIE = "calorie"  # 热量挑战
MODE_TASTE = "taste"  # 口味挑战

CHALLENGE_MODES = (MODE_BLIND, MODE_BUDGET, MODE_CALORIE, MODE_TASTE)

MODE_LABELS = {
    MODE_BLIND: "纯盲盒",
    MODE_BUDGET: "预算挑战",
    MODE_CALORIE: "热量挑战",
    MODE_TASTE: "口味挑战",
}

# 口味关键词映射（challenge-rules.md 第三节，逐字）
TASTE_KEYWORDS = {
    "辣": ("辣", "香辣", "麻辣", "藤椒", "火辣"),
    "鸡": ("鸡", "鸡块", "鸡翅", "麦乐鸡"),
    "牛": ("牛", "牛肉", "双层牛"),
    "鱼": ("鱼", "鳕鱼", "鱼堡"),
    "素": ("蔬", "素", "沙拉", "玉米", "派"),
    "甜": ("派", "冰淇淋", "圣代", "麦旋风", "甜筒"),
    "咖啡": ("咖啡", "拿铁", "美式", "麦咖啡"),
}

# 预算区间的组合骨架（challenge-rules.md 第三节 —— 参考区间，不是硬规则）
# 含义：(预算上限元, 主品数, 小食数, 饮料数)
BUDGET_SKELETON = (
    (20.0, 1, 1, 0),
    (30.0, 1, 1, 1),
    (40.0, 1, 1, 1),
    (50.0, 1, 1, 1),
    (float("inf"), 1, 1, 1),
)

# 离线自检要检查的 fixtures 清单（少一个都判不通过）
EXPECTED_FIXTURES = (
    "time.json",
    "stores.json",
    "addresses.json",
    "meals.json",
    "meal_details.json",
    "coupons.json",
    "price.json",
    "nutrition.json",
    "orders.json",
)

# 组合骨架里最多抽几个候选来试价（离线/联网都够用，且不触发限流）
CANDIDATE_COUNT = 8

# 派生候选种子的步长（取质数，避免相邻）
SEED_STRIDE = 7919

# ---------------------------------------------------------------------------
# 确认门禁（confirmation-gate.md）
# ---------------------------------------------------------------------------
# 构成确认：明确指向下单意图
CONFIRM_WHITELIST = (
    "确认下单",
    "确认",
    "下单吧",
    "就买这个",
    "帮我下单",
    "买了",
)

# 不构成确认：只是认可内容
CONFIRM_BLACKLIST_APPROVAL = (
    "就这个吧",
    "看起来不错",
    "行",
    "好吧",
    "嗯",
)

# 不构成确认：条件式 / 犹豫式
CONFIRM_BLACKLIST_HESITANT = (
    "如果可以的话就下单",
    "应该可以吧",
    "要不就这个",
)

# 提问标记：出现这些就不算确认
CONFIRM_QUESTION_MARKERS = (
    "?",
    "？",
    "多少钱",
    "多少",
    "热量",
    "能不能",
    "可以吗",
    "吗",
    "怎么",
    "为什么",
    "是什么",
    "哪些",
    "哪个",
)

# 感叹 / 复述标记
CONFIRM_EXCLAMATION_MARKERS = ("!", "！", "哇", "太", "哈哈", "笑死")

# 取消订单原因码
CANCEL_REASON_CODES = {
    "1": "改主意了",
    "2": "重复下单",
    "3": "点错了/点多了/点少了",
    "4": "地址/电话填错了",
    "5": "送达时间选错了",
    "-1": "其它原因",
}
CANCEL_REASON_DEFAULT = "-1"

# ---------------------------------------------------------------------------
# 商品分类标签（fixtures 与筛选逻辑共用）
# ---------------------------------------------------------------------------
CAT_MAIN = "main"
CAT_SIDE = "side"
CAT_DRINK = "drink"
CAT_DESSERT = "dessert"

# 揭晓卡上给配品标分类，让「配品到底是什么」一目了然
CATEGORY_LABELS = {
    CAT_MAIN: "主食",
    CAT_SIDE: "小食",
    CAT_DRINK: "饮品",
    CAT_DESSERT: "甜品",
}

# 真实菜单只给「分类名」（如「饮品」「小食甜品/其他」「鸡肉汉堡/卷」），
# 需要映射到上面四个桶。顺序即优先级。
#
# 兜底策略很关键：**认不出来的分类名一律算主品**。
# 这样最坏情况只是"主品池里多了一个不确定的东西"，
# 而不会出现"把汉堡当成配品"这种一眼假的结果。
MEAL_CATEGORY_KEYWORDS = (
    (CAT_DRINK, ("饮品", "饮料", "麦咖啡", "咖啡")),
    (CAT_DESSERT, ("甜品", "甜筒", "冰淇淋", "麦旋风", "新地", "派")),
    (CAT_SIDE, ("小食", "薯条", "蘸酱", "炸鸡", "鸡块", "鸡翅", "玉米", "拼盘")),
)

# ---------------------------------------------------------------------------
# 退出码
# ---------------------------------------------------------------------------
EXIT_OK = 0
EXIT_USAGE = 2
EXIT_BLOCKED = 3
EXIT_AUTH = 4
EXIT_RATE_LIMIT = 5
EXIT_UNSATISFIABLE = 6
