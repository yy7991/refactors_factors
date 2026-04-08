"""Test factors demonstrating all capabilities of the new framework."""

from __future__ import annotations

import numpy as np
import pandas as pd

from factors.decorators import factor_spec


# ========== 1. 简单因子：只用原始列 ==========

@factor_spec(
    name="test_001",
    description="简单买卖压因子，只用原始列",
    tags=["test", "simple", "raw_columns"],
    raw_columns=["b1", "a1", "Volume"],
)
def calc_test_001(ctx) -> pd.Series:
    """
    简单的买卖压指标。
    
    计算逻辑：(AskPrice1 - BidPrice1) / Volume
    """
    df = ctx.df
    spread = df["a1"] - df["b1"]
    volume = df["Volume"].replace(0, np.nan)
    return spread / volume


@factor_spec(
    name="test_002",
    description="中间价动量因子",
    tags=["test", "simple", "momentum"],
    raw_columns=["mid"],
)
def calc_test_002(ctx) -> pd.Series:
    """
    中间价的短期动量。
    
    计算逻辑：mid 的 50 tick 收益率
    """
    df = ctx.df
    mid = df["mid"]
    return mid.pct_change(50).fillna(0)


# ========== 2. 算子因子：使用 detailed_trade_allocation ==========

@factor_spec(
    name="test_010",
    description="主动买卖不平衡因子",
    tags=["test", "operator", "orderflow"],
    raw_columns=["LastPrice", "Volume", "b1", "a1", "b1_v_m", "a1_v_m"],
    operators=["detailed_trade_allocation"],
)
def calc_test_010(ctx) -> pd.Series:
    """
    基于成交分配的主动买卖不平衡。
    
    计算逻辑：(active_buy_vol - active_sell_vol) / total_vol
    """
    allo = ctx.operators["detailed_trade_allocation"]
    buy_vol = allo["active_buy_vol"]
    sell_vol = allo["active_sell_vol"]
    total_vol = buy_vol + sell_vol + 1e-10
    return (buy_vol - sell_vol) / total_vol


@factor_spec(
    name="test_011",
    description="五档盘口买入强度",
    tags=["test", "operator", "depth"],
    raw_columns=["LastPrice", "Volume", "b1", "a1"] + [f"b{i}_v_m" for i in range(1, 6)] + [f"a{i}_v_m" for i in range(1, 6)],
    operators=["detailed_trade_allocation"],
)
def calc_test_011(ctx) -> pd.Series:
    """
    买一至买五的累计买入强度。
    
    计算逻辑：sum(buy_qty_a1..a5) / Volume
    """
    allo = ctx.operators["detailed_trade_allocation"]
    df = ctx.df
    total_buy = sum(allo[f"buy_qty_a{i}"] for i in range(1, 6))
    volume = df["Volume"].replace(0, np.nan)
    return total_buy / volume


# ========== 3. 跨品种因子：需要关联品种数据 ==========

@factor_spec(
    name="test_020",
    description="跨品种价差因子 rb-hc",
    tags=["test", "related", "spread"],
    raw_columns=["mid"],
    related={
        "source": "explicit",
        "symbols": ["hc"],
        "columns": ["mid"],
    },
)
def calc_test_020(ctx) -> pd.Series:
    """
    螺纹钢与热卷的价差。
    
    计算逻辑：rb_mid - hc_mid
    """
    df = ctx.df
    rb_mid = df["mid"]
    hc_mid = ctx.related["hc"]["mid"]
    return rb_mid - hc_mid


@factor_spec(
    name="test_021",
    description="跨品种相对强弱",
    tags=["test", "related", "relative_strength"],
    raw_columns=["mid"],
    related={
        "source": "explicit",
        "symbols": ["hc", "fe"],
        "columns": ["mid"],
    },
)
def calc_test_021(df: pd.DataFrame, ctx=None) -> pd.Series:
    """
    多品种相对强弱指标。
    
    计算逻辑：(rb_mid - mean(hc_mid, fe_mid)) / mean(hc_mid, fe_mid)
    """
    rb_mid = df["mid"]
    hc_mid = ctx.related["hc"]["mid"]
    fe_mid = ctx.related["fe"]["mid"]
    
    avg_related = (hc_mid + fe_mid) / 2
    relative_strength = (rb_mid - avg_related) / (avg_related + 1e-10)
    return relative_strength


# ========== 4. 历史统计量因子：需要过去 N 天数据 ==========

@factor_spec(
    name="test_030",
    description="历史波动率 Z-score 因子",
    tags=["test", "stats", "volatility"],
    raw_columns=["mid"],
    stats={
        "days": 5,
        "columns": ["mid"],
        "metrics": ["mean", "std"],
    },
)
def calc_test_030(df: pd.DataFrame, ctx=None) -> pd.Series:
    """
    当前价格相对历史 5 天的 Z-score。
    
    计算逻辑：(mid - hist5_mean) / hist5_std
    """
    mid = df["mid"]
    hist_mean = df["hist5__mid__mean"]
    hist_std = df["hist5__mid__std"]
    
    z_score = (mid - hist_mean) / (hist_std + 1e-10)
    return z_score


@factor_spec(
    name="test_031",
    description="历史成交量分位数",
    tags=["test", "stats", "volume"],
    raw_columns=["Volume"],
    stats={
        "days": 10,
        "columns": ["Volume"],
        "metrics": ["mean", "std"],
    },
)
def calc_test_031(df: pd.DataFrame, ctx=None) -> pd.Series:
    """
    当前成交量相对历史的标准化。
    
    计算逻辑：(Volume - hist10_mean) / hist10_std
    """
    volume = df["Volume"]
    hist_mean = df["hist10__Volume__mean"]
    hist_std = df["hist10__Volume__std"]
    
    return (volume - hist_mean) / (hist_std + 1e-10)


# ========== 5. 历史数据 Prepend 因子：需要拼接历史日数据 ==========

@factor_spec(
    name="test_040",
    description="跨日动量因子",
    tags=["test", "history", "momentum"],
    raw_columns=["mid"],
    history={
        "days": 3,
        "columns": ["mid"],
        "mode": "prepend_rows",
    },
)
def calc_test_040(df: pd.DataFrame, ctx=None) -> pd.Series:
    """
    跨 3 天的动量指标。
    
    只在当前日的行上计算结果
    """
    # full_df 包含了 prepend 的历史数据
    full_mid = ctx.full_df["mid"]
    
    # 计算滚动动量
    momentum = full_mid.pct_change(100)
    
    # 只返回当前日的部分
    return momentum.loc[ctx.current_index]


@factor_spec(
    name="test_041",
    description="历史波动率排名",
    tags=["test", "history", "volatility"],
    raw_columns=["mid"],
    history={
        "days": 5,
        "columns": ["mid"],
        "mode": "separate",
    },
)
def calc_test_041(df: pd.DataFrame, ctx=None) -> pd.Series:
    """
    当前波动率在历史 5 天中的百分位排名。
    """
    # 计算当天的波动率
    current_vol = df["mid"].pct_change().std()
    
    # 计算历史每天的波动率
    hist_vols = []
    for day, day_df in ctx.history.items():
        if not day_df.empty:
            daily_vol = day_df["mid"].pct_change().std()
            hist_vols.append(daily_vol)
    
    if not hist_vols:
        return pd.Series(0.5, index=df.index[:1])
    
    # 计算百分位
    rank = sum(vol < current_vol for vol in hist_vols) / len(hist_vols)
    return pd.Series(rank, index=df.index[:1])


# ========== 6. Bar 因子：在聚合 bar 数据上计算 ==========

@factor_spec(
    name="test_050",
    description="3 秒时间 bar 动量",
    tags=["test", "bar", "time_bar"],
    bar={
        "type": "time",
        "freq": "3s",
    },
    raw_columns=["open", "high", "low", "close", "Volume"],
)
def calc_test_050(df: pd.DataFrame, ctx=None) -> pd.Series:
    """
    基于 3 秒 bar 的动量因子。
    
    计算逻辑：bar_close / bar_open - 1
    """
    bar_open = df.get("LastPrice_first", df.get("open", df["close"]))
    bar_close = df.get("LastPrice_last", df.get("close", df["close"]))
    
    return (bar_close / bar_open - 1).fillna(0)


@factor_spec(
    name="test_051",
    description="Bar 波动率因子",
    tags=["test", "bar", "volatility"],
    bar={
        "type": "time",
        "freq": "3s",
    },
    raw_columns=["open", "high", "low", "close"],
)
def calc_test_051(df: pd.DataFrame, ctx=None) -> pd.Series:
    """
    Bar 级别的波动率。
    
    计算逻辑：(high - low) / open
    """
    high = df.get("LastPrice_high", df.get("high", df["close"]))
    low = df.get("LastPrice_low", df.get("low", df["close"]))
    open_price = df.get("LastPrice_first", df.get("open", df["close"]))
    
    return (high - low) / (open_price + 1e-10)


# ========== 7. 复杂组合因子：同时使用多种依赖 ==========

@factor_spec(
    name="test_060",
    description="复杂组合因子",
    tags=["test", "complex", "combined"],
    raw_columns=["mid", "Volume"],
    operators=["detailed_trade_allocation"],
    related={
        "source": "explicit",
        "symbols": ["hc"],
        "columns": ["mid"],
    },
    stats={
        "days": 5,
        "columns": ["mid", "Volume"],
        "metrics": ["mean", "std"],
    },
)
def calc_test_060(df: pd.DataFrame, ctx=None) -> pd.Series:
    """
    综合因子：结合算子、关联品种、历史统计量。
    
    计算逻辑:
    1. 主动买卖不平衡
    2. 跨品种价差
    3. 历史标准化
    """
    # 主动买卖
    allo = ctx.operators["detailed_trade_allocation"]
    order_imbalance = (allo["active_buy_vol"] - allo["active_sell_vol"]) / (df["Volume"] + 1e-10)
    
    # 跨品种价差
    hc_mid = ctx.related["hc"]["mid"]
    spread = df["mid"] - hc_mid
    
    # 历史标准化
    hist_vol_mean = df["hist5__Volume__mean"]
    hist_vol_std = df["hist5__Volume__std"]
    vol_zscore = (df["Volume"] - hist_vol_mean) / (hist_vol_std + 1e-10)
    
    # 综合得分
    combined = (
        0.4 * order_imbalance.fillna(0) +
        0.3 * (spread / spread.rolling(50, min_periods=1).std() + 1e-10).fillna(0) +
        0.3 * vol_zscore.fillna(0)
    )
    
    return combined


# ========== 导出所有测试因子 ==========

TEST_FACTORS = [
    calc_test_001,
    calc_test_002,
    calc_test_010,
    calc_test_011,
    calc_test_020,
    calc_test_021,
    calc_test_030,
    calc_test_031,
    calc_test_040,
    calc_test_041,
    calc_test_050,
    calc_test_051,
    calc_test_060,
]

FACTOR_MAP = {getattr(f, "__factor_spec__").name: f for f in TEST_FACTORS}
