"""根据 catalog 和主力信息生成加载计划。"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import pandas as pd

from .main_contract import MainContractResolver
from .parser import CatalogRecord, LoadPlan, LoadRequest, PartitionKey, normalize_symbol


class TradingDayIndex:
    """轻量交易日索引，先用于 Catalog 层历史日期回看。"""

    def __init__(self, trading_days: list[str]) -> None:
        self.trading_days = sorted({str(day) for day in trading_days})

    def prev_n_trading_days(self, trading_day: str, n: int) -> list[str]:
        """返回指定交易日前 n 个交易日。"""
        if n <= 0 or not self.trading_days:
            return []

        day = str(trading_day)
        if day in self.trading_days:
            index = self.trading_days.index(day)
        else:
            previous_days = [candidate for candidate in self.trading_days if candidate < day]
            if not previous_days:
                return []
            index = self.trading_days.index(previous_days[-1])

        start = max(0, index - n)
        return self.trading_days[start:index]


class CatalogResolver:
    """把逻辑请求转成当前、关联、历史与算子的加载计划。"""

    def __init__(
        self,
        catalog_df: pd.DataFrame,
        main_contract_resolver: MainContractResolver,
        relations_map: Optional[dict[str, list[str]]] = None,
        trading_day_index: Optional[TradingDayIndex] = None,
    ) -> None:
        self.catalog_df = catalog_df.copy()
        if not self.catalog_df.empty:
            self.catalog_df["symbol"] = self.catalog_df["symbol"].map(normalize_symbol)
            self.catalog_df["session"] = self.catalog_df["session"].str.lower()
            self.catalog_df["trading_day"] = self.catalog_df["trading_day"].astype(str)
        self.main_contract_resolver = main_contract_resolver
        self.relations_map = {
            normalize_symbol(symbol): [normalize_symbol(item) for item in related_list]
            for symbol, related_list in (relations_map or {}).items()
        }

        trading_days = self.catalog_df["trading_day"].unique().tolist() if not self.catalog_df.empty else []
        if trading_day_index is None:
            self.trading_day_index = TradingDayIndex(trading_days=trading_days)
        else:
            self.trading_day_index = trading_day_index

    def resolve(self, request: LoadRequest) -> LoadPlan:
        """根据请求构建完整加载计划。"""
        normalized_symbol = normalize_symbol(request.symbol)
        sessions = self._resolve_sessions(request.session_scope)
        plan = LoadPlan(meta={"request": request})

        plan.current = self.resolve_current_files(
            symbol=normalized_symbol,
            trading_day=request.trading_day,
            sessions=sessions,
        )

        related_alias_map = self._resolve_related_alias_map(
            symbol=normalized_symbol,
            related_symbols=request.related_symbols,
            related_aliases=request.related_aliases,
        )
        plan.related = self.resolve_related_files(
            related_alias_map=related_alias_map,
            trading_day=request.trading_day,
            sessions=sessions,
        )

        plan.history = self.resolve_history_files(
            symbol=normalized_symbol,
            trading_day=request.trading_day,
            sessions=sessions,
            history_days=request.history_days,
        )

        plan.operators = self.resolve_operator_keys(
            symbol=normalized_symbol,
            trading_day=request.trading_day,
            session_scope=request.session_scope,
            data_level=request.data_level,
            operators=request.operators or [],
        )

        return plan

    def resolve_current_files(self, symbol: str, trading_day: str, sessions: list[str]) -> list[CatalogRecord]:
        """解析当前任务对应的原始文件。"""
        records: list[CatalogRecord] = []
        for session in sessions:
            contract = self.main_contract_resolver.get_main_contract(symbol, trading_day, session)
            records.extend(self.find_records("raw_csv", symbol, trading_day, session, contract))
        return records

    def resolve_related_files(
        self,
        related_alias_map: dict[str, str],
        trading_day: str,
        sessions: list[str],
    ) -> dict[str, list[CatalogRecord]]:
        """解析关联品种文件。"""
        related_records: dict[str, list[CatalogRecord]] = {}
        for related_alias, actual_symbol in related_alias_map.items():
            symbol_records: list[CatalogRecord] = []
            for session in sessions:
                contract = self.main_contract_resolver.get_main_contract(
                    actual_symbol, trading_day, session
                )
                symbol_records.extend(
                    self.find_records("raw_csv", actual_symbol, trading_day, session, contract)
                )
            related_records[related_alias] = symbol_records
        return related_records

    def resolve_history_files(
        self,
        symbol: str,
        trading_day: str,
        sessions: list[str],
        history_days: int,
    ) -> dict[str, list[CatalogRecord]]:
        """解析过去 n 个交易日的文件。"""
        history_records: dict[str, list[CatalogRecord]] = {}
        history_trading_days = self.trading_day_index.prev_n_trading_days(trading_day, history_days)
        for history_day in history_trading_days:
            day_records: list[CatalogRecord] = []
            for session in sessions:
                contract = self.main_contract_resolver.get_main_contract(symbol, history_day, session)
                day_records.extend(
                    self.find_records("raw_csv", symbol, history_day, session, contract)
                )
            history_records[history_day] = day_records
        return history_records

    @staticmethod
    def resolve_operator_keys(
        symbol: str,
        trading_day: str,
        session_scope: str,
        data_level: str,
        operators: list[str],
    ) -> dict[str, PartitionKey]:
        """生成算子逻辑分区键。"""
        return {
            operator_name: PartitionKey(
                symbol=symbol,
                trading_day=trading_day,
                session=session_scope,
                contract=None,
                data_level=data_level,
                bar_spec=None,
            )
            for operator_name in operators
        }

    def find_records(
        self,
        file_type: str,
        symbol: str,
        trading_day: str,
        session: str,
        contract: Optional[str],
    ) -> list[CatalogRecord]:
        """从 catalog 表中筛选结构化记录。"""
        if self.catalog_df.empty:
            return []

        mask = (
            (self.catalog_df["file_type"] == file_type)
            & (self.catalog_df["symbol"] == normalize_symbol(symbol))
            & (self.catalog_df["trading_day"] == str(trading_day))
            & (self.catalog_df["session"] == session.lower())
        )
        if contract is not None:
            # Normalize both sides for comparison
            mask &= self.catalog_df["contract"].map(normalize_symbol) == normalize_symbol(contract)

        matched = self.catalog_df.loc[mask]
        return [CatalogRecord.from_row(row.to_dict()) for _, row in matched.iterrows()]

    def _resolve_related_alias_map(
        self,
        symbol: str,
        related_symbols: Optional[list[str]],
        related_aliases: Optional[dict[str, str]],
    ) -> dict[str, str]:
        """统一解析关联品种别名与真实品种的映射。"""
        if related_aliases:
            return {
                normalize_symbol(alias): normalize_symbol(actual_symbol)
                for alias, actual_symbol in related_aliases.items()
            }

        if related_symbols is not None:
            return {
                normalize_symbol(related_symbol): normalize_symbol(related_symbol)
                for related_symbol in related_symbols
            }

        default_related = self.relations_map.get(symbol, [])
        return {
            normalize_symbol(related_symbol): normalize_symbol(related_symbol)
            for related_symbol in default_related
        }

    @staticmethod
    def _resolve_sessions(session_scope: str) -> list[str]:
        """把 all/day/night 统一展开。"""
        if session_scope == "all":
            return ["day", "night"]
        return [session_scope.lower()]
