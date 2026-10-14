# MCP 集成说明

本文说明本项目**实际使用了哪些麦当劳 MCP Server 能力、怎么把它们串成可用的流程、以及这样做解决了什么问题**。

- 服务器端点：`https://mcp.mcd.cn`
- 服务器名称：`mcd-mcp`
- 协议版本：`2025-03-26`（Streamable HTTP）
- 鉴权方式：`token-required` —— Token 只从环境变量 `MCD_MCP_TOKEN` 或 `--token` 读取
- 服务端可用工具：**35 个**；本项目实际使用 **11 个**
- 限流：每个 Token **600 次/分钟**

> 下文所有**响应字段名与结构**都来自对真实端点的实际调用，逐字核对过，不是从工具描述里推断的。完整的探测记录见 [`docs/live-verification.md`](./docs/live-verification.md)。

---

## 一、使用的工具（11 个）

| # | 工具 | 在我们流程里的角色 | 是否写操作 |
|---|---|---|---|
| 1 | `query-nearby-stores` | 到店自取 / 得来速的门店发现；`beCode` 来源 | 否 |
| 2 | `delivery-query-addresses` | 麦乐送的 `addressId` 来源 | 否 |
| 3 | `delivery-query-stores` | 麦乐送门店发现；`beCode` 来源 | 否 |
| 4 | `query-meals` | **`productCode` 的唯一入口** | 否 |
| 5 | `query-meal-detail` | 特调选项与 `selectedKey` / `unselectedKey` 来源 | 否 |
| 6 | `query-store-coupons` | **券后价的唯一合法券来源** | 否 |
| 7 | `calculate-price` | 券后价与 `takeWayList[].code` | 否 |
| 8 | `list-nutrition-foods` | 热量挑战的权威热量数据 | 否 |
| 9 | `now-time-info` | 可验证随机的种子（服务端毫秒时间戳） | 否 |
| 10 | `create-order` | 下单（**默认永不调用**） | ⚠️ **是** |
| 11 | `query-order` | 下单后查状态 | 否 |

### 逐个说明

**1. `query-nearby-stores`** — 到店自取（`beType=1`）与得来速（`beType=5`）的门店发现入口。

```
beType     integer  必填  1 = 到店自取，5 = 得来速
searchType integer  必填  1 = 搜索收藏餐厅，2 = 按位置搜索（默认 1）
city       string   条件  searchType=2 时必填
keyword    string   条件  searchType=2 时必填
```

返回形状：**`data` 本身就是一个列表**（不是 `data.stores`），行内含 `storeCode`、`storeName`、`address`、`distance`、`reservation`；**`beType=5` 的行里有 `beCode`，`beType=1` 的行里根本没有这个键** —— 这不是巧合，而是「`beType=1` 不能传 `beCode`」在数据结构层面的体现。

> 注意工具名是复数 `query-nearby-stores`。写成单数会调用不存在的工具 —— `rules.assert_tool_allowed()` 会用白名单把这类拼写错误直接拦下。
>
> 实测经验：`searchType=1`（附近搜）**在真实环境里常常返回空列表**，得来速尤其明显。这是数据条件而不是错误，所以拿到空列表时项目给出的是「改用城市 + 关键词搜」的建议，而不是重试同一个请求。

**2. `delivery-query-addresses`** — 取用户的配送地址列表，是麦乐送链路的起点。

返回形状：`data.addresses[]`，每项含 `addressId`、`contactName`、`phone`（**掩码**）、`fullAddress`。

**3. `delivery-query-stores`** — 麦乐送（`beType=2`）下，某个地址能配送的门店。

```
beType    integer  必填  2
addressId string   必填  来自 delivery-query-addresses
```

返回形状：与 `query-nearby-stores` 一样 `data` 是列表，行内含 `storeCode` 与 **`beCode`**（麦乐送必须携带）。

**4. `query-meals`** — **本项目取 `productCode` 的唯一入口**。规则第 4 条：下单任何商品都必须先查这一步，绝不允许凭记忆编编码。

```
storeCode string  必填
orderType integer 必填  1 或 2
beType    integer 必填  1 / 2 / 5
beCode    string  条件  beType=5/2 必传；beType=1 不传（传了报错）
```

返回形状（**这一步最容易踩**）：

- **`data.meals` 是「以产品编码为键的字典」，条目里没有 `productCode` 字段** —— 编码只能从字典的键拿；
- 分类名只在 `data.categories[].name` 上，需要先建「码 → 分类名」索引再展开字典（真实菜单里同一商品可能出现在多个分类，取第一次出现的那个）；
- `currentPrice` / `originalPrice` 是**元的字符串**（`"33.5"`），**不是分**。

真实门店的在售餐品在 120 件以上（实测三家 127 / 130 / 125 件），不能按个位数量写代码。

**5. `query-meal-detail`** — 取单个餐品的特调（可选配置）信息。

```
storeCode string  必填
orderType integer 必填
beType    integer 必填
code      string  必填  即 query-meals 返回的 productCode（字典的键）
beCode    string  条件
```

返回形状：顶层特调组挂在 **`data.modification.items[]`**；「默认选没选中」由 **`selectedQuantity`** 表示（没有 `selected` 布尔字段），每项带 `selectedKey` / `unselectedKey` —— **这两个 key 是传给 `calculate-price` 与 `create-order` 的唯一依据**。

套餐是 `data.rounds[].choices[]` 结构、顶层 `modification.items` 为空（否则算价会失败，见下面 `calculate-price` 一节）。

**6. `query-store-coupons`** — 查指定门店 + 订单类型下**真正可用**的券。

```
orderType integer 必填  1 = 到店（自取 + 得来速）；2 = 外送
beType    integer 必填
storeCode string  必填  编码必须全部由数字组成
beCode    string  条件
```

返回形状：**`data` 是列表**，而且券有**两种形态**：

| 形态 | 字段 | 可用性判定 |
|---|---|---|
| 门槛券 | `thresholdFen` / `discountFen` | 小计 ≥ 门槛即可用 |
| 商品券 | 只有 `products[].productCode` | **必须购物车里有它关联的商品**才可用 |

商品券（如「中薯条买一送一」）**没有**门槛与优惠金额，实际抵扣由服务端决定。所以既不能拿门槛去筛它（会选出一张用不上的券），也不能把它的优惠当成 0 而不作说明（那等于说这张券不值钱）。

> **为什么不能用 `query-my-coupons`**：它返回的是卡包资产，**不校验门店 / 渠道 / 配送方式是否可用**。拿它算券后价会出现「卡上写着减 5 元、实际结算没减」的情况 —— 揭晓卡的价格就是虚标的。项目在 `coupon.assert_coupon_source()` 里把 `query-my-coupons` 与 `available-coupons` 显式列入禁用名单，并保留 `FORBIDDEN_COUPON_SOURCES` 常量 + 测试守着这条线。

**7. `calculate-price`** — 购物车算价（含券），返回价格明细与取餐方式列表。

```
storeCode string  必填
orderType integer 必填
beType    integer 必填
beCode    string  条件
items     array   商品列表：{productCode, quantity, couponId?, couponCode?, modification.values[]}
```

返回形状：`data.originalPrice` / `data.price` / `data.discount`（**都是分**）、`data.takeWayList[]`。

三条硬规则：

- 价格字段单位是**分**，展示前必须 /100（`rules.fen_to_yuan()`）；
- `create-order.takeWayCode` **只能**取自这里的 `takeWayList[].code`，不允许自行构造（`rules.pick_take_way()` 找不到就抛错而不是猜一个）；
- **`roundList` 不要发。** 接口文档里列了 `roundList[]`，但实测**没有被接受**：带上它请求不报错，却拿不到任何价格（载荷被静默忽略）。既然没有验证通过的形状就不猜 —— 遇到 `rounds` 结构的套餐，本项目**不生成这类特调**，宁可少带一项也用默认配置。

`takeWayList[].code` **随 `beType` 变化**（实测三例）：

| beType | takeWayCode |
|---|---|
| `1` 到店自取 | `eat-in`（堂食）/ `take-in-store`（外带） |
| `5` 得来速 | `take-dt-quick` |
| `2` 麦乐送 | 无（返回空列表） |

这就是不能硬编码的原因 —— 硬编码 `eat-in` 会让得来速链路发出非法参数。

**8. `list-nutrition-foods`** — 官方营养数据，热量挑战的权威来源。

返回形状：**`data` 是一段纯文本表**，不是 JSON。

```
[160]{productName,nutritionDescription,energyKj,energyKcal,protein,fat,carbohydrate,sodium,calcium}:
  猪柳麦满分,null,1288,308,16,16,24,781,213
  中薯条,null,1210,289,4,12,38,165,18
```

首行的 `{列名}` 给出列序，所以解析要**按列名取 `energyKcal`**，不能假定列位置。

匹配策略是「按餐品名精确匹配 → 再退化为包含匹配 → **匹配不到就返回 `None`**」。绝不按 0 卡或估算值放行，因为那等于编造数据。体现在卡上就是：拿不到就是「无法计算」，并且不会把这一项算进总量。

**9. `now-time-info`** — 取服务端毫秒时间戳，作为盲盒的随机种子。

返回形状：毫秒时间戳在 **`data.timestamp`**（不是 `timestampMs`）。取错字段名会让种子永远是 0，而且**不报错**。

选它的原因：这是**服务端时间，可核验**。用它做种子，随机就从「模型编的」变成了「你可以自己算一遍的」。

**10. `create-order`** — 真实下单。**本项目默认永不调用。**

```
storeCode   string  必填
orderType   integer 必填
beType      integer 必填
beCode      string  条件
takeWayCode string  条件  orderType=1 必传，取自 calculate-price 的 takeWayList[].code
addressId   string  条件  orderType=2 必填
remark      string  否    最多 50 字，仅外送场景
items       array   否    与 calculate-price 同一套结构
```

**保护措施（三层，缺一不可）**：

1. CLI 层：`order` 子命令没有 `--confirm` 就返回退出码 3，**连数据源都不构造**；
2. 编排层：`pipeline.order()` 内部再判一次确认，即使 CLI 被绕过也不会误下单；
3. 判定层：给了用户原话时，以原话判定为准（`confirmation.classify()`），`--confirm` 不能盖过「好的」「先别下单」这类表达。

**11. `query-order`** — 按 `orderId` 查订单状态，用于下单后的支付引导。

### 关于特调 key 铁律

这是整个集成里最容易翻车的地方，单独说明。规则在 `calculate-price` 与 `create-order` 中**完全一致**：

1. 对某个特调组的**选中项**，`key` 用它的 `selectedKey`；
2. 对**未选中项**，如果它的 `unselectedKey` **非空**，则 key 必须用 `unselectedKey`，**并且这一项也必须传进去**；
3. 推论：**只要某个组里存在带 `unselectedKey` 的项，这个组下的所有项都要传**（不管用户选没选）；
4. key 为空的项直接跳过。

搞错的后果：含特调的商品算价直接报错；或者特调被静默丢弃，用户拿到的是默认款而不是盲盒抽出来的配置。

实现是**一个函数**，两处共用（`rules.build_modification_values()`），并有 11 个用例钉死（`tests/test_rules.py::TestModificationIronRule`）。

真实数据里的 key 形如 `"0-1"`（选中）/ `"0-0"`（未选中），不同组的编号段不同（也有 `"1-1"` / `"1-0"`）。**key 一律原样透传，绝不自己拼。** 实测例（`培根安格斯厚牛堡`）：

```json
[{"code": "100141", "key": "0-1", "quantity": 1},
 {"code": "100179", "key": "0-0", "quantity": 0}]
```

---

## 二、调用流程

### 三条取餐链路的差异

| 链路 | beType | orderType | beCode | addressId | remark | takeWayCode（实测对照） |
|---|---|---|---|---|---|---|
| 到店自取 | 1 | 1 | **不存在，绝对不能传** | 不使用 | 不允许 | `eat-in` / `take-in-store` |
| 得来速 | 5 | 1 | 必传（来自 `query-nearby-stores`） | 不使用 | 不允许 | `take-dt-quick` |
| 麦乐送 | 2 | 2 | 必传（来自 `delivery-query-stores`） | **必填** | 仅此链路可填 | 无 |

最后一列**不是写死在代码里的常量**，而是实测出来的对照 —— 代码一律从 `calculate-price` 的 `takeWayList[].code` 取。它随 `beType` 变化，所以硬编码任何一个都会在另一条链路上发出非法参数。

`beType` → `orderType` 是固定映射（`1/5 → 1`，`2 → 2`），写在 `constants.ORDER_TYPE_BY_BE_TYPE` 里，由 `rules.build_scene_args()` 统一构造参数 —— **所有点餐工具共用同一个构造函数**，所以不存在「某个工具忘了带 `beCode`」的可能。

### 主流程（6 步）

```
Step 0  模式确认        纯盲盒 / 预算 / 热量 / 口味（可叠加）；到店还是外送
          │
Step 1  定位门店        pickup/dt: query-nearby-stores(beType, searchType, [city, keyword])
          │             delivery : delivery-query-addresses() → delivery-query-stores(beType=2, addressId)
          │             ↳ 产出 storeCode 与 beCode
          │
Step 2  抓真实菜单      query-meals(storeCode, orderType, beType[, beCode])
          │             ↳ 产出候选池：这是 productCode 的唯一合法来源
          │
Step 3  开盲盒          （纯本地计算，不调用工具）
          │             · 种子 = now-time-info() 的 data.timestamp（毫秒）
          │             · 候选集按 productCode 升序排序，主品取 ts mod N
          │             · 配品用 floor(ts/N^level) 作种子，在剔除已选项的集合里取模
          │             · 热量挑战：list-nutrition-foods() 取热量，抽取前淘汰超标项
          │             · 需特调：query-meal-detail(storeCode, orderType, beType, code[, beCode])
          │             ↳ 产出组合（含 modification.values[]）
          │
Step 4  算价与用券      query-store-coupons(orderType, beType, storeCode[, beCode])
          │             → select_coupon(门槛券按门槛、商品券按关联商品筛选，再按优惠降序)
          │             → calculate-price(storeCode, orderType, beType, items[, beCode])
          │             ↳ 产出券后价（分）与 takeWayList[].code
          │
Step 5  揭晓 + 确认门禁 出揭晓卡，【默认到此为止】
          │
Step 6  下单（可选）    create-order(...)  ← 必须用户明确确认；本项目默认不执行
                        → 支付引导 → query-order(orderId)
```

> Step 3 与 Step 4 的**实际顺序是「先取券表，再抽组合」**：预算挑战需要在**抽取之前**用真实券表估算券后价，所以券表必须先拿到。这比单纯的「先抽后算」多一次调用，但换来的是「不会抽一套明知超预算的组合、再靠『已放宽』自圆其说」。

### 状态传递表

参数不是独立构造的，而是一条链往下传：

| 上游产出 | 下游用途 |
|---|---|
| `storeCode`（Step 1） | `query-meals` / `query-store-coupons` / `calculate-price` / `create-order` / `query-meal-detail` |
| `beCode`（Step 1，仅 beType 5/2） | 以上所有点餐工具；**换场景后必须作废重取** |
| `addressId`（Step 1，仅外送） | `delivery-query-stores` / `calculate-price` / `create-order` |
| `productCode`（Step 2） | `query-meal-detail` / `calculate-price` / `create-order` 的 `items[]` |
| `selectedKey` / `unselectedKey`（Step 3） | `items[].modification.values[].key` |
| `takeWayList[].code`（Step 4） | `create-order.takeWayCode`（**唯一合法来源**） |
| `orderId`（Step 6） | `query-order` |
`beCode` 与门店 + 取餐方式绑定，**换场景后旧值一律作废**。`pipeline.reset_scene()` 强制清空，避免跨店错配。

### 错误处理

| 错误 | 识别方式 | 处理 | 退出码 |
|---|---|---|---|
| Token 无效 / 未提供 | HTTP 401 | 报错并给出掩码后的 Token 与申请地址 | 4 |
| 触发限流 | HTTP 429 | 报错并说明 600 次/分钟限制，**不做重试风暴** | 5 |
| 场景参数不合法 | 本地前置校验 | 发请求前就拦（如 `beType=1` 带 `beCode`） | 2 |
| 门店未返回 `beCode` | 返回体校验 | 提前拦住，不等算价才炸 | 6 |
| 候选集为空 | 本地判定 | 给换口味 / 换门店 / 换模式的建议，**不编造菜品** | 6 |
| 约束凑不出 | 本地判定 | 逐级放宽并如实告知 | 0 |

---

## 三、业务价值

### 1. 把「决策疲劳」变成一个可结算的动作

用户真实痛点不是「没有选择」，而是「选择太多」。盲盒把五个步骤（选址 → 选品 → 配搭 → 看价 → 找券）压成一句话，并且**结果可以直接结算** —— 揭晓卡上的价格是 `calculate-price` 算出来的，取餐方式是 `takeWayList` 给的，不是展示用的假数据。

### 2. 用可验证性换取信任

「随机推荐」最容易被质疑的就是「你是不是在糊弄我」。本项目把随机变成可验算的：

```
种子 1789000000000 → 第 2 项（主品候选池 3 件；1789000000000 mod 3 = 1）
```

一行字，用户自己就能复核。约束凑不出时也如实说「没能在 5.00 元内凑出组合，这是最接近上限的一套」——**不假装达标**。这类诚实设计是让「盲盒」从噱头变成可用工具的关键。

### 3. 三种挑战覆盖三种真实约束

- **预算挑战**：不是「便宜点」，而是「券后价 ≤ 30 元」这个可判定的硬约束，并复用真实券表估算，所以多数情况下**一次就抽到达标组合**；
- **热量挑战**：接官方营养数据，拿不到就不给数字，避免把「不确定」包装成「低卡」；
- **口味挑战**：7 组关键词做模糊匹配，匹配不上就明说并给替代建议，而不是硬套一个不相干的商品。

### 4. 顺带暴露了 MCP 集成的真实坑，并且都做了防护

项目在 `SKILL.md` 里沉淀了 14 条关键规则，每一条都有对应代码与测试：

| 规则 | 不固化的后果 | 实现位置 |
|---|---|---|
| `beType=1` 不传 `beCode` | 到店链路一律失败 | `rules.assert_no_be_code_for_pickup` |
| `beType=5/2` 必传 `beCode` | 外送 / 得来速报错或跨店错配 | `rules.build_scene_args` |
| `orderType` 固定映射 | 枚举越界 / 取错取餐方式 | `constants.ORDER_TYPE_BY_BE_TYPE` |
| `productCode` 只能来自 `query-meals` | 凭记忆编编码 → 下单失败 | `pipeline.fetch_menu` |
| `query-meals` 的 `data.meals` 是**编码为键的字典** | 当成列表解析 → 一件餐品都取不到 | `pipeline._extract_products` |
| 分类名只能来自 `data.categories`，**不能靠编码猜** | 把汉堡当配品，卡上一眼假 | `models.classify_category` |
| `query-meals` 的价格是**元的字符串** | `"33.5"` 被当成 3350 分 → 显示成 ¥3350 | `models.yuan_to_fen` |
| `calculate-price` 的价格单位是**分** | 把 2850 分显示成「2850 元」 | `rules.fen_to_yuan` |
| `now-time-info` 的时间戳在 `data.timestamp` | 取错字段名 → 种子恒为 0，且**不报错** | `pipeline._server_timestamp_ms` |
| `list-nutrition-foods` 的 `data` 是**纯文本表** | 当成 JSON 解析 → 全部热量都拿不到 | `pipeline._calorie_map_from_text` |
| 特调 key 铁律 | 算价报错 / 特调丢失 | `rules.build_modification_values` |
| 券只能用 `query-store-coupons` | 券不抵扣，券后价虚标 | `coupon.assert_coupon_source` |
| **商品券要有关联商品才可用** | 选出一张根本用不上的券 | `coupon.applicable` |
| `takeWayCode` 只能来自算价结果，且**随 beType 变化** | 自造编码或硬编码 → 另一条链路参数非法 | `rules.pick_take_way` |
| **`roundList` 实测不被接受，不要发** | 载荷被静默忽略 → 特调丢失且价格为空 | `pipeline._modification_values` |
| 服务端可能给出**非券来源的优惠** | 写成「无可用券」→ 与价格栏自相矛盾 | `reveal.render_reveal` |
| `orderType=2` 必填 `addressId`；`remark` ≤50 字且仅外送 | 外送单缺地址；备注误填到自取单 | `rules.require_address_id` / `rules.validate_remark` |
| 自取单传了 `remark` 要**报错**而不是丢掉 | 用户以为备注写进去了，实际没有 | `pipeline.build_order_payload`（先校验后分支） |
| `reservationDate` 仅预约场景传 | 非预约场景乱传导致解析异常 | 非预约模式根本不构造该字段 |
| 写操作二次确认 | 误下单 / 误领券 | `confirmation.py`（三层保护） |
| 不得编造菜品 / 价格 / 券 / 热量 | 揭晓出不存在的商品或假数据 | 全流程只认工具返回值 |
| 未返回的 `couponId` / `couponCode` 不猜 | 传虚假 id 导致算价异常 | `CartItem.to_payload()` 不生成空值 |
| 工具名别写错（`query-nearby-stores` 是复数） | 调用不存在的工具 | `rules.assert_tool_allowed` 白名单 |
| 备注说的件数要和卡上一致 | 「没有任何配品」但卡上列着一件 | `pipeline._build_card`（按实际件数措辞） |

### 5. 离线可复现：让评审不用 Token 也能验证

`mcd-mcp` 需要 Token 才能访问，这对评审是个门槛。所以项目把 9 个 JSON fixture 当作**一台录制好的 MCP Server**，实现了与联网**完全相同**的 `Backend` 接口：

```python
class Backend:
    def call_tool(self, name, arguments=None) -> dict: ...
    def list_tools(self) -> list[dict]: ...
    # 两个实现共用的调用审计
    calls: list[str]
    @property
    def write_calls(self) -> list[str]: ...
```

算法层（`rules` / `challenge` / `coupon` / `pricing` / `pipeline`）完全不感知数据从哪来，**没有任何 `if offline:` 分支**。关键在于 fixtures 是**服务端响应的原样录制**（带完整信封），回放层不做任何字段变形 —— 于是离线跑的就是线上同一套解析代码：

- `python -m mcd_blind_box open --offline` 能跑通完整流程，零依赖、零 Token；
- 离线 fixtures 复刻了服务端的关键校验（比如 `beType=1` 携带 `beCode` 会被拒绝），所以离线跑通的链路在联网时不会因为同一个坑而失败；
- 想验证真实性时，加 `--live` 和 Token 即可，业务代码一行不改；
- 字段名、`data` 的类型、价格单位与字符串形态、取餐编码全部按 `docs/live-verification.md` 逐字核对过，**不是为了离线方便而自造一套结构**。

只有两个工具无法录制（结果取决于真实会话），它们是唯一在本地复算的例外：

- `calculate-price` —— 按菜单价与券规则复算，但**返回字段与真实响应完全一致**（`data.originalPrice` / `price` / `discount`，单位分）。商品券的抵扣额由服务端决定，本地**保守按 0 处理**，宁可估高不虚报。
- `create-order` —— 返回一张带 `offline: true` 标记的假订单，**永远不产生真实交易**。

fixtures 由 `scripts/make_fixtures.py` 生成，自带 `--check`。`tests/test_fixtures.py::test_fixtures_match_the_generator` 会在 fixtures 与生成器不同步时直接失败 —— 手改 fixtures 而不同步生成器，是这类项目最容易悄悄烂掉的地方。

---

## 四、未使用的工具与原因

服务端有 35 个工具，其余 24 个本项目刻意不用，理由分三类：

| 类别 | 工具 | 为什么不用 |
|---|---|---|
| 企业团餐 | `query-party-city`、`query-party-store`、`query-party-store-date`、`query-party-store-session`、`party-order-create` | `beType=6` 属于另一个玩法方向，本项目**未实现**，参数校验阶段直接拒绝 |
| 麦麦商城 / 积分 | `mall-points-products`、`mall-product-detail`、`mall-create-order`、`mall-order-list`、`mall-order-detail`、`query-my-account`、`query-my-prizes` | 与点餐盲盒无关，避免范围失控 |
| 营销 / 抽奖 / 问卷 | `draw-lottery`、`query-lottery-info`、`campaign-calendar`、`query-promotions`、`query-survey-coupon` | 同上；且多为写操作或带活动时效性，不适合做可复现的盲盒 |
| 卡包券 | `query-my-coupons`、`available-coupons`、`auto-bind-coupons` | 前两个**刻意禁用**：不校验门店/渠道可用性，会导致券后价虚标；`auto-bind-coupons` 是**写操作**（领券），盲盒默认不触发 |
| 订单管理 | `order-list`、`cancel-order` | 盲盒是「下一单」的场景，不做订单管理；取消订单属写操作，超出范围 |
| 地址写入 | `delivery-create-address` | 写操作；缺少地址时给出明确提示，交给用户自己在官方渠道新增 |
| 餐品辅助 | `query-meal-assistance` | 只读查询，本方向用不到（`query-meal-detail` 已覆盖所需信息） |

**注意**：未使用 ≠ 不知道。全部 35 个工具的参数、真实响应形状、坑与调用链都记录在 `skill/mcd-blind-box-challenge/references/tool-contract.md`，代码里的 `ALLOWED_TOOLS` 白名单也完整收录了 35 个，用来拦截拼写错误。这 35 个被切成 **7 个写操作 + 28 个只读**，两者互不相交、并集为全集（有测试守着）。

---

## 五、如何验证这份说明

不需要 Token：

```bash
# 1) 确认真的只用了这 11 个工具
python -m mcd_blind_box tools --offline

# 2) 确认 fixtures 齐全、可以离线复现全流程，且真的零依赖
python -m mcd_blind_box verify --offline

# 2b) 确认 fixtures 与生成器同步（手改会被拦住）
python scripts/make_fixtures.py --check

# 3) 看三条链路的实际参数差异（每条链路各跑一次）
python -m mcd_blind_box open --offline --scene pickup  --json | python -m json.tool
python -m mcd_blind_box open --offline --scene dt      --json | python -m json.tool
python -m mcd_blind_box open --offline --scene delivery --json | python -m json.tool

# 4) 确认到店链路全程没出现 beCode
python -m unittest tests.test_pipeline_offline.TestThreeChains -v

# 5) 确认没有 --confirm 时 create-order 从未被调用；
#    并确认「开盒只读」的调用审计在两个方向上都成立
python -m unittest tests.test_pipeline_offline.TestOrderGate -v
python -m unittest tests.test_pipeline_offline.TestNoWriteDuringOpen -v

# 6) 确认取餐编码是取自算价返回、而不是硬编码
python -m unittest tests.test_pipeline_offline.TestOrderPayload.test_take_way_code_is_never_invented -v
```

需要 Token（可选）：

```bash
export MCD_MCP_TOKEN=你的Token
python -m mcd_blind_box verify --live            # 握手 + 工具计数（预期 35）
python -m mcd_blind_box open --live --city 武汉市 --keyword 新街口
python -m mcd_blind_box open --live --scene dt --city 武汉市 --keyword 得来速
python -m mcd_blind_box open --live --scene delivery
```

`verify --live` 会报告协议版本、服务名、工具数是否等于 35、以及 Session 是否回传，且输出中的 Token 一定是掩码形式（`****XXXX`）。

完整的联网探测结果（含三家门店的真实分类分布、真实取餐编码、特调 key 实例）记录在 [`docs/live-verification.md`](./docs/live-verification.md)。
