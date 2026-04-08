# Catalog 层详细设计

## 1. 目标

这一份文档只展开 `Catalog` 层，不再讨论整个重构全景。

Catalog 层的目标不是“读取 CSV 数据本身”，而是先把整个数据仓库中有哪些文件、这些文件分别代表什么交易对象、它们和主力合约/关联品种/历史日期的关系是什么，做成一套稳定、可查询、可缓存的索引系统。

一句话概括：

1. `Catalog` 解决“有哪些文件、每个文件是谁、后续该读谁”的问题。
2. `DataPortal` 解决“真正把这些文件读成 DataFrame，并补齐关联品种/历史/算子”的问题。

所以 Catalog 是后续一切加载逻辑的上游入口。

---

## 2. Catalog 层的职责边界

Catalog 层负责：

1. 扫描目录，发现原始数据文件、bar 文件、factor 文件、label 文件、operator 文件。
2. 从路径和文件名中结构化解析：
   1. `symbol`
   2. `contract`
   3. `trading_day`
   4. `session`
   5. `file_type`
   6. `path`
3. 对文件做轻量探测，补充元数据：
   1. `mtime`
   2. `size`
   3. `columns`
   4. `row_count` 可选
   5. `first_event_time` / `last_event_time` 可选
4. 构建可持久化的 catalog 表。
5. 通过主力合约映射、关联品种映射、交易日历，把“当前任务需要加载哪些文件”转成一个结构化的 `LoadPlan`。

Catalog 层不负责：

1. 读取整份 CSV 并做标准化处理。
2. 生成 tick/base/bar DataFrame。
3. 计算算子。
4. 计算因子。
5. 计算 label。

也就是说：

1. Catalog 给的是“文件级索引 + 加载计划”
2. DataPortal 才真正执行“数据读取 + 对齐 + 拼接”

---

## 3. Catalog 层和当前仓库的直接关系

当前仓库里最接近 Catalog 职责的代码其实分散在：

1. `utils/tools.py`
2. `utils/main_contract.py`
3. `dataprocess/read_data.py`
4. `utils/save_fl_pkl.py`

目前这些代码的问题是：

1. 目录扫描和路径解析每次都重做。
2. 文件信息只有临时 tuple，没有 schema。
3. 主力合约过滤依赖 JSON + set 包含判断，过于脆弱。
4. 没有统一查询层，只能靠脚本临时组合。
5. 没有一个“把当前文件所需其他文件找出来”的中心模块。

所以 Catalog 层的重构，本质上是把这些零散能力收拢成统一的可查询系统。

---

## 4. Catalog 层推荐子模块

第一版代码骨架按当前要求采用“合并版”结构：

```text
catalog/
  parser.py
  catalog_builder.py
  catalog_store.py
  main_contract.py
  resolver.py
```

其中：

1. `models.py` 已并入 `parser.py`
2. `probes.py` 已并入 `catalog_builder.py`

后续如果 Catalog 继续膨胀，再按需要拆回更细的模块。

下面逐个讲。

---

## 5. `catalog/parser.py` 中的数据结构部分

### 5.1 作用

当前实现里，`parser.py` 同时负责：

1. 路径解析
2. Catalog 层核心数据结构定义

这样做是为了先把第一版骨架压缩到最少文件数，避免过早拆分。

建议至少定义以下结构：

1. `CatalogRecord`
2. `PartitionKey`
3. `FileProbeInfo`
4. `LoadRequest`
5. `LoadPlan`

### 5.2 为什么要单独有 models

现在仓库里大量逻辑默认 `file_info` 是一个 7 元组：

```python
(symbol, contract, date, year, month, session, path)
```

问题是：

1. 可读性差。
2. 字段顺序一旦变动，所有调用都要一起变。
3. 不能优雅扩展，比如加 `file_type`、`exchange`、`mtime`、`session_scope`。

所以需要正式结构。

### 5.3 推荐代码骨架

```python
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Literal

FileType = Literal["raw_csv", "prepared_tick", "time_bar", "volume_bar", "factor", "label", "operator"]
SessionType = Literal["day", "night", "all"]


@dataclass(frozen=True)
class PartitionKey:
    symbol: str
    trading_day: str
    session: SessionType
    contract: Optional[str] = None
    data_level: Optional[str] = None
    bar_spec: Optional[str] = None


@dataclass(frozen=True)
class CatalogRecord:
    file_type: FileType
    symbol: str
    contract: Optional[str]
    trading_day: str
    session: SessionType
    year: Optional[str]
    month: Optional[str]
    path: Path
    exchange: Optional[str] = None
    source: Optional[str] = None
    mtime_ns: Optional[int] = None
    size_bytes: Optional[int] = None


@dataclass(frozen=True)
class FileProbeInfo:
    columns: tuple[str, ...] = ()
    row_count: Optional[int] = None
    first_event_time: Optional[str] = None
    last_event_time: Optional[str] = None
    instrument_id: Optional[str] = None


@dataclass(frozen=True)
class LoadRequest:
    symbol: str
    trading_day: str
    session_scope: SessionType
    data_level: str
    bar_spec: Optional[dict] = None
    raw_columns: Optional[list[str]] = None
    related_symbols: Optional[list[str]] = None
    history_days: int = 0
    operators: Optional[list[str]] = None


@dataclass
class LoadPlan:
    current: list[CatalogRecord] = field(default_factory=list)
    related: dict[str, list[CatalogRecord]] = field(default_factory=dict)
    history: dict[str, list[CatalogRecord]] = field(default_factory=dict)
    operators: dict[str, PartitionKey] = field(default_factory=dict)
    meta: dict = field(default_factory=dict)
```

### 5.4 它后续怎么被串起来

1. `parser.py` 负责构造 `CatalogRecord`
2. `catalog_builder.py` 扫描所有路径后生成一批 `CatalogRecord`
3. `catalog_store.py` 持久化这些 record
4. `resolver.py` 根据 `LoadRequest` 查询 catalog，并输出 `LoadPlan`
5. `DataPortal` 接受 `LoadPlan`，真正去加载数据

---

## 6. `catalog/parser.py` 中的路径解析部分

### 6.1 作用

这个模块负责把“物理路径”解析成“结构化文件记录”。

针对当前仓库，最重要的是解析原始 CSV 路径。

当前实际样例：

```text
data/au/day/04/au2506_20250401.csv
data/au/night/04/au2506_20250401.csv
```

因此 parser 需要做到：

1. 根据路径模板判断文件类型。
2. 提取 `symbol`
3. 提取 `session`
4. 提取 `contract`
5. 提取 `trading_day`
6. 输出标准化字段

### 6.2 为什么单独拆 parser

因为路径解析是一个会不断扩展的能力：

1. 后面不只会解析 raw csv
2. 还要解析 factor store、label store、operator store、bar store

所以路径解析不能继续散落在 `utils/tools.py` 里做字符串匹配。

### 6.3 推荐接口

```python
class PathParser:
    def parse(self, path: Path) -> CatalogRecord | None:
        ...
```

或：

```python
def parse_path(path: Path) -> CatalogRecord | None:
    ...
```

### 6.4 推荐实现骨架

```python
import re
from pathlib import Path
from catalog.models import CatalogRecord


RAW_CSV_PATTERN = re.compile(
    r"(?P<contract>[A-Za-z]+\d+)_(?P<trading_day>\d{8})\.csv$"
)


def parse_raw_csv(path: Path) -> CatalogRecord | None:
    parts = [p for p in path.parts]
    # 期望结构: data / symbol / session / month / filename.csv
    if len(parts) < 5:
        return None

    symbol = parts[-4]
    session = parts[-3]
    month = parts[-2]
    filename = parts[-1]

    m = RAW_CSV_PATTERN.search(filename)
    if not m:
        return None

    contract = m.group("contract")
    trading_day = m.group("trading_day")

    return CatalogRecord(
        file_type="raw_csv",
        symbol=symbol.lower(),
        contract=contract,
        trading_day=trading_day,
        session=session,
        year=trading_day[:4],
        month=trading_day[4:6],
        path=path,
        source="raw_data",
    )
```

### 6.5 parser 未来需要支持哪些文件

至少支持：

1. `raw_csv`
2. `prepared_tick`
3. `time_bar`
4. `volume_bar`
5. `factor`
6. `label`
7. `operator`

建议每一种文件类型单独一个解析函数：

1. `parse_raw_csv()`
2. `parse_prepared_tick()`
3. `parse_bar_file()`
4. `parse_factor_file()`
5. `parse_label_file()`
6. `parse_operator_file()`

然后统一在一个调度器里调用：

```python
def parse_path(path: Path) -> CatalogRecord | None:
    for fn in PARSERS:
        record = fn(path)
        if record is not None:
            return record
    return None
```

---

## 7. `catalog/catalog_builder.py` 中的轻量探测部分

### 7.1 作用

当前实现里，轻量探测已经并入 `catalog_builder.py`，不再单独放一个 `probes.py`。

它负责对文件做轻量探测，而不是完整读取。

Catalog 层不该在构建阶段就把所有 CSV 全读进内存，但仍然可以探测一些很关键的信息：

1. 列名
2. 行数
3. 是否存在 `Date` / `ActionDay`
4. 是否存在 `UpdateMillisec` / `UpdateNanosec`
5. 第一行时间、最后一行时间 可选

### 7.2 为什么需要 probe

因为后续会有两类查询依赖这些信息：

1. 这份文件能否被某个 reader 正确处理
2. 某些数据源是不是缺失关键字段

### 7.3 轻量探测策略

建议分两档：

#### 档位 A：默认 probe

只读取：

1. 文件 stat 信息
2. 表头列名

速度最快，作为常规 catalog rebuild 的默认方式。

#### 档位 B：deep probe

额外读取：

1. 若干行样本
2. 行数
3. 第一条时间
4. 最后一条时间

只在：

1. 第一次建库
2. 调试数据源问题
3. 主力重算或异常检查

时启用。

### 7.4 推荐代码骨架

```python
from pathlib import Path
import pandas as pd
from catalog.models import FileProbeInfo


def probe_csv_header(path: Path) -> FileProbeInfo:
    df = pd.read_csv(path, nrows=0)
    return FileProbeInfo(columns=tuple(df.columns))


def probe_csv_sample(path: Path) -> FileProbeInfo:
    sample = pd.read_csv(path, nrows=5)
    return FileProbeInfo(
        columns=tuple(sample.columns),
        instrument_id=sample["InstrumentID"].iloc[0] if "InstrumentID" in sample.columns and not sample.empty else None,
    )
```

### 7.5 注意

Catalog 构建默认不建议做昂贵 probe，例如：

1. 完整统计 `Volume.sum()`
2. 完整统计 `OpenInterest.last()`

这些更适合单独的 summary pipeline，或主力合约重建 pipeline。

---

## 8. `catalog/catalog_builder.py`

### 8.1 作用

Builder 是 Catalog 层的实际构建器。

它负责：

1. 遍历根目录
2. 收集所有候选文件
3. 调用 parser 解析
4. 调用 probe 补充元数据
5. 最终产出一张 catalog 表

### 8.2 推荐接口

```python
class CatalogBuilder:
    def __init__(self, roots: dict[str, Path], parser: PathParser, probe_level: str = "header"):
        ...

    def build(self) -> pd.DataFrame:
        ...
```

### 8.3 roots 应该怎么定义

建议配置中明确：

```python
catalog_roots = {
    "raw_csv": Path("./data"),
    "factor": Path("./store/factors"),
    "label": Path("./store/labels"),
    "operator": Path("./store/operators"),
    "bars": Path("./store/bars"),
}
```

当前阶段先只扫 `./data` 就可以。

### 8.4 推荐代码骨架

```python
import pandas as pd
from pathlib import Path


class CatalogBuilder:
    def __init__(self, roots, parse_path, probe_fn=None):
        self.roots = roots
        self.parse_path = parse_path
        self.probe_fn = probe_fn

    def iter_files(self):
        for _, root in self.roots.items():
            if not root.exists():
                continue
            for path in root.rglob("*"):
                if path.is_file():
                    yield path

    def build(self) -> pd.DataFrame:
        rows = []
        for path in self.iter_files():
            record = self.parse_path(path)
            if record is None:
                continue

            row = record.__dict__.copy()
            stat = path.stat()
            row["mtime_ns"] = stat.st_mtime_ns
            row["size_bytes"] = stat.st_size

            if self.probe_fn is not None and record.file_type == "raw_csv":
                probe = self.probe_fn(path)
                row["columns"] = list(probe.columns)
                row["instrument_id"] = probe.instrument_id

            rows.append(row)

        df = pd.DataFrame(rows)
        if not df.empty:
            df = df.sort_values(["file_type", "symbol", "trading_day", "session", "contract"])
        return df
```

### 8.5 产出表的字段建议

Catalog 表建议至少有：

1. `file_type`
2. `symbol`
3. `contract`
4. `trading_day`
5. `session`
6. `year`
7. `month`
8. `path`
9. `source`
10. `mtime_ns`
11. `size_bytes`
12. `columns`
13. `instrument_id`

---

## 9. `catalog/catalog_store.py`

### 9.1 作用

Store 负责 Catalog 表的持久化、读取和增量刷新。

它是后续所有查询的基础。

### 9.2 为什么要单独有 Store

因为 Catalog 一旦建立，后续不能每次都重新扫描目录。

我们希望：

1. 大多数时候直接从 `_meta/catalog.parquet` 读取
2. 只有文件变动时才增量更新

### 9.3 推荐接口

```python
class CatalogStore:
    def __init__(self, path: Path):
        self.path = path

    def save(self, df: pd.DataFrame) -> None:
        ...

    def load(self) -> pd.DataFrame:
        ...

    def exists(self) -> bool:
        ...
```

### 9.4 推荐代码骨架

```python
from pathlib import Path
import pandas as pd


class CatalogStore:
    def __init__(self, path: Path):
        self.path = path

    def exists(self) -> bool:
        return self.path.exists()

    def save(self, df: pd.DataFrame) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(self.path, index=False)

    def load(self) -> pd.DataFrame:
        if not self.exists():
            return pd.DataFrame()
        return pd.read_parquet(self.path)
```

### 9.5 推荐保存位置

```text
_meta/catalog.parquet
```

可以额外再保存一个 schema/meta 文件：

```text
_meta/catalog_meta.json
```

记录：

1. build_time
2. version
3. roots
4. parser_version
5. probe_level

### 9.6 增量更新怎么做

第一版建议先简单：

1. 全量 rebuild

第二版再做增量：

1. 比较路径集合
2. 比较 `mtime_ns`
3. 只更新新增或变更文件

---

## 10. `catalog/main_contract.py`

### 10.1 作用

这个模块负责主力合约的读写、构建和查询。

它和 catalog 是紧密相关的，但不应该和 parser/store 混在一起。

### 10.2 主力合约数据来源

当前仓库已有：

1. `config/main_contracts*.json`

后续建议支持两条路：

#### 路线 A：读取已有主力映射

即沿用你已经准备好的主力表，作为主数据源。

#### 路线 B：从 summary/csv 探测数据中重建

后续若需要，也可以从目录中的 summary 或轻量统计表重建主力表。

### 10.3 推荐的主力表格式

建议读入后统一转成平表：

1. `trading_day`
2. `symbol`
3. `session`
4. `main_contract`

### 10.4 推荐代码骨架

```python
import json
import pandas as pd
from pathlib import Path


class MainContractStore:
    def __init__(self, path: Path):
        self.path = path

    def load_json(self) -> dict:
        with open(self.path, "r", encoding="utf-8") as f:
            return json.load(f)

    def load_table(self) -> pd.DataFrame:
        raw = self.load_json()
        rows = []
        for trading_day, day_info in raw.items():
            for symbol, symbol_info in day_info.items():
                if isinstance(symbol_info, dict):
                    for session, contract in symbol_info.items():
                        rows.append({
                            "trading_day": trading_day,
                            "symbol": symbol.lower(),
                            "session": session,
                            "main_contract": contract,
                        })
                else:
                    rows.append({
                        "trading_day": trading_day,
                        "symbol": symbol.lower(),
                        "session": "all",
                        "main_contract": symbol_info,
                    })
        return pd.DataFrame(rows)
```

### 10.5 主力查询器

Catalog 层需要一个稳定接口：

```python
class MainContractResolver:
    def __init__(self, table: pd.DataFrame):
        self.table = table

    def get_main_contract(self, symbol: str, trading_day: str, session: str) -> str | None:
        ...
```

推荐实现：

1. 先查 `(trading_day, symbol, session)`
2. 若没有，再查 `(trading_day, symbol, all)`
3. 若还没有，再查最近过去一个有效交易日

### 10.6 它怎么被后续串起来

后续 `resolver.py` 在生成当前任务的 `LoadPlan` 时，必须先问主力查询器：

1. 当前 symbol 当前交易日该读哪个 contract
2. 关联 symbol 当前交易日该读哪个 contract
3. 历史日期该 symbol 当天该读哪个 contract

这就是主力模块和 Catalog 的连接点。

---

## 11. `catalog/resolver.py`

### 11.1 作用

这是 Catalog 层里最关键的模块。

它不直接读文件，而是根据：

1. 当前任务
2. Catalog 表
3. 主力合约表
4. 关联品种配置
5. 历史日期请求
6. 算子请求

生成一个结构化的 `LoadPlan`。

它是 Catalog 和 DataPortal 之间的桥梁。

### 11.2 为什么必须有 resolver

因为你后续最常见的问题不再是“把一个文件读出来”，而是：

1. 当前 `rb` 在 `20250306` 该读哪份文件
2. 它的关联品种 `hc/fe` 该读哪份文件
3. 过去 5 天的 `rb` 各自该读哪份文件
4. `detailed_trade_allocation` 是否已有缓存，若有读哪份 operator 文件，若没有应该去哪里生成

这些都不是 parser/store 能解决的，这正是 resolver 的任务。

### 11.3 推荐接口

```python
class CatalogResolver:
    def __init__(self, catalog_df, main_contract_resolver, relations_map, trading_calendar):
        ...

    def resolve(self, request: LoadRequest) -> LoadPlan:
        ...
```

### 11.4 核心子方法建议

建议拆成这些方法：

1. `resolve_current_files()`
2. `resolve_related_files()`
3. `resolve_history_files()`
4. `resolve_operator_keys()`

### 11.5 当前文件解析逻辑

当前请求例如：

```python
LoadRequest(
    symbol="rb",
    trading_day="20250306",
    session_scope="all",
    data_level="tick",
    related_symbols=["hc", "fe"],
    history_days=3,
    operators=["detailed_trade_allocation"],
)
```

resolver 应做：

1. 查主力表得到：
   1. `rb` 在 `20250306` 的 `day` 主力合约
   2. `rb` 在 `20250306` 的 `night` 主力合约
2. 在 catalog 表中过滤出对应 raw csv 记录
3. 作为 `LoadPlan.current`

### 11.6 关联品种解析逻辑

若配置里 `rb -> [hc, fe]`

则 resolver 应对每个关联品种做：

1. 先查该品种该交易日的主力合约
2. 再在 catalog 里找到对应 `day/night` 文件
3. 放到 `LoadPlan.related["hc"]`、`LoadPlan.related["fe"]`

### 11.7 历史日期解析逻辑

若当前请求 `history_days=3`

则 resolver 需要先根据交易日历拿到：

1. `20250305`
2. `20250304`
3. `20250303`

然后对每个历史日：

1. 查 `rb` 当天主力
2. 找 catalog 中对应 raw csv
3. 放到 `LoadPlan.history`

注意：

1. 历史主力不一定和当前日主力相同
2. 所以不能简单地把当前 contract 往前套

### 11.8 算子解析逻辑

resolver 对算子不直接返回“原始 CSV 文件”，而是返回 operator 的分区键。

例如：

```python
operators = {
    "detailed_trade_allocation": PartitionKey(
        symbol="rb",
        trading_day="20250306",
        session="all",
        contract="rb2505",
        data_level="tick",
    )
}
```

意思是：

1. DataPortal 稍后可以先去 operator store 查这个 key
2. 若找不到，再用 current tick 数据现场计算

### 11.9 推荐代码骨架

```python
class CatalogResolver:
    def __init__(self, catalog_df, main_contract_resolver, relations_map, calendar):
        self.catalog_df = catalog_df
        self.main_contract_resolver = main_contract_resolver
        self.relations_map = relations_map
        self.calendar = calendar

    def _find_raw_files(self, symbol, trading_day, session, contract):
        df = self.catalog_df
        mask = (
            (df["file_type"] == "raw_csv") &
            (df["symbol"] == symbol) &
            (df["trading_day"] == trading_day) &
            (df["session"] == session) &
            (df["contract"] == contract)
        )
        rows = df.loc[mask]
        return [CatalogRecord(**row.to_dict()) for _, row in rows.iterrows()]

    def resolve(self, request: LoadRequest) -> LoadPlan:
        plan = LoadPlan()

        sessions = ["day", "night"] if request.session_scope == "all" else [request.session_scope]

        for session in sessions:
            contract = self.main_contract_resolver.get_main_contract(
                request.symbol, request.trading_day, session
            )
            plan.current.extend(
                self._find_raw_files(request.symbol, request.trading_day, session, contract)
            )

        related_symbols = request.related_symbols or self.relations_map.get(request.symbol, [])
        for rs in related_symbols:
            plan.related[rs] = []
            for session in sessions:
                contract = self.main_contract_resolver.get_main_contract(rs, request.trading_day, session)
                plan.related[rs].extend(
                    self._find_raw_files(rs, request.trading_day, session, contract)
                )

        if request.history_days > 0:
            prev_days = self.calendar.prev_n_trading_days(request.trading_day, request.history_days)
            for day in prev_days:
                plan.history[day] = []
                for session in sessions:
                    contract = self.main_contract_resolver.get_main_contract(
                        request.symbol, day, session
                    )
                    plan.history[day].extend(
                        self._find_raw_files(request.symbol, day, session, contract)
                    )

        for op_name in request.operators or []:
            plan.operators[op_name] = PartitionKey(
                symbol=request.symbol,
                trading_day=request.trading_day,
                session=request.session_scope,
                contract=None,
                data_level=request.data_level,
            )

        plan.meta["request"] = request
        return plan
```

---

## 12. Catalog 表本身怎么保存和读取

### 12.1 保存什么

Catalog 的持久化产物建议至少包括：

1. `_meta/catalog.parquet`
2. `_meta/main_contracts.parquet`
3. `_meta/catalog_meta.json`

### 12.2 保存代码

```python
builder = CatalogBuilder(
    roots={"raw_csv": Path("./data")},
    parse_path=parse_path,
    probe_fn=probe_csv_header,
)
catalog_df = builder.build()

CatalogStore(Path("./_meta/catalog.parquet")).save(catalog_df)

mc_df = MainContractStore(Path("./config/main_contracts_cvwaprb1124.json")).load_table()
mc_df.to_parquet("./_meta/main_contracts.parquet", index=False)
```

### 12.3 读取代码

```python
catalog_df = CatalogStore(Path("./_meta/catalog.parquet")).load()
mc_df = pd.read_parquet("./_meta/main_contracts.parquet")

resolver = CatalogResolver(
    catalog_df=catalog_df,
    main_contract_resolver=MainContractResolver(mc_df),
    relations_map=relations_map,
    calendar=trading_calendar,
)
```

---

## 13. Catalog 层如何串联后续 DataPortal

这里是你最关心的重点。

Catalog 层本身不读 DataFrame，但它要把“当前任务需要的所有文件和分区”描述完整，交给 DataPortal。

### 13.1 串联关系总图

建议链路如下：

```text
FactorSpec / BuildTask
    -> LoadRequest
    -> CatalogResolver.resolve()
    -> LoadPlan
    -> DataPortal.load_from_plan()
    -> FactorContext
    -> calc_xxx(df, ctx)
```

### 13.2 DataPortal 会收到什么

DataPortal 接收 `LoadPlan` 后，按下面顺序工作：

1. 读取 `plan.current` 对应的当前文件
2. 标准化为 current tick/base panel
3. 读取 `plan.related` 对应的关联品种文件
4. 读取 `plan.history` 对应的历史文件
5. 读取或计算 `plan.operators`
6. 把这些对象拼成 `FactorContext`

### 13.3 这意味着 Catalog 层必须输出什么信息

Catalog 层至少要保证 `LoadPlan` 里有：

1. 当前文件物理路径
2. 关联品种文件物理路径
3. 历史日期文件物理路径
4. 当前主力与历史主力映射结果
5. 算子缓存对应的逻辑键

只要这些键和路径准备完整，DataPortal 才能稳定运行。

---

## 14. 如何根据当前数据文件加载关联数据

你问的这个问题，本质上是：

1. 当前任务已经知道要算 `rb` 在 `20250306`
2. 那怎么找到 `hc`、`fe` 这些关联文件

Catalog 层建议这样做：

### 14.1 当前任务只有逻辑键，不直接拿物理路径

例如：

```python
request = LoadRequest(
    symbol="rb",
    trading_day="20250306",
    session_scope="all",
    data_level="tick",
    related_symbols=None,
)
```

### 14.2 resolver 先查关系配置

若 `related_symbols=None`，则：

1. 去 `relations.yaml` 里查 `rb`
2. 得到默认关联：
   1. `hc`
   2. `fe`
   3. `i`

### 14.3 resolver 再查主力

对每个关联品种：

1. 查 `20250306` 的 `day/night` 主力
2. 得到：
   1. `hc2505`
   2. `fe2504`

### 14.4 resolver 再查 catalog

最后再根据：

1. `symbol`
2. `trading_day`
3. `session`
4. `contract`

从 catalog 里得到物理文件路径。

这比当前“从训练脚本手工拼 friends_groups 再 merge 文件”要可靠得多。

---

## 15. 如何根据当前数据文件加载过去的数据

### 15.1 历史加载不是按自然日，而是按交易日历

不能简单做：

1. `trading_day - 1`

因为会遇到：

1. 周末
2. 节假日
3. 夜盘跨天
4. 某些数据缺失

所以 Catalog 层要依赖交易日历组件：

```python
prev_days = calendar.prev_n_trading_days("20250306", 5)
```

### 15.2 历史数据的 contract 不能直接沿用当前 contract

这点非常重要。

你如果当前日 `rb2505` 是主力，过去 5 天未必都是 `rb2505`。

所以 resolver 对每个历史日都要单独查主力：

1. `20250305 -> rb2505`
2. `20250304 -> rb2505`
3. `20250303 -> rb2505`
4. `20250228 -> rb2505 or rb2510`

然后再去 catalog 查文件。

### 15.3 历史返回形式

Catalog 层建议返回：

```python
plan.history = {
    "20250305": [CatalogRecord(...), CatalogRecord(...)],
    "20250304": [CatalogRecord(...), CatalogRecord(...)],
    "20250303": [CatalogRecord(...), CatalogRecord(...)],
}
```

后续 DataPortal 再决定：

1. 每天先单独读成 DataFrame
2. 再 prepend
3. 或 separate 返回

---

## 16. 如何根据当前数据文件加载相关算子

### 16.1 Catalog 不直接读 operator 文件，但要负责定位 operator 分区键

例如你当前因子声明：

```python
operators=["detailed_trade_allocation"]
```

那么 resolver 应该输出：

```python
plan.operators["detailed_trade_allocation"] = PartitionKey(
    symbol="rb",
    trading_day="20250306",
    session="all",
    data_level="tick",
)
```

### 16.2 为什么不是直接输出文件路径

因为 operator 有两种情况：

1. operator store 里已经存在缓存文件
2. 还没有缓存，需要 DataPortal 用当前 base panel 现场计算

所以对 operator 最合理的是先输出“逻辑键”，而不是强行要求现在就有物理文件。

### 16.3 DataPortal 如何消费 operator key

DataPortal 拿到 `PartitionKey` 后：

1. 先去 `operator_store` 查是否有缓存
2. 若有则直接读
3. 若无则：
   1. 根据 `plan.current` 先构造当前 base panel
   2. 调用 operator registry 计算
   3. 可选择写回 store

所以：

1. Catalog 负责定位 operator 要属于哪个 partition
2. DataPortal 负责判断是“读缓存”还是“现场算”

---

## 17. 一个完整的串联示例

这里用一个完整例子说明。

### 17.1 因子声明

假设后续某个因子写成：

```python
@factor_spec(
    name="301",
    raw_columns=["mid", "Volume", "Turnover"],
    related={"source": "config", "columns": ["mid"]},
    history={"days": 3, "columns": ["mid", "Volume"], "mode": "separate"},
    operators=["detailed_trade_allocation"],
)
def calc_301(df, ctx=None):
    ...
```

### 17.2 Build 任务

当前任务：

1. `symbol = rb`
2. `trading_day = 20250306`
3. `session_scope = all`

### 17.3 先生成 LoadRequest

```python
request = LoadRequest(
    symbol="rb",
    trading_day="20250306",
    session_scope="all",
    data_level="tick",
    raw_columns=["mid", "Volume", "Turnover"],
    related_symbols=None,
    history_days=3,
    operators=["detailed_trade_allocation"],
)
```

### 17.4 CatalogResolver 生成 LoadPlan

它会得到：

1. `current`
   1. `rb` 当天 `day/night` 主力文件
2. `related`
   1. `hc` 当天 `day/night` 主力文件
   2. `fe` 当天 `day/night` 主力文件
3. `history`
   1. `rb` 过去 3 个交易日的主力文件
4. `operators`
   1. `detailed_trade_allocation` 当前 partition key

### 17.5 DataPortal 执行加载

DataPortal 做：

1. 读取 `current`
2. 标准化 current tick panel
3. 读取 `related`
4. 对齐到 current index
5. 读取 `history`
6. 生成 `history_df`
7. 检查 operator store
8. 读取或计算 `detailed_trade_allocation`
9. 组装 `FactorContext`

### 17.6 因子函数拿到结果

最终因子函数内可以直接写：

```python
def calc_301(df, ctx=None):
    allo = ctx.operators["detailed_trade_allocation"]
    hc_mid = ctx.related["hc"].df["mid"]
    hist_df = ctx.history_df
    ...
```

这就是 Catalog 层如何把“当前文件所需其他文件”串起来。

---

## 18. Catalog 层第一批可以先实现到什么程度

第一批建议不要一次上太多复杂功能。

### 第一批最小可用版本

先实现：

1. `catalog/models.py`
2. `catalog/parser.py`
3. `catalog/catalog_builder.py`
4. `catalog/catalog_store.py`
5. `catalog/main_contract.py`
6. `catalog/resolver.py`

能力只做到：

1. 扫描 `./data`
2. 构建 raw csv catalog
3. 读取现有主力 JSON
4. 生成：
   1. 当前文件
   2. 关联文件
   3. 历史文件
   4. operator partition key

### 第二批再加

后续再加：

1. factor store catalog
2. label store catalog
3. operator store catalog
4. bar store catalog
5. deep probe
6. 增量刷新

---

## 19. 对当前目录最贴近的落地建议

如果马上要在当前仓库落地，我建议 Catalog 层第一步只做下面这些路径：

1. 原始数据根目录：
   1. `./data`
2. 主力映射：
   1. `./config/main_contracts_cvwaprb1124.json`
3. 关系映射：
   1. 后续新增 `./config/relations.yaml`
4. 输出：
   1. `./_meta/catalog.parquet`
   2. `./_meta/main_contracts.parquet`

这样不会碰到太多现有代码，但能先把 Catalog 底座立起来。

---

## 20. 结论

Catalog 层不是简单的“保存文件列表”，而是整个新架构里负责“定位数据”的核心索引层。

它的最终职责链路是：

1. `parser` 解析路径
2. `builder` 构建表
3. `store` 持久化 catalog
4. `main_contract` 提供主力映射
5. `resolver` 根据当前任务生成 `LoadPlan`
6. `DataPortal` 根据 `LoadPlan` 真实加载当前、关联、历史、算子数据

如果你要我继续往前推进，下一步最自然的就是：

1. 按这份文档先把 `catalog/` 目录和第一批代码骨架直接建出来
2. 然后再接 `DataPortal` 的最小版本
