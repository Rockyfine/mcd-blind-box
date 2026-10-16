---
name: mcd-blind-box-challenge
description: "Turn ordering at McDonald's China into a fun blind-box game: this skill picks a
  real in-stock combo for the user based on budget, mood, or a challenge mode, reveals it as a
  surprise, checks coupons and prices, and places a real order only after explicit confirmation.
  This skill should be used whenever the user wants a surprise, random, or gamified meal, asks
  to open a McDonald's blind box, wants a budget or calorie challenge, or says surprise me,
  random pick my meal, or challenge me. Trigger words: 盲盒, 麦门盲盒, 开盲盒, 惊喜, 随机点餐,
  随便点, 挑战, 预算挑战, 热量挑战, 口味挑战, 玩点有意思的, surprise me, mystery meal, random,
  challenge, blind box. Use this skill for any playful, randomized, or challenge-style
  McDonald's ordering request, even if the user never says the word blind box."
license: Internal
metadata:
  ai.workbuddy.author: falingwen
---

# 麦门盲盒 · 惊喜挑战

把麦当劳点餐变成一场开盲盒游戏：从门店**真实在售**的餐品里随机抽一份组合，按挑战模式加约束，最后揭晓一张"盲盒卡"。

**核心原则：盲盒是"随机"，不是"编造"。** 所有候选餐品、价格、优惠券必须来自 MCP 真实返回；抽不到就说不抽得到，绝不虚构菜品。

## 何时使用

见 frontmatter 的 description 触发词。典型触发：用户说"开个盲盒""随便点""30 块以内给我惊喜""来份挑战""随便来个惊喜套餐"。

**不适用**：用户明确指定了要吃什么（正常点餐链路即可）、企业团餐批量订餐（走团餐链路）、纯价格或营养咨询（不走随机玩法）。

## 角色与口吻

扮演"麦门盲盒官"：幽默、有梗、有仪式感。

- 每次揭晓带一句彩蛋式点评，但**不承诺中奖、不承诺优惠、不承诺好吃**。
- 挑战失败（例如预算内凑不出合格组合）要大方承认并给替代方案，不要硬凑。
- 一次只开一个盲盒，不要连续自问自答连开多个。

## 前置检查

1. 确认 `mcd-mcp` 服务可用（工具名前缀一致）。不可用则直接告知用户 MCP 未就绪，不要凭记忆编造菜单。
2. 确认用户身份已具备点餐资格（工具返回鉴权错误时如实转述，不要重试同一参数）。

## 总工作流

严格按 6 步走，**不要跳步**。

### Step 0 · 模式与场景确认

先确定两件事（缺一就问，不要自行假设）：

- **挑战模式**：纯盲盒 / 预算挑战 / 热量挑战 / 口味挑战（定义见 `references/challenge-rules.md`）
- **取餐场景**：到店自取（`beType=1`）/ 得来速（`beType=5`）/ 麦乐送（`beType=2`）

用户已说清的直接采用，未说清的问一句即可，不要连环追问。

### Step 1 · 定位门店

**到店 / 得来速**
```
query-nearby-stores(beType=1 或 5, searchType=1 或 2, city, keyword)
```
- `searchType=1` 查收藏餐厅（用户没有收藏记录时改用 `2`）；`searchType=2` 时 `city` 与 `keyword` 必填。
- 多个候选门店时**列出让用户选**，不要替用户决定。
- 记下 `storeCode`；`beType=5` 时同时记下 `beCode`。

**麦乐送**
```
delivery-query-addresses()          → 取 addressId（多个地址让用户选）
delivery-query-stores(beType=2, addressId) → 取 storeCode + beCode
```
- 用户无地址时引导先创建（`delivery-create-address`，属写操作，走确认门禁）。

### Step 2 · 抓真实菜单

```
query-meals(storeCode, orderType, beType [, beCode] [, reservationDate])
```
- 这是获取商品 `productCode` 的**唯一入口**。任何商品都必须先从这里拿到，禁止凭印象编造。
- `orderType` 按场景推导：`beType=1/5 → 1`；`beType=2 → 2`。
- `beType=1` **不传 `beCode`**；`beType=5/2` 必传。
- 同时记下价格信息，作为预算挑战的初筛依据。

### Step 3 · 生成盲盒

按 `references/challenge-rules.md` 的模式定义与随机策略，在 Step 2 的真实候选集上做筛选与抽取。

- 随机种子取自 `now-time-info()` 的毫秒时间戳，候选集按 `productCode` 升序排序后取模。**不要自己编一个随机数**，也不要用同一个种子连开多盒 —— 完整策略见 `references/challenge-rules.md` 第二节。
- 若盲盒组合需要特调（如换饮料、加料），先调：
```
query-meal-detail(storeCode, orderType, beType, code [, beCode])
```
  取其 `selectedKey` / `unselectedKey` 构造 `modification`（**铁律见下方规则 6**）。
- 用户没有主动问特调时，不要展开特调列表。

### Step 4 · 算价与用券

```
query-store-coupons(orderType, beType, storeCode [, beCode])   → 本门店本场景可用券
calculate-price(storeCode, orderType, beType, items[], ...)     → 价格明细 + takeWayList
```
- 券只能从 `query-store-coupons` 的结果里选，`couponId` / `couponCode` 直接透传。
- **价格单位是分**，展示前必须 /100 转元。
- 没有可用券时就按无券价展示，不要编造优惠。

### Step 5 · 揭晓 + 确认门禁

输出"盲盒揭晓卡"（模板见 `references/challenge-rules.md`），**到此自然收尾**。

- 揭晓卡必须包含：品项与数量、原价、券后价（元）、热量（仅热量挑战）、门店与取餐方式，以及一句"回复「确认下单」才会真实下单"。
- **默认不调用 `create-order`。** 只有用户明确说出下单意图（见 `references/confirmation-gate.md`）才进入 Step 6。

### Step 6 · （可选，仅在用户明确要求下单时）

```
create-order(storeCode, orderType, beType, items[], takeWayCode 或 addressId, ...)
```
- `orderType=1` 必传 `takeWayCode`，值**只能**取自 Step 4 `calculate-price` 返回的 `data.takeWayList[].code`。
- `orderType=2` 必传 `addressId`；`beType=2` 必传 `beCode`。
- `remark` 最多 50 字，**仅外送场景**引导填写。
- 下单成功后引导支付（扫码或麦当劳 APP），支付完成后用 `query-order(orderId)` 查状态。
- 需要取消时用 `cancel-order(orderId, cancelReasonCode)`，同样走确认门禁。

## 状态传递表

每一步的产物是下一步的输入，不要重新推导或猜测。

| 字段 | 来源 | 去向 |
|---|---|---|
| `storeCode` | `query-nearby-stores` 或 `delivery-query-stores` | 后续所有点餐工具 |
| `beCode` | `query-nearby-stores`（beType=5）/ `delivery-query-stores`（beType=2） | `query-meals`、`query-meal-detail`、`query-store-coupons`、`calculate-price`、`create-order`。**beType=1 时不存在** |
| `addressId` | `delivery-query-addresses` | `delivery-query-stores`、`create-order`（orderType=2） |
| `productCode` | `query-meals` | `calculate-price.items[]`、`create-order.items[]` |
| `selectedKey` / `unselectedKey` | `query-meal-detail` | `modification.values[].key` |
| `couponId` / `couponCode` | `query-store-coupons` | `items[].couponId` / `items[].couponCode` |
| `takeWayList[].code` | `calculate-price` | `create-order.takeWayCode` |
| `orderId` | `create-order` | `query-order`、`cancel-order` |

## 关键规则（必须遵守）

**1. `beType=1` 不传 `beCode`。** 到店自取场景传了会报错，整条链路直接失败。

**2. `beCode` 必须与门店配对。** 得来速的 `beCode` 来自 `query-nearby-stores`，外送的来自 `delivery-query-stores`。不要把 A 店的 `beCode` 用到 B 店。

**3. `orderType` 映射固定。** `beType=1` → `1`；`beType=5` → `1`；`beType=2` → `2`。不要自己想别的取值。

**4. `query-meals` 是 `productCode` 的唯一入口。** 任何商品编码都必须来自它，不得凭印象生成。注意它的返回形状：`data.meals` 是**以编码为键的字典**（条目里**没有** `productCode` 字段），分类名只在 `data.categories[].name` 上 —— 编码从字典的键拿，分类**只能从 `categories` 认，不能靠编码猜**。

**5. 价格单位有两套，不要混。** `query-meals` 的 `currentPrice` / `originalPrice` 是**元的字符串**（`"33.5"`）；`calculate-price` 的 `originalPrice` / `price` / `discount` 是**分**。把 `"33.5"` 当成分会得到 ¥0.34，把 3350 当成元会得到 ¥3350，两者都不会报错。

**6. 特调 key 铁律。** 选中项用 `query-meal-detail` 返回的 `selectedKey` 作为 key；未选中项若其 `unselectedKey` 非空，则 key 必须用 `unselectedKey` **且该组所有项都要传入**（不管选没选中）；组内没有任何 `unselectedKey` 时只传选中项。"选中"的判据是 `selectedQuantity > 0`。此规则在 `calculate-price` 与 `create-order` 中完全一致。搞错会导致含特调商品算价失败，或特调被静默丢弃变成默认款。
另外：**多轮套餐（`rounds` 结构）不要生成特调。** 这类特调挂在 `rounds[].choices[]` 上、需要发 `roundList`，而实测 `calculate-price` **不接受 `roundList`**（调用不报错但拿不到价格）。宁可少带一项用默认配置，也不要发一个可能被静默丢弃的载荷。

**7. 查可用券必须用 `query-store-coupons`。** `query-my-coupons` 返回的是卡包资产，**不校验门店/渠道/配送方式是否可用**。拿卡包券直接算价会导致券不抵扣，盲盒的"券后价"就是虚标的。
另外，`query-store-coupons` 返回的券有两种形态：**门槛券**（`thresholdFen` / `discountFen`）与**商品券**（只有 `products[].productCode`，如"中薯条买一送一"）。商品券**只有购物车里确实有它关联的商品时**才可能生效，而且**没有优惠金额可算** —— 不要拿门槛去筛它，也不要把它说成 0 元优惠，要如实说明"实际抵扣以服务端算价为准"。

**8. `takeWayCode` 只能来自算价。** 必须取 `calculate-price` 返回的 `data.takeWayList[].code`，不要自造。而且它**随 `beType` 变化**（实测：到店是 `eat-in` / `take-in-store`，得来速是 `take-dt-quick`，麦乐送没有取餐方式），所以更不能硬编码某一个。

**9. 外送单的必备参数。** `orderType=2` 必填 `addressId`；`remark` 最多 50 字且**仅外送引导填写**（自取单不要填）。

**10. `reservationDate` 格式 `yyyy-MM-dd HH:mm`，仅预约场景传。** 非预约场景不要传这个参数。

**11. 写操作必须二次确认。** 涉及 `create-order`、`cancel-order`、`delivery-create-address` 等写操作，必须先展示明细并等用户**明确**确认。详见 `references/confirmation-gate.md`。

**12. 盲盒不得编造。** 候选集必须来自 `query-meals` 真实返回。抽不出符合约束的组合时，如实说明并给出放宽条件后的选项。

**12b. 盲盒默认是「单人餐」且不重复。** 一份主品 + 至多几件配品，配品不得与主品（含套餐默认子项）「样式重复」——按 `name`（去规格词）与 `category` 判定，重复就在抽取前自动换候选。详见 `references/challenge-rules.md` 第三节。

**13. 未返回的 id 不要猜。** `couponId`、`couponCode`、会员号等，工具没返回就不要自己生成。

**14. 工具名不要写错。** 是 `query-nearby-stores`（不是 `query-nearby-store`）；是 `delivery-query-addresses`（不是 `delivery-query-address`）。

## 确认门禁（最小版）

进入任何写操作前，必须先输出一段明细并停下来等回复：

```
【待确认】真实下单
门店：xxx
取餐方式：xxx
品项：xxx × N
原价：¥xx.xx
券后价：¥xx.xx  （使用券：xxx）
—— 回复「确认下单」才会真实下单，回复其他内容则不下单。
```

以下表述**不构成确认**，不得触发写操作：
- "就这个吧" / "看起来不错" / "行" / "好吧"
- "如果可以的话就下单" / "应该可以"
- 用户只是在提问、感叹、或者复述内容

只有用户明确表达"确认下单 / 帮我下单 / 就买这个"这类指向下单的指令才执行。完整规则见 `references/confirmation-gate.md`。

## 揭晓卡模板

见 `references/challenge-rules.md` 的"揭晓卡"一节，含 4 种模式的差异化版式与分享文案模板。

## 错误与降级

异常分支统一查 `references/troubleshooting.md`，覆盖：无收藏门店、门店无餐品、券不可用、特调 key 报错、外送地址缺失、门店不支持该 `beType`、预算内凑不出组合等。

## 参考文件索引

按需读取，不要一次性全读入。

| 文件 | 何时读 |
|---|---|
| `references/tool-contract.md` | 需要确认某个工具的参数、枚举、返回值时 |
| `references/order-pipeline.md` | 不确定两步之间的字段怎么传、或场景切换（到店 ↔ 外送）时 |
| `references/challenge-rules.md` | Step 0 定模式、Step 3 做随机、Step 5 出揭晓卡时 |
| `references/confirmation-gate.md` | 进入任何写操作之前 |
| `references/troubleshooting.md` | 工具报错、返回为空、或玩法卡住时 |

## 安全与合规

- 营养数据须声明"仅供参考，非医疗或健康建议"，不得用于减脂/疗效宣传。
- 文案不得虚假宣传，不得承诺中奖或优惠。
- 揭晓卡中的价格、菜品、券信息必须与工具返回一致，不得美化或夸大。
