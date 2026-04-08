# 期货数据与因子框架重构方案

## 1. 文档目的

本文档用于指导当前仓库的整体重构，重点解决以下问题：

1. 数据挂载方式过于片面，读取逻辑分散在多个脚本里，且和训练脚本、预测脚本、因子脚本强耦合。
2. `factors/sc_factor_func_0807.py` 中的因子函数无法优雅声明“需要哪些额外数据”，导致跨品种、跨日期、算子数据、历史统计量都只能靠外部脚本硬拼。
3. 时间索引、日盘夜盘、主力合约、文件路径、缓存、因子存储、label 存储缺乏统一抽象。
4. 当前很多逻辑依赖临时 pickle、反复扫描目录、重复计算算子、重复读取数据，性能差且维护成本高。
5. 因子增量重算、部分日期重算、部分因子重算、多进程重算都不够自然。

本文档不直接实现全部代码，而是给出完整的目标架构、模块划分、数据结构、接口设计、迁移路径和执行逻辑。后续重构以本文档为准。

注：

1. 当前 `config.py` 中的 `data_root` 后续统一按当前目录下的 `data` 理解。
2. 方案基于本目录现有代码实际情况制定，尽量保留可复用部分，但会明确淘汰掉当前不适合继续扩展的做法。

---

## 2. 当前代码的主要问题

### 2.1 数据读取和处理逻辑分散

当前与“数据读取”相关的逻辑散落在：

1. `dataprocess/read_data.py`
2. `dataprocess/data_processor.py`
3. `utils/tools.py`
4. `utils/main_contract.py`
5. `get_data/data_loader.py`
6. `pred.py`
7. `model/tree_trainer.py`
8. `model/common_trainer.py`

同一个核心问题被重复实现了很多次，例如：

1. 目录扫描和文件解析反复执行。
2. 主力合约过滤在多个地方反复做。
3. 训练脚本内部又自己拼接历史统计量和关联品种数据。
4. 预测脚本里又自己实现一套朋友品种合并逻辑。
5. 因子函数里直接调用 `detailed_trade_allocation(df)`，多个因子会重复做相同算子计算。

### 2.2 当前 `Config` 过重且不可注入

`config/config.py` 现在同时承担了：

1. 路径配置
2. 交易时间配置
3. 因子配置
4. 标签配置
5. 主力合约配置
6. 模型训练配置
7. 历史统计配置

问题是：

1. 一个配置对象同时服务“数据层”和“模型层”，边界不清楚。
2. `dataprocess/data_processor.py` 里全局实例化了 `cfg = Config()`，导致 `calculate_target()` 等逻辑无法真正被外部配置驱动。
3. 训练和因子计算必须共享同一个大配置类，后续扩展困难。

### 2.3 主力合约过滤方式脆弱且低效

当前 `utils/tools.py` 的 `filter_main_contract()` 通过：

1. 读取 JSON
2. 递归转成 set
3. 对 `file_info` 做集合包含判断

这种方式的问题是：

1. 依赖路径字符串和元组字段碰巧可匹配。
2. 容易被大小写、路径、session 结构变化打断。
3. 每次调用都重新读 JSON、重新构造结构、重新过滤。
4. 难以扩展到“主力判定规则版本化”“手工覆盖”“按成交量/持仓量/名义成交额切换”。

### 2.4 时间索引与交易时段处理不统一

当前时间处理存在以下问题：

1. 夜盘时间依赖 `UpdateTime > '03:00:00'` 的修正方式，逻辑偏经验化。
2. `generate_complete_timeindex()` 使用数据最早时间作为起点，若开盘早段数据缺失，会直接改变补全基准。
3. 日盘/夜盘的补时逻辑不统一，且和交易所交易时段模板没有彻底解耦。
4. index 只有 datetime，没有同时保留 `trading_day`、`session`、`calendar_day`、`is_current_day` 等关键信息。
5. 历史 n 天 prepend 成大 df 时，如果只靠 datetime index，很容易产生逻辑歧义。

### 2.5 因子函数缺少依赖声明机制

当前因子函数只有：

```python
def calc_xxx(df):
    ...
```

这会导致：

1. 因子函数无法声明自己需要哪些原始列。
2. 无法声明需要哪些算子。
3. 无法声明需要哪些关联品种。
4. 无法声明需要过去几天统计量。
5. 无法声明需要 prepend 过去几天的数据。
6. 无法声明结果是否依赖主力映射、session 模式、对齐方式。

最终导致这些逻辑只能写在因子外层脚本里，因子本身并不是“自描述”的。

### 2.6 算子重复计算严重

例如：

1. `detailed_trade_allocation(df)` 在多个因子中都会调用。
2. 同一天同品种同一批因子如果都依赖这个算子，会反复计算多次。
3. 当前缺少“算子注册表 + 缓存层 + 按需加载”的机制。

### 2.7 存储方式不利于增量追加和部分重算

当前 `save_factor()` / `save_label()` 的存储方式是：

1. 按 `session/symbol/contract/date_contract_session.pkl` 保存。
2. 每个文件是一个宽表 pickle。

存在问题：

1. pickle 不适合作为中长期生产存储格式。
2. 不方便做列级别增量追加。
3. 不方便做 schema 管理和版本控制。
4. 不方便高效筛选日期范围和字段范围。
5. 不方便做文件清单管理。

### 2.8 训练端在补架构上的洞

`get_data/data_loader.py`、`utils/save_stats.py`、`pred.py`、`model/tree_trainer.py` 中存在大量“本应在数据层完成”的逻辑，例如：

1. 临时保存文件索引 pickle。
2. 手动构造历史均值和方差。
3. 手动加载朋友品种数据。
4. 手动 merge 外盘品种。
5. 手动做日期切片和对齐。

这说明当前数据服务层没有建立起来。

---

## 3. 重构总目标

重构后的框架需要满足以下目标：

1. 因子函数只负责写因子逻辑，不负责手工搬运数据。
2. 因子函数可以通过装饰器声明自己的数据依赖。
3. 数据读取统一走一个“数据门户”。
4. 原始数据、标准化基础数据、算子缓存、因子、label 分层存储。
5. 时间索引统一，日盘夜盘统一由交易时段模板驱动。
6. 主力合约、路径解析、日期选择、缓存、并行调度统一管理。
7. 支持：
   1. 新增单个因子
   2. 重算部分因子
   3. 重算部分日期
   4. 按品种重算
   5. 自动跳过已完成任务
   6. 增量追加因子列
8. 训练和预测只消费标准化后的 factor/label store，不再自带临时数据拼接逻辑。
9. 允许存储部分轻量中间结果，尤其是高复用算子，但避免大量冗余地存储整份非因子数据。

---

## 4. 设计原则

### 4.1 单一职责

1. 配置只管配置。
2. 文件目录解析只管 catalog。
3. 原始数据标准化只管 base panel。
4. 算子只管算子。
5. 因子只管因子。
6. pipeline 只管调度。
7. 训练只消费成品数据。

### 4.2 显式依赖

一个因子需要：

1. 哪些本品种原始列
2. 哪些关联品种
3. 哪些关联品种列
4. 哪些算子
5. 哪些历史统计量
6. 哪些历史天数
7. prepend 还是 separate history

都必须可以通过装饰器显式声明。

### 4.3 统一数据键

全系统统一以如下键描述一份“日内数据分区”：

1. `symbol`
2. `trading_day`
3. `session_scope`
4. `contract`
5. `source`

其中：

1. `trading_day` 是交易日，不是自然日。
2. `session_scope` 取值建议为 `day`、`night`、`all`。
3. 当前生产因子建议最终以 `all` 为主，保留 `day/night` 可选。

### 4.4 缓存优先于重复计算

1. 高频复用算子必须可缓存。
2. 目录索引必须缓存。
3. 主力映射必须缓存。
4. 准备好的基础 panel 可以做轻量缓存。

### 4.5 因子可增量构建

1. 新增一个因子，不应该强制重算全部历史因子。
2. 修改一个因子，只重算受影响的日期与品种。
3. 修改一个算子，能定位依赖该算子的因子范围。

---

## 5. 目标目录结构

建议重构后的目录结构如下：

```text
config/
  project.yaml
  trading_calendar.yaml
  relations.yaml
  factor_build.yaml
  main_contract_rules.yaml

core/
  schema.py
  types.py
  keys.py
  calendar.py
  selectors.py

catalog/
  parser.py
  catalog_builder.py
  catalog_store.py
  main_contract.py

dataio/
  base_reader.py
  prepared_store.py
  portal.py
  aligner.py
  stats.py

operators/
  registry.py
  base.py
  orderflow.py

factors/
  decorators.py
  registry.py
  sc_factor_func_0807.py

storage/
  factor_store.py
  label_store.py
  operator_store.py
  manifest.py

pipeline/
  build_base.py
  build_operators.py
  build_factors.py
  build_labels.py
  runner.py

training/
  dataset.py
  loader.py

legacy/
  ...
```

说明：

1. 保留 `factors/sc_factor_func_0807.py` 作为主要因子定义文件。
2. 当前 `dataprocess`、`get_data`、`utils` 中与目录解析、数据读取、主力映射相关的代码会逐步迁入上述模块。
3. 重构期间可保留旧代码，但不再继续扩展旧入口。

---

## 6. 核心数据模型

### 6.1 Catalog 层

Catalog 是原始文件的结构化清单，不再依赖路径字符串临时猜。

每条记录至少包含：

1. `symbol`
2. `contract`
3. `trading_day`
4. `session`
5. `exchange`
6. `source_type`
7. `file_path`
8. `file_type`
9. `mtime`
10. `size`
11. `has_day`
12. `has_night`
13. `volume_sum` 可选
14. `open_interest_last` 可选

Catalog 存储建议：

1. `_meta/catalog.parquet`
2. 首次扫描构建，后续增量刷新

这样可以彻底淘汰：

1. 每次 `rglob`
2. 每次重新解析路径
3. 每次重新基于 JSON 过滤主力

### 6.2 Main Contract 表

主力合约不再只保存成深层 JSON，而是保存为结构化表：

字段建议：

1. `trading_day`
2. `symbol`
3. `session`
4. `main_contract`
5. `score`
6. `rule_name`
7. `rule_version`
8. `is_manual_override`

主力判定规则支持：

1. 成交量最大
2. 持仓量最大
3. 名义成交额最大
4. 自定义加权
5. 手工覆盖

建议同时保留：

1. `main_contracts.parquet`
2. `main_contract_overrides.yaml`

### 6.3 Base Panel

Base panel 是标准化后的日内数据，是后续因子和 label 的唯一底层输入。

Base panel 不等于原始 CSV 全字段，也不等于最终因子。

建议只保留核心列：

1. `symbol`
2. `contract`
3. `trading_day`
4. `session`
5. `event_time`
6. `trade_date`
7. `calendar_date`
8. `TimeStamp`
9. `LastPrice`
10. `Volume`
11. `Turnover`
12. `AccVolume`
13. `AccTurnover`
14. `OpenInterest`
15. `b1~b5`
16. `b1_v_m~b5_v_m`
17. `a1~a5`
18. `a1_v_m~a5_v_m`
19. `mid`
20. `cvwap`
21. 其他必要规范化字段

说明：

1. 这是轻量标准化底座。
2. 不保留原始 CSV 的所有边角列。
3. 若存储 prepared base cache，也只存这套 canonical 列。

### 6.4 Operator Data

Operator data 是从 base panel 提取出的高复用中间结构，比如：

1. `detailed_trade_allocation`
2. 后续可能新增的主动买卖分解
3. 各种订单流分配矩阵
4. 盘口聚合统计

Operator data 独立于 factor 存储，原因是：

1. 一个算子常被多个因子复用。
2. 算子重算频率低于因子试验频率。
3. 算子单独缓存可以显著减少重复计算。

### 6.5 Factor Store

Factor store 是最终训练与研究消费的数据层。

建议存储单位为：

1. 一天一个分区
2. 一个品种一个分区
3. session_scope 默认为 `all`

字段结构：

1. index: `event_time`
2. 基础键列：`symbol`、`contract`、`trading_day`、`session`
3. 因子列：`001`、`002` ...

### 6.6 Label Store

Label 独立保存，字段建议：

1. `rts_5`
2. `rts_10`
3. `rts_15`
4. `rts_30`
5. `label_version`
6. `label_target`

---

## 7. 时间索引与交易时段统一方案

### 7.1 统一 index 设计

后续所有读取出来的 df 统一使用：

1. `event_time` 作为 DataFrame index

并且同时保留以下列：

1. `trading_day`
2. `session`
3. `is_current_day`
4. `is_history_row`

其中：

1. `event_time` 表示真实事件时间，按交易所本地时间排序。
2. `trading_day` 表示这条记录归属的交易日。
3. 夜盘 21:00 以后虽然自然日可能是前一天，但其 `trading_day` 应归属下一交易日。

### 7.2 日盘夜盘不再靠数据最早时间推断

当前 `generate_complete_timeindex()` 依赖 `min_time` 做补全起点，这会在开盘缺失时产生错误。

新的方式：

1. 使用 `trading_calendar.yaml` 定义每个 symbol 的交易时段模板。
2. 每个模板包含若干 segment。
3. 每个 segment 指定：
   1. `start`
   2. `end`
   3. `cross_midnight`
   4. `freq`
   5. `session`

示例：

```yaml
au:
  night:
    - {start: "21:00:00", end: "23:59:59.500", cross_midnight: false}
    - {start: "00:00:00", end: "02:30:00", cross_midnight: true}
  day:
    - {start: "09:00:00", end: "10:15:00"}
    - {start: "10:30:00", end: "11:30:00"}
    - {start: "13:30:00", end: "15:00:00"}
```

### 7.3 两类时间字段同时保留

建议在标准化 base panel 中保留：

1. `event_time`
2. `trading_day`
3. `calendar_date`
4. `session`
5. `TimeStamp`

这样可以同时满足：

1. 因子 rolling 计算
2. 历史 prepend
3. 训练按交易日切片
4. 精确定位异常 tick

### 7.4 补时与无效时间处理

补时逻辑改为模板驱动：

1. 先生成标准时段索引
2. 再按 symbol 的补时策略处理
3. 再应用无效区间 mask

无效区间不应硬编码在单函数中，而应在配置中声明：

1. 午休前后 mask
2. 夜盘收盘前 30s mask
3. 外盘品种特例

---

## 8. 数据门户 DataPortal 设计

`DataPortal` 是重构后的核心。

所有因子、算子、label 生产都只通过 `DataPortal` 加载数据。

### 8.1 DataPortal 的职责

1. 根据 `symbol + trading_day + session_scope` 加载 base panel。
2. 自动解析主力合约。
3. 自动加载关联品种数据。
4. 自动加载或计算算子数据。
5. 自动加载历史统计量。
6. 自动加载 prepend 历史天数据。
7. 自动做时间对齐。
8. 自动做缓存。

### 8.2 DataPortal 核心接口

建议设计如下：

```python
portal.get_panel(
    symbol="rb",
    trading_day="20250306",
    session_scope="all",
    data_level="tick",
    bar=None,
    columns=["b1", "a1", "Volume"],
    operators=["detailed_trade_allocation"],
    related_symbols=["hc", "fe"],
    related_columns=["mid", "cvwap"],
    related_operators=["detailed_trade_allocation"],
    stats_days=5,
    stats_columns=["mid", "cvwap"],
    stats_metrics=["mean", "std", "skew", "kurt"],
    history_days=3,
    history_columns=["mid", "cvwap"],
    history_operators=["detailed_trade_allocation"],
    history_mode="prepend_rows",
)
```

其中：

1. `data_level="tick"` 表示直接返回 tick 级 panel。
2. `data_level="bar"` 表示返回聚合后的 bar panel。
3. `bar` 用于描述 bar 规格，后文会单独展开。

返回对象建议不是裸 DataFrame，而是 `FactorContext`：

```python
ctx.df
ctx.current_df
ctx.full_df
ctx.related["hc"].df
ctx.related["fe"].df
ctx.operators["detailed_trade_allocation"]
ctx.related["hc"].operators["detailed_trade_allocation"]
ctx.stats_df
ctx.meta
```

### 8.3 为什么推荐返回 Context 而不是只返回 df

因为只返回 df 会让很多额外能力无法优雅表达：

1. 当前日与 prepend 历史日需要区分。
2. 本品种和关联品种需要区分。
3. 原始列和算子列需要区分。
4. 历史统计量需要有自己的命名空间。

因此建议：

1. 因子函数主参数仍然可以叫 `df`
2. 但装饰器内部实际传递 `df + ctx`

兼容接口建议：

```python
def calc_301(df, ctx=None):
    ...
```

这样简单因子仍然只用 `df`，复杂因子才使用 `ctx`。

---

## 9. 因子装饰器设计

### 9.1 目标

你希望在 `factors/sc_factor_func_0807.py` 里写因子时，直接通过装饰器声明：

1. 是否加载关联品种
2. 加载哪些关联品种
3. 关联品种要哪些原始列
4. 是否加载算子数据
5. 加载哪些算子
6. 是否加载过去 n 天统计量
7. 统计量用哪些列
8. 是否加载过去 n 天原始数据
9. prepend 到当前 df 之前还是分开提供

这正是装饰器要完成的事。

### 9.2 推荐装饰器接口

建议新增：

```python
@factor_spec(
    name="301",
    raw_columns=None,
    bar=None,
    operators=None,
    related=None,
    stats=None,
    history=None,
    align="left",
    session_scope="all",
)
def calc_301(df, ctx=None):
    ...
```

参数说明：

1. `raw_columns=None`
   1. `None` 表示加载默认 canonical raw 列全集
   2. 指定 list 表示只加载这些本品种原始列
2. `operators`
   1. 当前品种需要的算子名列表
3. `bar`
   1. `None` 表示使用 tick 级数据
   2. 指定 dict 表示使用某种 bar 级数据
3. `related`
   1. 声明关联品种加载规则
4. `stats`
   1. 声明过去 n 天统计量加载规则
5. `history`
   1. 声明过去 n 天原始或算子数据加载规则
6. `align`
   1. 本品种与关联品种时间对齐方式
7. `session_scope`
   1. 因子默认作用于 `all/day/night`

### 9.3 关联品种参数设计

`related` 推荐支持两种方式：

#### 方式 A：从 config 自动读取

```python
related=dict(
    source="config",
    group="default",
    columns=["mid", "cvwap"],
    operators=["detailed_trade_allocation"],
)
```

含义：

1. 从 `relations.yaml` 里读取当前 symbol 的关联品种。
2. 只加载指定列和指定算子。

#### 方式 B：显式指定

```python
related=dict(
    symbols=["hc", "fe"],
    columns=["mid", "cvwap"],
    operators=["detailed_trade_allocation"],
)
```

### 9.4 历史统计量参数设计

```python
stats=dict(
    days=5,
    columns=["mid", "cvwap", "Volume"],
    metrics=["mean", "std", "skew", "kurt"],
    attach_to_current=True,
)
```

行为：

1. 从过去 5 个交易日取指定列。
2. 计算均值、方差、偏度、峰度。
3. 将结果作为当前交易日每个 tick 都相同的常量列挂到 `ctx.df` 或 `ctx.current_df` 上。

列命名建议：

1. `hist5__mid__mean`
2. `hist5__mid__std`
3. `hist5__mid__skew`
4. `hist5__mid__kurt`

如果未指定 `columns`：

1. 建议默认不加载

原因：

1. 历史统计量如果默认对全部列计算，代价太大且很容易引入大量噪声列。

### 9.5 历史天数据参数设计

```python
history=dict(
    days=3,
    columns=["mid", "cvwap", "Volume"],
    operators=["detailed_trade_allocation"],
    mode="prepend_rows",
    include_today=True,
)
```

行为：

1. 加载过去 3 个交易日 + 当前交易日。
2. 按事件时间拼接成大 df。
3. 默认在 `ctx.full_df` 中提供。
4. 若 `mode="prepend_rows"`，则装饰器传给因子函数的 `df` 直接是拼好的大 df。
5. 若 `mode="separate"`，则 `df` 只含当前日，历史数据放在 `ctx.history_df`。

推荐：

1. 默认使用 `mode="separate"`
2. 需要旧逻辑兼容时再用 `prepend_rows`

因为 `prepend_rows` 更容易让因子作者无意中把历史行和当前行混在一起。

### 9.6 装饰器使用示例

#### 示例 1：只用当前品种原始列

```python
@factor_spec(name="301", raw_columns=["b1", "a1", "Volume"])
def calc_301(df, ctx=None):
    return (df["a1"] - df["b1"]) / df["Volume"].replace(0, np.nan)
```

#### 示例 2：加载算子

```python
@factor_spec(
    name="302",
    operators=["detailed_trade_allocation"],
)
def calc_302(df, ctx=None):
    allo = ctx.operators["detailed_trade_allocation"]
    return allo["buy_qty_a1"] - allo["sell_qty_b1"]
```

#### 示例 2.1：直接在 bar 数据上写因子

```python
@factor_spec(
    name="302b",
    bar=dict(
        type="time",
        freq="3s",
    ),
    raw_columns=["open", "high", "low", "close", "Volume", "vwap"],
)
def calc_302b(df, ctx=None):
    return (df["close"] - df["open"]) / df["vwap"].replace(0, np.nan)
```

#### 示例 3：自动加载关联品种

```python
@factor_spec(
    name="303",
    raw_columns=["mid"],
    related=dict(
        source="config",
        columns=["mid"],
    ),
)
def calc_303(df, ctx=None):
    au = ctx.related["au"].df
    return df["mid"] - au["mid"]
```

#### 示例 4：加载过去 5 天统计量

```python
@factor_spec(
    name="304",
    raw_columns=["mid", "Volume"],
    stats=dict(
        days=5,
        columns=["mid", "Volume"],
        metrics=["mean", "std", "skew", "kurt"],
    ),
)
def calc_304(df, ctx=None):
    return (df["mid"] - df["hist5__mid__mean"]) / df["hist5__mid__std"].replace(0, np.nan)
```

#### 示例 5：加载历史 3 天大 df

```python
@factor_spec(
    name="305",
    raw_columns=["mid", "Volume"],
    history=dict(
        days=3,
        columns=["mid", "Volume"],
        mode="prepend_rows",
    ),
)
def calc_305(df, ctx=None):
    current_mask = ctx.meta["is_current_day_mask"]
    signal = df["mid"].rolling(300).mean()
    return signal[current_mask]
```

---

## 10. 关联品种配置设计

建议将关联品种关系从训练脚本里移出，统一放到配置文件：

`config/relations.yaml`

示例：

```yaml
default:
  rb: ["hc", "fe", "i"]
  hc: ["rb", "fe", "i"]
  ag: ["au", "SI"]
  au: ["ag", "GC"]
  al: ["ao", "AH", "zn"]
  ao: ["al", "zn"]
```

支持：

1. 多套关系分组
2. 同品种不同关系版本
3. 手动覆盖

装饰器中通过：

```python
related=dict(source="config", group="default")
```

自动读取。

---

## 11. 算子注册与缓存方案

### 11.1 算子注册表

把 `detailed_trade_allocation` 这类高复用逻辑从因子文件中抽离为注册式 operator。

建议：

```python
@register_operator(
    name="detailed_trade_allocation",
    required_columns=[
        "Volume", "Turnover", "mid",
        "b1", "b2", "b3", "b4", "b5",
        "b1_v_m", "b2_v_m", "b3_v_m", "b4_v_m", "b5_v_m",
        "a1", "a2", "a3", "a4", "a5",
        "a1_v_m", "a2_v_m", "a3_v_m", "a4_v_m", "a5_v_m",
    ],
    output_columns=[
        "sell_qty_b1", "sell_qty_b2", "sell_qty_b3", "sell_qty_b4", "sell_qty_b5",
        "buy_qty_a1", "buy_qty_a2", "buy_qty_a3", "buy_qty_a4", "buy_qty_a5",
    ],
)
def detailed_trade_allocation(df):
    ...
```

### 11.2 Operator Store 存储建议

算子建议按“算子名 + 品种 + 交易日 + session_scope”存储：

```text
store/operators/operator=detailed_trade_allocation/symbol=rb/trading_day=20250306/session_scope=all.parquet
```

好处：

1. 新增算子不影响已有算子文件。
2. 只加载需要的算子。
3. 单个算子变更时只重算自身分区。

### 11.3 何时缓存算子

建议规则：

1. 依赖于基础列、结果稳定、复用频率高的算子默认缓存。
2. 依赖很轻且只被一个因子使用的算子可以临时现场提取。

建议缓存的第一批算子：

1. `detailed_trade_allocation`
2. 后续若还有成交分配、主动买卖识别、盘口聚合矩阵，也进入 operator store。

---

## 12. Factor Store 与 Label Store 设计

### 12.1 存储格式选择

建议主存储格式使用 `parquet`，原因：

1. 比 pickle 更稳健
2. 列裁剪方便
3. 日期分区方便
4. 可以增量重写单日分区
5. 训练加载时更高效

### 12.2 Factor Store 分区建议

建议路径：

```text
store/factors/symbol=rb/trading_day=20250306/session_scope=all/factors.parquet
```

文件中包含：

1. index 对应的 `event_time`
2. 基础键字段
3. 当前已生成的全部 factor 列

### 12.3 增量追加策略

因为 parquet 不是天然列追加型数据库，所以建议采用：

1. 读取该日该品种原有 factor 分区
2. 合并新生成列
3. 只重写这个分区文件

为什么这样是可接受的：

1. 单个分区规模是“单天单品种”，不会太大
2. 比按整仓库重写简单得多
3. 比按单因子单文件在训练时再拼接更方便

### 12.4 Label Store 分区建议

```text
store/labels/symbol=rb/trading_day=20250306/session_scope=all/labels.parquet
```

label 单独存，是因为：

1. label 口径可能变
2. 因子重算和 label 重算节奏可能不同
3. 训练时可以灵活指定 label 版本

### 12.5 Manifest 元数据

建议维护：

1. `_meta/factor_manifest.parquet`
2. `_meta/label_manifest.parquet`
3. `_meta/operator_manifest.parquet`

记录：

1. 已完成的 symbol/date
2. 已写入的因子列
3. schema hash
4. factor 版本
5. operator 版本
6. 源数据 mtime

这样才能做：

1. 智能跳过
2. 智能重算
3. 增量追加

---

## 13. 数据挂载与时间对齐策略

### 13.1 关联品种的对齐方式

关联品种数据对齐建议支持三种：

1. `left`
2. `asof_backward`
3. `asof_nearest`

默认建议：

1. 高频 L2 对齐使用 `left`
2. 不同源、不同采样频率时使用 `asof_backward`

### 13.2 关联品种列命名规范

建议统一命名：

1. `mid__self`
2. `mid__hc`
3. `mid__fe`
4. `buy_qty_a1__self`
5. `buy_qty_a1__hc`

不建议继续沿用 `_x/_y` 再 rename 的方式。

### 13.3 历史统计量列命名规范

建议：

1. `hist5__mid__mean`
2. `hist5__mid__std`
3. `hist5__Volume__skew`
4. `hist5__Volume__kurt`

### 13.4 历史 prepend 的标记列

若使用 prepend 模式，建议增加：

1. `__trading_day__`
2. `__is_current_day__`
3. `__history_offset__`

这样因子函数内部就能明确知道：

1. 哪些行是历史日
2. 哪些行是当前日
3. 哪一天相对今天是 `-1`、`-2`、`-3`

---

## 14. 标签生成重构方案

### 14.1 Label 生成不再依赖全局 Config

当前 `calculate_target()` 直接读全局 `cfg.label_target`，必须改掉。

改造为：

```python
label_builder.build(df, label_config)
```

其中 `label_config` 明确包含：

1. `label_target`
2. `horizons`
3. `price_field`
4. `direction`
5. `merge_tolerance`

### 14.2 Label 构建独立化

建议新增：

1. `pipeline/build_labels.py`
2. `storage/label_store.py`

使 label 和 factor 独立构建，但共享 base panel。

### 14.3 支持版本化

标签元数据需要明确：

1. `label_name`
2. `label_version`
3. `label_target`
4. `horizons`

---

## 15. 并行与计算调度设计

### 15.1 最佳任务粒度

建议以：

1. `symbol + trading_day`

作为最自然的任务粒度。

原因：

1. 单个任务内可以复用：
   1. base panel
   2. 关联品种 panel
   3. 算子结果
   4. 历史统计量
2. 单任务输出单日分区文件，天然适合增量写入。

### 15.2 因子分组执行

每个因子在注册时都有依赖声明，因此可以按依赖签名分组。

例如：

1. 一组因子都只依赖本品种原始列。
2. 一组因子都依赖 `detailed_trade_allocation`。
3. 一组因子都依赖关联品种 `au` 的 `mid/cvwap`。

调度器可先算依赖，再在组内批量执行因子，减少重复加载。

### 15.3 多进程策略

推荐：

1. 主进程负责调度任务和元数据
2. worker 进程负责单个 `symbol + trading_day`
3. 每个 worker 内部：
   1. 先加载本日 base panel
   2. 再按需加载关联品种和算子
   3. 批量执行本日需要的因子
   4. 一次性写回 factor/label/operator 分区

### 15.4 智能跳过策略

若 manifest 显示以下条件全部满足，则跳过：

1. 分区已存在
2. 所需因子列已存在
3. 因子代码版本未变化
4. 依赖算子版本未变化
5. 原始文件 mtime 未变化

### 15.5 支持的重算模式

CLI 需要支持：

1. `--symbols rb,ag`
2. `--dates 20250301:20250331`
3. `--dates 202503`
4. `--dates latest:20`
5. `--factors 001,138,244`
6. `--exclude-factors 050,051`
7. `--rebuild-operators detailed_trade_allocation`
8. `--force`
9. `--append-only`

---

## 16. 日期选择与路径解析优化

### 16.1 日期选择器

新增统一的日期选择器：

支持表达式：

1. `20250306`
2. `20250301:20250331`
3. `202503`
4. `latest:20`
5. `changed_since:20260301`

### 16.2 路径解析器

替换当前 `utils/tools.py` 的路径正则 + 字符串 contains 逻辑。

建议：

1. 在 `catalog/parser.py` 中定义路径模板
2. 解析出：
   1. symbol
   2. contract
   3. trading_day
   4. session
   5. exchange
   6. source type

同时做：

1. 大小写规范化
2. 特殊品种映射
3. session 识别标准化

---

## 17. 训练与预测接口重构

### 17.1 训练端不再手工拼数据

重构后训练端只应做：

1. 从 factor store 读取指定日期、品种、字段
2. 从 label store 读取对应 label
3. 构造 dataset

训练脚本不再做：

1. 自己扫目录
2. 自己存文件索引 pickle
3. 自己拼朋友品种
4. 自己做历史均值方差缓存

### 17.2 历史统计量有两种用途

需要明确区分：

1. 因子构造所需的历史统计量
2. 模型标准化所需的历史统计量

建议统一由 `dataio/stats.py` 提供计算能力，但存储和消费分开：

1. 因子层使用装饰器声明的统计量
2. 模型层使用训练配置声明的标准化统计量

### 17.3 预测端

预测脚本只消费 factor store：

1. 读取所需日期和因子列
2. 读取模型
3. 输出预测结果

不再临时从 raw data 现场拼朋友品种数据。

---

## 18. 代码迁移映射

建议旧代码到新模块的映射如下：

1. `utils/tools.py`
   1. 迁入 `catalog/parser.py` 和 `core/selectors.py`
2. `utils/main_contract.py`
   1. 迁入 `catalog/main_contract.py`
3. `dataprocess/data_processor.py`
   1. 切分为 `core/calendar.py`、`dataio/base_reader.py`、`pipeline/build_labels.py`
4. `dataprocess/read_data.py`
   1. 迁入 `dataio/portal.py`
5. `gen_factor/factor_generator.py`
   1. 迁入 `factors/registry.py` 与 `pipeline/build_factors.py`
6. `get_data/data_loader.py`
   1. 迁入 `training/dataset.py`
7. `utils/save_stats.py`
   1. 迁入 `dataio/stats.py`
8. `pred.py`
   1. 改成只读 factor store 的预测入口

---

## 19. 推荐分阶段实施顺序

### 阶段 1：先把底座统一

目标：

1. 完成 catalog
2. 完成 main contract resolver
3. 完成交易时段模板
4. 完成 base panel 标准化

产物：

1. `catalog/*.py`
2. `core/calendar.py`
3. `dataio/base_reader.py`
4. `storage/prepared_store.py`

### 阶段 2：建立 DataPortal 和 Operator Store

目标：

1. 实现统一数据挂载
2. 实现算子注册和缓存
3. 优先支持 `detailed_trade_allocation`

产物：

1. `dataio/portal.py`
2. `operators/registry.py`
3. `storage/operator_store.py`

### 阶段 3：重构因子装饰器和 factor build pipeline

目标：

1. 实现 `@factor_spec`
2. 让 `sc_factor_func_0807.py` 中的因子逐步迁移到新接口
3. 支持部分因子、部分日期、部分品种重算

产物：

1. `factors/decorators.py`
2. `factors/registry.py`
3. `pipeline/build_factors.py`
4. `storage/factor_store.py`

### 阶段 4：重构 label、训练和预测消费端

目标：

1. label store 独立
2. 训练和预测只消费成品 store
3. 删除临时 pickle 旁路

产物：

1. `pipeline/build_labels.py`
2. `storage/label_store.py`
3. `training/dataset.py`

---

## 20. 对 `sc_factor_func_0807.py` 的具体建议

### 20.1 保留因子函数文件，但加入注册和装饰器

保留这个文件的原因：

1. 你已经在这里积累了大量因子。
2. 因子研发入口保持不变最符合使用习惯。

建议做法：

1. 文件顶部引入新的装饰器和 operator registry。
2. 新因子全部按新接口写。
3. 旧因子先通过兼容层运行。

### 20.2 拆出高复用算子

以下内容不应继续散落在因子文件内部：

1. `detailed_trade_allocation`
2. 未来所有复用度高的中间计算

建议迁到 `operators/`，然后在因子里通过装饰器声明。

### 20.3 因子命名与元数据

每个因子建议都带元数据：

1. `name`
2. `version`
3. `description`
4. `tags`
5. `dependencies`

用于：

1. manifest
2. 调度器
3. 重算判断

---

## 21. 关键实现细节建议

### 21.1 历史统计量计算不要在训练脚本里另存一份临时 pickle

当前 `utils/save_stats.py` 这种做法应淘汰。

改为：

1. `dataio/stats.py` 提供 `compute_rolling_daily_stats()`
2. DataPortal 按需加载
3. 必要时将统计结果按 `symbol + trading_day` 缓存为轻量 parquet

### 21.2 仅缓存高价值中间结果

建议缓存：

1. catalog
2. main contract table
3. prepared base panel 可选
4. operator data
5. factor store
6. label store

不建议缓存：

1. 大量无裁剪的原始 CSV 副本
2. 训练脚本里的各种临时拼接结果

### 21.3 列裁剪必须尽早发生

装饰器声明 `raw_columns` 后，DataPortal 在最底层读取时就要尽量裁剪列。

原则：

1. 不要先读全量、上层再筛
2. 尤其是关联品种加载时，列裁剪非常关键

### 21.4 因子执行要区分当前日输出范围

若加载了 prepend 历史 df，最终输出必须只落当前交易日对应的行。

所以 `FactorContext` 必须始终提供：

1. `current_index`
2. `current_mask`

---

## 22. 最终推荐的核心接口草案

### 22.1 因子装饰器

```python
@factor_spec(
    name="138",
    version="v1",
    raw_columns=["mid", "Volume"],
    operators=["detailed_trade_allocation"],
    related={
        "source": "config",
        "columns": ["mid"],
        "operators": [],
    },
    stats={
        "days": 5,
        "columns": ["mid", "Volume"],
        "metrics": ["mean", "std", "skew", "kurt"],
    },
    history={
        "days": 3,
        "columns": ["mid", "Volume"],
        "mode": "separate",
    },
)
def calc_138(df, ctx=None):
    allo = ctx.operators["detailed_trade_allocation"]
    return (allo["buy_qty_a1"] - allo["sell_qty_b1"]).reindex(ctx.current_index)
```

### 22.2 Pipeline 入口

```bash
python -m pipeline.runner build-factors \
  --symbols rb,ag \
  --dates 20250301:20250331 \
  --factors 001,138,244 \
  --session-scope all \
  --workers 12 \
  --append-only
```

### 22.3 DataPortal 入口

```python
ctx = portal.get_factor_context(
    symbol="rb",
    trading_day="20250306",
    factor_name="138",
)
```

这样 factor pipeline 只要知道：

1. 当前因子名
2. 当前 symbol
3. 当前 trading_day

就能自动解析全部依赖。

---

## 23. Tick 与 Bar 双分支架构补充方案

当前文档主体默认以 tick 数据为底层输入，而你补充提出：

1. 现有数据读取和写因子本质上都是基于 tick 数据。
2. 需要在整体框架中加入 bar 分支。
3. bar 既可以预先聚合并存储，也可以由装饰器现场计算并加载。
4. 需要支持时间 bar，例如 `1s`、`3s`。
5. 需要支持 volume bar。
6. volume bar 需要以时间作为 index。
7. 对于时间 bar，index 的时间 delta 相同。
8. 对于 volume bar，index 的时间 delta 不同，但每天的 index 总数需要相同。

这一补充是合理的，而且建议直接纳入总体架构，而不是作为训练脚本或单独工具脚本的旁路能力。

### 23.1 目标定位

重构后应明确支持三类基础数据层级：

1. `tick`
   1. 当前默认底层输入，典型频率为 `500ms`
2. `time_bar`
   1. 由 tick 按固定时间窗口聚合
3. `volume_bar`
   1. 由 tick 按成交量进度聚合

因此 DataPortal 与因子装饰器都必须能显式声明：

1. 当前因子是跑在 tick 上
2. 当前因子是跑在 time bar 上
3. 当前因子是跑在 volume bar 上

### 23.2 总体设计原则

建议把 tick 和 bar 设计为同一套 DataPortal 下的不同 `data_level`：

1. `data_level="tick"`
2. `data_level="bar"`

当 `data_level="bar"` 时，再通过 `bar` 参数描述具体 bar 规格：

```python
bar=dict(
    type="time",
    freq="3s",
)
```

或：

```python
bar=dict(
    type="volume",
    bars_per_day=480,
    mode="historical_profile",
    profile_days=20,
)
```

这样做的好处是：

1. factor author 不需要关心 bar 来自预存还是现场聚合。
2. tick、time bar、volume bar 共用统一的依赖声明体系。
3. 历史统计、关联品种、算子、prepend 历史数据都可以统一工作。

### 23.3 时间 Bar 设计

#### 23.3.1 聚合方式

时间 bar 建议从 canonical tick panel 聚合而来，而不是直接从原始 CSV 聚合。

聚合基础字段建议如下：

1. 价格类
   1. `open`
   2. `high`
   3. `low`
   4. `close`
2. 成交类
   1. `Volume`
   2. `Turnover`
   3. `trade_count` 可选
3. 盘口类
   1. `b1~b5`
   2. `a1~a5`
   3. `b1_v_m~b5_v_m`
   4. `a1_v_m~a5_v_m`
4. 派生类
   1. `vwap = Turnover / Volume`
   2. `mid_close`
   3. `mid_mean`
   4. `cvwap_mean` 或 `cvwap_last`
   5. `OpenInterest_last`

推荐的默认聚合规则：

1. `open`: 窗口第一条有效价格
2. `high`: 窗口内最高价
3. `low`: 窗口内最低价
4. `close`: 窗口最后一条有效价格
5. `Volume`: sum
6. `Turnover`: sum
7. `OpenInterest`: last
8. 五档盘口价量：默认 `last`
9. `mid`: 可同时保留 `first/high/low/last/mean` 中的少数必要版本，默认建议至少保留 `mid_last`

#### 23.3.2 时间 Bar index

时间 bar 的 index 统一使用 bar 结束时刻：

1. `event_time = bar_end_time`

例如 `3s` bar：

1. `09:00:03`
2. `09:00:06`
3. `09:00:09`

优势：

1. 与 tick 的“过去到当前”滚动逻辑兼容。
2. 与 label 的向前收益计算兼容。
3. 时间 delta 固定，便于序列模型处理。

#### 23.3.3 时间 Bar 存储建议

对于常用时间窗口，建议预存：

1. `1s`
2. `3s`
3. `5s`

因为这些 bar 复用度高，预存能明显减少重复计算。

存储路径建议：

```text
store/bars/bar_type=time/freq=3s/symbol=rb/trading_day=20250306/session_scope=all/bars.parquet
```

### 23.4 Volume Bar 设计

#### 23.4.1 先纠正一个关键点

如果使用最传统的“固定 volume 阈值”做 volume bar，例如每累计 `X` 手形成一根 bar，那么：

1. 每天 bar 数一般不会固定。
2. 因为每天总成交量不同，最终 bar 数会波动。

所以如果你的目标是：

1. volume bar 的时间间隔不固定
2. 但每天 index 总数固定

那么不应该使用最朴素的 fixed-threshold volume bar 作为默认方案。

更合适的方案是：

1. `fixed-count volume bars`
2. 也可以称为 `quantile-volume bars`
3. 本质上是把一天的累计成交量切分成固定数量的 volume bucket

这是比“按过去平均 volume 推一个固定阈值”更稳的方案。

#### 23.4.2 关键约束：不能同时要“严格无泄露 + 使用当天最终总量切分”

这一点必须明确写死：

1. 如果 bar 的边界依赖“当天最终总成交量”
2. 那么边界本身就使用了收盘后才知道的信息
3. 这种定义会造成信息泄露

因此：

1. 基于 `daily_total_volume` 的 `offline_quantile volume bar`
2. 只能用于事后分析、成交分布研究、可视化或 benchmark 对照
3. 不能用于训练集构造、因子生产、标签生产、回测输入和在线推理

换句话说：

1. 若要求“绝对不能信息泄露”
2. 就必须放弃用“当天真实最终总量”来切分 volume bar

#### 23.4.3 推荐默认方案：Historical-Profile Fixed-Count Volume Bars

在“绝对不能信息泄露”的前提下，如果还要求：

1. 每天 bar 根数固定
2. index 仍然是时间
3. bar 的疏密体现 volume clock

那么更好的默认方案应为：

1. `historical_profile` 模式
2. 也可称为 `volume-time profile bars`

核心思想：

1. 不用当天收盘后的真实总量
2. 只用过去 `n` 天的历史数据
3. 估计该品种该 session 的历史平均累计成交量曲线
4. 将固定的 volume 分位点映射成“预先已知的时间边界”
5. 当天开盘前就把整天的 bar 时间边界全部确定下来

构造步骤：

1. 取过去 `profile_days` 个交易日
2. 对每一天计算日内累计成交量占比曲线：
   1. `cum_volume(t) / daily_total_volume`
3. 对这些曲线做稳健平均：
   1. 推荐 `median` 或 `trimmed_mean`
4. 得到历史平均累计成交量曲线 `C_hat(t)`
5. 设定固定 bar 数 `K = bars_per_day`
6. 对每个 `k=1..K`，求解时间边界：
   1. `t_k = C_hat^{-1}(k / K)`
7. 将当天 tick 按预先确定的时间区间 `[t_{k-1}, t_k]` 聚合为第 `k` 根 bar

这套方案的性质：

1. 每天 bar 数固定
2. 边界在开盘前可确定
3. 完全不依赖当天未来成交量
4. 没有信息泄露
5. index 仍然是时间
6. 相邻 bar 的时间 delta 一般不同

它的本质不是“真实等成交量 bar”，而是：

1. 用历史 volume profile 构造的固定根数 volume-clock bars

这是在“固定根数”和“严格无泄露”之间最合理的取舍。

建议配置参数：

```python
bar=dict(
    type="volume",
    mode="historical_profile",
    bars_per_day=480,
    profile_days=20,
    profile_estimator="median",
)
```

#### 23.4.4 可选增强方案：Causal Online Adaptive Volume Bars

如果希望 bar 更贴近当天真实成交节奏，而不仅仅依赖开盘前历史模板，可以增加一个更高级但仍然严格因果的版本：

1. `online_adaptive_profile`

其思想是：

1. 开盘前先用历史 profile 给出初始边界
2. 盘中只利用当前时刻以前已经发生的成交量
3. 动态更新“剩余时段的边界时间预测”
4. 但绝不修改已经完成的 bar
5. 绝不调用未来时刻信息

可用的在线更新信息包括：

1. 当前累计成交量
2. 当前时刻相对历史 profile 的偏离程度
3. 历史同星期几、同主力切换阶段、同波动 regime 的 volume profile

这个模式更接近在线可交易系统，但实现复杂度较高。

因此建议：

1. 第一版不默认实现
2. 先实现 `historical_profile`
3. 后续再扩展 `online_adaptive_profile`

#### 23.4.4 Volume Bar index 设计

volume bar 依然使用时间作为 index，但采用 bar 结束时刻：

1. `event_time = 当前 bar 完成切分点时对应的最后一个 tick 时间`

因此：

1. 相邻 bar 的时间 delta 不固定
2. 每天 bar 的数量固定

这与你的要求是一致的。

#### 23.4.5 Volume Bar 聚合字段

volume bar 的 OHLCV 聚合与时间 bar 基本一致：

1. `open/high/low/close`
2. `Volume`
3. `Turnover`
4. `vwap`
5. `mid_last`
6. `OpenInterest_last`
7. 五档盘口最后状态
8. bar 内部统计
   1. `duration_ms`
   2. `tick_count`
   3. `volume_imbalance`
   4. `turnover_per_ms`

建议额外保留：

1. `bar_id_in_day`
2. `target_volume_share`
3. `realized_volume_share`
4. `profile_boundary_start`
5. `profile_boundary_end`

这样便于研究 volume bar 本身的稳定性。

#### 23.4.6 Volume Bar 存储建议

路径建议：

```text
store/bars/bar_type=volume/mode=historical_profile/bars_per_day=480/profile_days=20/symbol=rb/trading_day=20250306/session_scope=all/bars.parquet
```

对于使用频率很高的 volume bar 规格，可以预存。

另外建议明确区分两类存储：

1. `mode=historical_profile`
   1. 可用于训练、因子、标签、回测、预测
2. `mode=offline_quantile`
   1. 仅用于研究分析
   2. 默认禁止进入训练和生产 pipeline

### 23.5 Tick / Bar 在 DataPortal 中的统一接口

建议将 `get_panel()` 扩展为：

```python
portal.get_panel(
    symbol="rb",
    trading_day="20250306",
    session_scope="all",
    data_level="tick" | "bar",
    bar=None | {
        "type": "time" | "volume",
        ...
    },
    columns=[...],
    operators=[...],
    related={...},
    stats={...},
    history={...},
)
```

核心逻辑：

1. 若 `data_level="tick"`，直接取 canonical tick panel。
2. 若 `data_level="bar"`：
   1. 先检查 bar store 中是否已有对应规格
   2. 有则直接加载
   3. 没有则根据策略：
      1. 现场从 tick 聚合
      2. 并可选择写回缓存

### 23.6 Tick / Bar 在装饰器中的统一声明

建议在 `@factor_spec` 中正式加入 `bar` 参数：

```python
@factor_spec(
    name="401",
    bar=dict(
        type="time",
        freq="3s",
    ),
    raw_columns=["open", "high", "low", "close", "Volume", "vwap"],
)
def calc_401(df, ctx=None):
    return (df["close"] / df["open"] - 1)
```

volume bar 示例：

```python
@factor_spec(
    name="402",
    bar=dict(
        type="volume",
        mode="historical_profile",
        bars_per_day=480,
        profile_days=20,
    ),
    raw_columns=["close", "Volume", "duration_ms"],
)
def calc_402(df, ctx=None):
    return df["close"].pct_change() / df["duration_ms"].replace(0, np.nan)
```

### 23.7 历史统计量与历史 prepend 在 Bar 上的行为

bar 分支中，`stats` 和 `history` 也必须继续可用，但要明确计算层级：

#### 方案 A：在 tick 层先统计，再挂到 bar

适合：

1. 希望 bar 因子仍然引用 tick 口径历史统计

#### 方案 B：在 bar 层重新统计

适合：

1. bar 模型完全以 bar 口径建模

建议配置中加入：

```python
stats=dict(
    days=5,
    columns=["close", "Volume"],
    metrics=["mean", "std"],
    source_level="bar",
)
```

和：

```python
history=dict(
    days=3,
    columns=["close", "Volume"],
    mode="prepend_rows",
    source_level="bar",
)
```

默认建议：

1. tick 因子使用 tick 统计
2. bar 因子使用同层级 bar 统计

### 23.8 Bar 与 Label 的关系

label 仍建议优先在 tick 标准层生成，然后再按需要映射到 bar：

1. 对时间 bar：
   1. 可直接在 bar close 时刻对齐未来收益
2. 对 volume bar：
   1. 用 bar 结束时刻去映射未来收益

如果某些模型完全在 bar 层运行，也可以额外构建 bar-level label store。

建议 label 配置中增加：

1. `label_source_level = tick | bar`
2. `label_alignment = close_time`

### 23.9 预存还是现场聚合

建议规则如下：

#### 预存

适合：

1. 高频复用的 `1s/3s/5s` time bar
2. 高频复用的某一个或两个 `historical_profile` volume bar 规格

#### 现场聚合

适合：

1. 实验性 bar 规格
2. 低复用 research-only 场景
3. 临时测试某个 `bars_per_day`

推荐 DataPortal 支持参数：

1. `cache_policy="use_or_build"`
2. `cache_policy="build_and_store"`
3. `cache_policy="memory_only"`

对于 `offline_quantile`，建议只允许：

1. `cache_policy="memory_only"`
2. 或单独写入 research store

不允许进入生产 factor store 依赖链。

### 23.10 对每天 index 数固定的实现建议

这一点必须明确：

1. 时间 bar 要每天 index 数固定，只要交易时段模板和 bar 频率固定即可天然满足。
2. volume bar 要每天 index 数固定，且不能泄露未来信息，必须使用“固定日内 bar 数 + 预先确定边界”的构造方式。

因此推荐：

1. 时间 bar:
   1. `freq = 1s / 3s / 5s`
2. volume bar:
   1. `bars_per_day = 240 / 360 / 480 / 600`
   2. 默认模式使用 `historical_profile`

不建议默认采用：

1. 固定 volume 阈值 bar

因为这无法保证每天根数一致。

严格禁止用于训练主链的方式：

1. 使用当天真实最终总量切分的 `offline_quantile`

### 23.11 推荐新增的目录与存储层

建议在目录结构中增加：

```text
storage/
  bar_store.py
```

并增加分区：

```text
store/bars/
  bar_type=time/freq=1s/...
  bar_type=time/freq=3s/...
  bar_type=volume/mode=historical_profile/bars_per_day=480/profile_days=20/...
  research/bar_type=volume/mode=offline_quantile/bars_per_day=480/...
```

### 23.12 实施顺序建议

bar 分支建议放在主线重构完成后的“2.5 阶段”插入：

1. 先完成 tick canonical base panel
2. 再基于 tick 构建 time bar
3. 再基于 tick 构建 volume bar
4. 最后把 `bar` 参数接入装饰器和 DataPortal

具体优先顺序：

1. 先做 `time bar`
2. 再做 `historical_profile volume bar`
3. 最后再做 `online_adaptive_profile volume bar`

原因：

1. time bar 实现最稳
2. `historical_profile volume bar` 同时满足“固定根数”和“严格无泄露”
3. `online_adaptive_profile volume bar` 更复杂，适合第二阶段扩展
4. `offline_quantile volume bar` 只能做事后研究，不应进入主生产线

---

## 24. 结论

这次重构的核心不是“改几个函数”，而是把当前框架从“脚本堆叠式工程”变成“声明式数据门户 + 算子缓存 + 因子注册 + 分区存储”的生产架构。

最重要的几个落点是：

1. 用 `DataPortal` 统一数据挂载。
2. 用 `@factor_spec` 让因子函数显式声明依赖。
3. 用 `operator store` 缓存 `detailed_trade_allocation` 等高复用算子。
4. 用 `catalog + main_contract table` 替代当前脆弱的路径解析和 JSON set 匹配。
5. 用统一的 `event_time + trading_day` 体系处理日盘夜盘。
6. 用按日分区的 parquet factor/label store 支持追加和部分重算。
7. 将 tick、time bar、volume bar 纳入同一套 DataPortal 与装饰器依赖体系。
8. 将无泄露的 `historical_profile volume bar` 作为 volume 分支默认方案。
8. 让训练和预测只消费成品 store，不再自行补架构漏洞。

如果按实施优先级排序，建议先做：

1. catalog + main contract resolver
2. trading calendar + base panel
3. DataPortal + operator cache
4. time bar + historical_profile volume bar
5. factor decorator + factor store
6. label store + training/prediction adapter

这个顺序最稳，也最容易边迁移边验证。
