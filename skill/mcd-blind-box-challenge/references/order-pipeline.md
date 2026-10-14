# 点餐链路与状态传递

> 两条主线：到店（`beType=1` / `5`，`orderType=1`）与外送（`beType=2`，`orderType=2`）。
> 每一步的产物是下一步的输入。**不要重新推导，不要猜测，直接透传。**

## 链路 A｜到店自取（`beType=1`，`orderType=1`）

```
query-nearby-stores(beType=1, searchType, city, keyword)
    ↓  storeCode              （注意：beType=1 没有 beCode）
query-meals(storeCode, orderType=1, beType=1)
    ↓  productCode（字典的键）+ 价格（元的字符串）
query-meal-detail(storeCode, orderType=1, beType=1, code=productCode)      ← 仅需特调时
    ↓  modification.items[].values[] 里的 selectedKey / unselectedKey
query-store-coupons(orderType=1, beType=1, storeCode)
    ↓  couponId / couponCode
calculate-price(storeCode, orderType=1, beType=1, items[])
    ↓  价格明细（分） + takeWayList[].code
【确认门禁】展示揭晓卡 → 等用户明确确认
    ↓
create-order(storeCode, orderType=1, beType=1, items[], takeWayCode=takeWayList[].code)
    ↓  orderId
query-order(orderId)                    ← 用户回复"支付完成"后查
```

**这条链的硬规则**

- 全程**不传 `beCode`**。传了会报错。
- `create-order` 的 `takeWayCode` 只能取自刚才那次 `calculate-price` 的 `data.takeWayList[].code`。
- 不填 `remark`（`remark` 仅外送场景）。

## 链路 B｜得来速（`beType=5`，`orderType=1`）

与链路 A 完全相同，唯一差别：**全程额外携带 `beCode`**。

```
query-nearby-stores(beType=5, searchType, city, keyword)
    ↓  storeCode + beCode
query-meals(storeCode, orderType=1, beType=5, beCode)
    ↓  ...
query-meal-detail(..., beType=5, beCode)
query-store-coupons(orderType=1, beType=5, storeCode, beCode)
calculate-price(..., beType=5, beCode, items[])
create-order(..., beType=5, beCode, items[], takeWayCode)
```

**这条链的硬规则**

- `beCode` 必须来自 `query-nearby-stores`，**且必须与 `storeCode` 同属一家门店**。跨店错配会导致下单失败或下到错误门店。

## 链路 C｜麦乐送（`beType=2`，`orderType=2`）

```
delivery-query-addresses()
    ↓  addressId              （多个地址让用户选）
delivery-query-stores(beType=2, addressId)
    ↓  storeCode + beCode
query-meals(storeCode, orderType=2, beType=2, beCode)
    ↓  productCode（字典的键）+ 价格（元的字符串）
query-meal-detail(storeCode, orderType=2, beType=2, code=productCode, beCode)   ← 仅需特调时
    ↓  modification.items[].values[] 里的 selectedKey / unselectedKey
query-store-coupons(orderType=2, beType=2, storeCode, beCode)
    ↓  couponId / couponCode
calculate-price(storeCode, orderType=2, beType=2, beCode, items[])
    ↓  价格明细（分）；takeWayList 为空（外送无取餐方式）
【确认门禁】展示揭晓卡 → 等用户明确确认
    ↓
create-order(storeCode, orderType=2, beType=2, beCode, addressId, items[], remark?)
    ↓  orderId
query-order(orderId)
```

**这条链的硬规则**

- `orderType=2` 时 **`addressId` 必填**（在 `create-order` 里也要传）。
- 全程携带 `beCode`。
- 这条链的 `calculate-price` 返回的 **`takeWayList` 为空**，所以 `create-order` **不传 `takeWayCode`**（外送没有取餐方式）。
- `remark` 最多 50 字，**只有这条链才引导填写**（如"无接触配送"）。
- 用户没有地址时，先走 `delivery-create-address`（写操作，需确认门禁）。

## 场景切换注意事项

| 用户改口 | 要做什么 |
|---|---|
| 到店 → 外送 | 丢弃链路 A/B 的 `storeCode`/`beCode`，从 `delivery-query-addresses` 重新开始 |
| 外送 → 到店 | 丢弃 `addressId`/`beCode`，从 `query-nearby-stores` 重新开始 |
| 自取 → 得来速 | 同一门店可能不可用，需用 `beType=5` 重新查门店拿 `beCode` |

**关键：换场景后旧的 `beCode` 一律作废，必须重新查门店。** `beCode` 与门店 + 取餐方式绑定，不能沿用。

## 一步对照表

| 步骤 | 链路 A（自取） | 链路 B（得来速） | 链路 C（麦乐送） |
|---|---|---|---|
| 定位 | `query-nearby-stores(1)` | `query-nearby-stores(5)` | `delivery-query-addresses` → `delivery-query-stores(2)` |
| `beType` | `1` | `5` | `2` |
| `orderType` | `1` | `1` | `2` |
| `beCode` | **不传** | 必传 | 必传 |
| `addressId` | 不需要 | 不需要 | `delivery-query-stores` 与 `create-order` 都要 |
| 取餐方式参数 | `takeWayCode` | `takeWayCode` | 不传 `takeWayCode`，改用 `addressId` |
| `remark` | 不填 | 不填 | 可填，≤50 字 |
