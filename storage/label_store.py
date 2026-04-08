"""Label storage for target variables."""

from __future__ import annotations

from pathlib import Path
from typing import Optional
import pandas as pd


class LabelStore:
    """
    标签存储，独立于因子存储。

    目录结构:
        store/labels/symbol=rb/trading_day=20250306/session=all/labels.parquet
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
            / "labels.parquet"
        )

    def save(
        self,
        symbol: str,
        trading_day: str,
        label_frame: pd.DataFrame,
        session_scope: str = "all",
        label_version: str = "v1",
        metadata: Optional[dict] = None,
    ) -> Path:
        """
        保存标签数据。

        参数:
            symbol: 品种代码
            trading_day: 交易日
            label_frame: 标签 DataFrame (index=event_time, columns=label names)
            session_scope: Session 范围
            label_version: 标签版本
            metadata: 额外元数据
        """
        path = self._build_path(symbol, trading_day, session_scope)
        path.parent.mkdir(parents=True, exist_ok=True)

        # 添加元数据列
        result = label_frame.copy()
        result["__label_version__"] = label_version
        if metadata:
            for key, value in metadata.items():
                result[f"__meta_{key}__"] = value

        result.to_parquet(path, index=True)
        return path

    def load(
        self,
        symbol: str,
        trading_day: str,
        session_scope: str = "all",
        columns: Optional[list[str]] = None,
        label_version: Optional[str] = None,
    ) -> pd.DataFrame:
        """
        加载标签数据。

        参数:
            symbol: 品种代码
            trading_day: 交易日
            session_scope: Session 范围
            columns: 需要加载的标签列
            label_version: 指定标签版本，None 表示最新版本
        """
        path = self._build_path(symbol, trading_day, session_scope)
        if not path.exists():
            raise FileNotFoundError(f"Label file not found: {path}")

        df = pd.read_parquet(path)

        if label_version is not None:
            # 筛选指定版本
            df = df[df["__label_version__"] == label_version]
            if df.empty:
                raise ValueError(f"No labels found for version {label_version}")

        if columns is not None:
            # 只返回指定的标签列
            available_cols = [col for col in columns if col in df.columns]
            return df[available_cols]

        return df

    def has_labels(
        self,
        symbol: str,
        trading_day: str,
        session_scope: str = "all",
        label_names: Optional[list[str]] = None,
    ) -> bool:
        """检查是否已有标签。"""
        path = self._build_path(symbol, trading_day, session_scope)
        if not path.exists():
            return False

        try:
            df = pd.read_parquet(path)
            if label_names is None:
                return True
            return all(name in df.columns for name in label_names)
        except Exception:
            return False

    def get_label_versions(
        self,
        symbol: str,
        trading_day: str,
        session_scope: str = "all",
    ) -> list[str]:
        """获取某天的所有标签版本。"""
        path = self._build_path(symbol, trading_day, session_scope)
        if not path.exists():
            return []

        try:
            df = pd.read_parquet(path)
            return sorted(df["__label_version__"].unique().tolist())
        except Exception:
            return []
