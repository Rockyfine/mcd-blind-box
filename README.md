# 麦门盲盒 · mcd-blind-box

> 把「今天吃什么」交给服务器时间戳。
> 基于麦当劳中国官方 MCP Server（`mcd-mcp`）的可验证开盲盒工具 —— 它真的会算价、用券、下单，但**默认绝不下单**。

一个 WorkBuddy Skill + 一套零依赖 Python 实现。clone 下来就能离线跑通全部流程，不需要 Token、不联网、不产生任何真实订单。

```
【麦门盲盒 · 预算挑战 + 口味挑战（辣）】

预算      40.00 元
实际      券后 ¥31.00  →  余额 9.00 元
状态      挑战成功

主品  麻辣鸡腿堡 × 1
配品  藤椒风味鸡块 × 1（小食）

门店      南京新街口餐厅（到店自取）
原价      ¥36.00
券后价    ¥31.00     使用券：满 25 减 5（满 25.00 减 5.00）
取餐方式  堂食 / 外带（takeWayCode=eat-in）

一句话点评：服务器在 1789000000000 毫秒时点了点头，于是有了这顿。

—— 回复「确认下单」才会真实下单。
   种子 1789000000000 → 第 2 项（主品候选池 3 件；1789000000000 mod 3 = 1）
```

（上面这段是**真实输出**，不是手写的示意：`python -m mcd_blind_box open --offline --mode budget --mode taste --budget 40 --taste 辣 --seed 1789000000000`）

---

## 一、这是什么

一句话：**给「随便点点」这个场景做一个有约束、有惊喜、又完全可复核的决策器。**

用户说「随便来一份」「30 块以内」「要辣的」「不超过 600 大卡」，工具就从这家门店**真实在售**的菜单里抽出一套组合，揭晓为一颗盲盒：算好价、选好券、把「为什么是这一套」用一行可验算的公式写清楚。

和「随机推荐」的区别在于三件事：

| | 普通随机推荐 | 麦门盲盒 |
|---|---|---|
| 随机来源 | 不可解释 | 服务端 `now-time-info` 毫秒时间戳，**可验算** |
| 商品来源 | 可能来自模型记忆 | 只能来自 `query-meals` 的真实返回，**编不出来** |
| 价格 | 常常是估算 | 走 `calculate-price` + `query-store-coupons`，**分转元后原样展示** |

### 四种挑战模式（可叠加）

| 模式 | 用户会怎么说 | 约束 |
|---|---|---|
| **纯盲盒** | 「随便」「给我惊喜」 | 无价格约束，只要门店在售 |
| **预算挑战** | 「30 块以内」「别超过 25」 | 券后价 ≤ 预算上限 |
| **热量挑战** | 「不超过 600 大卡」 | 组合总热量 ≤ 上限（数据来自 `list-nutrition-foods`） |
| **口味挑战** | 「要辣的」「想吃鸡」 | 餐品名命中口味关键词 |

模式取**交集**：「30 块以内要辣的」= 预算挑战 + 口味挑战。

### 三条取餐链路

| 链路 | beType | orderType | 关键差异 |
|---|---|---|---|
| 到店自取 | 1 | 1 | **全程不携带 `beCode`**（传了服务端直接报错）；取餐 code 是 `eat-in` / `take-in-store` |
| 得来速 | 5 | 1 | 必传 `beCode`，来自 `query-nearby-stores`；取餐 code 是 `take-dt-quick` |
| 麦乐送 | 2 | 2 | 必传 `beCode` + `addressId`；`remark` **仅此链路**可填；无取餐方式 |

`takeWayCode` **一律取自 `calculate-price` 返回的 `takeWayList[].code`**，代码里不硬编码任何一个。上表只是把实测结果写出来供对照 —— 因为它们真的不一样，硬编码一个就会在另一个链路上发出非法参数（有测试守着，见 `test_take_way_code_is_never_invented`）。

> 得来速门店常常**搜不到**：`searchType=1`（按位置附近搜）在很多城市返回空列表。这不是 bug —— 换成 `searchType=2` 带城市与关键词即可：
> `python -m mcd_blind_box open --live --scene dt --city 武汉市 --keyword 得来速`

---

## 二、30 秒上手（离线，零依赖）

需要 Python 3.9+，**不需要装任何第三方包**。

```bash
git clone <repo-url> mcd-blind-box
cd mcd-blind-box

# 1) 开一颗盲盒（只读，绝不下单）
python -m mcd_blind_box open --offline --seed 1789000000000

# 2) 加约束
python -m mcd_blind_box open --offline --mode budget --budget 30 --seed 7
python -m mcd_blind_box open --offline --mode calorie --calorie-max 600 --seed 3

# 3) 换取餐方式
python -m mcd_blind_box open --offline --scene dt
python -m mcd_blind_box open --offline --scene delivery
```

第一次跑建议先做一次自检：

```bash
python -m mcd_blind_box verify --offline
```

它会检查 fixtures 是否齐全、能不能解析，并**扫描源码证明真的只依赖标准库**：

```
【离线自检】不联网、不消耗 Token
fixtures 目录   .../fixtures

  [正常] time.json（6 个顶层键/项）
  [正常] stores.json（3 个顶层键/项）
  [正常] addresses.json（6 个顶层键/项）
  ...
第三方依赖      无（仅标准库）
可用工具数      35

结论：可以离线复现全部流程。
```

想一次跑完所有链路：

```bash
python scripts/smoke_offline.py     # 20 条冒烟用例，含安全闸与错误路径
python -m unittest discover -s tests -t .   # 357 个单元测试
```

---

## 三、安装

### 方式 A：作为 WorkBuddy Skill 使用（推荐）

```bash
# macOS / Linux
bash scripts/install_skill.sh

# Windows PowerShell
powershell -ExecutionPolicy Bypass -File scripts/install_skill.ps1
```

脚本会把 `skill/mcd-blind-box-challenge/` 复制到 `~/.workbuddy/skills/` 下。WorkBuddy 会自动扫描该目录下的 `SKILL.md`，**无需额外注册**，重启对话后即可用自然语言触发：

> 「帮我开个 30 块以内的盲盒」
> 「来个不超过 600 大卡的，要辣的」
> 「用麦乐送开一个」

Skill 侧是纯提示词编排（无脚本依赖）：模型按 `SKILL.md` 的六步工作流调用 MCP 工具，规则细节按需从 `references/` 三级渐进披露读取。

### 方式 B：只用命令行

零依赖，直接在仓库里 `python -m mcd_blind_box ...` 即可，不需要安装。

### 方式 C：联网跑真实门店

需要 MCP Token（申请地址 <https://open.mcd.cn/mcp>）：

```bash
export MCD_MCP_TOKEN=你的Token          # Windows: set MCD_MCP_TOKEN=...
python -m mcd_blind_box verify --live   # 握手自检，Token 会掩码显示
python -m mcd_blind_box open --live --city 南京市 --keyword 新街口
```

Token 只从**环境变量**或 `--token` 读取，代码**刻意不读** `~/.workbuddy/mcp.json` —— 避免把宿主凭证耦合进项目，也满足参赛声明的信息安全要求。

---

## 四、为什么随机是可信的

这是整个项目最想讲清楚的一件事。

规则来自 `skill/.../references/challenge-rules.md` 第二节，实现是逐条对应的：

1. 调 `now-time-info()` 拿服务端毫秒时间戳 `ts` 作种子；
2. 候选集按 `productCode` **升序**排序，得到稳定序号 `0 .. N-1`；
3. 主品索引 `i = ts mod N`（N = 主食数量）；
4. 需要配品时，用 `floor(ts / N)` 作新种子，在**剔除已选项**的集合里重复取模（抽第 2 个配品用 `floor(ts / N²)`）；
5. 揭晓卡底部写一行小字：`种子 1789000000000 → 第 2 项（主品候选池 3 件；1789000000000 mod 3 = 1）`。

**这一行是可验算的。** 你可以自己算 `1789000000000 mod 3`，也可以把同一颗种子重跑一百遍 —— 结果必然一样。想验证的话：

```bash
# 同一颗种子必然得到同一套组合
python -m mcd_blind_box open --offline --seed 1789000000000
python -m mcd_blind_box open --offline --seed 1789000000000
```

配品的抽取有一处**刻意的取舍**：规则字面上说配品在「剔除主品后的候选集」里抽（那个集合是含其他主食的），但同一份规则的「组合骨架」又把配品定义为小食 / 薯条 / 饮料。照字面实现的话，真实菜单里约四成种子会抽出「两个汉堡」，其中一个还被标成「配品」—— 玩家一眼就会觉得是 bug。

所以实现是：**配品优先在非主食类里抽**；只有当非主食为空、或都被约束淘汰时，才回退到含主食的完整集合。既保住骨架语义，又不会在「整本菜单都是主食」这类极端门店上直接开不出盒。（`challenge-rules.md` 与 `challenge.py::candidate_for_seed` 的文档字符串里都写了这件事。）

### 约束不满足时，逐级放宽，且每一步都说出来

顺序也写死在 `challenge-rules.md` 里（代码在 `mcd_blind_box/challenge.py` 与 `pipeline.py`）：

1. **先淘汰不符合约束的候选，只在剩余集合里抽。** 这一步发生在**抽取之前** —— 预算挑战按券后价估算、热量挑战按累计热量估算，把不可能满足约束的配品从候选池里剔掉再取模。
2. 剩余集合为空 → 放宽到「尽量接近约束」（预算取最接近上限、热量取最低），并**明确告知**「没能在约束内凑出，这是最接近的一套」。
3. 仍然凑不出 → 走降级分支，给出换口味 / 换门店 / 换模式的建议。

**禁止**：候选集为空时凭记忆编一套「经典套餐」；约束达不到时假装达到了。

同理，如果约束把原始种子那一颗候选筛掉了、管线顺延到后续候选，揭晓卡会**如实标注**：

```
注意：原始种子抽到的组合不满足当前约束，已按步长 7919 顺延到第 5 个候选（种子 31683）。
```

---

## 五、安全设计：三条硬约束

盲盒的好玩之处在于意外，但**下单不能有意外**。项目把三条约束写进了代码而不是提示词里：

### 1. 没有 `--confirm` 就绝不会调用 `create-order`

`order` 子命令在解析参数后立刻判断 `--confirm`，**不通过就直接返回退出码 3，连数据源都不会构造**：

```console
$ python -m mcd_blind_box order --offline --seed 1789000000000
【已拦截】order 子命令会真实下单，但你没有传 --confirm。

建议流程：
  1) 先看揭晓卡： mcd-blind-box open --offline --seed 1789000000000
  2) 确认无误后： mcd-blind-box order --confirm --offline --seed 1789000000000

本项目默认不下单。
```

测试 `tests/test_pipeline_offline.py::TestOrderGate` 用 `backend.calls` 断言：**被拦截时 `create-order` 一次都没有出现在调用记录里**。

而且这份调用记录**离线与联网共用同一份实现**（`Backend` 基类），所以「开盒全程只读」在真实端点上同样可查：

```python
backend.write_calls   # 开盒之后永远是 []；只有真的下单才会出现 ["create-order"]
```

`TestNoWriteDuringOpen` 两个方向都测了：开盒后审计为空，且真下单时审计**必须看得见** —— 后者用来证明这个审计不是个永远返回空的摆设。

### 2. 原话判定比 `--confirm` 更严格

命令行给 `--confirm` 是显式动作，但如果你同时给了一段用户原话，**以原话判定为准**。`confirmation.py` 把「什么算确认」编码成可测试的函数，判定顺序本身即规则：

| 输入 | 判定 | 理由 |
|---|---|---|
| `确认下单` / `帮我下单` / `买了` | ✅ 确认 | 明确下单意图 |
| `就这个吧` / `行` / `好吧` / `嗯` | ❌ 不确认 | 只是认可内容 |
| `如果可以的话就下单` | ❌ 不确认 | 条件式表达 |
| `多少钱？` / `能下单吗` | ❌ 不确认 | 提问 |
| `不确认` / `先别下单吧` / `不想买了` | ❌ 不确认 | 否定表达 |
| `随便` / `再看看` | ❌ 不确认 | **拿不准一律判为不确认** |

### 3. 写操作与只读操作被显式分区

`constants.py` 把官方 35 个工具切成 7 个写操作 + 28 个只读，两者**互不相交且并集等于全集**（有测试守着）。`calculate-price` 虽然叫「下单前必调」，但它只算价不下单，属于只读。

---

## 六、目录结构

```
.
├── README.md                    本文件
├── CONTEST_DECLARATION.md       参赛声明（官方原文，内容不可改动）
├── MCP_INTEGRATION.md           MCP 集成说明：用了哪些工具、怎么串、业务价值
├── LICENSE
├── mcp-config.example.json      MCP 客户端配置模板（不含真实 Token）
├── pyproject.toml
│
├── mcd_blind_box/               ★ 项目主体：零依赖 Python 包
│   ├── constants.py             枚举与映射的唯一真相源（beType/orderType/工具白名单…）
│   ├── rules.py                 规则铁律的纯函数实现（含特调 key 铁律）
│   ├── challenge.py             四种模式、可验证随机、约束放宽
│   ├── coupon.py                券来源校验与选择（含商品券）
│   ├── pricing.py               真实算价响应的解析与热量合计
│   ├── confirmation.py          确认门禁判定
│   ├── models.py                数据载体（stdlib dataclass）
│   ├── backend.py               数据源接缝：离线 / 联网同一接口 + 调用审计
│   ├── fixtures_client.py       离线数据源（把 fixtures 当一台录好的 MCP Server）
│   ├── transport.py             零依赖 Streamable HTTP 客户端
│   ├── mcp_client.py            联网数据源
│   ├── pipeline.py              编排层：三条链路
│   ├── reveal.py                揭晓卡渲染
│   └── cli.py                   命令行入口
│
├── skill/mcd-blind-box-challenge/   ★ WorkBuddy Skill（纯提示词编排）
│   ├── SKILL.md                 六步工作流 + 14 条关键规则
│   └── references/
│       ├── tool-contract.md     35 个工具的参数、真实响应形状与坑
│       ├── order-pipeline.md    三条链路的参数传递
│       ├── challenge-rules.md   玩法层：模式 / 随机 / 骨架 / 揭晓卡
│       ├── confirmation-gate.md 写操作二次确认
│       └── troubleshooting.md   错误码与降级
│
├── fixtures/                    离线数据（9 个 JSON，**服务端响应的原样录制**）
├── tests/                       357 个单元测试
├── scripts/
│   ├── make_fixtures.py         fixtures 生成器（零依赖、可复现、自带 --check）
│   ├── smoke_offline.py         20 条端到端冒烟
│   ├── install_skill.sh
│   └── install_skill.ps1
└── docs/
    ├── architecture.md          三层架构与接缝设计
    ├── live-verification.md     联网验证记录（真实响应的形状清单）
    └── dialogue.md              自然语言对话示例
```

> `workbuddy.md`（WorkBuddy 专项奖励用）本仓库未包含，如何补交见 `docs/live-verification.md`。

---

## 七、命令行参考

```
python -m mcd_blind_box <command> [options]
```

| 子命令 | 是否写操作 | 说明 |
|---|---|---|
| `open` | ❌ 只读 | 定位门店 → 抓菜单 → 开盲盒 → 算价用券 → 输出揭晓卡 |
| `order` | ⚠️ **会真实下单** | 必须显式 `--confirm`，否则直接拦截 |
| `tools` | ❌ 只读 | 列出用到的工具（`--live` 时向服务端枚举） |
| `verify` | ❌ 只读 | 自检：离线查 fixtures，联网做握手与鉴权探测 |

主要选项：

| 选项 | 说明 |
|---|---|
| `--scene {pickup,dt,delivery}` | 取餐场景：到店自取 / 得来速 / 麦乐送 |
| `--mode {blind,budget,calorie,taste}` | 挑战模式，可重复传递以叠加 |
| `--budget 30` | 预算上限（元） |
| `--calorie-max 600` | 热量上限（大卡） |
| `--taste 辣` | 口味关键词：辣 / 鸡 / 牛 / 鱼 / 素 / 甜 / 咖啡 |
| `--seed 1789000000000` | 随机种子（毫秒时间戳）；传入即完全确定可复现 |
| `--with-mod` | 为可特调商品附上特调项 |
| `--json` | 结构化输出 |
| `--share` | 额外输出一段可复制的分享文案 |
| `--offline` / `--live` | 离线（默认）/ 联网；二者互斥 |
| `--token` / `--endpoint` | 覆盖 Token 与端点 |

退出码：

| 码 | 含义 |
|---|---|
| 0 | 成功 |
| 2 | 参数问题（未知口味、到店场景传了 `beCode` 等） |
| 3 | **安全闸拦截**（`order` 缺少 `--confirm`，或原话不构成确认） |
| 4 | 鉴权失败（Token 无效 / 未提供） |
| 5 | 触发限流（HTTP 429） |
| 6 | 约束无法满足（候选集为空等） |

---

## 八、测试

```bash
python -m unittest discover -s tests -t .     # 357 个单元测试
python scripts/smoke_offline.py               # 20 条端到端冒烟
python scripts/make_fixtures.py --check       # fixtures 是否与生成器同步
```

| 测试文件 | 覆盖 |
|---|---|
| `test_rules.py` | 枚举映射、`beCode` 禁令、**特调 key 铁律**、分/元换算、备注与取餐方式 |
| `test_random.py` | 抽取可复现、`ts mod N` 落点、配品种子算式、约束在抽取前生效 |
| `test_challenge.py` | 四种模式、口味匹配、热量粗筛、骨架、逐级放宽 |
| `test_pricing.py` | 真实算价响应解析、**券只认 `query-store-coupons`**、**商品券要有关联商品才可用**、热量缺失不估算 |
| `test_confirmation.py` | 确认门禁的全部正反例、写/只读工具分区完整性 |
| `test_transport.py` | SSE 与 JSON 双形态解析、三步握手、401/429 映射、**Token 脱敏** |
| `test_pipeline_offline.py` | 三条链路 × 四种模式端到端、安全闸、下单载荷、降级路径、揭晓卡措辞 |
| `test_fixtures.py` | **fixtures 与生成器是否同步**、信封完整性、回放不改写、算价字段与单位、无凭证残留 |

其中最有价值的三组：

- `TestOrderGate` + `TestNoWriteDuringOpen`：断言被拦截时 `create-order` **从未出现在调用记录中**（不是「没产生订单」，而是「根本没调」），且真下单时审计必须看得见。
- `TestModificationIronRule`：把「含 `unselectedKey` 的整组都要传」这条最容易翻车的规则拆成 11 个用例钉死。
- `test_fixtures.py`：fixtures 一旦被人手改而没同步生成器，整组离线测试就会开始验证一个不存在的世界 —— 这里第一个拦住它。

---

## 九、目标用户

**主要：想「随便点但又不想踩雷」的普通消费者。**
他们的真实痛点是决策疲劳 —— 打开 App 划五分钟不知道吃什么。盲盒把选择成本降到一句话，同时预算/热量/口味三种挑战又能兜住「我不能乱花」「我在控卡」「我今天想辣」这些真实约束。

**次要：想给自己加一点仪式感的麦当劳重度用户。**
揭晓卡配一句点评、一行可验算的种子、一段可复制的分享文案 —— 这是一个可以发朋友圈的「结果」，而不只是一张订单截图。

**第三类：把点餐流程当作 Agent 能力演示的开发者。**
`docs/architecture.md` 里的三层架构 + `backend.py` 这一个接缝，说明了如何让同一套业务代码同时跑通「零依赖离线复现」与「真连 MCP」。离线 fixtures 的设计目标就是让别人 clone 下来立刻能验证，而不是只能读代码。

**不面向**：需要批量/企业采购的团餐场景（`beType=6` 在企业团餐方向，本项目**未实现**，会在参数校验阶段明确拒绝）。

---

## 十、已知边界

项目刻意不做的事，写在这里以免被误解：

- **不实现 `beType=6`（企业团餐）。** 它在 `order_type_for()` 里被显式拒绝，而不是悄悄当成外送。
- **热量挑战拿不到营养数据时不给总量。** 任何一项匹配不到 `list-nutrition-foods`，总热量就是 `None`，卡上显示「无法计算」，**绝不按 0 卡或估算值放行**。
- **无可用券时不编造优惠。** 没有券就显示「无可用券」，不会凭空生成 `couponId`。
- **商品券的优惠金额算不出来，就说不算得出来。** 「中薯条买一送一」这类券没有「满 X 减 Y」的数字，实际抵扣由服务端决定；本地把它当 0 元优惠（保守），卡上会标注「商品券，实际抵扣以服务端算价为准」，而不是假装它不值钱，也不会凭空编一个折扣额。
- **服务端自己减的钱不会被说成「无可用券」。** 实测麦乐送算价会返回一笔非券来源的优惠（如 30 分）；这种时候卡上写「服务端优惠 ¥0.30（非券来源）」，避免价格栏和券栏自相矛盾。
- **组合骨架是参考区间，不是硬规则。** 预算/热量约束下如果放不下配品，就少开一件，并在卡上写明「只放得进 N 件配品，骨架想要 M 件」——**不为了凑数把约束撑破**，也不假装抽出来的就是标准套餐。
- **单次只开一份主品 + 可选配品。** 规则明确「不要一次抽一大桌」。
- **`remark` 仅外送链路。** 自取单传了备注会**报错**而不是静默丢弃 —— 静默丢弃会让人以为备注已经写进去了。
- **多轮套餐（`rounds`）不生成特调。** 套餐的特调挂在 `rounds[].choices[]` 上，需要发 `roundList`；而 `roundList` 的载荷结构**实测没有被 `calculate-price` 接受**（调用不报错，但拿不到价格）。既然没有验证通过的形状就不猜 —— 宁可少带一项特调，也不发一个可能被静默丢弃的载荷。
- **离线数据是 fixtures，不是真实门店。** 门店名、价格、券都是为可复现而构造的示例数据，但**响应结构与字段名是从真实端点逐字核对的**（见 `docs/live-verification.md`）；真实数据只能通过 `--live` 获取。
- **`--live` 的可达性取决于位置。** 得来速门店按「附近」常常搜不到（真实数据条件，不是 bug），需要改用城市 + 关键词搜索。

---

## 十一、合规与声明

- 本项目为「麦当劳程序员节创意开发大赛」参赛作品，原创性、合规性与信息安全声明见 [`CONTEST_DECLARATION.md`](./CONTEST_DECLARATION.md)（该文件为官方原文，内容不可改动）。
- 项目基于 `mcd-mcp`（<https://mcp.mcd.cn>）公开能力开发，**未使用任何非公开接口、未绕过任何鉴权**。
- 仓库内**不含任何真实凭证**：Token 只从环境变量读取，`.gitignore` 排除 `mcp.json` / `.env` / `*.token`；测试里的 Token 是合成占位串。
- 营养数据仅作展示参考，**非医疗或健康建议**。
- 商标与商品信息归麦当劳及其关联公司所有，本项目与麦当劳无隶属关系。

## 十二、License

[MIT](./LICENSE)
