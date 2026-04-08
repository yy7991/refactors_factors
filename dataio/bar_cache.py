"""Bar cache management for pre-aggregated bar data."""

from __future__ import annotations

from pathlib import Path
import pandas as pd


class BarCache:
    """
    Bar 数据缓存管理器。
    
    核心思想:
    - 首次聚合后保存到缓存
    - 后续直接读取缓存，避免重复计算
    - 按 symbol/trading_day/session/bar_spec 分区存储
    
    目录结构:
        cache_root/bar_cache/symbol=rb/trading_day=20240520/session=day/time_3s.parquet
    """
    
    def __init__(self, cache_root: Path) -> None:
        self.cache_root = Path(cache_root) / "bar_cache"
        self.cache_root.mkdir(parents=True, exist_ok=True)
    
    def _get_cache_path(
        self,
        symbol: str,
        trading_day: str,
        session: str,
        bar_type: str,
        bar_spec: dict,
    ) -> Path:
        """获取缓存文件路径。"""
        # 根据 bar 规格生成唯一的文件名
        if bar_type == "time":
            freq = bar_spec.get("freq", "3s")
            cache_name = f"time_{freq.replace('s', 'sec')}"
        elif bar_type == "volume":
            bars_per_day = bar_spec.get("bars_per_day", 480)
            cache_name = f"volume_{bars_per_day}"
        else:
            cache_name = f"{bar_type}_default"
        
        return (
            self.cache_root
            / f"symbol={symbol}"
            / f"trading_day={trading_day}"
            / f"session={session}"
            / f"{cache_name}.parquet"
        )
    
    def get_cached_bar(
        self,
        symbol: str,
        trading_day: str,
        session: str,
        bar_type: str,
        bar_spec: dict,
    ) -> pd.DataFrame | None:
        """
        从缓存加载 bar 数据。
        
        返回:
            如果缓存存在则返回 DataFrame，否则返回 None
        """
        path = self._get_cache_path(symbol, trading_day, session, bar_type, bar_spec)
        if path.exists():
            try:
                return pd.read_parquet(path)
            except Exception as e:
                print(f"Warning: Failed to load cached bar {path}: {e}")
                return None
        return None
    
    def save_cached_bar(
        self,
        symbol: str,
        trading_day: str,
        session: str,
        bar_type: str,
        bar_spec: dict,
        bar_df: pd.DataFrame,
    ) -> Path:
        """保存 bar 数据到缓存。"""
        if bar_df.empty:
            print("Warning: Not saving empty bar DataFrame")
            return None
        
        path = self._get_cache_path(symbol, trading_day, session, bar_type, bar_spec)
        path.parent.mkdir(parents=True, exist_ok=True)
        
        try:
            bar_df.to_parquet(path, index=True)
            return path
        except Exception as e:
            print(f"Error: Failed to save cached bar {path}: {e}")
            return None
    
    def clear_cache(
        self,
        symbol: str | None = None,
        trading_day: str | None = None,
    ) -> int:
        """
        清理缓存。
        
        参数:
            symbol: 指定品种则只清理该品种
            trading_day: 指定交易日则只清理该天
            
        返回:
            删除的文件数量
        """
        if not self.cache_root.exists():
            return 0
        
        deleted_count = 0
        
        if symbol is None:
            # Clear all cache
            import shutil
            shutil.rmtree(self.cache_root)
            self.cache_root.mkdir(parents=True, exist_ok=True)
            return deleted_count
        
        # Build path pattern
        base_path = self.cache_root / f"symbol={symbol}"
        if not base_path.exists():
            return 0
        
        if trading_day is None:
            # Clear all days for this symbol
            import shutil
            shutil.rmtree(base_path)
            return deleted_count
        
        # Clear specific day
        day_path = base_path / f"trading_day={trading_day}"
        if day_path.exists():
            import shutil
            shutil.rmtree(day_path)
            deleted_count += 1
        
        return deleted_count
    
    def get_cache_stats(self) -> dict:
        """获取缓存统计信息。"""
        if not self.cache_root.exists():
            return {
                "total_files": 0,
                "total_size_mb": 0.0,
                "symbols": [],
            }
        
        total_files = 0
        total_size = 0
        symbols = set()
        
        for parquet_file in self.cache_root.rglob("*.parquet"):
            total_files += 1
            total_size += parquet_file.stat().st_size
            # Extract symbol from path
            parts = parquet_file.parts
            for i, part in enumerate(parts):
                if part.startswith("symbol="):
                    symbols.add(part.split("=")[1])
                    break
        
        return {
            "total_files": total_files,
            "total_size_mb": total_size / 1024 / 1024,
            "symbols": sorted(list(symbols)),
        }
