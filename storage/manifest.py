"""Manifest management for tracking completed factors and metadata."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional
import pandas as pd


class ManifestManager:
    """
    元数据管理器，跟踪已完成的因子、算子、标签及其版本。

    功能:
    - 记录哪些品种/日期/因子已经计算完成
    - 记录因子代码版本和依赖版本
    - 支持智能跳过和增量重算
    """

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.factor_manifest_path = self.root / "factor_manifest.parquet"
        self.label_manifest_path = self.root / "label_manifest.parquet"
        self.operator_manifest_path = self.root / "operator_manifest.parquet"

    def _ensure_root(self) -> None:
        """确保根目录存在。"""
        self.root.mkdir(parents=True, exist_ok=True)

    # ========== Factor Manifest ==========

    def record_factor_completion(
        self,
        symbol: str,
        trading_day: str,
        factor_names: list[str],
        session_scope: str = "all",
        factor_versions: Optional[dict[str, str]] = None,
        operator_versions: Optional[dict[str, str]] = None,
        source_mtime: Optional[int] = None,
    ) -> None:
        """记录因子计算完成。"""
        self._ensure_root()

        row = {
            "symbol": symbol,
            "trading_day": str(trading_day),
            "session": session_scope,
            "factors": ",".join(sorted(factor_names)),
            "factor_versions": json.dumps(factor_versions or {}),
            "operator_versions": json.dumps(operator_versions or {}),
            "source_mtime": source_mtime,
            "computed_at": pd.Timestamp.now().isoformat(),
        }

        if self.factor_manifest_path.exists():
            df = pd.read_parquet(self.factor_manifest_path)
            # 检查是否已有记录
            mask = (
                (df["symbol"] == symbol)
                & (df["trading_day"] == str(trading_day))
                & (df["session"] == session_scope)
            )
            if mask.any():
                # 更新已有记录
                df.loc[mask, "factors"] = row["factors"]
                df.loc[mask, "factor_versions"] = row["factor_versions"]
                df.loc[mask, "operator_versions"] = row["operator_versions"]
                df.loc[mask, "computed_at"] = row["computed_at"]
            else:
                # 添加新记录
                new_row = pd.DataFrame([row])
                df = pd.concat([df, new_row], ignore_index=True)
        else:
            df = pd.DataFrame([row])

        df.to_parquet(self.factor_manifest_path, index=False)

    def should_compute_factors(
        self,
        symbol: str,
        trading_day: str,
        factor_names: list[str],
        session_scope: str = "all",
        current_factor_versions: Optional[dict[str, str]] = None,
    ) -> bool:
        """
        判断是否需要重新计算因子。

        返回 True 表示需要计算，False 表示可以跳过。
        """
        if not self.factor_manifest_path.exists():
            return True

        df = pd.read_parquet(self.factor_manifest_path)
        mask = (
            (df["symbol"] == symbol)
            & (df["trading_day"] == str(trading_day))
            & (df["session"] == session_scope)
        )

        if not mask.any():
            return True  # 从未计算过

        record = df.loc[mask].iloc[0]
        existing_factors = set(record["factors"].split(","))

        # 检查是否所有因子都已存在
        if not all(name in existing_factors for name in factor_names):
            return True

        # 如果提供了当前版本信息，检查版本是否变化
        if current_factor_versions:
            try:
                recorded_versions = json.loads(record["factor_versions"])
                for factor_name, version in current_factor_versions.items():
                    if factor_name in recorded_versions:
                        if recorded_versions[factor_name] != version:
                            return True  # 版本变化，需要重算
            except Exception:
                pass  # 解析失败时默认重算

        return False  # 可以跳过

    def get_completed_factors(
        self,
        symbol: Optional[str] = None,
        trading_day: Optional[str] = None,
    ) -> pd.DataFrame:
        """获取已完成的因子记录。"""
        if not self.factor_manifest_path.exists():
            return pd.DataFrame()

        df = pd.read_parquet(self.factor_manifest_path)
        if symbol:
            df = df[df["symbol"] == symbol]
        if trading_day:
            df = df[df["trading_day"] == str(trading_day)]

        return df

    # ========== Label Manifest ==========

    def record_label_completion(
        self,
        symbol: str,
        trading_day: str,
        label_names: list[str],
        session_scope: str = "all",
        label_version: str = "v1",
    ) -> None:
        """记录标签生成完成。"""
        self._ensure_root()

        row = {
            "symbol": symbol,
            "trading_day": str(trading_day),
            "session": session_scope,
            "labels": ",".join(sorted(label_names)),
            "label_version": label_version,
            "generated_at": pd.Timestamp.now().isoformat(),
        }

        if self.label_manifest_path.exists():
            df = pd.read_parquet(self.label_manifest_path)
            new_row = pd.DataFrame([row])
            df = pd.concat([df, new_row], ignore_index=True)
        else:
            df = pd.DataFrame([row])

        df.to_parquet(self.label_manifest_path, index=False)

    def has_labels(
        self,
        symbol: str,
        trading_day: str,
        session_scope: str = "all",
        label_version: Optional[str] = None,
    ) -> bool:
        """检查标签是否已生成。"""
        if not self.label_manifest_path.exists():
            return False

        df = pd.read_parquet(self.label_manifest_path)
        mask = (
            (df["symbol"] == symbol)
            & (df["trading_day"] == str(trading_day))
            & (df["session"] == session_scope)
        )

        if not mask.any():
            return False

        if label_version is None:
            return True

        records = df.loc[mask]
        return any(version == label_version for version in records["label_version"])

    # ========== Operator Manifest ==========

    def record_operator_completion(
        self,
        operator_name: str,
        symbol: str,
        trading_day: str,
        session_scope: str = "all",
        operator_version: str = "v1",
    ) -> None:
        """记录算子缓存生成。"""
        self._ensure_root()

        row = {
            "operator_name": operator_name,
            "symbol": symbol,
            "trading_day": str(trading_day),
            "session": session_scope,
            "operator_version": operator_version,
            "cached_at": pd.Timestamp.now().isoformat(),
        }

        if self.operator_manifest_path.exists():
            df = pd.read_parquet(self.operator_manifest_path)
            # 去重：如果已有相同记录则不添加
            mask = (
                (df["operator_name"] == operator_name)
                & (df["symbol"] == symbol)
                & (df["trading_day"] == str(trading_day))
                & (df["session"] == session_scope)
            )
            if not mask.any():
                new_row = pd.DataFrame([row])
                df = pd.concat([df, new_row], ignore_index=True)
        else:
            df = pd.DataFrame([row])

        df.to_parquet(self.operator_manifest_path, index=False)

    def has_operator_cache(
        self,
        operator_name: str,
        symbol: str,
        trading_day: str,
        session_scope: str = "all",
        operator_version: Optional[str] = None,
    ) -> bool:
        """检查算子缓存是否存在。"""
        if not self.operator_manifest_path.exists():
            return False

        df = pd.read_parquet(self.operator_manifest_path)
        mask = (
            (df["operator_name"] == operator_name)
            & (df["symbol"] == symbol)
            & (df["trading_day"] == str(trading_day))
            & (df["session"] == session_scope)
        )

        if not mask.any():
            return False

        if operator_version is None:
            return True

        records = df.loc[mask]
        return any(version == operator_version for version in records["operator_version"])
