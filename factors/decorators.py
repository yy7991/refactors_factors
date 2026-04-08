"""Factor decorator definitions."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional


@dataclass
class FactorSpec:
    """因子规格说明，用于声明因子的数据依赖。"""

    name: str
    version: str = "v1"
    description: str = ""
    tags: list[str] = field(default_factory=list)

    # 数据依赖
    raw_columns: Optional[list[str]] = None
    bar: Optional[dict] = None
    operators: list[str] = field(default_factory=list)

    # 关联品种
    related: Optional[dict] = None

    # 历史统计量
    stats: Optional[dict] = None

    # 历史数据
    history: Optional[dict] = None

    # 对齐方式
    align: str = "left"

    # Session 范围
    session_scope: str = "all"

    def __post_init__(self):
        """验证和规范参数。"""
        if self.bar is not None:
            if "type" not in self.bar:
                raise ValueError("bar spec must contain 'type' key")
            if self.bar["type"] not in {"time", "volume"}:
                raise ValueError("bar type must be 'time' or 'volume'")

        if self.stats is not None:
            if "days" not in self.stats:
                raise ValueError("stats spec must contain 'days' key")
            if "columns" not in self.stats:
                raise ValueError("stats spec must contain 'columns' key")

        if self.history is not None:
            if "days" not in self.history:
                raise ValueError("history spec must contain 'days' key")


def factor_spec(
    name: str,
    version: str = "v1",
    description: str = "",
    tags: Optional[list[str]] = None,
    raw_columns: Optional[list[str]] = None,
    bar: Optional[dict] = None,
    operators: Optional[list[str]] = None,
    related: Optional[dict] = None,
    stats: Optional[dict] = None,
    history: Optional[dict] = None,
    align: str = "left",
    session_scope: str = "all",
) -> Callable:
    """
    因子装饰器，用于声明因子的数据依赖。

    参数:
        name: 因子名称
        version: 因子版本
        description: 因子描述
        tags: 因子标签列表
        raw_columns: 需要的原始列，None 表示使用默认 canonical 列
        bar: Bar 规格配置，None 表示使用 tick 数据
            - type: "time" 或 "volume"
            - freq: 时间频率 (仅 time 类型), 如 "3s"
            - bars_per_day: 每天 bar 数 (仅 volume 类型)
            - mode: volume bar 模式 ("historical_profile" 或 "offline_quantile")
            - profile_days: 历史轮廓天数 (仅 volume bar)
        operators: 需要的算子列表
        related: 关联品种配置
            - source: "config" 从配置读取，或 "explicit" 显式指定
            - group: 配置组名 (当 source="config")
            - symbols: 品种列表 (当 source="explicit")
            - columns: 需要的列
            - operators: 需要的算子
        stats: 历史统计量配置
            - days: 过去几天
            - columns: 计算统计量的列
            - metrics: 统计指标 ["mean", "std", "skew", "kurt"]
            - attach_to_current: 是否附加到当前 df
        history: 历史数据配置
            - days: 过去几天
            - columns: 需要的列
            - operators: 需要的算子
            - mode: "prepend_rows" 或 "separate"
            - include_today: 是否包含今天
        align: 关联品种对齐方式 ("left", "asof_backward", "asof_nearest")
        session_scope: Session 范围 ("all", "day", "night")

    示例:
        @factor_spec(
            name="301",
            raw_columns=["mid", "Volume"],
            operators=["detailed_trade_allocation"],
            related={"source": "config", "columns": ["mid"]},
            stats={"days": 5, "columns": ["mid"], "metrics": ["mean", "std"]},
        )
        def calc_301(df, ctx=None):
            ...
    """

    def decorator(func: Callable) -> Callable:
        spec = FactorSpec(
            name=name,
            version=version,
            description=description,
            tags=tags or [],
            raw_columns=raw_columns,
            bar=bar,
            operators=operators or [],
            related=related,
            stats=stats,
            history=history,
            align=align,
            session_scope=session_scope,
        )
        func.__factor_spec__ = spec
        return func

    return decorator
