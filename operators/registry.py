"""Operator registry for managing reusable computation primitives."""

from __future__ import annotations

from typing import Callable, Optional, Union
import pandas as pd


class OperatorRegistry:
    """算子注册表，管理所有可复用的算子函数。"""

    def __init__(self) -> None:
        self._operators: dict[str, Callable] = {}
        self._metadata: dict[str, dict] = {}

    def register(
        self,
        name: str,
        operator_func: Callable[[pd.DataFrame], Union[pd.Series, pd.DataFrame]],
        required_columns: Optional[list[str]] = None,
        output_columns: Optional[list[str]] = None,
        description: str = "",
        tags: Optional[list[str]] = None,
    ) -> None:
        """
        注册算子函数。

        参数:
            name: 算子名称
            operator_func: 算子函数
            required_columns: 需要的输入列
            output_columns: 输出的列名
            description: 算子描述
            tags: 标签列表
        """
        self._operators[name] = operator_func
        self._metadata[name] = {
            "function": operator_func,
            "required_columns": required_columns or [],
            "output_columns": output_columns or [],
            "description": description,
            "tags": tags or [],
        }

    def get(self, name: str) -> Callable[[pd.DataFrame], Union[pd.Series, pd.DataFrame]]:
        """获取指定名称的算子函数。"""
        if name not in self._operators:
            raise KeyError(f"Operator '{name}' not found in registry")
        return self._operators[name]

    def has(self, name: str) -> bool:
        """检查算子是否已注册。"""
        return name in self._operators

    def list_operators(self) -> list[str]:
        """列出所有已注册的算子名称。"""
        return sorted(self._operators.keys())

    def get_metadata(self, name: str) -> dict:
        """获取算子的元数据。"""
        if name not in self._metadata:
            raise KeyError(f"Operator '{name}' not found in registry")
        return self._metadata[name].copy()


# 全局默认注册表实例
_global_registry = OperatorRegistry()


def get_operator_registry() -> OperatorRegistry:
    """获取全局算子注册表。"""
    return _global_registry


def register_operator(
    name: str,
    required_columns: Optional[list[str]] = None,
    output_columns: Optional[list[str]] = None,
    description: str = "",
    tags: Optional[list[str]] = None,
) -> Callable:
    """
    装饰器：注册算子到全局注册表。

    用法:
        @register_operator(
            name="detailed_trade_allocation",
            required_columns=["Volume", "Turnover", "mid", ...],
            description="Allocate trades to active buy/sell sides",
        )
        def detailed_trade_allocation(df):
            ...
    """

    def decorator(func: Callable) -> Callable:
        _global_registry.register(
            name=name,
            operator_func=func,
            required_columns=required_columns,
            output_columns=output_columns,
            description=description,
            tags=tags,
        )
        return func

    return decorator
