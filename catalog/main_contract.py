"""主力合约读写与查询。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional, Union

import pandas as pd

try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False

from .parser import normalize_symbol


class MainContractStore:
    """负责读取主力合约配置，并转成平表。"""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def load_config(self) -> Union[dict, list]:
        """读取配置文件（支持 JSON 和 YAML）。"""
        with open(self.path, "r", encoding="utf-8") as file:
            if self.path.suffix.lower() in ['.yaml', '.yml']:
                if not HAS_YAML:
                    raise ImportError("PyYAML is required for YAML support. Install with: pip install pyyaml")
                return yaml.safe_load(file)
            else:
                return json.load(file)

    def load_table(self) -> pd.DataFrame:
        """把层级配置转成统一平表。"""
        raw_info = self.load_config()
        rows: list[dict] = []

        # Handle YAML format: {contracts: [...], metadata: {...}}
        if isinstance(raw_info, dict) and "contracts" in raw_info:
            contracts_list = raw_info["contracts"]
            
            for day_info in contracts_list:
                trading_day = day_info.get("date") or day_info.get("trading_day")
                if not trading_day:
                    continue
                
                # Extract all symbol-contract pairs
                for key, contract in day_info.items():
                    if key in ["date", "trading_day"]:
                        continue
                    
                    normalized_symbol = normalize_symbol(key)
                    rows.append(
                        {
                            "trading_day": str(trading_day),
                            "symbol": normalized_symbol,
                            "session": "all",
                            "main_contract": contract,
                        }
                    )
        
        # Handle old JSON format: {date: {symbol: contract, ...}, ...}
        elif isinstance(raw_info, dict):
            for trading_day, day_info in raw_info.items():
                for symbol, symbol_info in day_info.items():
                    normalized_symbol = normalize_symbol(symbol)
                    if isinstance(symbol_info, dict):
                        for session, contract in symbol_info.items():
                            rows.append(
                                {
                                    "trading_day": str(trading_day),
                                    "symbol": normalized_symbol,
                                    "session": str(session).lower(),
                                    "main_contract": contract,
                                }
                            )
                    else:
                        rows.append(
                            {
                                "trading_day": str(trading_day),
                                "symbol": normalized_symbol,
                                "session": "all",
                                "main_contract": symbol_info,
                            }
                        )
        
        # Handle list format directly
        elif isinstance(raw_info, list):
            for day_info in raw_info:
                trading_day = day_info.get("date") or day_info.get("trading_day")
                if not trading_day:
                    continue
                
                for key, contract in day_info.items():
                    if key in ["date", "trading_day"]:
                        continue
                    
                    normalized_symbol = normalize_symbol(key)
                    rows.append(
                        {
                            "trading_day": str(trading_day),
                            "symbol": normalized_symbol,
                            "session": "all",
                            "main_contract": contract,
                        }
                    )

        main_contract_df = pd.DataFrame(rows)
        if not main_contract_df.empty:
            main_contract_df = main_contract_df.sort_values(
                ["trading_day", "symbol", "session"]
            ).reset_index(drop=True)
        return main_contract_df


class MainContractResolver:
    """主力合约查询器。"""

    def __init__(self, main_contract_df: pd.DataFrame) -> None:
        self.main_contract_df = main_contract_df.copy()
        if self.main_contract_df.empty:
            self.trading_days: list[str] = []
        else:
            self.main_contract_df["trading_day"] = self.main_contract_df["trading_day"].astype(str)
            self.main_contract_df["symbol"] = self.main_contract_df["symbol"].map(normalize_symbol)
            self.main_contract_df["session"] = self.main_contract_df["session"].str.lower()
            self.trading_days = sorted(self.main_contract_df["trading_day"].unique().tolist())

    def get_main_contract(self, symbol: str, trading_day: str, session: str) -> Optional[str]:
        """查询指定交易日和 session 的主力合约，必要时回退到最近历史日。"""
        normalized_symbol = normalize_symbol(symbol)
        normalized_session = session.lower()
        query_day = self._find_best_trading_day(str(trading_day))
        if query_day is None:
            return None

        exact_contract = self._query_contract(normalized_symbol, query_day, normalized_session)
        if exact_contract is not None:
            return exact_contract

        all_session_contract = self._query_contract(normalized_symbol, query_day, "all")
        if all_session_contract is not None:
            return all_session_contract

        return None

    def _query_contract(self, symbol: str, trading_day: str, session: str) -> Optional[str]:
        matched = self.main_contract_df[
            (self.main_contract_df["symbol"] == symbol)
            & (self.main_contract_df["trading_day"] == trading_day)
            & (self.main_contract_df["session"] == session)
        ]
        if matched.empty:
            return None
        return str(matched.iloc[0]["main_contract"])

    def _find_best_trading_day(self, trading_day: str) -> Optional[str]:
        """若当天不存在，回退到最近过去一个交易日。"""
        if not self.trading_days:
            return None
        if trading_day in self.trading_days:
            return trading_day

        candidates = [day for day in self.trading_days if day <= trading_day]
        if candidates:
            return candidates[-1]
        return self.trading_days[0]

