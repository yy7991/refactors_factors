"""Factor registry for managing factor functions."""

from __future__ import annotations

from typing import Callable, Optional
import pandas as pd


class FactorRegistry:
    """因子注册表，管理所有可用的因子函数。"""

    def __init__(self) -> None:
        self._factors: dict[str, Callable] = {}
        self._metadata: dict[str, dict] = {}

    def register(
        self,
        factor_func: Callable,
        name: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> None:
        """
        注册因子函数。

        参数:
            factor_func: 因子函数
            name: 因子名称，默认使用 __factor_spec__.name
            metadata: 额外元数据
        """
        spec = getattr(factor_func, "__factor_spec__", None)
        if spec is None:
            raise ValueError(f"Factor {factor_func.__name__} missing @factor_spec decorator")

        factor_name = name or spec.name
        self._factors[factor_name] = factor_func
        self._metadata[factor_name] = {
            "spec": spec,
            "function_name": factor_func.__name__,
            "description": spec.description,
            "version": spec.version,
            "tags": spec.tags,
            "dependencies": {
                "raw_columns": spec.raw_columns,
                "bar": spec.bar,
                "operators": spec.operators,
                "related": spec.related,
                "stats": spec.stats,
                "history": spec.history,
            },
            **(metadata or {}),
        }

    def get(self, name: str) -> Callable:
        """获取指定名称的因子函数。"""
        if name not in self._factors:
            raise KeyError(f"Factor '{name}' not found in registry")
        return self._factors[name]

    def has(self, name: str) -> bool:
        """检查因子是否已注册。"""
        return name in self._factors

    def list_factors(self) -> list[str]:
        """列出所有已注册的因子名称。"""
        return sorted(self._factors.keys())

    def get_metadata(self, name: str) -> dict:
        """获取因子的元数据。"""
        if name not in self._metadata:
            raise KeyError(f"Factor '{name}' not found in registry")
        return self._metadata[name].copy()

    def filter_by_tag(self, tag: str) -> list[str]:
        """根据标签筛选因子。"""
        return [name for name, meta in self._metadata.items() if tag in meta["tags"]]

    def filter_by_operator(self, operator_name: str) -> list[str]:
        """根据依赖的算子筛选因子。"""
        return [
            name
            for name, meta in self._metadata.items()
            if operator_name in meta["dependencies"]["operators"]
        ]


# 全局默认注册表实例
_global_registry = FactorRegistry()


def get_factor_registry() -> FactorRegistry:
    """获取全局因子注册表。"""
    return _global_registry


def register_factor(
    factor_func: Callable,
    name: Optional[str] = None,
    metadata: Optional[dict] = None,
) -> Callable:
    """
    便捷函数：注册因子到全局注册表。

    用法:
        @register_factor()
        @factor_spec(name="001")
        def calc_001(df, ctx=None):
            ...
    """
    _global_registry.register(factor_func, name=name, metadata=metadata)
    return factor_func
