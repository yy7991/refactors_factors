"""
统一因子生产工具 - 支持 tick/bar 级别、日期范围、品种筛选、因子选择

功能:
- 支持 tick 级和 bar 级数据聚合
- 支持所有已定义的因子
- 灵活的日期范围和品种筛选
- 多进程并行处理
- Bar 缓存优化
"""

import sys
from pathlib import Path
from datetime import datetime
from concurrent.futures import ProcessPoolExecutor, as_completed
import pandas as pd
import numpy as np

# Add project root to path
project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root))

from catalog import CatalogStore, MainContractStore
from catalog.main_contract import MainContractResolver
from catalog.resolver import CatalogResolver
from dataio import DataPortal, build_default_processing_config, PortalFactorRunner
from storage import FactorStore, LabelStore
from factors.factors_fun import FACTOR_FUNCTIONS, get_factor_function


# ============================================================================
# Part 1: Factor Library Import
# ============================================================================
# All factor functions are now in factors/factors_fun.py
# Import and use via FACTOR_FUNCTIONS, FACTOR_SPECS
# ============================================================================


# ============================================================================
# Part 2: Factor Selection Helpers
# ============================================================================

def get_factor_functions(factor_names: list = None, use_bar: bool = False) -> list:
    """
    Get list of factor functions to compute.
    
    Args:
        factor_names: List of factor names to select. None = all factors.
        use_bar: Whether to include bar-level factors (currently all factors work with bars)
        
    Returns:
        List of factor function objects
    """
    if factor_names is None:
        # Return all available factors
        return list(FACTOR_FUNCTIONS.values())
    
    selected = []
    for name in factor_names:
        func = get_factor_function(name)
        if func:
            selected.append(func)
        else:
            print(f"Warning: Unknown factor '{name}', skipping...")
    
    if not selected:
        print("No valid factors selected, using all factors.")
        return list(FACTOR_FUNCTIONS.values())
    
    return selected


def get_available_factors() -> list:
    """Get list of all available factor names."""
    return list(FACTOR_FUNCTIONS.keys())


# ============================================================================
# Part 3: 数据处理
# ============================================================================

def get_all_symbols_and_days(
    catalog_df: pd.DataFrame, 
    start_date: str = None, 
    end_date: str = None
) -> dict:
    """获取所有可用的品种和交易日（包含 day 和 night）。"""
    raw_data = catalog_df[catalog_df["file_type"] == "raw_csv"]
    
    symbol_days = {}
    for symbol in raw_data["symbol"].unique():
        symbol_data = raw_data[raw_data["symbol"] == symbol]
        trading_days = sorted(symbol_data["trading_day"].astype(str).unique().tolist())
        
        # Apply date filters
        if start_date:
            trading_days = [d for d in trading_days if d >= start_date]
        if end_date:
            trading_days = [d for d in trading_days if d <= end_date]
        
        day_sessions = {}
        for day in trading_days:
            day_data = symbol_data[symbol_data["trading_day"].astype(str) == day]
            sessions = set(day_data["session"].values)
            
            # Collect BOTH day and night sessions
            available_sessions = []
            if "day" in sessions:
                available_sessions.append("day")
            if "night" in sessions:
                available_sessions.append("night")
            
            # Store as list if both exist, otherwise single string
            day_sessions[day] = available_sessions if len(available_sessions) > 1 else available_sessions[0]
        
        if day_sessions:
            symbol_days[symbol] = day_sessions
    
    return symbol_days


def process_single_day(args):
    """处理单个品种单个工作日的因子计算。"""
    symbol, trading_day, session, config_dict, factor_functions, bar_spec = args
    
    try:
        from catalog import CatalogStore, MainContractStore
        from catalog.main_contract import MainContractResolver
        from catalog.resolver import CatalogResolver
        from dataio import DataPortal, build_default_processing_config, PortalFactorRunner
        from storage import FactorStore, LabelStore
        
        # Load catalog
        catalog_df = CatalogStore(config_dict["catalog_path"]).load()
        
        # Load main contract
        main_contract_df = MainContractStore(config_dict["main_contract_path"]).load_table()
        
        # Build resolver
        processing_config = build_default_processing_config(config_dict["data_root"])
        
        resolver = CatalogResolver(
            catalog_df=catalog_df,
            main_contract_resolver=MainContractResolver(main_contract_df),
            relations_map=config_dict["relations_map"],
        )
        
        # Create DataPortal with bar cache (only if using bar)
        use_bar_cache = bar_spec is not None
        portal = DataPortal(
            resolver=resolver,
            processing_config=processing_config,
            cache_root=config_dict["cache_root"] / f"cache_{symbol}",
            use_bar_cache=use_bar_cache,
        )
        
        runner = PortalFactorRunner(portal)
        
        # Compute factors with or without bar
        factor_frame, context = runner.compute_factor_frame(
            symbol=symbol,
            trading_day=trading_day,
            factor_functions=factor_functions,
            session_scope=session,
            bar_spec=bar_spec,
        )
        
        # Save factors
        factor_store = FactorStore(config_dict["output_root"] / "factors")
        factor_store.save(symbol, trading_day, factor_frame, session, mode="overwrite")
        
        # Save labels
        label_cols = [col for col in context.current_df.columns if col.startswith("target_")]
        if label_cols:
            label_frame = context.current_df[label_cols].rename(
                columns={
                    "target_5": "rts_5",
                    "target_10": "rts_10",
                    "target_15": "rts_15",
                    "target_30": "rts_30",
                }
            )
            label_store = LabelStore(config_dict["output_root"] / "labels")
            label_store.save(symbol, trading_day, label_frame, session, label_version="v1")
        
        return {
            "symbol": symbol,
            "trading_day": trading_day,
            "session": session,
            "status": "success",
            "rows": len(factor_frame),
            "factor_count": factor_frame.shape[1],
        }
        
    except Exception as e:
        return {
            "symbol": symbol,
            "trading_day": trading_day,
            "session": session,
            "status": "error",
            "error": str(e),
        }


# ============================================================================
# Part 4: 主生产流程
# ============================================================================

def run_production(
    max_workers: int = 4,
    symbols_filter: list = None,
    start_date: str = None,
    end_date: str = None,
    factor_names: list = None,
    use_bar: bool = True,
    bar_freq: str = "1s",
):
    """运行批量因子生产。"""
    
    # Auto-initialize if needed
    from tools.auto_initialize import check_and_initialize
    if not check_and_initialize():
        print("ERROR: Initialization failed. Please run: python tools/auto_initialize.py")
        return
    
    print("=" * 80)
    if use_bar:
        print(f"BAR PRODUCTION - {bar_freq} TIME BAR")
    else:
        print("TICK PRODUCTION - NO BAR AGGREGATION")
    print("=" * 80)
    
    start_time = datetime.now()
    
    # Paths
    data_root = project_root / "data"
    meta_root = project_root / "_meta"
    
    if use_bar:
        output_root = project_root / f"output_{bar_freq.replace('s', 'sec')}_bar"
        cache_root = project_root / f"_{bar_freq.replace('s', 'sec')}_bar_cache"
    else:
        output_root = project_root / "output_tick"
        cache_root = project_root / "_tick_cache"
    
    # Use auto-generated main contracts (created by build_catalog_standalone.py)
    # Prefer YAML format, fallback to JSON if needed
    yaml_main_contract_path = meta_root / "main_contracts_auto.yaml"
    json_main_contract_path = meta_root / "main_contracts_auto.json"
    old_main_contract_path = Path("/home/yangpu/dyyprojects/m_t_futures/new_version/config/main_contracts_cvwaprb1124.json")
    
    # Priority: YAML > JSON (auto) > old JSON
    if yaml_main_contract_path.exists():
        main_contract_path = yaml_main_contract_path
        print(f"Using YAML main contract config: {main_contract_path}")
    elif json_main_contract_path.exists():
        main_contract_path = json_main_contract_path
        print(f"Using JSON main contract config: {main_contract_path}")
    elif old_main_contract_path.exists():
        print(f"Warning: Using old main contract config: {old_main_contract_path}")
        main_contract_path = old_main_contract_path
    else:
        print("Error: No main contract config found!")
        print("Please run: python tools/build_catalog_standalone.py")
        return
    
    for path in [meta_root, cache_root, output_root]:
        path.mkdir(parents=True, exist_ok=True)
    
    # Load catalog
    print("\n[1] Loading Catalog...")
    catalog_store = CatalogStore(meta_root / "catalog.parquet")
    catalog_df = catalog_store.load()
    print(f"    Loaded {len(catalog_df)} files")
    
    # Get available data with date filtering
    print("\n[2] Analyzing available data...")
    if start_date or end_date:
        print(f"    Date range: {start_date or 'earliest'} to {end_date or 'latest'}")
    symbol_days = get_all_symbols_and_days(catalog_df, start_date, end_date)
    
    # Apply symbol filter
    if symbols_filter:
        symbol_days = {k: v for k, v in symbol_days.items() if k in symbols_filter}
    
    total_combinations = sum(len(days) for days in symbol_days.values())
    print(f"    Symbols: {list(symbol_days.keys())}")
    print(f"    Total symbol-day combinations: {total_combinations:,}")
    
    # Prepare config
    config_dict = {
        "catalog_path": meta_root / "catalog.parquet",
        "main_contract_path": main_contract_path,
        "data_root": data_root,
        "cache_root": cache_root,
        "output_root": output_root,
        "relations_map": {
            "rb": ["hc", "fe"],
            "hc": ["rb", "fe"],
            "fe": ["rb", "hc"],
        },
    }
    
    # Get selected factors
    selected_factors = get_factor_functions(factor_names, use_bar)
    print(f"\n    Selected factors: {[func.__name__ for func in selected_factors]}")
    
    # Determine bar_spec based on parameters
    if use_bar:
        bar_spec = {"type": "time", "freq": bar_freq}
        print(f"    Bar spec: time_{bar_freq}")
    else:
        bar_spec = None  # Use tick data directly
    
    # Create task list
    tasks = []
    for symbol, day_sessions in symbol_days.items():
        for day, session_or_sessions in day_sessions.items():
            if isinstance(session_or_sessions, list):
                for session in session_or_sessions:
                    tasks.append((symbol, day, session, config_dict, selected_factors, bar_spec))
            else:
                tasks.append((symbol, day, session_or_sessions, config_dict, selected_factors, bar_spec))
    
    print(f"\n[3] Starting production with {max_workers} workers...")
    if use_bar:
        print(f"    Bar aggregation: ENABLED ({bar_freq})")
        print(f"    Bar cache: ENABLED")
    else:
        print(f"    Bar aggregation: DISABLED (using tick data)")
    print(f"    Factors: {[func.__name__ for func in selected_factors]}")
    print(f"    Total tasks: {len(tasks)} (includes both day and night sessions)")
    print(f"    Output directory: {output_root}")
    
    # Process in parallel
    results = []
    success_count = 0
    error_count = 0
    
    with ProcessPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(process_single_day, task) for task in tasks]
        
        for i, future in enumerate(as_completed(futures), 1):
            result = future.result()
            results.append(result)
            
            if result["status"] == "success":
                success_count += 1
            else:
                error_count += 1
            
            if i % 10 == 0 or i == len(futures):
                print(f"    Progress: {i}/{len(tasks)} ({success_count} ✓, {error_count} ✗)")
    
    # Summary
    end_time = datetime.now()
    duration = end_time - start_time
    
    print("\n" + "=" * 80)
    print("PRODUCTION SUMMARY")
    print("=" * 80)
    
    success_results = [r for r in results if r["status"] == "success"]
    error_results = [r for r in results if r["status"] == "error"]
    
    print(f"\nTotal processed: {len(results)}")
    print(f"Successful: {len(success_results)}")
    print(f"Errors: {len(error_results)}")
    print(f"Duration: {duration}")
    
    if success_results:
        total_rows = sum(r["rows"] for r in success_results)
        print(f"\nTotal factor rows: {total_rows:,}")
        print(f"Average rows per day: {total_rows / len(success_results):,.0f}")
    
    if error_results:
        print(f"\nErrors:")
        for err in error_results[:10]:
            print(f"  - {err['symbol']} {err['trading_day']} ({err['session']}): {err['error']}")
    
    print(f"\nOutput directory: {output_root}")
    print(f"Factors: {output_root / 'factors'}")
    print(f"Labels: {output_root / 'labels'}")
    print(f"Cache: {cache_root}")
    
    # Save summary
    summary_df = pd.DataFrame(results)
    summary_df.to_csv(output_root / "production_summary.csv", index=False)
    print(f"\nSummary saved to: {output_root / 'production_summary.csv'}")
    
    print("\n" + "=" * 80)
    print("PRODUCTION COMPLETE ✓")
    print("=" * 80)


# ============================================================================
# Part 5: 命令行接口
# ============================================================================

if __name__ == "__main__":
    import argparse
    
    # Get available factors for help message
    available_factors = get_available_factors()
    
    parser = argparse.ArgumentParser(
        description="Unified Factor Production Tool - Flexible factor/date/symbol selection",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""
Available Factors ({len(available_factors)} total):
  {', '.join(available_factors)}

Examples:
  # Test run (last day only)
  python pipeline/produce_all_factors_unified.py --test --workers 2
  
  # Full production (all data)
  python pipeline/produce_all_factors_unified.py --workers 8
  
  # Specific symbols and factors
  python pipeline/produce_all_factors_unified.py --symbols rb hc --factors cross_var_return_diff bar_momentum --workers 4
  
  # Date range
  python pipeline/produce_all_factors_unified.py --start-date 20250401 --end-date 20250430 --workers 4
  
  # Bar aggregation
  python pipeline/produce_all_factors_unified.py --bar-freq 5s --workers 4
"""
    )
    
    # Worker control
    parser.add_argument("--workers", type=int, default=4, help="Number of parallel worker processes (default: 4)")
    
    # Symbol filter
    parser.add_argument("--symbols", nargs="+", help="Symbols to process (default: all)")
    
    # Date range
    parser.add_argument("--start-date", type=str, help="Start date in YYYYMMDD format")
    parser.add_argument("--end-date", type=str, help="End date in YYYYMMDD format")
    
    # Factor selection
    parser.add_argument("--factors", nargs="+", choices=available_factors, 
                        help="Factors to compute (default: all). Choices: %(choices)s")
    
    # Bar aggregation control
    parser.add_argument("--no-bar", action="store_true", 
                        help="Disable bar aggregation, use raw tick data")
    parser.add_argument("--bar-freq", type=str, default="1s", 
                        choices=["1s", "3s", "5s", "10s", "1m", "5m"],
                        help="Bar aggregation frequency (default: 1s)")
    
    # Test mode
    parser.add_argument("--test", action="store_true", help="Test mode: process only last trading day")
    
    args = parser.parse_args()
    args.start_date = '20240112'; args.end_date = '20240112'; args.symbols = ['rb']; args.workers = 1; args.factors = ['cross_var_return_diff']
    # Handle test mode
    if args.test and not (args.start_date or args.end_date):
        print("TEST MODE: Processing only last day for each symbol\n")
        run_production(
            max_workers=args.workers,
            symbols_filter=args.symbols,
            factor_names=args.factors,
            use_bar=not args.no_bar,
            bar_freq=args.bar_freq,
        )
    else:
        run_production(
            max_workers=args.workers,
            symbols_filter=args.symbols,
            start_date=args.start_date,
            end_date=args.end_date,
            factor_names=args.factors,
            use_bar=not args.no_bar,
            bar_freq=args.bar_freq,
        )
