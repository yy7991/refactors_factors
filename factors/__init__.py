"""Factors layer - decorator definitions and registry."""

from .decorators import factor_spec, FactorSpec
from .registry import FactorRegistry, get_factor_registry, register_factor

__all__ = [
    "FactorRegistry",
    "FactorSpec",
    "factor_spec",
    "get_factor_registry",
    "register_factor",
]
