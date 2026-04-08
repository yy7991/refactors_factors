"""
Factor Function Library - All factor calculation functions

This module contains all factor calculation functions with @factor_spec decorators.
Each function is decorated with its specification for automatic data loading.
"""

from __future__ import annotations
import numpy as np
import pandas as pd
from typing import Dict, Any

# Import enhanced decorator
from factors.decorators_enhanced import factor_spec


# ============================================================================
# Helper Functions
# ============================================================================

def get_price_column(df: pd.DataFrame) -> str:
    """Get the appropriate price column name."""
    if "close" in df.columns:
        return "close"
    elif "LastPrice_last" in df.columns:
        return "LastPrice_last"
    elif "LastPrice" in df.columns:
        return "LastPrice"
    else:
        raise ValueError("No price column found")


def calculate_returns(prices: pd.Series, periods: int = 1) -> pd.Series:
    """Calculate returns from price series."""
    # Use fill_method=None to avoid FutureWarning
    return prices.pct_change(periods=periods, fill_method=None).fillna(0)


def calculate_volatility(returns: pd.Series, window: int = 60, min_periods: int = 20) -> pd.Series:
    """Calculate rolling volatility."""
    return returns.rolling(window=window, min_periods=min_periods).std().fillna(0)


# ============================================================================
# Cross-Variety Factors (跨品种因子)
# ============================================================================

@factor_spec(
    name="cross_var_return_diff",
    description="Cross-variety return difference",
    raw_columns=["LastPrice"],
    related={"dynamic": None},  # Auto-load related varieties from config
    history=None,  # No history needed
    bar=None,  # Works with both tick and bar
    category="cross_variety",
    version="1.0.0",
)
def calc_cross_var_return_diff(ctx) -> pd.Series:
    """
    Cross-Variety Return Difference
    
    Calculates the difference between current variety's return and 
    the average return of related varieties.
    
    Note: Related data is aggregated to bars using SAME index as current.
          Then reindexed to ensure identical datetime alignment.
    
    Returns:
        pd.Series: Return difference
    """
    price_col = get_price_column(ctx.df)
    current_returns = calculate_returns(ctx.df[price_col])
    
    # DEBUG: Check what we have in ctx.related
    print(f"\n[DEBUG] {ctx.symbol} on {ctx.trading_day} {ctx.session_scope}")
    print(f"  ctx.df type: {type(ctx.df).__name__}, shape: {ctx.df.shape}, index: {len(ctx.df.index)} timestamps")
    
    related_returns_aligned = []
    for rel_symbol, rel_df in ctx.related.items():
        print(f"  Related '{rel_symbol}': type={type(rel_df).__name__}, empty={rel_df.empty}, shape={rel_df.shape if not rel_df.empty else 'N/A'}")
        if not rel_df.empty and get_price_column(rel_df):
            rel_price_col = get_price_column(rel_df)
            
            # Reindex prices to current index (should already be aligned from portal)
            rel_prices_aligned = rel_df[rel_price_col].reindex(
                ctx.df.index,
                method='nearest'
            ).ffill().bfill().fillna(0)
            
            print(f"    After reindex: len={len(rel_prices_aligned)}, NaN={rel_prices_aligned.isna().sum()}")
            
            rel_ret = calculate_returns(rel_prices_aligned)
            print(f"    Returns: len={len(rel_ret)}, NaN={rel_ret.isna().sum()}")
            
            related_returns_aligned.append(rel_ret)
    
    if related_returns_aligned:
        avg_related_return = pd.concat(related_returns_aligned, axis=1).mean(axis=1)
        return_diff = current_returns - avg_related_return
        return_diff.name = "cross_var_return_diff"
        print(f"  Result: len={len(return_diff)}, NaN={return_diff.isna().sum()} ({return_diff.isna().sum()/len(return_diff)*100:.1f}%)")
        return return_diff
    
    return current_returns * 0


@factor_spec(
    name="cross_var_volume_corr",
    description="Cross-variety volume correlation",
    raw_columns=["Volume"],
    related={"dynamic": None},  # Auto-load related varieties
    history={"days": 5, "mode": "prepend"},  # Need 5 days history
    bar=None,
    category="cross_variety",
    version="1.0.0",
)
def calc_cross_var_volume_corr(ctx) -> pd.Series:
    """
    Cross-Variety Volume Correlation
    
    Calculates the correlation between current variety's volume and
    the average volume of related varieties.
    
    Note: Related data is aggregated to bars using SAME index as current.
          Then reindexed to ensure identical datetime alignment.
    
    Returns:
        pd.Series: Volume correlation
    """
    volume = ctx.df["Volume"].fillna(0)
    
    related_volumes_aligned = []
    for rel_symbol, rel_df in ctx.related.items():
        if not rel_df.empty and "Volume" in rel_df.columns:
            # Reindex volumes to current index (should already be aligned from portal)
            rel_vol_aligned = rel_df["Volume"].reindex(
                ctx.df.index,
                method='nearest'
            ).ffill().bfill().fillna(0)
            related_volumes_aligned.append(rel_vol_aligned)
    
    if related_volumes_aligned:
        avg_related_volume = pd.concat(related_volumes_aligned, axis=1).mean(axis=1)
        corr = volume.rolling(60, min_periods=20).corr(avg_related_volume).fillna(0)
        corr.name = "cross_var_volume_corr"
        return corr
    
    return volume * 0


# ============================================================================
# Cross-Date Factors (跨日期因子)
# ============================================================================

@factor_spec(
    name="cross_date_return_3d",
    description="3-day cross-date return",
    raw_columns=["LastPrice"],
    related=None,  # No related varieties needed
    history={"days": 3, "mode": "prepend"},  # Need 3 days history
    bar=None,
    category="cross_date",
    version="1.0.0",
)
def calc_cross_date_return_3d(ctx) -> pd.Series:
    """
    Cross-Date Return (3-day)
    
    Calculates return over the past 3 days using historical data.
    
    Returns:
        pd.Series: 3-day cross-date return
    """
    price_col = get_price_column(ctx.full_df)
    full_prices = ctx.full_df[price_col]
    
    # Calculate return with period length equal to current day's data
    returns = full_prices.pct_change(periods=len(ctx.df)).fillna(0)
    current_returns = returns.loc[ctx.current_index]
    current_returns.name = "cross_date_return_3d"
    return current_returns


@factor_spec(
    name="cross_date_vol_ratio_3d",
    description="3-day volatility ratio",
    raw_columns=["LastPrice"],
    related=None,
    history={"days": 3, "mode": "append"},  # History appended to current
    bar=None,
    category="cross_date",
    version="1.0.0",
)
def calc_cross_date_vol_ratio_3d(ctx) -> pd.Series:
    """
    Cross-Date Volatility Ratio (3-day)
    
    Compares current volatility to historical volatility.
    
    Returns:
        pd.Series: Volatility ratio
    """
    price_col = get_price_column(ctx.df)
    current_returns = calculate_returns(ctx.df[price_col])
    current_vol = calculate_volatility(current_returns)
    
    hist_vols = []
    for day, hist_df in ctx.history.items():
        if not hist_df.empty and get_price_column(hist_df):
            hist_price_col = get_price_column(hist_df)
            hist_ret = calculate_returns(hist_df[hist_price_col])
            hist_vol = calculate_volatility(hist_ret)
            hist_vols.append(hist_vol.mean())
    
    if hist_vols:
        avg_hist_vol = np.mean(hist_vols)
        vol_ratio = (current_vol / avg_hist_vol).fillna(0)
        vol_ratio.name = "cross_date_vol_ratio_3d"
        return vol_ratio
    
    return current_vol


@factor_spec(
    name="cross_date_var_spread_mean",
    description="Variance spread mean",
    raw_columns=["LastPrice"],
    related=None,
    history={"days": 3, "mode": "prepend"},
    bar=None,
    category="cross_date",
    version="1.0.0",
)
def calc_cross_date_var_spread_mean(ctx) -> pd.Series:
    """
    Cross-Date Variance Spread Mean
    
    Calculates variance spread between current and historical periods.
    
    Returns:
        pd.Series: Variance spread mean
    """
    price_col = get_price_column(ctx.df)
    current_returns = calculate_returns(ctx.df[price_col])
    current_var = current_returns.rolling(60, min_periods=20).var()
    
    hist_vars = []
    for day, hist_df in ctx.history.items():
        if not hist_df.empty and get_price_column(hist_df):
            hist_price_col = get_price_column(hist_df)
            hist_ret = calculate_returns(hist_df[hist_price_col])
            hist_var = hist_ret.rolling(60, min_periods=20).var()
            hist_vars.append(hist_var.mean())
    
    if hist_vars:
        avg_hist_var = np.mean(hist_vars)
        var_spread = (current_var - avg_hist_var).fillna(0)
        var_spread.name = "cross_date_var_spread_mean"
        return var_spread
    
    return current_var


# ============================================================================
# Bar-Level Factors (Bar 级别因子)
# ============================================================================

@factor_spec(
    name="bar_momentum",
    description="Bar-level momentum",
    raw_columns=["LastPrice"],
    related=None,
    history=None,
    bar={"type": "time", "freq": "1s"},  # Requires bar aggregation
    category="bar",
    version="1.0.0",
)
def calc_bar_momentum(ctx) -> pd.Series:
    """
    Bar Momentum Factor
    
    Calculates momentum based on bar-level price changes.
    
    Returns:
        pd.Series: Momentum factor
    """
    price_col = get_price_column(ctx.df)
    
    # Simple momentum: rate of change over last N bars
    momentum = ctx.df[price_col].pct_change(periods=10).fillna(0)
    momentum.name = "bar_momentum"
    return momentum


@factor_spec(
    name="bar_volatility",
    description="Bar-level volatility",
    raw_columns=["LastPrice"],
    related=None,
    history=None,
    bar={"type": "time", "freq": "1s"},  # Requires bar aggregation
    category="bar",
    version="1.0.0",
)
def calc_bar_volatility(ctx) -> pd.Series:
    """
    Bar Volatility Factor
    
    Calculates volatility at bar level.
    
    Returns:
        pd.Series: Volatility factor
    """
    price_col = get_price_column(ctx.df)
    returns = calculate_returns(ctx.df[price_col])
    volatility = calculate_volatility(returns, window=20, min_periods=10)
    volatility.name = "bar_volatility"
    return volatility


# ============================================================================
# Factor Registry
# ============================================================================

FACTOR_FUNCTIONS = {
    # Cross-Variety Factors
    "cross_var_return_diff": calc_cross_var_return_diff,
    "cross_var_volume_corr": calc_cross_var_volume_corr,
    
    # Cross-Date Factors
    "cross_date_return_3d": calc_cross_date_return_3d,
    "cross_date_vol_ratio_3d": calc_cross_date_vol_ratio_3d,
    "cross_date_var_spread_mean": calc_cross_date_var_spread_mean,
    
    # Bar-Level Factors
    "bar_momentum": calc_bar_momentum,
    "bar_volatility": calc_bar_volatility,
}

# Note: Factor specs are now attached to functions via @factor_spec decorator
# Access via: func.__factor_spec__


def get_factor_function(factor_name: str):
    """
    Get factor function by name.
    
    Args:
        factor_name: Name of the factor
        
    Returns:
        Callable factor function or None if not found
    """
    return FACTOR_FUNCTIONS.get(factor_name)


def get_factor_spec(factor_name: str) -> Dict[str, Any]:
    """
    Get factor specification by name.
    
    Args:
        factor_name: Name of the factor
        
    Returns:
        Dict with factor spec or None if not found
    """
    return FACTOR_SPECS.get(factor_name)


def list_factors(category: str = None) -> list:
    """
    List available factors.
    
    Args:
        category: Optional category filter ('cross_variety', 'cross_date', 'bar')
        
    Returns:
        List of factor names
    """
    if category is None:
        return list(FACTOR_FUNCTIONS.keys())
    
    return [
        name for name, spec in FACTOR_SPECS.items()
        if spec.get("category") == category
    ]


def get_all_factor_specs() -> Dict[str, Dict]:
    """Get all factor specifications."""
    return FACTOR_SPECS.copy()


def get_factor_requirements(factor_name: str) -> Dict[str, any]:
    """
    Get detailed requirements for a specific factor.
    
    Args:
        factor_name: Name of the factor
        
    Returns:
        Dict with requirements from __factor_spec__
    """
    func = FACTOR_FUNCTIONS.get(factor_name)
    if not func or not hasattr(func, '__factor_spec__'):
        return None
    
    spec = func.__factor_spec__
    return {
        "raw_columns": spec.raw_columns,
        "related": spec.related,
        "history": spec.history,
        "bar": spec.bar,
        "category": spec.category,
        "version": spec.version,
    }


def needs_related_data(factor_name: str) -> bool:
    """Check if factor requires related variety data."""
    func = FACTOR_FUNCTIONS.get(factor_name)
    return func is not None and hasattr(func, '__factor_spec__') and func.__factor_spec__.related is not None


def needs_history_data(factor_name: str) -> bool:
    """Check if factor requires historical data."""
    func = FACTOR_FUNCTIONS.get(factor_name)
    return func is not None and hasattr(func, '__factor_spec__') and func.__factor_spec__.history is not None


def needs_bar_aggregation(factor_name: str) -> bool:
    """Check if factor requires bar aggregation."""
    func = FACTOR_FUNCTIONS.get(factor_name)
    return func is not None and hasattr(func, '__factor_spec__') and func.__factor_spec__.bar is not None


def get_history_days(factor_name: str) -> int:
    """Get number of history days required by factor."""
    func = FACTOR_FUNCTIONS.get(factor_name)
    if not func or not hasattr(func, '__factor_spec__') or not func.__factor_spec__.history:
        return 0
    return func.__factor_spec__.history.get("days", 0)


def get_bar_frequency(factor_name: str) -> str:
    """Get required bar frequency for factor."""
    func = FACTOR_FUNCTIONS.get(factor_name)
    if not func or not hasattr(func, '__factor_spec__') or not func.__factor_spec__.bar:
        return None
    return func.__factor_spec__.bar.get("freq", "1s")
