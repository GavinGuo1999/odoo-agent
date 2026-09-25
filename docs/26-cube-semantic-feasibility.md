# Cube 语义层可行性验证（阶段一）

> 目的：判断能不能把 Cube 接成第三种语义层，与自研层、Wren 做**单一变量**的三方对照
>
> 日期：2026-09-18；**未花费模型额度**（查询手写，Cube 只做编译，守卫是本地 AST 检查）
>
> 结论：**可行**。障碍恰好两层，且都是表示形式问题，不是语义问题
>
> 进度：三个阶段全部完成。**阶段三实跑结论见 §11**

## 0. 先说结论

四条代表性查询，每条验三个版本：

| 版本 | 说明 | 过守卫 |
| --- | --- | --- |
| **A** 原样 | Cube 返回的参数化 SQL（`$1/$2/$3` + params 数组） | **0/4** |
| **B** 回填 | 把 params 填回 SQL，保留 Cube 的原始类型 | **0/4** |
| **C** 回填 + 数字还原 | 再把数字字符串 `'1'` 还原成裸数字 `1` | **4/4 全过** |

两层障碍：

1. **Cube 返回参数化语句**，字面值被换成占位符。而守卫验证公司隔离的方式是在 AST 里
   找**字面整数**——占位符里没有值可找，于是判 `missing_company_filter`。
2. **Cube 对整数列也用字符串传参**（`params = ['1']`）。只做回填会得到
   `company_id = '1'`，守卫要求 `literal.is_int`，仍然不过。

两层都补上之后，Cube 编译出的 SQL **全部通过现有守卫，守卫代码一行没改**。

## 1. 对照怎么设计成单一变量

Cube 与 Wren 是同一生态位（语义层 / headless BI），但接口形态不同：Wren 是一次性 CLI，
Cube 是常驻服务；Wren 的主接口是"对 MDL 模型名写 SQL"，Cube 的主接口是结构化 JSON 查询。

直接用 Cube 的 JSON 查询会引入一个混淆变量——模型的任务从"写 SQL"变成"填 JSON"，
测出来的差异就无法归因到语义层本身。所幸 Cube 的 `/v1/sql` 端点支持 `format=sql`：
**接一段写给 Cube 视图的 SQL，返回编译后的物理 SQL，不执行**。这与 Wren 的
`dry-plan` 完全同形，于是三方可以对齐成：

| | 模型写什么 | 编译 | 守卫 | 执行 |
| --- | --- | --- | --- | --- |
| 自研 | 物理 SQL | 无 | ✓ | 项目自己的只读连接 |
| Wren | 对 MDL 模型名的 SQL | `wren dry-plan` | ✓ | 同上 |
| Cube | 对 Cube 视图的 SQL | `/v1/sql?format=sql` | ✓ | 同上 |

三条路径的执行层完全相同，**唯一变量是中间那层语义层**。

## 2. 阶段一验了什么

`sales_semantics.json` 的 9 表 8 指标翻译成 Cube 数据模型，编译出
**9 个 cube + 2 个视图**（`sales_analysis` 订单级、`sales_line_analysis` 行级）。

口径对齐的两个关键处理：

- `state IN ('sale','done')` **不做成预过滤子查询**。那会让 Cube 生成
  `FROM (SELECT * FROM sale_order WHERE ...)`，而守卫拒绝一切 `SELECT *`，一条都过不了。
  改成逐指标 `filters`，落地为 `SUM(CASE WHEN state IN (...) THEN ... END)`，语义等价。
- 产品名的 `COALESCE(name->>'zh_CN', name->>'en_US')` 契约**直接编进维度定义**，
  Cube 原样带进生成的 SQL。

四条查询对应黄金集的四种形状：KPI 标量、跨 cube 连接的 Top-N 排名、时间分桶趋势、
行级排名（同时触发 JSONB 译名契约）。

## 3. 两层障碍的成因，以及为什么"回填"是必需的而非取巧

守卫对 `LIMIT` 的处理是**改写而不是拒绝**：Cube 默认加的 `LIMIT 50000` 被重写成
`max_rows`（500），并且守卫**返回规范化后的 SQL 交给执行层**。

这意味着参数化版本本来就到不了执行那一步——执行的是守卫的输出，一个纯 SQL 字符串。
所以接 Cube 必须先回填参数再交守卫，这不是绕过安全机制，是守卫的工作方式决定的。

**但回填器会进入安全路径，必须单独钉测试**。尤其是数字还原不能误伤字符串值：
时间参数 `'2026-01-01T00:00:00.000Z'` 必须保持带引号，只有能被 `int()` 解析的才裸化。

## 4. 一个被推翻的预判

阶段一立项时我判断的主要风险是"Cube 的 SQL 生成比 Wren 复杂得多，会撞上守卫的
复杂度预算"。**实测相反**：生成的 SQL 单层、无 `SELECT *`、无嵌套子查询、JOIN 在预算内。
最长一条 662 字符。复杂度从头到尾没有成为问题。

真正的障碍是参数化与类型表示——事先完全没有预料到，也不是靠读文档能发现的。

## 5. 环境上的两个坑

**一、npm 装不上。** 镜像源 `registry.npmmirror.com` 滞后（`@aws-sdk/core` 只到
`3.977.9`，Cube 需要 `^3.978.0`），且全局 `.npmrc` 有 `prefer-offline=true`，
即便换官方源仍读陈旧缓存元数据。解法：官方源 + 独立缓存目录，不动机器上的共享缓存。

**二、Cube 起不来，而报错是误导性的。**

```
Unable to load @cubejs-backend/native, probably your system (x64-win32)
with Node.js v24.18.0 is not supported.
```

平台**是支持的**——release 里有 `native-win32-x64-unknown-fallback.tar.gz`。真实原因是
那个 Rust 原生二进制由 `@cubejs-infra/post-installer` 从 **GitHub release 产物**下载，
而本机未认证 GitHub 访问被限流，postinstall 静默失败。

**这与 Waza 曾卡住一个多月是同一根因，解法也相同**：用认证 `gh release download` 取下来，
放到加载器期望的 `node_modules/@cubejs-backend/native/index.node`。
两个不同工具、同一个坑——凡是"装完就是跑不起来，报错说平台不支持"的，先查是不是
GitHub 产物没下来，而不是信那句报错。

## 6. 这一步**没有**证明什么

- **没有测准确率、延迟、成本。** 阶段一完全没花模型额度，不含任何端到端问答。
  Cube 是不是比自研层/Wren 更准更快，目前一无所知。
- **口径翻译是我手写的，可能有错。** Cube 模型与 `sales_semantics.json` 的等价性
  只经过人工核对，没有数值比对。阶段二必须加"同一问题三方结果一致性"检查。
- **只有 4 条查询。** 覆盖四种形状，但远不是黄金集的 20 题。可能存在只在某些
  查询形状上暴露的第三类不兼容。
- **没有验证过执行。** 全程只做编译与静态校验，没有真正跑过任何一条 SQL 取数。

## 7. 阶段二三要做什么（阶段二已完成，见 §9）

**阶段二**（不花额度）：`CubeSemanticProvider` 实现现有 Protocol（`plan_sql` 打
`/v1/sql` → 回填 → 交守卫；`retrieve` 用 `/v1/meta` 产 Prompt 上下文）；配置加 `cube` 分支；
Cube 版 SQL 提示词；把 `run_semantic_benchmark.py` 的 comparison 段从硬编码两方
泛化成"基线 vs N 候选"（`reports_by_provider` 本身已支持 N 方）；回填器的契约测试。

**阶段三**（需额度）：20 题 × 3 轮 × 3 方 = 90 次观测，估 $0.15~0.25。

## 8. 代价与复现

`node_modules` 480 MB + 原生二进制 67 MB，均在 `.gitignore` 覆盖的 `node_modules/` 下。

```bash
# 装依赖（镜像源滞后，必须用官方源 + 独立缓存）
cd backend/app/bi/cube_project
npm install --registry=https://registry.npmjs.org --cache <独立目录> --prefer-offline=false

# 补原生二进制（postinstall 从 GitHub 拉，未认证会被限流）
gh release download v1.7.40 --repo cube-js/cube \
  --pattern 'native-win32-x64-unknown-fallback.tar.gz'
tar -xzf native-win32-x64-unknown-fallback.tar.gz
cp native/index.node node_modules/@cubejs-backend/native/index.node

# 起服务
CUBEJS_DEV_MODE=true CUBEJS_API_SECRET=probe CUBEJS_SCHEMA_PATH=model \
  node node_modules/@cubejs-backend/server/bin/server
```

## 9. 阶段二实施结果（2026-09-18）

已接通，**全量测试 341 项通过**，并对着真实 Cube 服务做了端到端验证：
`build_semantic_provider` → `retrieve`（真打 `/v1/meta`）→ `plan_sql`（真打 `/v1/sql`）
→ 参数回填 → 守卫放行 → 输出可执行的规范化 SQL。

### 落地的东西

| 文件 | 作用 |
| --- | --- |
| `backend/app/bi/cube_client.py` | 编译、取元数据、**参数回填**。单独成模块是因为回填器进入安全路径 |
| `backend/app/bi/semantic_provider.py` | `CubeSemanticProvider`，与 Wren 同构 |
| `backend/app/bi/cube_project/` | Cube 服务的配置与数据模型（9 cube + 2 视图） |
| `backend/app/bi/semantic.py` | `as_prompt` 按 provider 分支出 Cube 的命名空间指令 |
| `evals/run_semantic_benchmark.py` | 差值计算从硬编码两方泛化为"基线 vs N 候选" |
| `backend/tests/test_cube_client.py` | 回填器契约测试 14 条 |

HTTP 用标准库 `urllib` + `asyncio.to_thread`，与 Wren provider 用线程跑子进程的方式
一致，**不引入新依赖**（`httpx` 只是传递依赖，未在依赖清单中声明）。

### 回填器为什么值得 14 条测试

它决定守卫看到什么。两条最容易写错、且错了不会立刻暴露的性质：

- **单趟替换。** 必须一次 `re.sub` 扫完。若分多趟从 `$n` 往 `$1` 替换，某个参数值里
  含有 `$1` 这样的文本时会被二次替换——如果客户名恰好是 `$2`，产出的 SQL 语义完全
  不同却仍能通过守卫。这是真实的注入面，已有测试钉住。
- **只还原整数。** `'2026-01-01T00:00:00.000Z'` 裸化会变成非法 SQL。宁可少还原
  也不能错还原，浮点同样保持引号交给 Postgres 转型。

### 两个已知的不对等，阶段三解读结果时必须记住

1. **Wren 多一条业务规则通道。** Wren 用 `context instructions` 往提示词里塞一段
   业务规则文本，Cube 没有对应机制。所以三方对照里 Wren 的提示词比 Cube 多一块内容，
   这是语义层能力差异的一部分，但不是纯粹的"编译质量"差异。
2. **Wren 的提示词文本一字未动。** 这是刻意的——文档 18 的 Wren 成绩是在那段提示词下
   测出来的，改了就不再可比。Cube 的指令走独立分支。

### 阶段三的前置条件：**口径等价性尚未验证**

Cube 模型的 9 表 8 指标是我手写翻译的，目前只经人工核对，**没有做过数值比对**。
如果翻译有偏差，阶段三测出来的是"我翻译错了"而不是"语义层的差异"。

跑阶段三之前必须先做：同一指标分别用自研层 SQL 与 Cube 编译出的 SQL 取数，比对数值。

**这件事和阶段三本身都被同一个东西阻塞：数据库没启动。** 端口 55432 来自
`odoo-stack/compose.yaml`，而 Docker 守护进程未运行。Cube 的编译不碰数据库
（`/v1/sql` 只编译不执行），所以阶段一二能在库不可用的情况下完成，阶段三不能。

## 10. 阶段三的准备工作（2026-09-18）

### 口径等价性：8/8 一致

跑基准前先验了 Cube 模型的翻译有没有偏差——两侧用同一个库取数、逐指标比值：

| 指标 | 自研层 | Cube |
| --- | ---: | ---: |
| sales_amount | 28536736.53 | 28536736.53 |
| tax_included_sales | 31984161.83 | 31984161.83 |
| order_count | 1526 | 1526 |
| average_order_value | 18700.351592398427 | 18700.351592398427 |
| sales_quantity | 34472.00 | 34472.00 |
| delivered_quantity | 6.00 | 6.00 |
| invoiced_quantity | 5.00 | 5.00 |
| uninvoiced_quantity | 34467.00 | 34467.00 |

**8/8 完全一致**，所以基准里的差异可以归因到语义层，不是翻译错误。

### 第三类不兼容确实存在，出现在趋势题上

文档 §6 预留过"只有 4 条查询，可能存在只在某些形状上暴露的第三类不兼容"。确实有：

```
SQL ORDER BY 与 QueryPlan.sort 不一致：期望 [('month','asc')]，实际 [('cast_datetrunc_u','asc')]
```

模型对时间分桶按表达式排序时，Cube 会把该表达式改写成内部别名 `cast_datetrunc_u`，
于是与 QueryPlan 声明的维度名对不上。阶段一的探针之所以没踩到，是因为我手写的是
`ORDER BY 1`——**序号会被守卫解析回第一个输出列**，天然对齐。

处理方式是往 Cube 的提示词里加一条方言指引：一律按列序号 GROUP BY / ORDER BY。
这与 Wren 提示词里那句"对 MDL 模型名写 SQL"属于同一类，是集成工作的一部分。

### 一个我自己造成的坑：视图命名不一致

改完排序后仍失败，真因是**我的视图设计有歧义**：`sales_analysis` 里 sale_order 是基础
cube，字段不带前缀（`date_order`）；而 `sales_line_analysis` 里 sale_order 的字段带了
`prefix: true`（`sale_order_date_order`）。同一个字段两个名字，模型猜了错的那个，
Cube 直接拒绝编译：

```
Invalid identifier '#sale_order_date_order'
```

已把行级视图的 sale_order 字段改为不加前缀，两个视图从此同名；行级的 `company_id`
一并去掉（公司范围以订单为准，留两个同义字段只会再制造歧义）。改完重跑口径比对仍是 8/8。

**这条值得记住**：语义层的价值之一是给模型一套稳定的命名，而命名不一致的代价不是
"模型偶尔写错"，而是**查询根本编译不过**。这不是 Cube 的缺陷，是建模的缺陷。

### 必须声明的不对等

Cube 的集成只有一天，期间为它做了两轮方言/建模修正；Wren 的集成在文档 13 与 18
经历过多轮打磨，本次会话里**没有**为 Wren 做任何调整（提示词一字未动，正是为了与
已归档结果可比）。所以基准里若 Cube 落后，**不能直接读成"Cube 不如 Wren"**，
其中含有集成成熟度的差异。

## 11. 三方实跑结果（2026-09-18，20 题 × 3 轮 × 3 方 = 180 次观测）

| 语义层 | 总通过率 | 结构通过率 | 结果签名 | p50 | p95 | Token | Cost USD | Repair |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| native | 92.11% | 94.74% | 93.55% | 5210 ms | 33998 ms | 657994 | 0.3472 | 10 |
| wren | 92.98% | 92.98% | 98.92% | 5742 ms | 32061 ms | 834381 | 0.4202 | 17 |
| **cube** | **67.11%** | 69.30% | 41.94% | 4827 ms | 15803 ms | 699361 | 0.3534 | **80** |

报告在 `evals/reports/20260918-114854/`。

### 这个 67% 不能读成"Cube 的质量比 Wren 差 25 个百分点"

**Cube 的失败有 62 次是 `data_accessed=False`——SQL 根本没跑成，不是答错。**
修复次数 80 次（native 10、wren 17）也指向同一件事：模型在反复试，试不通。

原生与 Wren 有 4 个共同的稳定失败（数据集与 agent 的既有局限）。Cube 是那 4 个
**再加 18 个**。逐一追踪这 18 个，原因归成三类，而三类同源：

| 类别 | 案例数 | 机制 |
| --- | ---: | --- |
| 别名 16 字符截断 | 6 | Cube 把输出列别名硬截断到 16 字符 |
| 物理列名 vs 计划维度名 | ~6 | 编译产物用 `res_partner.name`，计划声明的是 `customer` |
| 时间分桶别名 | ~3 | `date_trunc` 表达式被改写成 `cast_datetrunc_u` |
| 视图表达不了 | ~3 | 订单级指标 × 行级维度（详见下文） |

**同源在于**：项目的守卫是**按名字**校验的——输出列要与 `QueryPlan.select_columns`
逐一对齐、过滤字段要在计划里声明过、ORDER BY 要与 `sort` 对齐。它隐含一个前提：
**编译只改写结构、不改写命名**。Wren 的 dry-plan 满足这个前提，Cube 的编译器不满足，
它按自己的规则重写命名。三类失败是同一个不兼容的三种表现。

### 16 字符截断是实测确认的，不是推测

```
sales_amount        (12) -> sales_amount      ✓
invoiced_quantity   (17) -> 截断
tax_included_sales  (18) -> tax_included_sal
delivered_quantity  (18) -> delivered_quanti
average_order_value (19) -> average_order_va
```

项目 8 个指标里有 5 个名字超过 16 字符，凡用到它们的查询都过不了守卫的列名对齐。
**这一条有明确的修法**（在 Cube provider 里把编译产物的外层别名还原成模型请求的名字），
本次没有实现——因为它是第三个进入安全路径的改写，值得单独设计和测试。

### 延迟看着更好，是假象

Cube 的 p50 最低（4827 ms）、p95 低了 18 秒。但这不是"更快"，是**失败得早**：
编译失败的查询根本不执行，直接走完修复循环就结束。拿失败样本算延迟没有意义。

### 一个不是 Cube 的问题：我的视图表达不了某些查询

`ranking-products`（按产品看销售额）直接被 Cube 拒绝编译：

```
Invalid identifier '#sales_amount' for schema fields:[sales_line_analysis.sales_quantity, ...]
```

因为 `sales_amount` 是订单级指标、只在 `sales_analysis` 里，而产品维度在
`sales_line_analysis` 里。我把模型拆成两个视图，于是"订单级指标 × 行级维度"这类
查询无从表达。这是建模缺陷，不是 Cube 的能力缺陷——单一宽视图可以表达（代价是
订单级金额会沿行数扇出，需要额外处理）。

### 结论

1. **Cube 能接进这套架构**，语义与口径都对（8/8 指标数值一致），端到端跑得通。
2. **但它与"按名字校验"的守卫存在系统性不兼容**，这是本次最有价值的发现——
   它不是 Cube 的缺点，也不是守卫的缺点，是两套设计前提的冲突。
3. **当前的 67% 不足以支持"Cube 不如 Wren"的质量结论。** 它衡量的是一个还缺一层
   命名还原的适配器。要得到可用于选型的数字，得先把命名还原做掉再重跑。
4. 对照框架本身已经是 N 方的了（`--providers native,wren,cube`），再加第四种语义层
   不需要改基准代码。

### 仍未消除的不对等

Cube 的集成只有一天，期间为它做了两轮方言与建模修正；Wren 经历过文档 13 与 18
的多轮打磨，本次会话对 Wren **一字未动**（正是为了与归档结果可比）。
解读上表时这一点必须在场。

## 12. 命名还原后的重测（2026-09-19，只重跑 Cube 一腿）

实现了别名还原（`restore_cube_aliases`），只重跑 Cube 的 3 轮，native/wren 沿用
09-18 的数据——同一份数据集、同一台机器，且本次会话对这两条腿的代码与提示词一字未动。

### 改前 / 改后

| 指标 | 改前 | 改后 |
| --- | ---: | ---: |
| 总通过率 | 67.11% | **74.56%** |
| 结构通过率 | 69.30% | 76.32% |
| 结果签名 | 41.94% | **61.29%** |
| Repair 次数 | 80 | 61 |
| p50 | 4827 ms | 4299 ms |

比 native/wren 多出的稳定失败：**18 → 12**。修好的正好 6 个，全部是长名指标那一类
（`comparison-delivery`、`comparison-invoice`、`comparison-month-delivery`、
`comparison-month-invoice`、`kpi-average-order`、`kpi-month-average-paraphrase`）。
**剩下 12 个里不再有任何一个涉及长名指标**——截断这条因已彻底消除，预测与实测吻合。

### 三方对照（当前状态）

| 语义层 | 总通过率 | 结果签名 | p50 | Repair |
| --- | ---: | ---: | ---: | ---: |
| native | 92.11% | 93.55% | 5210 ms | 10 |
| wren | 92.98% | 98.92% | 5742 ms | 17 |
| cube | 74.56% | 61.29% | 4299 ms | 61 |

### 剩下的 12 个卡在两处，仍是同一个根源

**一、`sort_mismatch`（趋势与同比，约 5 例）。** SELECT 里一旦出现表达式，
Cube 会把整个查询包进子查询，外层列名是对的，但 ORDER BY 引用的是**内层别名**：

```sql
SELECT "sales_analysis"."cast_datetrunc_u" "month", ...
FROM ( SELECT CAST(date_trunc(...) AS DATE) "cast_datetrunc_u", ... ) AS "sales_analysis"
ORDER BY "sales_analysis"."cast_datetrunc_u" ASC NULLS LAST
```

**这里要纠正 §10 的一个说法**：当时我认为"让模型写 `ORDER BY 1` 就能避开"。
实测不成立——不论模型写序号还是写表达式，Cube 都会重写成上面这个形状。
§10 那条方言指引没有起到我以为的作用。

别名还原之所以没顺手修掉它，是因为 `cast_datetrunc_u` **不是** `month` 的前缀截断，
而是一个全新的生成名。还原函数按设计拒绝改写这种情况（"不是截断就不动"），这是对的。

修法是明确的：外层 ORDER BY 若引用的表达式与某个外层输出列完全相同，就改写成该列的别名。
规则清晰、可结构化实现，但这是**第四条**进入安全路径的改写，本次没有做。

**二、`filter_undeclared`（客户/销售员过滤，约 6 例）。** 模型写
`WHERE res_partner_name = '...'`，Cube 编译成 `WHERE res_partner.name = '...'`，
而 QueryPlan 声明的过滤字段名对不上，守卫报"未声明的过滤字段：name"。

### 结论没变，但更精确了

修掉一条命名不兼容，通过率涨 7.45 个百分点、结果签名涨 19.35 个百分点。
**剩下的差距仍然全部是同一类问题**：Cube 按自己的规则重写命名，而守卫按名字校验。
这不是查询质量的差距——`data_accessed` 失败 45 次，SQL 依然是"没跑成"而不是"答错"。

所以 74.56% 依旧不是 Cube 的质量上限，而是适配器完成度的读数。两条已知路径都修完之后
才值得拿来和 Wren 比质量。

## 13. 修完全部命名问题后的最终对照（2026-09-19）

只重跑 Cube 一腿，native/wren 沿用 09-18 数据（本次会话对这两条腿一字未动）。

| 指标 | 初版 | 最终 |
| --- | ---: | ---: |
| 总通过率 | 67.11% | **85.09%** |
| 结构通过率 | 69.30% | 89.47% |
| 结果签名 | 41.94% | **82.80%** |
| Repair 次数 | 80 | 29 |

比 native/wren 多出的稳定失败：**18 → 4**。

| 语义层 | 总通过率 | 结果签名 | p50 | Repair |
| --- | ---: | ---: | ---: | ---: |
| native | 92.11% | 93.55% | 5210 ms | 10 |
| wren | 92.98% | 98.92% | 5742 ms | 17 |
| cube | 85.09% | 82.80% | 4518 ms | 29 |

### 这一轮修的三件事

1. **ORDER BY 重对齐**。外层 ORDER BY 引用内层生成名（`cast_datetrunc_u`）时，
   改写成对应输出列的别名。判据是表达式逐字相等，不做模糊匹配。
2. **物理列名进 Prompt**。`/v1/meta` 的 `aliasMember` 给出
   `sales_analysis.res_partner_name → res_partner.name`，据此在 schema 说明里为 11 个
   扁平化维度标注 `[物理列 X]`，并要求 QueryPlan.filters 用物理列名声明。
   **物理列名是从 Cube 自己推出来的，不是靠提示词让模型猜。**
3. **时区**（下节单独说，这是本轮最重要的发现）。

另补一个建模缺口：视图里原本**没有销售员姓名**，"销售额最高的销售员"这类问题
根本无法表达。已加 `salesperson_name`——姓名与客户名同在 `res_partner`，
必须显式改名才不与 `res_partner_name` 撞前缀。

### 时区：一个总量比对看不出来的口径错误

Cube 生成的时间表达式是：

```sql
date_trunc('month', CAST(date_order AS TIMESTAMPTZ) AT TIME ZONE 'UTC')
```

Odoo 把 `date_order` 存成 `timestamp without time zone`、内容是 UTC；而 `CAST` 会按
**执行连接的会话时区**（本机 Asia/Shanghai）解释这个裸值，等于减去 8 小时。两个后果：
月初 00:00–08:00 的订单被算进上个月，**时间过滤的边界也跟着偏**（今年 1 月因此少了
5 万多）。

修法写在模型里：`sql: "timezone('UTC', {CUBE}.date_order)"`，裸值被显式当作 UTC，
Cube 外面那层转换正好抵消。两个细节：

- **改 Cube 自己的连接时区没有用。** 本项目只用 Cube 编译，生成的 SQL 是项目自己的
  只读连接执行的。我第一次就是改的连接时区，写完才反应过来不起作用，已撤销。
- **不能写 `AT TIME ZONE 'UTC'`**，会被 Cube 的 SQL 生成器改写成
  `AT TIME ZONE CAST('UTC' AS TIMESTAMPTZ)`，非法语法。必须用函数形式。

### 必须纠正 §10 的两个说法

**一、"8/8 口径一致，翻译无偏差"这个结论下得不充分。** 那次只比了**总量**，
而时区 bug 恰好不改变总量——它只把边界订单挪到相邻月份。总量比对根本看不出来，
直到跑基准才暴露。校验方法本身不够，不是执行得不够。

现在的校验加了 5 项**分组**比对（按月、按年、按客户、按销售员、按产品），逐行比数值，
8/8 全过。总量一致只是必要条件。

**二、"让模型写 `ORDER BY 1` 就能避开排序别名问题"是错的。** 实测表明 SELECT 里
只要出现表达式，Cube 就会包一层子查询并在 ORDER BY 里引用内层生成名，
与模型写序号还是写表达式无关。那条方言指引没有起到我以为的作用，真正解决它的是
上面第 1 条的结构化重写。

### 剩下 4 个已经不是同一类问题

`filtered-customer`、`filtered-customer-month`、`empty-future-kpi`、`trend-customer-by-month`。
前三个都是 `result_signature` 差一个值；手工复现 `filtered-customer` 时，
**自研与 Cube 返回同一个值**（该客户无订单，两边都是 NULL），说明差异来自模型的措辞
与参考答案之间，而不是 Cube 的编译。也就是说：**结构性不兼容已经修完了**，
剩下的属于查询表述层面的零散差异。

### 结论

- 三次命名不兼容 + 一次时区口径错误修完后，Cube 从 67.11% 到 **85.09%**，
  与 native/wren 的差距从 25 个百分点收到 **7～8 个百分点**。
- 至此 **74.56%/85.09% 这些数字才开始有选型参考价值**——之前衡量的是适配器完成度。
- 但**不对等仍然存在**：Cube 的集成前后花了两天、做了四轮修正；Wren 经历过文档 13 与 18
  的多轮打磨，本次会话对它一字未动。剩下的 7～8 个百分点里有多少是语义层本身的差距、
  多少是集成成熟度，这次实验回答不了。
- 一个可复用的经验：**语义层之间的差异，主要不在"能不能编译出 SQL"，而在"编译出的
  SQL 是否保持命名"**。任何按名字校验的下游（本项目的守卫）都会和重写命名的语义层冲突，
  且冲突会以四五种互不相同的表象出现。
