# 架构：三层 + 一个接缝

本文说明「麦门盲盒」为什么长成现在这个样子 —— 尤其是那个让**离线复现**与**真连 MCP** 共用同一套业务代码的接缝。

---

## 一、整体形状

```
                     用户（自然语言 / CLI）
                              │
                    ┌─────────▼─────────┐
                    │  Skill 层（提示词） │   skill/mcd-blind-box-challenge/
                    │  六步工作流 + 规则  │   纯提示词编排，无脚本依赖
                    └─────────┬─────────┘
                              │  同一套规则、同一套顺序
                    ┌─────────▼─────────┐
                    │  Python 包（本地）  │   mcd_blind_box/
                    │  cli → pipeline    │   零依赖，stdlib only
                    └─────────┬─────────┘
                              │  call_tool(name, args) -> dict
                    ┌─────────▼─────────┐
                    │   Backend 接缝     │   backend.py（唯一的一处分支）
                    └──┬──────────────┬─┘
             ┌─────────▼───┐    ┌─────▼─────────┐
             │ FixtureBackend│    │  McpBackend   │
             │ 录制回放       │    │ 真实 Streamable HTTP │
             └──────────────┘    └───────────────┘
```

**自上而下只有一条数据通道**：`call_tool(name, arguments) -> dict`。所以「离线」与「联网」的差别被压缩到一个类的替换上。

## 二、三个「唯一真相源」

项目刻意让规则只写一遍，其余位置都从它派生：

| 真相源 | 位置 | 派生出的东西 |
|---|---|---|
| 枚举与映射 | `constants.py` | beType/orderType 映射、工具白名单、写/只读分区、分类关键词 |
| 规则铁律 | `rules.py` | 参数构造与校验、特调 key 铁律、备注与取餐方式规则 |
| 随机与骨架 | `challenge.py` | 候选排序、取模抽取、约束过滤、逐级放宽 |

`pipeline.py` 只负责**编排**（按什么顺序调哪些工具），`reveal.py` 只负责**渲染**（把结果说成人话），两者都不重复规则。

## 三、接缝：`Backend`

```python
class Backend:
    def call_tool(self, name, arguments=None) -> dict: ...
    def list_tools(self) -> list[dict]: ...
    # 顺带提供调用审计，两个实现共用
    calls: list[str]
    call_args: list[dict]
    @property
    def write_calls(self) -> list[str]: ...
```

**业务代码里没有任何 `if offline:`。** 三条链路、四种模式、券的选择、算价、特调构造，走的都是同一份代码。这一点有测试守着（`tests/test_pipeline_offline.py` 整组都是跑真实编排路径）。

两个实现的分工：

| | `FixtureBackend` | `McpBackend` |
|---|---|---|
| 数据来源 | `fixtures/*.json` | `https://mcp.mcd.cn` |
| 行为 | **按参数取出录制响应、原样返回** | 转发到传输层 |
| 传输 | 无 | 零依赖 Streamable HTTP（`transport.py`） |
| 额外 | 复现服务端的关键拒绝行为（beType=1 带 beCode 报错） | `probe()` 做握手自检 |

### 为什么「原样回放」很关键

fixtures 里存的就是**服务端响应的完整信封**（`{"success":…, "data":…}`），回放层不做任何字段变形。这意味着：

- 离线跑的就是线上那套**解析代码**。任何响应形状漂移（字段改名、`data` 从字典变列表）都会先在离线测试里炸出来，而不是等到联网时算出一个静默错误的价格。
- 如果回放层顺手「整理」一下数据（比如把 `data.meals` 字典转成列表），离线测试就会开始验证一个**线上不存在的世界**，测试全绿而线上必挂。

只有两个工具无法录制，因为结果取决于真实会话：

- `calculate-price` —— 按菜单价与券规则本地复算，但**返回字段与真实响应完全一致**（`data.originalPrice` / `data.price` / `data.discount`，单位分）。商品券的抵扣额由服务端决定，本地**保守按 0 处理**，宁可估高不虚报。
- `create-order` —— 返回一张带 `offline: true` 标记的假订单，**永远不产生真实交易**。

`scripts/make_fixtures.py` 负责生成 fixtures，自带 `--check` 校验是否与仓库中的文件一致。`tests/test_fixtures.py::test_fixtures_match_the_generator` 会在两者不同步时直接失败 —— 手改 fixtures 而不同步生成器，是这类项目最容易悄悄烂掉的地方。

## 四、编排层：一次开盒的完整链路

以到店自取为例（`--scene pickup`）：

```
1. set_scene(1)                       → be_type=1, order_type=1
2. locate_store(search_type=1)        → query-nearby-stores {beType:1, searchType:1}
                                       ← data 是列表；取第一行；beType=1 无 beCode
3. fetch_menu()                       → query-meals {storeCode, orderType:1, beType:1}
                                       ← data.meals 是 code-keyed 字典
                                       ← 建「码 → 分类名」索引并展开
4. [可选] _calorie_map()               → list-nutrition-foods
                                       ← data 是纯文本表，按列名取 energyKcal
5. _fetch_coupons()                    → query-store-coupons {…}
                                       ← data 是列表；区分门槛券与商品券
6. 抽取：种子 = now-time-info 的 data.timestamp（或 --seed）
        主品 = 主食池[seed mod N]
        配品 = 非主食池[floor(seed/N^level) mod M]，且**抽取前**已按约束淘汰
7. _price()                           → calculate-price {…, items[]}
                                       ← data.originalPrice / price / discount（分）+ takeWayList
8. _build_card()                      → RevealCard（含顺延/放宽/骨架缺件的如实说明）
```

**只有第 8 步之后、且用户明确确认，才会出现 `create-order`。** 全程可以用 `backend.write_calls` 审计。

## 五、安全设计的分层

安全不靠提示词，靠三层机械保障：

| 层 | 机制 | 位置 |
|---|---|---|
| 参数层 | `beType=1` 带 `beCode` 直接抛错（不静默丢弃）；`orderType` 与 `beType` 映射不一致直接抛错 | `rules.py` |
| 门禁层 | 没有 `--confirm` 连数据源都不构造；原话判定比显式确认更严格 | `cli.py` / `confirmation.py` / `pipeline.order()` |
| 审计层 | 每次 `call_tool` 都记录；`write_calls` 在开盒后必须为空 | `backend.py` |

其中「参数层」和「门禁层」都是**双重**的：CLI 拦一次，`pipeline.order()` 内部再判一次 —— 即使有人绕过 CLI 直接调 API，也不会误下单。

## 六、为什么是零依赖

`pyproject.toml` 的 `dependencies = []`，`verify --offline` 会用 AST 扫描包内源码，把标准库之外的 `import` 全列出来。这不是自律声明，是可执行的检查。

代价是必须自己写 HTTP 客户端（`transport.py`，基于 `urllib.request`，实现 Streamable HTTP 的三步握手与 SSE/JSON 双形态解析）。收益是 clone 下来就能跑 —— 评委不需要配置任何环境。

## 七、目录与职责

```
mcd_blind_box/
├── constants.py         枚举、映射、白名单 —— 唯一真相源，无逻辑
├── errors.py            异常类型（决定 CLI 退出码）
├── rules.py             规则铁律的纯函数；不碰网络、不碰文件
├── challenge.py         模式、随机抽取、约束过滤、逐级放宽
├── coupon.py            券来源校验、可用性判定、选择
├── pricing.py           算价响应解析、热量合计
├── confirmation.py      确认门禁判定
├── models.py            dataclass 载体 + from_api 解析（宽容处理字段缺失）
├── backend.py           ★ 接缝 + 调用审计
├── fixtures_client.py   离线数据源（录制回放）
├── transport.py         零依赖 Streamable HTTP
├── mcp_client.py        联网数据源
├── pipeline.py          ★ 编排：三条链路的状态机
├── reveal.py            揭晓卡 / 确认面板 / 分享文案渲染
└── cli.py               命令行入口（退出码映射）
```

依赖方向是单向的：`cli → pipeline → {challenge, coupon, pricing, rules, models} → constants`，以及 `pipeline → backend`。没有任何反向依赖，所以 `rules.py` / `challenge.py` / `coupon.py` 可以被完全离线地单元测试。
