"""Orderflow operators - trade allocation and market microstructure primitives."""

from __future__ import annotations

import numpy as np
import pandas as pd

from .registry import register_operator


@register_operator(
    name="detailed_trade_allocation",
    required_columns=[
        "Volume",
        "Turnover",
        "mid",
        "b1",
        "b2",
        "b3",
        "b4",
        "b5",
        "b1_v_m",
        "b2_v_m",
        "b3_v_m",
        "b4_v_m",
        "b5_v_m",
        "a1",
        "a2",
        "a3",
        "a4",
        "a5",
        "a1_v_m",
        "a2_v_m",
        "a3_v_m",
        "a4_v_m",
        "a5_v_m",
    ],
    output_columns=[
        "sell_qty_b1",
        "sell_qty_b2",
        "sell_qty_b3",
        "sell_qty_b4",
        "sell_qty_b5",
        "buy_qty_a1",
        "buy_qty_a2",
        "buy_qty_a3",
        "buy_qty_a4",
        "buy_qty_a5",
        "active_buy_vol",
        "active_sell_vol",
        "order_imbalance",
    ],
    description="Allocate trades to active buy/sell sides based on price-matching rules",
    tags=["orderflow", "trade_allocation", "market_microstructure"],
)
def detailed_trade_allocation(df: pd.DataFrame) -> pd.DataFrame:
    """
    详细的成交分配算子，将成交量分解到主动买入和主动卖出。

    核心逻辑:
    1. 使用 LastPrice 与 Bid/Ask 价格比较判断主动方向
    2. 如果 LastPrice > BidPrice1，认为是主动买入
    3. 如果 LastPrice < AskPrice1，认为是主动卖出
    4. 如果介于中间，根据前后关系分配

    参数:
        df: 包含盘口和成交数据的 DataFrame

    返回:
        包含分配结果的 DataFrame
    """
    if df.empty:
        return pd.DataFrame(index=df.index)

    result = pd.DataFrame(index=df.index)

    # 基础检查
    required_cols = [
        "LastPrice",
        "Volume",
        "b1",
        "b2",
        "b3",
        "b4",
        "b5",
        "b1_v_m",
        "b2_v_m",
        "b3_v_m",
        "b4_v_m",
        "b5_v_m",
        "a1",
        "a2",
        "a3",
        "a4",
        "a5",
        "a1_v_m",
        "a2_v_m",
        "a3_v_m",
        "a4_v_m",
        "a5_v_m",
    ]

    missing_cols = [col for col in required_cols if col not in df.columns]
    if missing_cols:
        raise ValueError(f"detailed_trade_allocation 缺少必要列：{missing_cols}")

    last_price = df["LastPrice"].fillna(df["mid"])
    volume = df["Volume"]

    # 主动买入判断：成交价 >= Ask1
    active_buy_mask = last_price >= df["a1"]

    # 主动卖出判断：成交价 <= Bid1
    active_sell_mask = last_price <= df["b1"]

    # 中间价成交：按前后关系或默认平分
    neutral_mask = ~(active_buy_mask | active_sell_mask)

    # 初始化结果列
    for i in range(1, 6):
        result[f"buy_qty_a{i}"] = 0.0
        result[f"sell_qty_b{i}"] = 0.0

    # 主动买入：优先消耗 Ask1，然后 Ask2...
    buy_vol = volume.where(active_buy_mask, 0.0)
    remaining = buy_vol.copy()

    for i in range(1, 6):
        ask_vol_col = f"a{i}_v_m"
        alloc_col = f"buy_qty_a{i}"

        allocated = np.minimum(remaining, df[ask_vol_col].fillna(0))
        result[alloc_col] = allocated
        remaining = remaining - allocated
        remaining = remaining.clip(lower=0)

    # 主动卖出：优先消耗 Bid1，然后 Bid2...
    sell_vol = volume.where(active_sell_mask, 0.0)
    remaining = sell_vol.copy()

    for i in range(1, 6):
        bid_vol_col = f"b{i}_v_m"
        alloc_col = f"sell_qty_b{i}"

        allocated = np.minimum(remaining, df[bid_vol_col].fillna(0))
        result[alloc_col] = allocated
        remaining = remaining - allocated
        remaining = remaining.clip(lower=0)

    # 中性成交：简单处理为平分或按盘口比例
    if neutral_mask.any():
        neutral_vol = volume.where(neutral_mask, 0.0)
        # 简化处理：一半计入主动买，一半计入主动卖
        half_vol = neutral_vol / 2.0

        # 主动买侧分配到 a1
        result.loc[neutral_mask, "buy_qty_a1"] += half_vol.where(neutral_mask, 0.0)
        # 主动卖侧分配到 b1
        result.loc[neutral_mask, "sell_qty_b1"] += half_vol.where(neutral_mask, 0.0)

    # 计算汇总指标
    result["active_buy_vol"] = sum(result[f"buy_qty_a{i}"] for i in range(1, 6))
    result["active_sell_vol"] = sum(result[f"sell_qty_b{i}"] for i in range(1, 6))
    result["order_imbalance"] = (
        result["active_buy_vol"] - result["active_sell_vol"]
    ) / (result["active_buy_vol"] + result["active_sell_vol"] + 1e-10)

    return result


@register_operator(
    name="order_imbalance",
    required_columns=["Volume", "LastPrice", "b1", "a1"],
    output_columns=["oi_simple", "oi_signed"],
    description="Simple order imbalance metrics",
    tags=["orderflow", "imbalance"],
)
def order_imbalance(df: pd.DataFrame) -> pd.Series | pd.DataFrame:
    """
    简单的订单不平衡指标。

    计算:
    - oi_simple: (主动买 - 主动卖) / 总成交量
    - oi_signed: 带符号的不平衡，正表示买方主导
    """
    if df.empty:
        return pd.DataFrame(index=df.index)

    last_price = df["LastPrice"].fillna(df.get("mid", (df["b1"] + df["a1"]) / 2))
    volume = df["Volume"]

    # 简单判断
    buy_mask = last_price >= df["a1"]
    sell_mask = last_price <= df["b1"]

    buy_vol = volume.where(buy_mask, 0.0)
    sell_vol = volume.where(sell_mask, 0.0)

    total_vol = buy_vol + sell_vol + 1e-10

    result = pd.DataFrame(index=df.index)
    result["oi_simple"] = (buy_vol - sell_vol) / total_vol
    result["oi_signed"] = result["oi_simple"]

    return result


@register_operator(
    name="trade_sign",
    required_columns=["LastPrice", "b1", "a1", "mid"],
    output_columns=["trade_sign", "aggressive_score"],
    description="Determine trade direction and aggressiveness",
    tags=["orderflow", "trade_classification"],
)
def trade_sign(df: pd.DataFrame) -> pd.Series | pd.DataFrame:
    """
    交易方向标记。

    返回:
    - trade_sign: +1 主动买，-1 主动卖，0 中性
    - aggressive_score: 攻击性得分 (考虑价格偏离中价的程度)
    """
    if df.empty:
        return pd.DataFrame(index=df.index)

    last_price = df["LastPrice"]
    mid = df["mid"]
    spread = df["a1"] - df["b1"]

    result = pd.DataFrame(index=df.index)

    # 基础方向
    buy_mask = last_price >= df["a1"]
    sell_mask = last_price <= df["b1"]

    trade_sign = pd.Series(0, index=df.index)
    trade_sign[buy_mask] = 1
    trade_sign[sell_mask] = -1

    result["trade_sign"] = trade_sign

    # 攻击性得分：价格偏离中价的程度归一化
    price_deviation = (last_price - mid).abs() / (spread / 2 + 1e-10)
    result["aggressive_score"] = price_deviation.clip(0, 1) * trade_sign.abs()

    return result
