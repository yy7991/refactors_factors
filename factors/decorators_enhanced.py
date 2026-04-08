"""
Enhanced Factor Spec Decorator

Provides @factor_spec decorator for factor functions with full specification support.
"""

from typing import Dict, List, Any, Optional


class FactorSpec:
    """Factor specification object attached to decorated functions."""
    
    def __init__(
        self,
        name: str,
        description: str = "",
        raw_columns: List[str] = None,
        related: Optional[Dict] = None,
        history: Optional[Dict] = None,
        bar: Optional[Dict] = None,
        category: str = "custom",
        version: str = "1.0.0",
    ):
        self.name = name
        self.description = description
        self.raw_columns = raw_columns or []
        self.related = related
        self.history = history
        self.bar = bar
        self.category = category
        self.version = version
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert spec to dictionary."""
        return {
            "name": self.name,
            "description": self.description,
            "raw_columns": self.raw_columns,
            "related": self.related,
            "history": self.history,
            "bar": self.bar,
            "category": self.category,
            "version": self.version,
        }
    
    def __getitem__(self, key: str) -> Any:
        """Allow dict-like access for compatibility."""
        return self.to_dict().get(key)
    
    def get(self, key: str, default: Any = None) -> Any:
        """Dict-like get method."""
        return self.to_dict().get(key, default)
    
    def __repr__(self) -> str:
        return f"FactorSpec(name='{self.name}', category='{self.category}')"


def factor_spec(
    name: str,
    description: str = "",
    raw_columns: List[str] = None,
    related: Optional[Dict] = None,
    history: Optional[Dict] = None,
    bar: Optional[Dict] = None,
    category: str = "custom",
    version: str = "1.0.0",
):
    """
    Decorator to attach factor specification to factor functions.
    
    Args:
        name: Factor name
        description: Human-readable description
        raw_columns: Required raw data columns
        related: Related varieties config (None or dict)
        history: History config (None or dict with 'days' and 'mode')
        bar: Bar aggregation config (None or dict)
        category: Factor category ('cross_variety', 'cross_date', 'bar', etc.)
        version: Factor version string
    
    Returns:
        Decorator function that attaches __factor_spec__ attribute
    
    Example:
        @factor_spec(
            name="cross_var_return_diff",
            description="Cross-variety return difference",
            raw_columns=["LastPrice"],
            related={"dynamic": None},
            category="cross_variety",
        )
        def calc_cross_var_return_diff(ctx) -> pd.Series:
            # Implementation
            return result
    """
    def decorator(func):
        func.__factor_spec__ = FactorSpec(
            name=name,
            description=description,
            raw_columns=raw_columns,
            related=related,
            history=history,
            bar=bar,
            category=category,
            version=version,
        )
        return func
    return decorator


# Registry to collect all decorated factors
FACTOR_REGISTRY = {}


def register_factor(name: str, func):
    """Register a factor function in the global registry."""
    FACTOR_REGISTRY[name] = func


def get_registered_factors() -> Dict[str, any]:
    """Get all registered factor functions."""
    return FACTOR_REGISTRY.copy()
