"""Catalog 构建器，包含目录扫描与轻量探测逻辑。"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Iterable, Optional

import pandas as pd

from .parser import CatalogRecord, FileProbeInfo, PathParser


ProbeMode = str


class CatalogBuilder:
    """扫描目录、解析路径并构建 catalog 表。"""

    def __init__(
        self,
        roots: dict[str, Path | list[Path] | tuple[Path, ...]],
        parser: Optional[PathParser] = None,
        probe_mode: ProbeMode = "header",
    ) -> None:
        self.roots = roots
        self.parser = parser or PathParser()
        self.probe_mode = probe_mode

    def iter_root_paths(self) -> Iterable[tuple[str, Path]]:
        """展开 root 配置，统一产出 `(root_name, root_path)`。"""
        for root_name, root_value in self.roots.items():
            if isinstance(root_value, (list, tuple)):
                for root_path in root_value:
                    yield root_name, Path(root_path)
            else:
                yield root_name, Path(root_value)

    def iter_files(self) -> Iterable[tuple[str, Path]]:
        """遍历所有待扫描文件。"""
        for root_name, root_path in self.iter_root_paths():
            if not root_path.exists():
                continue
            for file_path in sorted(root_path.rglob("*")):
                if file_path.is_file():
                    yield root_name, file_path

    def probe_file(self, record: CatalogRecord) -> FileProbeInfo:
        """按探测模式补充轻量文件信息。"""
        if record.file_type != "raw_csv":
            return FileProbeInfo()
        if self.probe_mode == "none":
            return FileProbeInfo()
        if self.probe_mode == "sample":
            return self._probe_csv_sample(record.path)
        return self._probe_csv_header(record.path)

    @staticmethod
    def _probe_csv_header(path: Path) -> FileProbeInfo:
        """只读取表头，适合作为默认构建模式。"""
        header = pd.read_csv(path, nrows=0)
        return FileProbeInfo(columns=tuple(header.columns))

    @staticmethod
    def _probe_csv_sample(path: Path) -> FileProbeInfo:
        """读取极少量样本行，补充 instrument_id。"""
        sample = pd.read_csv(path, nrows=5)
        instrument_id = None
        if "InstrumentID" in sample.columns and not sample.empty:
            instrument_id = str(sample["InstrumentID"].iloc[0])
        return FileProbeInfo(columns=tuple(sample.columns), instrument_id=instrument_id)

    def build(self) -> pd.DataFrame:
        """扫描根目录并返回结构化 catalog 表。"""
        rows: list[dict] = []

        for root_name, file_path in self.iter_files():
            record = self.parser.parse(file_path)
            if record is None:
                continue

            stat = file_path.stat()
            record_with_stat = CatalogRecord(
                file_type=record.file_type,
                symbol=record.symbol,
                contract=record.contract,
                trading_day=record.trading_day,
                session=record.session,
                year=record.year,
                month=record.month,
                path=record.path,
                exchange=record.exchange,
                source=root_name or record.source,
                mtime_ns=stat.st_mtime_ns,
                size_bytes=stat.st_size,
            )

            probe_info = self.probe_file(record_with_stat)
            finalized_record = CatalogRecord(
                file_type=record_with_stat.file_type,
                symbol=record_with_stat.symbol,
                contract=record_with_stat.contract,
                trading_day=record_with_stat.trading_day,
                session=record_with_stat.session,
                year=record_with_stat.year,
                month=record_with_stat.month,
                path=record_with_stat.path,
                exchange=record_with_stat.exchange,
                source=record_with_stat.source,
                mtime_ns=record_with_stat.mtime_ns,
                size_bytes=record_with_stat.size_bytes,
                columns=probe_info.columns,
                instrument_id=probe_info.instrument_id,
            )
            rows.append(finalized_record.to_row())

        catalog_df = pd.DataFrame(rows)
        if not catalog_df.empty:
            catalog_df = catalog_df.sort_values(
                ["file_type", "symbol", "trading_day", "session", "contract", "path"]
            ).reset_index(drop=True)
        return catalog_df


def build_catalog(
    roots: dict[str, Path | list[Path] | tuple[Path, ...]],
    parser: Optional[PathParser] = None,
    probe_mode: ProbeMode = "header",
) -> pd.DataFrame:
    """函数式入口，便于脚本快速调用。"""
    builder = CatalogBuilder(roots=roots, parser=parser, probe_mode=probe_mode)
    return builder.build()

