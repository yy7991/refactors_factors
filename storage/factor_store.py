"""Factor storage with parquet partitioning."""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Union
import pandas as pd


class FactorStore:
    """
    因子结果存储，按 symbol/trading_day/session 分区存储。

    目录结构:
        store/factors/symbol=rb/trading_day=20250306/session=all/factors.parquet
    """

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def _build_path(
        self,
        symbol: str,
        trading_day: str,
        session_scope: str = "all",
    ) -> Path:
        """构建分区路径。"""
        return (
            self.root
            / f"symbol={symbol}"
            / f"trading_day={trading_day}"
            / f"session={session_scope}"
            / "factors.parquet"
        )

    def save(
        self,
        symbol: str,
        trading_day: str,
        factor_frame: pd.DataFrame,
        session_scope: str = "all",
        mode: str = "overwrite",
    ) -> Path:
        """
        保存因子结果。

        参数:
            symbol: 品种代码
            trading_day: 交易日
            factor_frame: 因子 DataFrame (index=event_time, columns=factor names)
            session_scope: Session 范围
            mode: 写入模式
                - "overwrite": 覆盖已有文件
                - "append_columns": 追加新因子列（读取旧文件合并）
        """
        path = self._build_path(symbol, trading_day, session_scope)
        path.parent.mkdir(parents=True, exist_ok=True)

        if mode == "append_columns" and path.exists():
            # 读取已有数据并合并新因子列
            existing = pd.read_parquet(path)
            # 只保留不在已有数据中的新列
            new_cols = [col for col in factor_frame.columns if col not in existing.columns]
            if new_cols:
                merged = pd.concat([existing, factor_frame[new_cols]], axis=1)
            else:
                merged = existing
            merged.to_parquet(path, index=True)
        else:
            factor_frame.to_parquet(path, index=True)

        return path

    def load(
        self,
        symbol: str,
        trading_day: str,
        session_scope: str = "all",
        columns: Optional[list[str]] = None,
    ) -> pd.DataFrame:
        """
        加载因子结果。

        参数:
            symbol: 品种代码
            trading_day: 交易日
            session_scope: Session 范围
            columns: 需要加载的因子列，None 表示全部
        """
        path = self._build_path(symbol, trading_day, session_scope)
        if not path.exists():
            raise FileNotFoundError(f"Factor file not found: {path}")

        if columns is None:
            return pd.read_parquet(path)
        else:
            return pd.read_parquet(path, columns=columns)

    def has_factors(
        self,
        symbol: str,
        trading_day: str,
        session_scope: str = "all",
        factor_names: Optional[list[str]] = None,
    ) -> bool:
        """
        检查指定因子是否已存在。

        参数:
            symbol: 品种代码
            trading_day: 交易日
            session_scope: Session 范围
            factor_names: 要检查的因子列表，None 表示检查是否存在任何因子
        """
        path = self._build_path(symbol, trading_day, session_scope)
        if not path.exists():
            return False

        if factor_names is None:
            return True

        try:
            existing = pd.read_parquet(path)
            return all(name in existing.columns for name in factor_names)
        except Exception:
            return False

    def list_trading_days(self, symbol: str) -> list[str]:
        """列出某品种所有有因子数据的交易日。"""
        symbol_path = self.root / f"symbol={symbol}"
        if not symbol_path.exists():
            return []

        days = []
        for day_path in symbol_path.iterdir():
            if day_path.name.startswith("trading_day="):
                day = day_path.name.split("=")[1]
                days.append(day)

        return sorted(days)

    def list_factors(self, symbol: str, trading_day: str, session_scope: str = "all") -> list[str]:
        """列出某品种某天的所有因子列。"""
        path = self._build_path(symbol, trading_day, session_scope)
        if not path.exists():
            return []

        df = pd.read_parquet(path)
        return [col for col in df.columns if not col.startswith("__")]
