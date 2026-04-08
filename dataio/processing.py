"""新数据读取层使用的标准化 tick 处理逻辑。"""

from __future__ import annotations

from dataclasses import dataclass, field
import datetime as dt
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from catalog.parser import CatalogRecord


TARGET_SECONDS = (5, 10, 15, 30)


def _build_cols_dict() -> dict[str, str]:
    """构建盘口列的统一命名映射。"""
    cols_dict = {f"BidPrice{i}": f"b{i}" for i in range(1, 6)}
    cols_dict.update({f"BidVolume{i}": f"b{i}_v_m" for i in range(1, 6)})
    cols_dict.update({f"AskPrice{i}": f"a{i}" for i in range(1, 6)})
    cols_dict.update({f"AskVolume{i}": f"a{i}_v_m" for i in range(1, 6)})
    return cols_dict


def _build_trading_time_dict() -> dict[str, dict[str, Optional[str]]]:
    """初始化夜盘结束时间字典。"""
    return {
        "wr": {"night_start": None, "night_end": None},
        "br": {"night_start": "21:00:00", "night_end": "23:00:00"},
        "bu": {"night_start": "21:00:00", "night_end": "23:00:00"},
        "fu": {"night_start": "21:00:00", "night_end": "23:00:00"},
        "hc": {"night_start": "21:00:00", "night_end": "23:00:00"},
        "rb": {"night_start": "21:00:00", "night_end": "23:00:00"},
        "ru": {"night_start": "21:00:00", "night_end": "23:00:00"},
        "sp": {"night_start": "21:00:00", "night_end": "23:00:00"},
        "al": {"night_start": "21:00:00", "night_end": "01:00:00"},
        "ao": {"night_start": "21:00:00", "night_end": "01:00:00"},
        "cu": {"night_start": "21:00:00", "night_end": "01:00:00"},
        "ni": {"night_start": "21:00:00", "night_end": "01:00:00"},
        "pb": {"night_start": "21:00:00", "night_end": "01:00:00"},
        "sn": {"night_start": "21:00:00", "night_end": "01:00:00"},
        "ss": {"night_start": "21:00:00", "night_end": "01:00:00"},
        "zn": {"night_start": "21:00:00", "night_end": "01:00:00"},
        "ag": {"night_start": "21:00:00", "night_end": "02:30:00"},
        "au": {"night_start": "21:00:00", "night_end": "02:30:00"},
        "fe": {"night_start": "21:00:00", "night_end": "02:30:00"},
        "gc": {"night_start": "21:00:00", "night_end": "02:30:00"},
        "lu": {"night_start": "21:00:00", "night_end": "02:30:00"},
        "hg": {"night_start": "21:00:00", "night_end": "02:30:00"},
        "default": {"night_start": "21:00:00", "night_end": "02:30:00"},
    }


@dataclass
class PortalProcessingConfig:
    """DataPortal 读取原始 tick 时使用的处理配置。"""

    data_root: Path
    time_freq: str = "500ms"
    fill_timestamps: bool = True
    label_target: str = "mid"
    day_segments: tuple[tuple[str, str], ...] = (
        ("08:59:00", "10:15:00"),
        ("10:30:00", "11:30:00"),
        ("13:30:00", "15:00:00"),
    )
    night_preopen: str = "20:59:00"
    cols_dict: dict[str, str] = field(default_factory=_build_cols_dict)
    trading_time_dict: dict[str, dict[str, Optional[str]]] = field(
        default_factory=_build_trading_time_dict
    )


def build_default_processing_config(data_root: str | Path) -> PortalProcessingConfig:
    """构造默认处理配置。"""
    return PortalProcessingConfig(data_root=Path(data_root))


def load_record(record: CatalogRecord, config: PortalProcessingConfig) -> pd.DataFrame:
    """把单个 catalog 记录加载为标准化后的 tick DataFrame。"""
    raw_df = pd.read_csv(record.path)
    if raw_df.empty:
        return pd.DataFrame()

    df = timestamp_process(raw_df, record)
    if df.empty:
        return df

    df = generate_complete_timeindex(df, record, config)
    df = data_process(df, record)
    df = calculate_target(df, label_target=config.label_target)
    df = mask_unreliable_time(record.session, df)
    df = finalize_columns(df, record, config)
    
    return df


def timestamp_process(raw_df: pd.DataFrame, record: CatalogRecord) -> pd.DataFrame:
    """把交易所字段转换为统一的时间索引。"""
    df = raw_df.copy()

    if "Date" not in df.columns:
        if "ActionDay" not in df.columns:
            raise ValueError(f"{record.path} 缺少 Date 或 ActionDay 列")
        df["Date"] = df["ActionDay"]

    if record.session == "night":
        date_series = pd.to_datetime(df["Date"].astype(str), format="%Y%m%d", errors="coerce")
        mask = df["UpdateTime"].astype(str) > "03:00:00"
        df["Date"] = date_series
        df.loc[mask, "Date"] = df.loc[mask, "Date"] - pd.Timedelta(days=1)
        df["Date"] = df["Date"].dt.strftime("%Y%m%d")

    # Smart handling of UpdateTime with optional milliseconds
    if "UpdateTime" not in df.columns:
        raise ValueError(f"{record.path} 缺少 UpdateTime 列")
    
    update_time_str = df["UpdateTime"].astype(str)
    date_str = df["Date"].astype(str)
    
    # Check if UpdateTime already contains milliseconds (has a dot)
    has_millis_in_time = update_time_str.str.contains('.', regex=False)
    
    if has_millis_in_time.all():
        # All times already have milliseconds
        # Need to combine with Date to get full timestamp
        datetime_str = date_str + " " + update_time_str
        df["datetime"] = pd.to_datetime(datetime_str, errors="coerce")
    elif "UpdateMillisec" in df.columns:
        # Standard case: separate milliseconds column
        millis = df["UpdateMillisec"].astype(str).str.zfill(3).str[:3]
        datetime_str = date_str + " " + update_time_str + "." + millis
        df["datetime"] = pd.to_datetime(datetime_str, errors="coerce")
    elif "UpdateNanosec" in df.columns:
        millis = df["UpdateNanosec"].astype(str).str.zfill(9).str[:3]
        datetime_str = date_str + " " + update_time_str + "." + millis
        df["datetime"] = pd.to_datetime(datetime_str, errors="coerce")
    else:
        # No milliseconds available, parse as-is
        df["datetime"] = pd.to_datetime(date_str + " " + update_time_str, errors="coerce")

    df = df.dropna(subset=["datetime"])
    df = df.set_index("datetime").sort_index()
    return df[~df.index.duplicated(keep="last")]


def generate_complete_timeindex(
    df: pd.DataFrame,
    record: CatalogRecord,
    config: PortalProcessingConfig,
) -> pd.DataFrame:
    """用标准 session 时间轴补齐 tick。"""
    if df.empty:
        return df

    if not config.fill_timestamps:
        return df

    complete_index = build_session_index(record, config)
    if complete_index.empty:
        return df

    df = df.reindex(complete_index)

    ffill_columns = [
        "InstrumentID",
        "LastPrice",
        "AccVolume",
        "AccTurnover",
        "OpenInterest",
        "AskPrice1",
        "BidPrice1",
        "AskPrice2",
        "BidPrice2",
        "AskPrice3",
        "BidPrice3",
        "AskPrice4",
        "BidPrice4",
        "AskPrice5",
        "BidPrice5",
        "AskVolume1",
        "BidVolume1",
        "AskVolume2",
        "BidVolume2",
        "AskVolume3",
        "BidVolume3",
        "AskVolume4",
        "BidVolume4",
        "AskVolume5",
        "BidVolume5",
    ]
    zero_columns = ["Volume", "Turnover"]

    existing_ffill = [column for column in ffill_columns if column in df.columns]
    existing_zero = [column for column in zero_columns if column in df.columns]

    if existing_ffill:
        df[existing_ffill] = df[existing_ffill].ffill()
    if existing_zero:
        df[existing_zero] = df[existing_zero].fillna(0)

    return df


def build_session_index(
    record: CatalogRecord,
    config: PortalProcessingConfig,
) -> pd.DatetimeIndex:
    """按照交易日和 session 生成统一时间索引。"""
    trading_day = pd.Timestamp(str(record.trading_day))

    if record.session == "day":
        ranges = [
            pd.date_range(
                start=pd.Timestamp(f"{record.trading_day} {start_time}"),
                end=pd.Timestamp(f"{record.trading_day} {end_time}"),
                freq=config.time_freq,
            )
            for start_time, end_time in config.day_segments
        ]
    elif record.session == "night":
        symbol_time = config.trading_time_dict.get(
            record.symbol,
            config.trading_time_dict["default"],
        )
        night_end = symbol_time.get("night_end")
        if not night_end:
            return pd.DatetimeIndex([])

        start_day = trading_day - pd.Timedelta(days=1)
        start_dt = pd.Timestamp(f"{start_day.strftime('%Y%m%d')} {config.night_preopen}")
        end_dt = pd.Timestamp(f"{record.trading_day} {night_end}")
        ranges = [pd.date_range(start=start_dt, end=end_dt, freq=config.time_freq)]
    else:
        return pd.DatetimeIndex([])

    merged = np.concatenate([item.values for item in ranges]) if ranges else []
    return pd.DatetimeIndex(merged).sort_values().unique()


def data_process(df: pd.DataFrame, record: CatalogRecord) -> pd.DataFrame:
    """完成盘口清洗和中间特征计算。"""
    if df.empty:
        return df

    processed = df.copy()
    level_columns = (
        [f"BidPrice{i}" for i in range(1, 6)]
        + [f"BidVolume{i}" for i in range(1, 6)]
        + [f"AskPrice{i}" for i in range(1, 6)]
        + [f"AskVolume{i}" for i in range(1, 6)]
    )
    existing_level_columns = [column for column in level_columns if column in processed.columns]
    if existing_level_columns:
        processed[existing_level_columns] = processed[existing_level_columns].where(
            processed[existing_level_columns] > 0,
            np.nan,
        )

    processed["mid"] = (processed["AskPrice1"] + processed["BidPrice1"]) / 2
    depth_sum = processed["AskVolume1"] + processed["BidVolume1"]
    processed["cvwap"] = (
        processed["AskPrice1"] * processed["BidVolume1"]
        + processed["BidPrice1"] * processed["AskVolume1"]
    ) / depth_sum.replace(0, np.nan)

    if record.session == "night":
        mask = (
            (processed.index.time < dt.time(21, 0, 0))
            & (processed.index.time >= dt.time(12, 0, 0))
        )
        processed = processed.loc[~mask]
    elif record.session == "day":
        mask = processed.index.time < dt.time(8, 59, 0)
        processed = processed.loc[~mask]

    return processed


def calculate_target(df: pd.DataFrame, label_target: str) -> pd.DataFrame:
    """基于未来 5/10/15/30 秒生成标签。"""
    if df.empty:
        return df

    if label_target not in {"mid", "cvwap", "cvwaprt"}:
        label_target = "mid"

    result = df.copy()
    index_ns = result.index.view("i8")

    if label_target == "mid":
        current = result["mid"].to_numpy(dtype=float)
        target_source = current
    else:
        current = result["cvwap"].to_numpy(dtype=float)
        target_source = current

    for target_second in TARGET_SECONDS:
        lookup_ns = index_ns + target_second * 1_000_000_000
        positions = np.searchsorted(index_ns, lookup_ns, side="right") - 1
        valid = (positions >= 0) & (positions < len(result))

        future_value = np.full(len(result), np.nan, dtype=float)
        future_value[valid] = target_source[positions[valid]]

        if label_target == "cvwap":
            target_value = future_value - current
        else:
            target_value = (future_value - current) / current

        target_value = np.where(np.isfinite(target_value), target_value, np.nan)
        result[f"target_{target_second}"] = target_value

    return result


def mask_unreliable_time(session: str, df: pd.DataFrame) -> pd.DataFrame:
    """去除休市前后以及撮合期的脏时间段。"""
    if df.empty:
        return df

    if session == "night":
        visible_mask = (df.index.time < dt.time(3, 0, 0)) | (df.index.time > dt.time(20, 0, 0))
        night_index = df.loc[visible_mask].index
        if night_index.empty:
            return df

        midnight_max_time = night_index.max().time()
        if midnight_max_time > dt.time(22, 50, 0):
            unreliable_mask = (df.index.time > dt.time(22, 59, 30)) & (df.index.time <= dt.time(23, 0, 0))
        elif midnight_max_time < dt.time(2, 0, 0):
            unreliable_mask = (df.index.time > dt.time(0, 59, 30)) & (df.index.time <= dt.time(1, 0, 0))
        else:
            unreliable_mask = (df.index.time > dt.time(2, 29, 30)) & (df.index.time <= dt.time(2, 30, 0))
    elif session == "day":
        unreliable_mask = (
            ((df.index.time > dt.time(10, 14, 30)) & (df.index.time <= dt.time(10, 30, 0)))
            | ((df.index.time > dt.time(11, 29, 30)) & (df.index.time <= dt.time(13, 30, 0)))
            | ((df.index.time > dt.time(14, 59, 30)) & (df.index.time <= dt.time(15, 0, 0)))
        )
    else:
        return df

    return df.loc[~unreliable_mask]


def finalize_columns(
    df: pd.DataFrame,
    record: CatalogRecord,
    config: PortalProcessingConfig,
) -> pd.DataFrame:
    """选出标准列、完成重命名，并附加元信息。"""
    if df.empty:
        return df

    finalized = df.copy()
    timestamp_column = None
    if "TimeStamp" in finalized.columns:
        timestamp_column = "TimeStamp"
    elif "SystemTime" in finalized.columns:
        timestamp_column = "SystemTime"

    if finalized["Turnover"].sum() <= 0:
        if finalized["Volume"].sum() > 0:
            finalized["Turnover"] = finalized["Volume"] * (
                finalized["mid"] + finalized["mid"].shift(1)
            ) / 2
        elif "AccVolume" in finalized.columns and finalized["AccVolume"].sum() > 0:
            finalized["Volume"] = finalized["AccVolume"].diff()
            finalized["Volume"] = finalized["Volume"].where(finalized["Volume"] >= 0, 0)
            finalized["Turnover"] = finalized["Volume"] * (
                finalized["mid"] + finalized["mid"].shift(1)
            ) / 2
        else:
            finalized["Volume"] = (finalized["AskVolume1"] + finalized["BidVolume1"]) / 2
            finalized["Volume"] = (finalized["Volume"] + finalized["Volume"].shift(1)) / 2
            finalized["Turnover"] = finalized["Volume"] * finalized["mid"]

    required_columns = (
        ["InstrumentID"]
        + [f"BidPrice{i}" for i in range(1, 6)]
        + [f"BidVolume{i}" for i in range(1, 6)]
        + [f"AskPrice{i}" for i in range(1, 6)]
        + [f"AskVolume{i}" for i in range(1, 6)]
        + ["Turnover", "mid", "Volume", "LastPrice", "OpenInterest", "cvwap", "AccVolume", "AccTurnover"]
        + ([timestamp_column] if timestamp_column else [])
        + [f"target_{target_second}" for target_second in TARGET_SECONDS]
    )
    existing_required_columns = [column for column in required_columns if column in finalized.columns]
    finalized = finalized[existing_required_columns].copy()

    rename_map = dict(config.cols_dict)
    if timestamp_column is not None:
        rename_map[timestamp_column] = "TimeStamp"
    finalized = finalized.rename(columns=rename_map)

    finalized["__symbol__"] = record.symbol
    finalized["__contract__"] = record.contract
    finalized["__trading_day__"] = record.trading_day
    finalized["__session__"] = record.session
    finalized.index.name = "datetime"

    target_columns = [f"target_{target_second}" for target_second in TARGET_SECONDS if f"target_{target_second}" in finalized.columns]
    if target_columns:
        finalized = finalized.dropna(subset=target_columns)

    return finalized.sort_index()
