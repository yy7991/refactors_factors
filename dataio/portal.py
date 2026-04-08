"""DataPortal 最小可运行版本。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import numpy as np
import pandas as pd

from catalog import CatalogResolver, LoadPlan, LoadRequest, PartitionKey
from .processing import PortalProcessingConfig, load_record
from .bar_builder import build_time_bars, build_volume_bars_historical_profile, aggregate_bar_features
from .bar_cache import BarCache
from operators.orderflow import detailed_trade_allocation


DEFAULT_OPERATOR_REGISTRY: dict[str, Callable[[pd.DataFrame], pd.Series | pd.DataFrame]] = {
    "detailed_trade_allocation": detailed_trade_allocation,
}


@dataclass
class FactorContext:
    """因子函数运行时看到的统一上下文。"""

    symbol: str
    trading_day: str
    session_scope: str
    df: pd.DataFrame
    current_df: pd.DataFrame
    related: dict[str, pd.DataFrame] = field(default_factory=dict)
    history: dict[str, pd.DataFrame] = field(default_factory=dict)
    history_df: pd.DataFrame = field(default_factory=pd.DataFrame)
    full_df: pd.DataFrame = field(default_factory=pd.DataFrame)
    operators: dict[str, pd.DataFrame] = field(default_factory=dict)
    plan: Optional[LoadPlan] = None
    current_index: pd.Index = field(default_factory=lambda: pd.Index([]))
    current_mask: pd.Series = field(default_factory=lambda: pd.Series(dtype=bool))
    meta: dict = field(default_factory=dict)


class OperatorStore:
    """算子缓存层，只缓存当前最值得复用的算子结果。"""

    def __init__(self, cache_root: Path) -> None:
        self.cache_root = Path(cache_root)

    def build_path(self, operator_name: str, partition_key: PartitionKey) -> Path:
        """根据分区键构造算子缓存路径。"""
        data_level = partition_key.data_level or "tick"
        session = partition_key.session or "all"
        filename = f"{partition_key.trading_day}_{data_level}.pkl"
        return (
            self.cache_root
            / operator_name
            / partition_key.symbol
            / session
            / filename
        )

    def load_or_build(
        self,
        operator_name: str,
        partition_key: PartitionKey,
        base_df: pd.DataFrame,
        operator_func: Callable[[pd.DataFrame], pd.Series | pd.DataFrame],
    ) -> pd.DataFrame:
        """优先读取缓存，否则现场计算并写入缓存。"""
        cache_path = self.build_path(operator_name, partition_key)
        if cache_path.exists():
            cached = pd.read_pickle(cache_path)
            return self._normalize_output(cached, operator_name, base_df.index)

        result = operator_func(base_df.copy())
        normalized = self._normalize_output(result, operator_name, base_df.index)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        normalized.to_pickle(cache_path)
        return normalized

    @staticmethod
    def _normalize_output(
        result: pd.Series | pd.DataFrame,
        default_name: str,
        target_index: pd.Index,
    ) -> pd.DataFrame:
        """统一算子输出结构。"""
        if isinstance(result, pd.Series):
            frame = result.to_frame(name=result.name or default_name)
        elif isinstance(result, pd.DataFrame):
            frame = result.copy()
        else:
            frame = pd.DataFrame(result, index=target_index, columns=[default_name])
        return frame.reindex(target_index)


class DataPortal:
    """承接 catalog 的 LoadPlan，并输出因子可以直接消费的上下文。"""

    def __init__(
        self,
        resolver: CatalogResolver,
        processing_config: PortalProcessingConfig,
        cache_root: str | Path = "_portal_cache",
        operator_registry: Optional[dict[str, Callable[[pd.DataFrame], pd.Series | pd.DataFrame]]] = None,
        use_bar_cache: bool = True,
    ) -> None:
        self.resolver = resolver
        self.processing_config = processing_config
        self.operator_registry = operator_registry or DEFAULT_OPERATOR_REGISTRY
        self.operator_store = OperatorStore(Path(cache_root) / "operators")
        
        # Bar cache (optional)
        self.use_bar_cache = use_bar_cache
        if use_bar_cache:
            self.bar_cache = BarCache(Path(cache_root))

    def build_request(
        self,
        symbol: str,
        trading_day: str,
        factor_spec: object,
        session_scope: str = "all",
        data_level: str = "tick",
    ) -> LoadRequest:
        """把装饰器定义转换成 resolver 使用的 LoadRequest。"""
        return LoadRequest(
            symbol=symbol,
            trading_day=str(trading_day),
            session_scope=session_scope,
            data_level=data_level,
            raw_columns=getattr(factor_spec, "raw_columns", None),
            related_symbols=getattr(factor_spec, "related_symbols", None),
            related_aliases=getattr(factor_spec, "related_aliases", None),
            history_days=getattr(factor_spec, "history_days", 0),
            operators=getattr(factor_spec, "operators", None),
        )

    def load_factor_context(
        self,
        symbol: str,
        trading_day: str,
        factor_spec: object,
        session_scope: str = "all",
    ) -> FactorContext:
        """根据因子定义加载当前、关联、历史和算子数据。"""
        request = self.build_request(
            symbol=symbol,
            trading_day=trading_day,
            factor_spec=factor_spec,
            session_scope=session_scope,
        )
        plan = self.resolver.resolve(request)
        history_mode = getattr(factor_spec, "history_mode", "separate")
        bar_spec = getattr(factor_spec, "bar", None)  # Get bar spec from factor
        return self.load_from_plan(
            plan=plan,
            symbol=symbol,
            trading_day=str(trading_day),
            session_scope=session_scope,
            raw_columns=request.raw_columns,
            history_mode=history_mode,
            bar_spec=bar_spec,  # Pass bar_spec for related variety aggregation
        )

    def load_from_plan(
        self,
        plan: LoadPlan,
        symbol: str,
        trading_day: str,
        session_scope: str,
        raw_columns: Optional[list[str]] = None,
        history_mode: str = "separate",
        bar_spec: Optional[dict] = None,
    ) -> FactorContext:
        """执行 LoadPlan，对外产出 FactorContext。"""
        current_full = self._load_records(plan.current)
        if current_full.empty:
            raise ValueError(f"{symbol} {trading_day} 当前主力数据为空，无法计算因子")

        current_df = self._select_factor_columns(current_full, raw_columns)

        related_frames: dict[str, pd.DataFrame] = {}
        for related_symbol, records in sorted(plan.related.items()):
            related_full = self._load_records(records)
            if related_full.empty:
                continue
            
            # CRITICAL: Aggregate related variety's ticks to bars using SAME spec as current
            # This ensures both have identical datetime index from bar aggregation
            related_df = self._select_factor_columns(related_full, raw_columns)
            
            # Re-aggregate related ticks to match current's bar structure
            if bar_spec and len(current_df) > 0:
                # Use current's index as the template for bar aggregation
                related_bars = self._build_bars_from_df_with_index(
                    related_full,
                    current_df.index,
                    bar_spec=bar_spec,
                    symbol=related_symbol,
                    trading_day=trading_day,
                    session=session_scope
                )
                related_df = self._select_factor_columns(related_bars, raw_columns)
            
            # Now both current and related have same index, just need final alignment
            related_frames[related_symbol] = self._align_to_current_index(current_df, related_df)

        history_frames: dict[str, pd.DataFrame] = {}
        for history_day, records in sorted(plan.history.items()):
            history_full = self._load_records(records)
            if history_full.empty:
                continue
            history_frames[history_day] = self._select_factor_columns(history_full, raw_columns)

        history_df = self._concat_frames(history_frames.values())
        full_df = current_df if history_mode != "prepend" else self._concat_frames([history_df, current_df])

        operators: dict[str, pd.DataFrame] = {}
        for operator_name, partition_key in plan.operators.items():
            operators[operator_name] = self._load_operator(operator_name, partition_key, current_full)

        current_mask = pd.Series(full_df.index.isin(current_df.index), index=full_df.index)
        return FactorContext(
            symbol=symbol,
            trading_day=trading_day,
            session_scope=session_scope,
            df=current_df,
            current_df=current_full,
            related=related_frames,
            history=history_frames,
            history_df=history_df,
            full_df=full_df,
            operators=operators,
            plan=plan,
            current_index=current_df.index,
            current_mask=current_mask,
            meta={"raw_columns": raw_columns, "history_mode": history_mode},
        )

    def _load_records(self, records: list) -> pd.DataFrame:
        """读取多条 catalog 记录并拼成同一天的大表。"""
        frames = []
        for record in records:
            frame = load_record(record, self.processing_config)
            if not frame.empty:
                frames.append(frame)
        return self._concat_frames(frames)

    def _build_bars_from_df(
        self,
        df: pd.DataFrame,
        bar_spec: dict,
        symbol: str = None,
        trading_day: str = None,
        session: str = None,
    ) -> pd.DataFrame:
        """从 tick DataFrame 构建 bar。"""
        if df.empty:
            return df
        
        # Try to load from cache first (if enabled and parameters provided)
        if self.use_bar_cache and symbol and trading_day and session:
            cached_bar = self.bar_cache.get_cached_bar(
                symbol, trading_day, session,
                bar_spec.get("type", "time"),
                bar_spec
            )
            if cached_bar is not None:
                print(f"  [BarCache HIT] {symbol} {trading_day} {session}")
                return cached_bar
        
        # Cache miss or disabled, build bar
        bar_type = bar_spec.get("type", "time")

        if bar_type == "time":
            freq = bar_spec.get("freq", "3s")
            bar_df = build_time_bars(df, freq=freq)
        elif bar_type == "volume":
            bars_per_day = bar_spec.get("bars_per_day", 480)
            profile_days = bar_spec.get("profile_days", 20)
            mode = bar_spec.get("mode", "historical_profile")

            if mode == "historical_profile":
                bar_df = build_volume_bars_historical_profile(
                    df,
                    bars_per_day=bars_per_day,
                    profile_days=profile_days,
                )
            else:
                # offline_quantile 暂不实现
                bar_df = build_volume_bars_historical_profile(
                    df,
                    bars_per_day=bars_per_day,
                    profile_days=profile_days,
                )
        else:
            raise ValueError(f"Unsupported bar type: {bar_type}")

        # 派生额外特征
        bar_df = aggregate_bar_features(bar_df, include_derivatives=True)
        
        # Save to cache if enabled and parameters provided
        if self.use_bar_cache and symbol and trading_day and session and not bar_df.empty:
            self.bar_cache.save_cached_bar(
                symbol, trading_day, session,
                bar_type, bar_spec,
                bar_df
            )
            print(f"  [BarCache MISS - saved] {symbol} {trading_day} {session}")
        
        return bar_df

    def _build_bars_from_df_with_index(
        self,
        df: pd.DataFrame,
        target_index: pd.DatetimeIndex,
        bar_spec: dict,
        symbol: str = None,
        trading_day: str = None,
        session: str = None,
    ) -> pd.DataFrame:
        """
        从 tick DataFrame 构建 bar，使用给定的时间索引。
        
        This ensures all varieties have IDENTICAL bar timestamps.
        
        Args:
            df: Tick data
            target_index: Target datetime index (from current variety's bars)
            bar_spec: Bar specification dict
            symbol, trading_day, session: For caching
            
        Returns:
            Bar data with same index as target_index
        """
        if df.empty or len(target_index) == 0:
            return pd.DataFrame()
        
        # For time-based bars
        if bar_spec and bar_spec.get("type") == "time":
            freq = bar_spec.get("freq", "1s")
            
            # Build bars normally first (this aggregates ticks to bars)
            bar_df = build_time_bars(df, freq=freq)
            
            # Then reindex to match target exactly
            # This ensures all varieties use SAME timestamps
            if not bar_df.empty:
                bar_df = bar_df.reindex(target_index, method='nearest').ffill().bfill().fillna(0)
            
            # Aggregate features AFTER reindex (more tolerant of missing columns)
            # Only aggregate if we have the minimum required columns
            if 'open' in bar_df.columns and 'high' in bar_df.columns and 'low' in bar_df.columns and 'close' in bar_df.columns:
                bar_df = aggregate_bar_features(bar_df, include_derivatives=True)
            else:
                print(f"[WARN] Missing OHLC columns for {symbol} on {trading_day} {session}. Skipping feature aggregation.")
            
            return bar_df
        else:
            # For non-time bars, fall back to regular bar building + reindex
            bar_df = self._build_bars_from_df(df, bar_spec, symbol, trading_day, session)
            return bar_df.reindex(target_index, method='nearest').ffill().bfill().fillna(0)

    @staticmethod
    def _concat_frames(frames) -> pd.DataFrame:
        """拼接多个 DataFrame，并保持索引递增。"""
        valid_frames = [frame for frame in frames if frame is not None and not frame.empty]
        if not valid_frames:
            return pd.DataFrame()
        return pd.concat(valid_frames, axis=0).sort_index()

    @staticmethod
    def _select_factor_columns(df: pd.DataFrame, raw_columns: Optional[list[str]]) -> pd.DataFrame:
        """按因子定义裁剪原始字段，避免无关列跟着走。"""
        if not raw_columns:
            return df.copy()

        keep_columns = list(raw_columns)
        keep_columns += [column for column in df.columns if column.startswith("target_")]
        keep_columns += [column for column in df.columns if column.startswith("__")]
        for base_column in ["InstrumentID", "TimeStamp"]:
            if base_column in df.columns:
                keep_columns.append(base_column)

        keep_columns = [column for column in dict.fromkeys(keep_columns) if column in df.columns]
        return df[keep_columns].copy()

    @staticmethod
    def _align_to_current_index(current_df: pd.DataFrame, related_df: pd.DataFrame) -> pd.DataFrame:
        """把关联品种重索引到当前品种时间轴上。"""
        if related_df.empty:
            return related_df
        
        # Use nearest method for initial alignment
        aligned = related_df.reindex(current_df.index, method='nearest')
        
        # Handle any remaining NaN from edge cases or large gaps
        # Forward fill first, then backward fill, finally fill remaining with 0
        aligned = aligned.ffill().bfill().fillna(0)
        
        return aligned

    def _load_operator(
        self,
        operator_name: str,
        partition_key: PartitionKey,
        current_full_df: pd.DataFrame,
    ) -> pd.DataFrame:
        """加载或计算当前因子声明需要的算子结果。"""
        if operator_name not in self.operator_registry:
            raise KeyError(f"算子 {operator_name} 未注册")
        operator_func = self.operator_registry[operator_name]
        return self.operator_store.load_or_build(
            operator_name=operator_name,
            partition_key=partition_key,
            base_df=current_full_df,
            operator_func=operator_func,
        )


class PortalFactorRunner:
    """因子计算执行器。"""

    def __init__(self, portal: DataPortal) -> None:
        self.portal = portal

    def compute_factor(
        self,
        symbol: str,
        trading_day: str,
        factor_func: Callable[[FactorContext], pd.Series | pd.DataFrame],
        session_scope: str = "all",
    ) -> tuple[pd.DataFrame, FactorContext]:
        """计算单个因子，并返回因子结果和上下文。"""
        factor_spec = getattr(factor_func, "__factor_spec__", None)
        if factor_spec is None:
            raise ValueError(f"{factor_func.__name__} 缺少 factor_spec 装饰器")

        context = self.portal.load_factor_context(
            symbol=symbol,
            trading_day=trading_day,
            factor_spec=factor_spec,
            session_scope=session_scope,
        )
        output = factor_func(context)
        factor_frame = self._normalize_factor_output(output, factor_spec.name, context.current_index)
        return factor_frame, context

    def compute_factor_frame(
        self,
        symbol: str,
        trading_day: str,
        factor_functions: list[Callable[[FactorContext], pd.Series | pd.DataFrame]],
        session_scope: str = "all",
        bar_spec: dict = None,  # New parameter for bar-based factors
    ) -> tuple[pd.DataFrame, FactorContext]:
        """顺序计算一组因子，并拼成同一张因子表。"""
        factor_frames = []
        latest_context: Optional[FactorContext] = None
        
        # If bar_spec is provided, load bar data first
        if bar_spec:
            # Get the first factor's context to access tick data
            first_factor_func = factor_functions[0] if factor_functions else None
            if first_factor_func:
                factor_spec = getattr(first_factor_func, "__factor_spec__", None)
                if factor_spec:
                    context = self.portal.load_factor_context(
                        symbol=symbol,
                        trading_day=trading_day,
                        factor_spec=factor_spec,
                        session_scope=session_scope,
                    )
                    # Build bar from tick data for current variety
                    bar_df = self.portal._build_bars_from_df(
                        df=context.current_df,
                        bar_spec=bar_spec,
                        symbol=symbol,
                        trading_day=trading_day,
                        session=session_scope,
                    )
                    
                    # CRITICAL FIX: Also aggregate related varieties to SAME bar index
                    # This ensures ALL varieties have IDENTICAL timestamps AND columns
                    related_bars = {}
                    for rel_symbol, rel_tick_df in context.related.items():
                        if not rel_tick_df.empty:
                            # First, ensure related variety has same columns as current
                            # Reindex columns to match current variety's structure
                            rel_bar_df = self.portal._build_bars_from_df_with_index(
                                df=rel_tick_df,
                                target_index=bar_df.index,  # Use SAME index as current
                                bar_spec=bar_spec,
                                symbol=rel_symbol,
                                trading_day=trading_day,
                                session=session_scope,
                            )
                            
                            # CRITICAL: Align columns to match current variety's bar structure
                            # This ensures all varieties have identical column names for factor calculation
                            if not rel_bar_df.empty:
                                # Reindex columns to match current bar_df columns
                                missing_cols = set(bar_df.columns) - set(rel_bar_df.columns)
                                if missing_cols:
                                    # Add missing columns with NaN (will be filled later)
                                    for col in missing_cols:
                                        rel_bar_df[col] = np.nan
                                
                                # Reorder columns to match current
                                rel_bar_df = rel_bar_df[bar_df.columns]
                                
                                # Fill NaN values appropriately
                                # Price columns: forward fill then backward fill
                                price_cols = [col for col in bar_df.columns if 'price' in col.lower() or col.startswith(('b', 'a'))]
                                for col in price_cols:
                                    if col in rel_bar_df.columns:
                                        rel_bar_df[col] = rel_bar_df[col].ffill().bfill()
                                
                                # Volume/Turnover columns: fill with 0
                                vol_cols = [col for col in bar_df.columns if 'vol' in col.lower() or 'turnover' in col.lower()]
                                for col in vol_cols:
                                    if col in rel_bar_df.columns:
                                        rel_bar_df[col] = rel_bar_df[col].fillna(0)
                                
                                # Other columns: fill with 0
                                other_cols = [col for col in bar_df.columns if col not in price_cols and col not in vol_cols]
                                for col in other_cols:
                                    if col in rel_bar_df.columns:
                                        rel_bar_df[col] = rel_bar_df[col].fillna(0)
                            
                            related_bars[rel_symbol] = rel_bar_df
                    
                    # Update context to use bar data instead of tick data
                    latest_context = FactorContext(
                        symbol=symbol,
                        trading_day=trading_day,
                        session_scope=session_scope,
                        df=bar_df,
                        current_df=bar_df,
                        related=related_bars,  # ← FIXED: use aggregated bars
                        history=context.history,
                        history_df=context.history_df,
                        full_df=bar_df,
                        operators=context.operators,
                        plan=context.plan,
                        current_index=bar_df.index,
                        current_mask=pd.Series(bar_df.index.isin(bar_df.index), index=bar_df.index),
                        meta={**context.meta, "bar_spec": bar_spec},
                    )
        
        for factor_func in factor_functions:
            # Use the pre-computed bar context if available, otherwise load normally
            if bar_spec and latest_context is not None:
                # Always use the pre-loaded bar context (with aggregated related varieties)
                context = latest_context
                output = factor_func(context)
                factor_frame = self._normalize_factor_output(output, factor_func.__factor_spec__.name, context.current_index)
            else:
                # Subsequent factors or no bar spec - normal loading
                factor_frame, context = self.compute_factor(
                    symbol=symbol,
                    trading_day=trading_day,
                    factor_func=factor_func,
                    session_scope=session_scope,
                )
            factor_frames.append(factor_frame)
            latest_context = context

        if latest_context is None:
            raise ValueError("factor_functions 不能为空")

        merged = pd.concat(factor_frames, axis=1).sort_index()
        merged = merged.loc[latest_context.current_index]
        return merged, latest_context

    @staticmethod
    def _normalize_factor_output(
        output: pd.Series | pd.DataFrame,
        factor_name: str,
        target_index: pd.Index,
    ) -> pd.DataFrame:
        """统一单个因子的输出格式。"""
        if isinstance(output, pd.Series):
            frame = output.to_frame(name=output.name or factor_name)
        elif isinstance(output, pd.DataFrame):
            frame = output.copy()
        else:
            frame = pd.DataFrame(output, index=target_index, columns=[factor_name])
        return frame.reindex(target_index)
