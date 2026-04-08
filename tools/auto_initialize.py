"""
自动初始化工具 - 首次运行时自动构建 catalog 和配置

功能:
1. 检查 catalog.parquet 是否存在
2. 如果不存在，自动运行 build_catalog_standalone.py
3. 确保生产脚本可以无依赖运行
"""

import sys
import subprocess
from pathlib import Path

# Add project root to path
project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root))


def check_and_initialize():
    """
    检查并初始化必要的配置文件。
    
    Returns:
        bool: 是否成功初始化
    """
    meta_root = project_root / "_meta"
    catalog_path = meta_root / "catalog.parquet"
    main_contract_path = meta_root / "main_contracts_auto.json"
    
    print("=" * 80)
    print("AUTO-INITIALIZATION CHECK")
    print("=" * 80)
    print()
    
    # Check if catalog exists
    if catalog_path.exists():
        print(f"✓ Catalog exists: {catalog_path}")
        
        # Try to load and verify
        try:
            import pandas as pd
            catalog_df = pd.read_parquet(catalog_path)
            print(f"  Records: {len(catalog_df):,} files")
            
            if catalog_df.empty:
                print("  ⚠ Warning: Catalog is empty, will rebuild...")
                need_rebuild = True
            else:
                print(f"  Symbols: {catalog_df['symbol'].unique().tolist()}")
                print(f"  Date range: {catalog_df['trading_day'].min()} to {catalog_df['trading_day'].max()}")
                need_rebuild = False
        except Exception as e:
            print(f"  ✗ Error reading catalog: {e}")
            need_rebuild = True
    else:
        print(f"✗ Catalog not found: {catalog_path}")
        need_rebuild = True
    
    # Rebuild if needed
    if need_rebuild:
        print("\n" + "=" * 80)
        print("BUILDING CATALOG AND CONFIGS")
        print("=" * 80)
        print()
        
        # Run the standalone builder
        builder_script = project_root / "tools" / "build_catalog_standalone.py"
        
        if not builder_script.exists():
            print(f"ERROR: Builder script not found: {builder_script}")
            print("Please create it first.")
            return False
        
        print(f"Running: python {builder_script}")
        print()
        
        result = subprocess.run(
            [sys.executable, str(builder_script)],
            cwd=str(project_root),
        )
        
        if result.returncode != 0:
            print(f"\nERROR: Build process failed with code {result.returncode}")
            return False
        
        print()
        print("=" * 80)
        print("BUILD COMPLETE")
        print("=" * 80)
        
        # Verify the results
        if catalog_path.exists():
            import pandas as pd
            catalog_df = pd.read_parquet(catalog_path)
            print(f"✓ New catalog created: {len(catalog_df):,} files")
        
        if main_contract_path.exists():
            import json
            with open(main_contract_path, "r") as f:
                mc_list = json.load(f)
            print(f"✓ Main contracts created: {len(mc_list):,} days")
    
    print()
    print("=" * 80)
    print("INITIALIZATION COMPLETE ✓")
    print("=" * 80)
    print()
    print("You can now run factor production:")
    print(f"  python pipeline/produce_all_factors_unified.py --workers 4\n")
    
    return True


if __name__ == "__main__":
    success = check_and_initialize()
    sys.exit(0 if success else 1)
