# 联网验证记录

本文记录对真实 `mcd-mcp` 端点的实际调用结果。**fixtures 里的所有字段名与结构都来自这份记录**，不是从工具描述里猜的。

- 验证日期：**2026-10-14**
- 端点：`https://mcp.mcd.cn`
- 方式：只读 —— 全程未调用任何写工具，未产生订单
- 复现：`python -m mcd_blind_box verify --live`，然后按第 3 节的三条命令各跑一次

> 说明：门店编码与门店名属于公开信息；**配送地址 ID 已打码**，那是个与账号绑定的标识，不应当进公开仓库。

---

## 1. 握手与鉴权

```
endpoint             https://mcp.mcd.cn
serverName           mcd-mcp
serverVersion        1.0.0
protocolVersion      2025-03-26
toolCount            35
expectedToolCount    35
toolCountMatches     True
sessionIdPresent     False
```

`tools/list` 枚举到 **35 个**工具，与官方文档一致。这一步只做握手与枚举，**不调用任何工具**。

## 2. 响应形状（逐字核对）

### 2.1 统一信封

每个工具的返回顶层键完全一致：

```
['code', 'data', 'datetime', 'message', 'success', 'traceId']
```

业务数据一律在 `data` 里，而 **`data` 的类型因工具而异**：

| 工具 | `data` 的类型 |
|---|---|
| `query-nearby-stores` | **list** |
| `delivery-query-stores` | **list** |
| `query-store-coupons` | **list** |
| `delivery-query-addresses` | dict（`data.addresses[]`） |
| `query-meals` | dict（`data.meals` 是 code-keyed 字典） |
| `list-nutrition-foods` | **str**（一段纯文本表） |

「`data` 是列表还是字典」如果假定错了，取数会静默落空 —— 这正是不假定它的原因。

### 2.2 `query-nearby-stores`

`data` 是列表，首行的键：

```
['address', 'distance', 'latitude', 'longitude', 'reservation',
 'reservationTimeOptions', 'storeCode', 'storeName']
```

**`beCode` 不在里面** —— 这印证了「`beType=1` 不能传 `beCode`」是数据结构层面的事实，而不是单纯的调用约定。`beType=5/2` 的返回里则有 `beCode`。

### 2.3 `now-time-info`

`data` 的键：

```
['date', 'datetime', 'day', 'dayOfWeek', 'formatted', 'month',
 'offset', 'timestamp', 'timezone', 'utc', 'year']
```

毫秒时间戳的字段名是 **`timestamp`**，不是 `timestampMs`。取错了会让随机种子永远是 0，而且**不报错**。

### 2.4 `list-nutrition-foods`

`data` 的类型是 **`str`**（纯文本表），不是 JSON 数组：

```
[160]{productName,nutritionDescription,energyKj,energyKcal,protein,fat,carbohydrate,sodium,calcium}:
  猪柳麦满分,null,1288,308,16,16,24,781,213
  中薯条,null,1210,289,4,12,38,165,18
```

首行 `[行数]{列名}:` 给出列序，所以解析要**按列名取 `energyKcal`**，不能假定列位置。

### 2.5 `query-meals`

真实门店返回 **120 件以上**在售餐品（本次三家分别是 127 / 130 / 125 件）。结构是：

- `data.meals` —— **以产品编码为键的字典**，条目里**没有** `productCode` 字段；
- `data.categories[].name` —— **分类名的唯一来源**，要建「码 → 分类名」索引；
- 价格字段 `currentPrice` / `originalPrice` 是**元的字符串**（`"33.5"`）。

用关键词把分类名映射到「主食 / 小食 / 饮品 / 甜品」四桶后，真实分布是合理的：

| 门店 | main | side | drink | dessert | 合计 |
|---|---|---|---|---|---|
| 到店自取 | 68 | 20 | 21 | 18 | 127 |
| 得来速 | 68 | 20 | 25 | 17 | 130 |
| 麦乐送 | 71 | 17 | 19 | 18 | 125 |

对不上号的分类名会被保守地算作**主食**（宁可主品池里多一个不确定的东西，也不要出现「把汉堡当配品」这种一眼假的结果）。

### 2.6 `calculate-price`：`takeWayList` 随 `beType` 变化

这是本次验证里最有价值的一个发现 —— 三种链路的取餐编码**各不相同**：

| 场景 | 返回的 `takeWayList` |
|---|---|
| 到店自取（beType=1） | `[{"code":"eat-in","title":"堂食","subtitle":"店内用餐"}, {"code":"take-in-store","title":"外带","subtitle":"店内自提"}]` |
| 得来速（beType=5） | `[{"code":"take-dt-quick","title":"得来速"}]` |
| 麦乐送（beType=2） | `[]`（空） |

所以 `takeWayCode` **只能取自算价返回的 `takeWayList[].code`**，代码里硬编码任何一个都会在另一条链路上发出非法参数。（`tests/test_pipeline_offline.py::test_take_way_code_is_never_invented` 守着这条。）

字段名与单位：`data.originalPrice` / `data.price` / `data.discount`，**都是分**。

### 2.7 服务端会自己减一笔钱

实测麦乐送那次算价返回了 `discount: 30`，但**没有任何券被选中**（本地券表里没有可用券）。也就是说：

```
原价 ¥103.80   券后价 ¥103.50   但没有任何券被选中
```

如果展示层用「减了多少钱」当判据，就会写出「券后价 ¥103.50 无可用券」这种自相矛盾的话。所以展示层判据改成「**有没有选到券**」，没有券但金额变少时写「服务端优惠 ¥0.30（非券来源）」。

### 2.8 `query-meal-detail`：特调 key 铁律得到验证

实测两个商品，恰好覆盖了两条分支：

```
9900005456 麦辣鸡腿汉堡三件套:  supportModify=False  顶层特调组=0  rounds=True   → 生成 0 条
511781     培根安格斯厚牛堡:    supportModify=True   顶层特调组=1  rounds=False  → 生成 5 条
```

`培根安格斯厚牛堡` 生成的前三条：

```json
[{"code": "100141", "key": "0-1", "quantity": 1},
 {"code": "100179", "key": "0-0", "quantity": 0},
 {"code": "120512", "key": "0-0", "quantity": 0}]
```

对照 `tool-contract.md` 第 9 节的铁律：

- 默认选中的那一项用 `selectedKey`（`"0-1"`）、`quantity=1`；
- 默认未选中的项用 **`unselectedKey`**（`"0-0"`）、`quantity=0`，**但仍然要传进去**；
- key 里的编号段来自服务端，**原样透传**，不能自己拼。

同时确认了「套餐」的形态：`supportModify=False` + `rounds=True` + 顶层特调组为空。因此本项目**对这类多轮套餐不生成特调** —— 见下面第 4 节。

---

## 3. 三条链路的完整开盒

> 全程只读。每一步的 `beType` / `orderType` / `beCode` 组合都由 `rules.build_scene_args()` 生成并校验。

### 到店自取（beType=1）

```
门店        1960153 麦当劳武汉理工大南湖校区餐厅
beCode      None            ← beType=1 全程不带 beCode
在售餐品    127 件

【麦门盲盒 · 纯盲盒】
主品  “我就喜欢”鸡薯双全盒 × 1
配品  柠檬脆脆麦旋风 × 1（甜品）
门店      麦当劳武汉理工大南湖校区餐厅（到店自取）
原价      ¥31.90
券后价    ¥31.90     无可用券
取餐方式  堂食 / 外带（takeWayCode=eat-in）
   种子 1791970393511 → 第 64 项（主品候选池 68 件；1791970393511 mod 68 = 63）
```

### 得来速（beType=5）

`searchType=1`（附近搜）返回**空列表**：

```
该位置没有查到可点餐门店。可以换个更宽泛的关键词，或改用其他取餐方式。
```

这是真实数据条件而不是错误 —— 得到来速常常不在「附近」。改用 `searchType=2` 带城市 + 关键词即可：

```
门店        1960220 武汉东风大道得来速餐厅
beCode      196022041       ← beType=5 必传
在售餐品    130 件

【麦门盲盒 · 纯盲盒】
主品  培根安格斯厚牛堡 × 1
配品  【美汁源】“黄金橙橙” × 1（饮品）
原价      ¥47.50
券后价    ¥47.50     无可用券
取餐方式  得来速（takeWayCode=take-dt-quick）
```

### 麦乐送（beType=2）

```
门店        1960059 麦当劳武汉未来城餐厅
beCode      196005902
配送地址    1036…87        ← 地址 ID 已打码
在售餐品    125 件

【麦门盲盒 · 纯盲盒】
主品  培根蔬萃双层牛堡 × 1
配品  无糖可口可乐 × 1（饮品）
原价      ¥45.10
券后价    ¥45.10     无可用券
```

### 写操作审计

链路的调用记录：

```
['calculate-price', 'delivery-query-addresses', 'delivery-query-stores',
 'list-nutrition-foods', 'now-time-info', 'query-meal-detail',
 'query-meals', 'query-nearby-stores', 'query-store-coupons']

写工具交集：无（只读，未产生订单）
```

`Backend.write_calls` 在离线与联网下是同一份实现，所以这句话在两边含义完全相同。

---

## 4. 一次「不猜」的记录：`roundList` 实测不被接受

工具描述里给 `calculate-price` 列了 `roundList[]`（套餐的「轮次 → 候选项」结构）。我们按文档形态构造后**实测的结果是**：

- 请求**不报错**；
- 但返回里**拿不到任何价格**（价格字段为空）。

也就是载荷被静默忽略了。既然没有验证通过的形状，项目就**不猜**：

> **遇到 `rounds` 结构的套餐，不生成这类特调。**

宁可少带一项特调（用默认配置），也不要发一个可能被静默丢弃的载荷 —— 后者会让用户以为自己拿到的是定制款，实际是默认款。这条决定写在 `models.MealDetail` 的文档字符串和 `pipeline._modification_values()` 里，并有测试守着（`test_rounds_only_detail_contributes_no_modification`）。

---

## 4b. 套餐的两套价格口径（实测：单项之和 ≠ 原价）

联网真店实测（2026-10-16，武汉工大南湖校区餐厅，到店自取）：

- `query-meals.currentPrice` 对套餐只返回**主品挂牌价**（火腿扒麦满分套餐 ¥10.20）；
- `calculate-price.originalPrice` 对同一套餐返回**展开默认子项后**的总价（¥17.00）；
- 麦当劳 App 实查证实结算价按展开口径走。

如果把两个口径混排在揭晓卡上（单项行用挂牌价、原价用算价），就会出现
「单项之和 ¥19.70 ≠ 原价 ¥26.50」。修复（`pipeline._fill_combo_unit_price`）：

- 对名含套餐信号词（`COMBO_NAME_HINTS`）的主品，**单件调一次 `calculate-price`**（无券、无特调），
  用返回的 `originalPrice` 回填 `unit_price_fen`，按 `product_code` 缓存；
- 普通单品零额外调用；探测失败保守保留挂牌价，开盒流程不失败；
- 回填后单项价、原价合计、券后价三者口径一致。

离线侧用 `fixtures/combo_prices.json` 录制该口径差（1005 鳕鱼堡套餐：挂牌 ¥18.00 / 展开价 ¥24.00，
录制值为示意，真实值以联网单件算价为准），`FixtureBackend` 复算时消费同一张表，
离线与线上共用同一回填代码路径。测试：`TestComboUnitPriceFillBack`。

---

## 5. `--live` 与离线的关系

- **离线是默认。** 不传 `--live` 就走 fixtures，不联网、不消耗额度。
- **离线不是「假的」**：fixtures 里存的是带完整信封的**服务端响应录制**，回放层不做任何字段变形，所以离线跑的就是线上同一套解析代码。字段名、`data` 的类型、价格的单位与字符串形态、取餐编码，全部按本文件逐字核对过。
- 唯一的差异是数据内容：门店名、价格、券是为可复现而构造的示例；真实数据只能通过 `--live` 获取。

要自己重跑一遍第 2、3 节：

```bash
export MCD_MCP_TOKEN=你的Token        # Windows: set MCD_MCP_TOKEN=...
python -m mcd_blind_box verify --live
python -m mcd_blind_box open --live --city 武汉市 --keyword 新街口
python -m mcd_blind_box open --live --scene dt --city 武汉市 --keyword 得来速
python -m mcd_blind_box open --live --scene delivery
```

Token 只从环境变量或 `--token` 读取 —— 代码**刻意不读** `~/.workbuddy/mcp.json`，避免把宿主凭证耦合进项目，也满足参赛声明的信息安全要求。

---

## 6. 关于 `workbuddy.md`

本仓库**未包含** `workbuddy.md`（WorkBuddy 专项奖励需要，非必须项）。

如果要补交：`workbuddy.md` 的内容是**使用 WorkBuddy 开发本项目时的对话上下文导出**，用于核验是否符合专项奖励条件。它不在源码仓库里生成，需要从 WorkBuddy 客户端导出使用过程的对话记录，然后把文件放到仓库根目录（文件名不可改动）。本项目已经包含的、可佐证开发过程与联动关系的材料是：

- 本文件（联网验证记录，含真实调用结果）
- `docs/architecture.md`（架构决策与取舍）
- `MCP_INTEGRATION.md`（实际使用了哪些 MCP 工具、怎么串、业务价值）
- `skill/`（作为 WorkBuddy Skill 的完整实现）
