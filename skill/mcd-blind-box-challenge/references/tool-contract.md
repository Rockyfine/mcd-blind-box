# 工具契约（本方向用到的 11 个工具）

> 参数名与枚举值逐字取自 `mcd-mcp v1.0.0`（`https://mcp.mcd.cn`）的真实 `inputSchema`，不要改写。
> **响应形状**则取自对这个端点的实际调用记录（见 `docs/live-verification.md`），不是从描述里猜的。
> 要查完整体检其他工具（积分商城、派对预约、抽奖等）见工具能力清单全文。

## 0. 全局约定

**统一信封。** 每个工具的返回都是同一层信封：

```json
{"success": true, "code": 200, "message": "请求成功", "datetime": "…", "traceId": "…", "data": …}
```

**业务数据一律在 `data` 里**，而 `data` 的类型因工具而异：有时是列表（门店、券），有时是字典（`data.addresses`、`data.meals`），`list-nutrition-foods` 甚至是一段**纯文本**。取数时不要假定 `data` 是列表或字典。

**`beType` 枚举**

| 值 | 含义 | `orderType` | `beCode` |
|---|---|---|---|
| `1` | 到店自取 | `1` | **不存在，绝对不能传** |
| `5` | 得来速车道取餐 | `1` | 必传，来自 `query-nearby-stores` |
| `2` | 麦乐送到家（外送） | `2` | 必传，来自 `delivery-query-stores` |

**`orderType` 枚举**：`1` = 到店（自取 + 得来速）；`2` = 外送。

**价格单位（两个工具不一样，别搞混）**

| 工具 | 字段 | 单位 |
|---|---|---|
| `query-meals` | `data.meals[code].currentPrice` / `originalPrice` | **元的字符串**（`"33.5"`） |
| `calculate-price` | `data.originalPrice` / `data.price` / `data.discount` | **分**（整数） |

把 `"33.5"` 当成 3350 分会得到一辆车，把 3350 当成元会得到 3350 块 —— 两边都要严格按字段语义换算。

**`reservationDate`**：格式 `yyyy-MM-dd HH:mm`，**仅预约场景传**。

---

## 1. `query-nearby-stores`

到店场景查可点餐门店。**到店链路的起点。**

| 参数 | 类型 | 必填 | 取值 |
|---|---|---|---|
| `beType` | integer | 是 | `1` = 到店自取，`5` = 得来速 |
| `searchType` | integer | 是 | `1` = 搜索收藏餐厅，`2` = 按位置搜索（默认 1） |
| `city` | string | 条件 | `searchType=2` 时必填 |
| `keyword` | string | 条件 | `searchType=2` 时必填，位置关键词 |

**关键约束**

- `beType=1` 返回的门店**没有 `beCode`**，后续工具一律不传 `beCode`。
- `beType=5` 返回的门店**有 `beCode`**，后续工具一律必传。
- 用户无收藏记录时用 `searchType=2`（需要 `city` + `keyword`）。
- **`searchType=1`（附近搜）常常返回空列表**，得来速尤其明显 —— 这是真实数据条件，不是错误。拿不到门店时应当建议改用 `searchType=2` 带城市 + 关键词，而不是重试同一个请求。

**真实返回形状**：`data` **本身就是一个列表**（不是 `data.stores`）。

```json
{"data": [
  {"storeCode": "1960153", "storeName": "…", "address": "…", "distance": 182,
   "latitude": …, "longitude": …, "reservation": true, "reservationTimeOptions": […]}
]}
```

行内有 `storeCode`、`storeName`、`address`、`distance`、`reservation`；`beType=1` 的行里**根本没有 `beCode` 这个键**。

---

## 2. `delivery-query-addresses`

查用户已有配送地址。**外送链路起点。无参数。**

**真实返回形状**：`data.addresses[]`。

```json
{"data": {"addresses": [
  {"addressId": "…", "contactName": "…", "phone": "138****0000", "fullAddress": "…"}
]}}
```

`phone` 是**掩码**形式。`addressId` 供 `delivery-query-stores` 与 `create-order` 使用。

**注意**：其他工具的描述里可能把它写成 `delivery-query-address`（单数），**实际工具名是复数 `delivery-query-addresses`**。

---

## 3. `delivery-query-stores`

按地址查可配送门店。

| 参数 | 类型 | 必填 | 取值 |
|---|---|---|---|
| `beType` | integer | 是 | `2` = 麦乐送 |
| `addressId` | string | 是 | 来自 `delivery-query-addresses` |

**关键约束**：到店场景请用 `query-nearby-stores`，**不要混用**。

**真实返回形状**：与 `query-nearby-stores` 一样，`data` 是列表，行内含 `storeCode` **与 `beCode`**（外送必传）。

---

## 4. `query-meals`

查门店餐品清单与价格。**获取 `productCode` 的唯一入口。**

| 参数 | 类型 | 必填 | 取值 |
|---|---|---|---|
| `storeCode` | string | 是 | 门店编码 |
| `orderType` | integer | 是 | `1` 或 `2` |
| `beType` | integer | 是 | `1` / `2` / `5` |
| `beCode` | string | 条件 | `beType=5/2` 必传；**`beType=1` 不传** |
| `reservationDate` | string | 条件 | 仅预约场景 |

**关键约束**：下单任何商品都必须先查这个接口取 `code`，**不得凭印象编造商品编码**。

**真实返回形状（最容易踩的一个）**

```json
{"data": {
  "categories": [{"name": "饮品", "meals": [{"code": "6352", "tags": []}]}],
  "meals": {
    "6352": {"name": "…", "image": "…", "currentPrice": "13.5", "originalPrice": "13.5", "canWithOrder": false}
  },
  "frequent": []
}}
```

- **`data.meals` 是「以产品编码为键的字典」，条目里没有 `productCode` 字段。** 编码只能从字典的键拿。
- **分类名只在 `data.categories[].name` 上**，需要先建「产品码 → 分类名」的索引再展开字典。同一个产品可能出现在多个分类里，取第一次出现的那个。
- `currentPrice` / `originalPrice` 是**元的字符串**。
- 真实门店的在售餐品数量在 120 件以上，不要假定是个位数量。

---

## 5. `query-meal-detail`

查单个餐品的组成与特调选项。**盲盒需要特调时才调。**

| 参数 | 类型 | 必填 | 取值 |
|---|---|---|---|
| `storeCode` | string | 是 | 不能为空 |
| `orderType` | integer | 是 | `1` 或 `2` |
| `beType` | integer | 是 | `1` / `2` / `5` |
| `code` | string | 是 | 餐品编码，来自 `query-meals` 的 `productCode`（字典的键） |
| `beCode` | string | 条件 | `beType=5/2` 必传；**`beType=1` 不传，传了会报错** |
| `reservationDate` | string | 条件 | 仅预约场景 |

**真实返回形状**

```json
{"data": {
  "productCode": "511781", "name": "…", "supportModify": true, "rounds": [],
  "modification": {"items": [
    {"maxValues": 2, "minValues": 0, "values": [
      {"code": "100141", "name": "…", "selectedQuantity": 1, "selectedKey": "0-1", "unselectedKey": "0-0"},
      {"code": "100179", "name": "…", "selectedQuantity": 0, "selectedKey": "0-1", "unselectedKey": "0-0"}
    ]}
  ]}
}}
```

- 顶层特调组挂在 **`data.modification.items[]`**（不是 `groups`），组用 `maxValues` / `minValues` 约束。
- 「这一项默认选没选中」由 **`selectedQuantity`** 表示（**没有** `selected` 布尔字段）；`0` 表示未选中。
- 套餐是 **`data.rounds[].choices[]`** 结构，特调挂在「轮次 → 候选项」上；这类商品的 `supportModify` 常常是 `false`，而顶层 `modification.items` 是空的。
- 展示规则：`data.supportModify=true` 时在商品名后标【可特调】；`choice.supportModify=true` 时在该 choice 名后标【可特调】。
- **不要主动展开特调列表**，仅用户主动询问时展示 `modification`。
- 展示后总结默认搭配。

---

## 6. `query-store-coupons`

查指定门店 + 订单类型下**真正可用**的券。

| 参数 | 类型 | 必填 | 取值 |
|---|---|---|---|
| `orderType` | integer | 是 | `1` = 到店（自取+得来速）；`2` = 外送 |
| `beType` | integer | 是 | `1` / `2` / `5` |
| `storeCode` | string | 是 | **编码必须全部由数字组成** |
| `beCode` | string | 条件 | `beType=5/2` 必传；**`beType=1` 不传** |
| `reservationDate` | string | 条件 | 仅预约场景 |

**关键约束**：这是盲盒算券后价的**唯一正确来源**。不要用 `query-my-coupons`（那是卡包资产，不校验门店/渠道/配送方式可用性，拿它算价会导致券不抵扣）。

**真实返回形状：`data` 是列表，而且券有两种形态。**

```json
{"data": [
  {"couponId": "…", "couponCode": "…", "title": "满 25 减 5", "thresholdFen": 2500, "discountFen": 500, "products": []},
  {"couponId": "…", "couponCode": "…", "title": "中薯条买一送一", "products": [{"productCode": "2001", "productName": "中薯条"}]}
]}
```

- **门槛券**：带 `thresholdFen` / `discountFen`，小计达到门槛即可用。
- **商品券**：**只有 `products[].productCode`，没有门槛与优惠金额**（如「中薯条买一送一」「9.9 元中杯冰美式」）。它**只有当购物车里确实有它关联的商品时才可能抵扣**；实际抵扣额由服务端决定，本地算不出来。
- 硬按门槛筛商品券会选出一张根本用不上的券；把商品券的优惠当成 0 又会在卡上把它说成不值钱。正确做法是：**只在关联商品出现时才把它列为可用，并如实说明抵扣以服务端算价为准。**

---

## 7. `calculate-price`

算价。**下单前必调。只算价、不下单，无副作用。**

| 参数 | 类型 | 必填 | 取值 |
|---|---|---|---|
| `storeCode` | string | 是 | |
| `orderType` | integer | 是 | `1` 或 `2` |
| `beType` | integer | 是 | `1` / `2` / `5` |
| `beCode` | string | 条件 | `beType=5/2` 必传；**`beType=1` 不传** |
| `reservationDate` | string | 否 | 仅预约场景 |
| `needTableware` | boolean | 否 | 是否需要餐具费 |
| `withOrder` | object | 否 | 随单购：`{cardId, cardType(int), membershipCode, membershipSpecId}` |
| `items` | array | 否 | 商品列表，见下 |

**`items[]` 结构**

- `productCode` (string) — 来自 `query-meals`
- `quantity` (int) — 必填且 ≥ 1
- `couponId` (string) — 选填，来自 `query-store-coupons`
- `couponCode` (string) — 选填，同上
- `modification.values[]` — `{code, key, quantity}`

**真实返回形状**

```json
{"data": {
  "originalPrice": 2850, "discount": 500, "price": 2350,
  "takeWayList": [{"code": "eat-in", "title": "堂食", "subtitle": "店内用餐"}]
}}
```

- 三个金额字段都是**分**。
- **服务端可能自己减一笔钱**（实测遇到过 `discount: 30` 但没有任何券被选中）。展示时不要把这种情况说成「无可用券」，否则价格栏和券栏自相矛盾。

**`roundList` 的实测结论（重要）：不要发。**

接口文档里列了 `roundList[]`（套餐的「轮次 → 候选项」），但我们**实测没有被接受**：带上它请求不报错，却**拿不到任何价格**（价格字段为空），也就是载荷被静默忽略了。

所以：**遇到 `rounds` 结构的套餐，不要生成这类特调**。宁可少带一项特调（用默认配置），也不要发一个可能被静默丢弃的载荷 —— 后者会让用户以为自己拿到的是定制款，实际是默认款。

**`takeWayList[].code` 是 `create-order.takeWayCode` 的唯一合法来源**，不同 `beType` 返回的编码不同（实测三例）：

| beType | takeWayCode |
|---|---|
| `1` 到店自取 | `eat-in`（堂食）/ `take-in-store`（外带） |
| `5` 得来速 | `take-dt-quick` |
| `2` 麦乐送 | 无取餐方式列表（返回空） |

**绝不自造 `takeWayCode`**（例如别写 `TW001`），也绝不在代码里硬编码某一个。

---

## 8. `list-nutrition-foods`

查餐品营养成分（能量、蛋白质、脂肪、碳水、钠、钙）。**无参数。纯查询。**

**用途**：热量挑战模式下的热量标签与揭晓卡热量栏。

**真实返回形状：`data` 是一段纯文本，不是 JSON。**

```
[160]{productName,nutritionDescription,energyKj,energyKcal,protein,fat,carbohydrate,sodium,calcium}:
  猪柳麦满分,null,1288,308,16,16,24,781,213
  中薯条,null,1210,289,4,12,38,165,18
```

首行是 `[行数]{列名}:`，其余是缩进的逗号分隔行。解析要先读表头拿到列序，**按列名取 `energyKcal`**，不要假定列位置。

**关键约束**：营养表条目与门店菜单名需要模糊匹配。**匹配不上就不要显示热量，绝不允许编造或估算数值。** 展示营养信息时必须声明"仅供参考，非医疗或健康建议"。

---

## 9. 特调 key 铁律（`calculate-price` 与 `create-order` 通用）

这是最容易出错的地方，单独强调：

1. 对某个特调组的**选中项**，`key` 用 `query-meal-detail` 返回的 `selectedKey`。
2. 对**未选中项**，如果它的 `unselectedKey` 非空，则 `key` 必须用 `unselectedKey`，**并且这一项也必须传进去**。
3. 换句话说：**只要某个特调组里存在带 `unselectedKey` 的项，这个组下的所有项都要传**（不管用户选没选）。
4. 如果某个组里**没有任何** `unselectedKey`，那这个组就只能表达「加」，此时只传选中项。
5. 「选中」的判据在真实数据里是 `selectedQuantity > 0`（没有 `selected` 布尔字段）。
6. 这条规则在 `calculate-price` 和 `create-order` 中**完全一致**，两边都要按同样方式构造。

真实数据里的 key 形如 `"0-1"`（选中）/ `"0-0"`（未选中），不同组的编号段不同（也会出现 `"1-1"` / `"1-0"`）。**key 一律原样透传，绝不自己拼。**

**搞错的后果**：含特调的商品算价报错；或者特调被静默丢弃，用户拿到的是默认款而不是盲盒抽出来的配置。

---

## 10. `create-order`（**写操作 · 真实下单**）

| 参数 | 类型 | 必填 | 取值 |
|---|---|---|---|
| `storeCode` | string | 是 | |
| `orderType` | integer | 是 | `1` 或 `2` |
| `beType` | integer | 是 | `1` / `2` / `5` |
| `beCode` | string | 条件 | `beType=5/2` 必传；**`beType=1` 不传** |
| `takeWayCode` | string | 条件 | **`orderType=1` 必传**，只能取自 `calculate-price` 的 `data.takeWayList[].code` |
| `addressId` | string | 条件 | **`orderType=2` 必填** |
| `reservationDate` | string | 否 | 仅预约场景 |
| `remark` | string | 否 | **最多 50 字，仅外送场景引导填写** |
| `needTableware` | boolean | 否 | |
| `withOrder` | object | 否 | 同 `calculate-price` |
| `items` | array | 否 | 同 `calculate-price` 的 `items[]` 结构 |

**关键约束**

- 必须先确定订单类型，再填对应参数。
- `takeWayCode` **只能用算价返回的**，不要自造。
- `remark` 仅外送场景引导填写；**自取单传了备注要直接报错，不要静默丢弃** —— 静默丢弃会让用户以为备注已经写进去了。
- 特调 key 铁律同第 9 节。
- **这是真实下单，会生成订单。** 必须先走完确认门禁（见 `confirmation-gate.md`）。
- 下单后引导用户扫码支付或打开麦当劳 APP 支付，完成后用 `query-order` 查状态。

**返回**：同样带信封，订单信息在 `data` 里，含 `orderId`（供 `query-order`、`cancel-order`）。

---

## 11. `query-order`

按订单号查详情与状态。**无副作用。**

| 参数 | 类型 | 必填 |
|---|---|---|
| `orderId` | string | 是 |

**返回**：`data` 里的订单状态、取餐/配送进度、价格。

---

## 12. `now-time-info`

获取服务器当前时间。**无参数。纯查询。**

**用途**：作为盲盒随机抽取的种子来源（见 `challenge-rules.md` 第二节）。用服务器时间戳而不是模型自己编一个数字，随机才可验证。

**真实返回形状**：`data.timestamp` 是**毫秒**时间戳（注意不是 `timestampMs`）。

```json
{"data": {"timestamp": 1791970294094, "date": "…", "time": "…", "day": …, "dayOfWeek": …, "month": …, "year": …, "timezone": "…", "offset": "…", "utc": …, "formatted": "…"}}
```

**关键约束**：这是"求随机"用的，不是"查现在几点"用的。不要拿它回答时间类闲聊问题。

---

## 附：本方向用不到但要知道的工具名

盲盒玩法只用到上面这些。以下是容易混淆或可能被误调的工具，**本 Skill 不应主动调用**：

- `query-my-coupons` — 卡包资产，不校验可用性。**不要用它算券后价。**
- `available-coupons` — 同理，不是门店可用券的正确来源。
- `auto-bind-coupons` — 领券，**写操作**，需确认门禁，盲盒默认不触发。
- `cancel-order` — 取消订单，**写操作**，需确认门禁。`cancelReasonCode` 取值：`1` 改主意了 / `2` 重复下单 / `3` 点错了·点多了·点少了 / `4` 地址·电话填错了 / `5` 送达时间选错了 / `-1` 其它原因。
- `delivery-create-address` — 新建配送地址，**写操作**。`phone` 需 11 位纯数字且以 1 开头。
- `query-meal-assistance` — 餐品相关辅助查询，**只读**，本方向没有用到。
- `draw-lottery`、`mall-create-order`、`party-order-create` — 属其他玩法方向，本 Skill 不涉及。

> 官方一共 35 个工具，其中 **7 个是写操作**：`create-order`、`cancel-order`、`delivery-create-address`、`auto-bind-coupons`、`draw-lottery`、`mall-create-order`、`party-order-create`。其余 28 个是只读。这两组互不相交、并集为全集。
