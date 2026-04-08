"""Catalog 持久化读写。"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pandas as pd


class CatalogStore:
    """负责 catalog 表的保存和读取。"""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.fallback_path = self.path.with_suffix(".pkl")

    def exists(self) -> bool:
        """判断 catalog 文件是否已存在。"""
        return self.path.exists() or self.fallback_path.exists()

    def save(self, catalog_df: pd.DataFrame) -> None:
        """保存 catalog 表，优先 parquet，不可用时回退到 pickle。"""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self._has_parquet_engine():
            catalog_df.to_parquet(self.path, index=False)
            return
        catalog_df.to_pickle(self.fallback_path)

    def load(self) -> pd.DataFrame:
        """读取 catalog 表，优先 parquet，不存在时读取 pickle 回退文件。"""
        if self.path.exists():
            return pd.read_parquet(self.path)
        if self.fallback_path.exists():
            return pd.read_pickle(self.fallback_path)
        return pd.DataFrame()

    @staticmethod
    def _has_parquet_engine() -> bool:
        """判断当前环境是否具备 parquet 引擎。"""
        return importlib.util.find_spec("pyarrow") is not None or importlib.util.find_spec("fastparquet") is not None
