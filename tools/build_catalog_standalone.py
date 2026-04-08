"""
独立 Catalog 构建工具 - 完全脱离原框架

功能:
1. 扫描数据目录生成 catalog.parquet
2. 自动生成主力合约配置（基于文件统计）
3. 不依赖任何旧框架文件

使用:
python tools/build_catalog_standalone.py --data-root /path/to/data
"""

import sys
import json
import re
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict
import argparse

# Add project root to path
project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root))

import pandas as pd
from catalog import CatalogBuilder, CatalogStore


def scan_and_build_catalog(data_root: Path, output_path: Path) -> pd.DataFrame:
    """
    扫描数据目录并生成 catalog。
    
    Args:
        data_root: 数据根目录（包含 rb/hc/fe 等品种目录）
        output_path: 输出 catalog.parquet 路径
    
    Returns:
        catalog DataFrame
    """
    print("=" * 80)
    print("STEP 1: BUILDING CATALOG")
    print("=" * 80)
    print(f"\nData root: {data_root}")
    print(f"Output: {output_path}\n")
    
    # Use CatalogBuilder to scan
    catalog_builder = CatalogBuilder(
        roots={"raw_data": data_root},
        probe_mode="header",
    )
    
    catalog_df = catalog_builder.build()
    
    print(f"Scanned {len(catalog_df)} files\n")
    
    # Show statistics
    print("Files by symbol:")
    print(catalog_df["symbol"].value_counts())
    print()
    
    print("Files by year/month:")
    catalog_df["year_month"] = catalog_df["year"] + "-" + catalog_df["month"]
    print(catalog_df["year_month"].value_counts().sort_index())
    print()
    
    # Save catalog
    output_path.parent.mkdir(parents=True, exist_ok=True)
    catalog_store = CatalogStore(output_path)
    catalog_store.save(catalog_df)
    
    print(f"✓ Catalog saved to: {output_path}\n")
    
    return catalog_df


def extract_contract_from_path(path: str) -> str:
    """从文件路径中提取合约代码"""
    # 示例：/data/rb/day/2024/05/RB2405_20240520.csv -> RB2405
    filename = Path(path).stem  # RB2405_20240520
    contract = filename.split("_")[0]  # RB2405
    return contract.upper()


def auto_generate_main_contracts(catalog_df: pd.DataFrame) -> dict:
    """
    根据 catalog 自动生成主力合约配置。
    
    逻辑:
    1. 对每个品种每个交易日，统计哪个合约出现最多
    2. 假设出现最多的就是主力合约
    
    Returns:
        主力合约配置字典 {date: {symbol: contract}}
    """
    print("=" * 80)
    print("STEP 2: AUTO-GENERATING MAIN CONTRACTS")
    print("=" * 80)
    print("\nAnalyzing contract distribution...\n")
    
    # Filter only raw CSV files
    raw_data = catalog_df[catalog_df["file_type"] == "raw_csv"].copy()
    
    # Extract contract from path
    raw_data["contract_parsed"] = raw_data["path"].apply(extract_contract_from_path)
    
    # Group by trading_day and symbol, count contracts
    grouped = raw_data.groupby(["trading_day", "symbol", "contract_parsed"]).size().reset_index(name="count")
    
    # Find the most frequent contract for each day/symbol
    idx = grouped.groupby(["trading_day", "symbol"])["count"].idxmax()
    main_contracts = grouped.loc[idx].reset_index(drop=True)
    
    # Reorganize to date-based format
    dates_dict = defaultdict(dict)
    for _, row in main_contracts.iterrows():
        date = str(row["trading_day"])
        symbol = row["symbol"].lower()
        contract = row["contract_parsed"]
        dates_dict[date][symbol] = contract
    
    # Sort by date
    sorted_dates = sorted(dates_dict.keys())
    
    print(f"Found main contracts for {len(sorted_dates)} trading days\n")
    
    # Show sample
    print("Sample (first 5 days):")
    for date in sorted_dates[:5]:
        print(f"  {date}: {dates_dict[date]}")
    print()
    
    # Convert to list format for JSON
    main_contract_list = []
    for date in sorted_dates:
        record = {"date": date}
        record.update(dates_dict[date])
        main_contract_list.append(record)
    
    return main_contract_list


def save_main_contracts(main_contract_list: list, output_path: Path):
    """保存主力合约配置到 YAML 文件"""
    print("=" * 80)
    print("STEP 3: SAVING MAIN CONTRACT CONFIG")
    print("=" * 80)
    print(f"\nOutput: {output_path}\n")
    
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Convert to YAML format with metadata
    import yaml
    from datetime import datetime
    
    yaml_data = {
        "contracts": main_contract_list,
        "metadata": {
            "version": "v1",
            "generated_by": "build_catalog_standalone.py",
            "description": "Auto-generated main contract configuration based on trading volume",
            "last_updated": datetime.now().strftime("%Y-%m-%d"),
        }
    }
    
    with open(output_path, "w", encoding="utf-8") as f:
        yaml.dump(
            yaml_data, 
            f, 
            default_flow_style=False, 
            allow_unicode=True,
            sort_keys=False
        )
    
    print(f"✓ Main contracts saved to: {output_path}\n")
    
    # Show file size
    file_size = output_path.stat().st_size
    print(f"File size: {file_size:,} bytes\n")


def analyze_catalog(catalog_df: pd.DataFrame):
    """分析 catalog 数据统计信息"""
    print("=" * 80)
    print("CATALOG STATISTICS")
    print("=" * 80)
    print()
    
    # Total files
    print(f"Total files: {len(catalog_df):,}\n")
    
    # By file type
    print("By file type:")
    print(catalog_df["file_type"].value_counts())
    print()
    
    # By symbol
    print("By symbol:")
    print(catalog_df["symbol"].value_counts())
    print()
    
    # By session
    print("By session:")
    print(catalog_df["session"].value_counts())
    print()
    
    # Date range
    if "trading_day" in catalog_df.columns:
        trading_days = sorted(catalog_df["trading_day"].astype(str).unique())
        print(f"Date range: {trading_days[0]} to {trading_days[-1]}")
        print(f"Total trading days: {len(trading_days)}\n")
    
    # By year/month
    if "year" in catalog_df.columns and "month" in catalog_df.columns:
        print("Recent months:")
        catalog_df["ym"] = catalog_df["year"] + "-" + catalog_df["month"]
        print(catalog_df["ym"].value_counts().sort_index().tail(6))
        print()


def verify_setup(catalog_path: Path, main_contract_path: Path):
    """验证生成的配置是否正确"""
    print("=" * 80)
    print("VERIFICATION")
    print("=" * 80)
    print()
    
    # Check catalog
    if catalog_path.exists():
        catalog_df = pd.read_parquet(catalog_path)
        print(f"✓ Catalog exists: {catalog_path}")
        print(f"  Records: {len(catalog_df):,}")
    else:
        print(f"✗ Catalog not found: {catalog_path}")
    print()
    
    # Check main contracts
    if main_contract_path.exists():
        # Try YAML first, then JSON
        try:
            import yaml
            if main_contract_path.suffix.lower() in ['.yaml', '.yml']:
                with open(main_contract_path, "r") as f:
                    mc_data = yaml.safe_load(f)
                mc_list = mc_data.get("contracts", [])
            else:
                with open(main_contract_path, "r") as f:
                    mc_list = json.load(f)
            print(f"✓ Main contracts exists: {main_contract_path}")
            print(f"  Days: {len(mc_list):,}")
            
            # Show last few records
            if mc_list:
                print(f"  Latest: {mc_list[-1]}")
        except Exception as e:
            print(f"✗ Error reading main contracts: {e}")
    else:
        print(f"✗ Main contracts not found: {main_contract_path}")
    print()


def run_standalone_build(
    data_root: Path = None,
    meta_root: Path = None,
    skip_main_contract: bool = False,
):
    """
    运行独立的 catalog 构建流程。
    
    Args:
        data_root: 数据根目录
        meta_root: 元数据输出目录
        skip_main_contract: 是否跳过主力合约生成
    """
    print("\n" + "=" * 80)
    print("STANDALONE CATALOG BUILDER - COMPLETE WORKFLOW")
    print("=" * 80 + "\n")
    
    # Default paths
    if data_root is None:
        data_root = project_root / "data"
    
    if meta_root is None:
        meta_root = project_root / "_meta"
    
    catalog_path = meta_root / "catalog.parquet"
    main_contract_path = meta_root / "main_contracts_auto.yaml"  # Changed to .yaml
    
    print("Configuration:")
    print(f"  Data root: {data_root}")
    print(f"  Meta root: {meta_root}")
    print(f"  Catalog output: {catalog_path}")
    print(f"  Main contracts output: {main_contract_path}")
    print()
    
    # Verify data directory exists
    if not data_root.exists():
        print(f"ERROR: Data directory not found: {data_root}\n")
        return False
    
    # Step 1: Build catalog
    catalog_df = scan_and_build_catalog(data_root, catalog_path)
    
    # Analyze
    analyze_catalog(catalog_df)
    
    # Step 2: Auto-generate main contracts
    if not skip_main_contract:
        main_contract_list = auto_generate_main_contracts(catalog_df)
        save_main_contracts(main_contract_list, main_contract_path)
    
    # Step 3: Verify
    verify_setup(catalog_path, main_contract_path)
    
    print("=" * 80)
    print("BUILD COMPLETE ✓")
    print("=" * 80)
    print()
    print("Next steps:")
    print("1. Review generated main_contracts_auto.yaml")
    print("2. Update produce_all_factors_unified.py to use new paths:")
    print(f"   main_contract_path = Path('{main_contract_path}')")
    print("3. Run factor production:\n")
    print(f"   python pipeline/produce_all_factors_unified.py --workers 4\n")
    
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="独立 Catalog 构建工具 - 不依赖旧框架"
    )
    
    parser.add_argument(
        "--data-root",
        type=Path,
        help="数据根目录（默认：project_root/data）",
    )
    
    parser.add_argument(
        "--meta-root",
        type=Path,
        help="元数据输出目录（默认：project_root/_meta）",
    )
    
    parser.add_argument(
        "--skip-main-contract",
        action="store_true",
        help="跳过主力合约配置生成",
    )
    
    args = parser.parse_args()
    
    success = run_standalone_build(
        data_root=args.data_root,
        meta_root=args.meta_root,
        skip_main_contract=args.skip_main_contract,
    )
    
    sys.exit(0 if success else 1)
