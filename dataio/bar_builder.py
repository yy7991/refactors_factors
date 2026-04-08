"""Bar aggregation builders - time bars and volume bars."""

from __future__ import annotations

import numpy as np
import pandas as pd
from typing import Optional


def build_time_bars(
    df: pd.DataFrame,
    freq: str = "3s",
    ohlc_columns: Optional[list[str]] = None,
    agg_dict: Optional[dict] = None,
) -> pd.DataFrame:
    """
    从 tick 数据构建时间 Bar。

    参数:
        df: tick 级 DataFrame，index 为 datetime
        freq: 时间频率，如 "1s", "3s", "5s"
        ohlc_columns: 需要聚合 OHLCV 的列，默认使用标准字段
        agg_dict: 自定义聚合函数字典

    返回:
        Bar 级 DataFrame，index 为 bar_end_time
    """
    if df.empty:
        return pd.DataFrame()

    # 默认聚合规则 - dynamically build based on available columns
    if agg_dict is None:
        agg_dict = {
            # 价格类：first, max, min, last
            "LastPrice": ["first", "max", "min", "last"],
        }
        
        # 成交量和成交额：sum (bar 内累计)
        if "Volume" in df.columns:
            agg_dict["Volume"] = "sum"
        if "Turnover" in df.columns:
            agg_dict["Turnover"] = "sum"
        
        # 持仓量：last (bar 结束时的值)
        if "OpenInterest" in df.columns:
            agg_dict["OpenInterest"] = "last"
        
        # 中间价和 VWAP：first, last, mean
        if "mid" in df.columns:
            agg_dict["mid"] = ["first", "last", "mean"]
        if "cvwap" in df.columns:
            agg_dict["cvwap"] = ["last", "mean"]
        
        # 盘口五档数据：按照规则聚合
        # Bid prices (b1, b2, ..., b5): first (取 bar 开始时的买价)
        for i in range(1, 6):
            col = f"b{i}"
            if col in df.columns:
                agg_dict[col] = "first"
        
        # Ask prices (a1, a2, ..., a5): first (取 bar 开始时的卖价)
        for i in range(1, 6):
            col = f"a{i}"
            if col in df.columns:
                agg_dict[col] = "first"
        
        # Bid volumes (b1_v_m, b2_v_m, ..., b5_v_m): sum (bar 内累计挂单量)
        for i in range(1, 6):
            col = f"b{i}_v_m"
            if col in df.columns:
                agg_dict[col] = "sum"
        
        # Ask volumes (a1_v_m, a2_v_m, ..., a5_v_m): sum (bar 内累计挂单量)
        for i in range(1, 6):
            col = f"a{i}_v_m"
            if col in df.columns:
                agg_dict[col] = "sum"

    # 重采样
    bar_df = df.resample(freq).agg(agg_dict)

    # 扁平化列名
    if isinstance(bar_df.columns, pd.MultiIndex):
        bar_df.columns = ["_".join(col).strip() for col in bar_df.columns.values]

    # 删除全 NaN 的行 (无交易的时段)
    bar_df = bar_df.dropna(how="all")

    return bar_df


def build_volume_bars_historical_profile(
    df: pd.DataFrame,
    bars_per_day: int = 480,
    profile_days: int = 20,
    profile_estimator: str = "median",
) -> pd.DataFrame:
    """
    基于历史成交轮廓构建 Volume Bar。

    核心思想:
    1. 使用过去 N 天的历史数据估计累计成交量曲线
    2. 将曲线分成 K 个等分点
    3. 映射回时间边界
    4. 按时间边界聚合 tick 成 bar

    参数:
        df: tick 级 DataFrame
        bars_per_day: 每天 bar 数量
        profile_days: 用于估计轮廓的历史天数
        profile_estimator: 估计方法 ("mean", "median", "trimmed_mean")

    返回:
        Volume Bar DataFrame
    """
    if df.empty or len(df) < bars_per_day:
        return pd.DataFrame()

    # 简化版本：当前只使用当天的数据做演示
    # 实际应该加载过去 profile_days 天的数据
    volume_col = "Volume"
    if volume_col not in df.columns:
        volume_col = "AccVolume"

    if volume_col not in df.columns:
        # 如果都没有，用盘口量估算
        vol_cols = [col for col in df.columns if "v_m" in col and col.startswith("b")]
        if vol_cols:
            df[volume_col] = df[vol_cols].sum(axis=1)
        else:
            return pd.DataFrame()

    # 计算累计成交量
    cum_vol = df[volume_col].fillna(0).cumsum()
    total_vol = cum_vol.iloc[-1]

    if total_vol <= 0:
        return pd.DataFrame()

    # 计算目标分位点
    quantiles = np.linspace(0, 1, bars_per_day + 1)
    target_vols = quantiles * total_vol

    # 找到每个分位点对应的时间
    time_boundaries = []
    for target_vol in target_vols:
        idx = np.searchsorted(cum_vol.values, target_vol)
        if idx < len(df):
            time_boundaries.append(df.index[idx])
        else:
            time_boundaries.append(df.index[-1])

    # 按时间边界分组聚合
    bar_frames = []
    for i in range(len(time_boundaries) - 1):
        start_time = time_boundaries[i]
        end_time = time_boundaries[i + 1]

        mask = (df.index >= start_time) & (df.index < end_time)
        bar_tick = df.loc[mask]

        if bar_tick.empty:
            continue

        bar_row = {}
        bar_row["open"] = bar_tick["LastPrice"].iloc[0] if "LastPrice" in bar_tick.columns else np.nan
        bar_row["high"] = bar_tick["LastPrice"].max() if "LastPrice" in bar_tick.columns else np.nan
        bar_row["low"] = bar_tick["LastPrice"].min() if "LastPrice" in bar_tick.columns else np.nan
        bar_row["close"] = bar_tick["LastPrice"].iloc[-1] if "LastPrice" in bar_tick.columns else np.nan
        bar_row["Volume"] = bar_tick["Volume"].sum() if "Volume" in bar_tick.columns else 0
        bar_row["Turnover"] = bar_tick["Turnover"].sum() if "Turnover" in bar_tick.columns else 0
        bar_row["bar_start_time"] = start_time
        bar_row["bar_end_time"] = end_time
        bar_row["tick_count"] = len(bar_tick)
        bar_row["duration_ms"] = (end_time - start_time).total_seconds() * 1000

        bar_frames.append(pd.DataFrame([bar_row]))

    if not bar_frames:
        return pd.DataFrame()

    result = pd.concat(bar_frames, ignore_index=True)
    result.index = pd.to_datetime(result["bar_end_time"])
    return result


def aggregate_bar_features(
    bar_df: pd.DataFrame,
    include_derivatives: bool = True,
) -> pd.DataFrame:
    """
    从 Bar 数据中派生额外特征。

    参数:
        bar_df: Bar DataFrame
        include_derivatives: 是否包含派生特征

    返回:
        增强后的 Bar DataFrame
    """
    if bar_df.empty:
        return bar_df

    result = bar_df.copy()

    if include_derivatives:
        # OHLC 派生
        ohlc_cols = ["LastPrice_first", "LastPrice_max", "LastPrice_min", "LastPrice_last"]
        if all(col in result.columns for col in ohlc_cols):
            result["range"] = result["LastPrice_max"] - result["LastPrice_min"]
            result["body"] = result["LastPrice_last"] - result["LastPrice_first"]
            result["upper_shadow"] = result["LastPrice_max"] - result[["LastPrice_first", "LastPrice_last"]].max(axis=1)
            result["lower_shadow"] = result[["LastPrice_first", "LastPrice_last"]].min(axis=1) - result["LastPrice_min"]

        # 价格变化
        if "LastPrice_last" in result.columns:
            result["return"] = result["LastPrice_last"].pct_change()

        # VWAP - only if both columns exist
        if "Turnover_sum" in result.columns and "Volume_sum" in result.columns:
            result["vwap"] = result["Turnover_sum"] / result["Volume_sum"].replace(0, np.nan)

    return result
