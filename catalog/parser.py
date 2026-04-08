"""Catalog 路径解析与基础数据结构。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re
from typing import Callable, Literal, Optional


FileType = Literal["raw_csv", "prepared_tick", "time_bar", "volume_bar", "factor", "label", "operator"]
SessionType = Literal["day", "night", "all"]


def normalize_symbol(symbol: str) -> str:
    """统一品种大小写，当前先全部转成小写。"""
    return symbol.lower()


@dataclass(frozen=True)
class CatalogRecord:
    """Catalog 中的一条文件记录。"""

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
    columns: tuple[str, ...] = ()
    instrument_id: Optional[str] = None

    def to_row(self) -> dict:
        """转成适合 DataFrame 存储的字典。"""
        return {
            "file_type": self.file_type,
            "symbol": self.symbol,
            "contract": self.contract,
            "trading_day": self.trading_day,
            "session": self.session,
            "year": self.year,
            "month": self.month,
            "path": str(self.path),
            "exchange": self.exchange,
            "source": self.source,
            "mtime_ns": self.mtime_ns,
            "size_bytes": self.size_bytes,
            "columns": list(self.columns),
            "instrument_id": self.instrument_id,
        }

    @classmethod
    def from_row(cls, row: dict) -> "CatalogRecord":
        """从 DataFrame 行恢复为结构化对象。"""
        columns_val = row.get("columns")
        if isinstance(columns_val, (list, tuple)):
            columns = list(columns_val)
        elif hasattr(columns_val, "tolist"):
            columns = columns_val.tolist()
        else:
            columns = []
        return cls(
            file_type=row["file_type"],
            symbol=normalize_symbol(row["symbol"]),
            contract=row.get("contract"),
            trading_day=str(row["trading_day"]),
            session=row["session"],
            year=row.get("year"),
            month=row.get("month"),
            path=Path(row["path"]),
            exchange=row.get("exchange"),
            source=row.get("source"),
            mtime_ns=row.get("mtime_ns"),
            size_bytes=row.get("size_bytes"),
            columns=tuple(columns),
            instrument_id=row.get("instrument_id"),
        )


@dataclass(frozen=True)
class FileProbeInfo:
    """文件轻量探测结果。"""

    columns: tuple[str, ...] = ()
    instrument_id: Optional[str] = None


@dataclass(frozen=True)
class PartitionKey:
    """逻辑分区键，用于后续定位算子或派生数据。"""

    symbol: str
    trading_day: str
    session: SessionType
    contract: Optional[str] = None
    data_level: Optional[str] = None
    bar_spec: Optional[str] = None


@dataclass(frozen=True)
class LoadRequest:
    """DataPortal 请求 Catalog 生成加载计划时使用的结构。"""

    symbol: str
    trading_day: str
    session_scope: SessionType
    data_level: str
    bar_spec: Optional[dict] = None
    raw_columns: Optional[list[str]] = None
    related_symbols: Optional[list[str]] = None
    related_aliases: Optional[dict[str, str]] = None
    history_days: int = 0
    operators: Optional[list[str]] = None


@dataclass
class LoadPlan:
    """Catalog 输出给后续 DataPortal 的加载计划。"""

    current: list[CatalogRecord] = field(default_factory=list)
    related: dict[str, list[CatalogRecord]] = field(default_factory=dict)
    history: dict[str, list[CatalogRecord]] = field(default_factory=dict)
    operators: dict[str, PartitionKey] = field(default_factory=dict)
    meta: dict = field(default_factory=dict)


class PathParser:
    """路径解析器，负责把物理路径转成结构化记录。"""

    RAW_CSV_PATTERN = re.compile(r"(?P<contract>[A-Za-z]+\d+)_(?P<trading_day>\d{8})\.csv$")

    def __init__(self) -> None:
        self._parsers: list[Callable[[Path], Optional[CatalogRecord]]] = [
            self.parse_raw_csv,
        ]

    def parse(self, path: Path) -> Optional[CatalogRecord]:
        """依次尝试注册的解析函数，返回首个匹配结果。"""
        for parser in self._parsers:
            record = parser(path)
            if record is not None:
                return record
        return None

    def parse_raw_csv(self, path: Path) -> Optional[CatalogRecord]:
        """解析当前仓库的原始 CSV 路径。"""
        if path.suffix.lower() != ".csv":
            return None

        # 支持两种目录结构:
        # 1. data / symbol / session / month / filename.csv
        # 2. data / symbol / session / year / month / filename.csv
        parts = [p for p in path.parts]
        if len(parts) < 5:
            return None

        # 从后往前解析
        filename = path.name
        
        # 判断是哪种结构
        if len(parts) >= 6 and parts[-3].isdigit() and len(parts[-3]) == 4:
            # 结构 2: data / symbol / session / year / month / filename
            symbol = parts[-5]
            session = parts[-4].lower()
            year = parts[-3]
            month = parts[-2]
        else:
            # 结构 1: data / symbol / session / month / filename
            symbol = parts[-4]
            session = parts[-3].lower()
            month = parts[-2]
            year = None

        if session not in {"day", "night"}:
            return None

        matched = self.RAW_CSV_PATTERN.search(filename)
        if matched is None:
            return None

        contract = matched.group("contract")
        trading_day = matched.group("trading_day")

        return CatalogRecord(
            file_type="raw_csv",
            symbol=normalize_symbol(symbol),
            contract=contract,
            trading_day=trading_day,
            session=session,
            year=year or trading_day[:4],
            month=month or trading_day[4:6],
            path=path,
            source="raw_data",
            exchange=None,
        )
