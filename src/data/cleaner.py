from datetime import datetime, timezone
from typing import Any, Mapping
from zoneinfo import ZoneInfo

import math


VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")


def normalize_timestamp_vn(ts: str | int | float | datetime | None) -> str:
    """
    Chuẩn hóa mọi định dạng thời gian (ISO string, Unix epoch s/ms, datetime)
    sang chuỗi ISO-8601 theo múi giờ Việt Nam (Asia/Ho_Chi_Minh, UTC+7).
    """
    if ts is None:
        dt = datetime.now(VN_TZ)
    elif isinstance(ts, datetime):
        dt = ts.replace(tzinfo=VN_TZ) if ts.tzinfo is None else ts.astimezone(VN_TZ)
    elif isinstance(ts, (int, float)):
        # Tự động nhận diện epoch milliseconds (>= 1e12) hay seconds
        epoch_sec = ts / 1000.0 if ts > 1e12 else float(ts)
        dt = datetime.fromtimestamp(epoch_sec, tz=timezone.utc).astimezone(VN_TZ)
    elif isinstance(ts, str):
        cleaned = ts.strip().replace("Z", "+00:00")
        parsed = datetime.fromisoformat(cleaned)
        dt = parsed.replace(tzinfo=VN_TZ) if parsed.tzinfo is None else parsed.astimezone(VN_TZ)
    else:
        raise ValueError(f"Định dạng thời gian không được hỗ trợ: {type(ts)}")

    return dt.isoformat(timespec="seconds")


def safe_float(value: Any, default: float = 0.0) -> float:
    """Ép kiểu số thực an toàn, xử lý None, NaN, chuỗi rỗng và dấu phẩy ngăn cách."""
    if value is None:
        return default
    if isinstance(value, (int, float)):
        if math.isnan(value) or math.isinf(value):
            return default
        return float(value)
    if isinstance(value, str):
        cleaned = value.strip().replace(",", "")
        if not cleaned or cleaned.lower() in {"null", "none", "nan", "-"}:
            return default
        try:
            val = float(cleaned)
            return default if (math.isnan(val) or math.isinf(val)) else val
        except ValueError:
            return default
    return default


def clean_intraday_record(raw: Mapping[str, Any], source: str = "NEO_API") -> dict[str, Any] | None:
    """
    Chuẩn hóa 1 bản ghi cổ phiếu nội ngày từ API.
    Trả về None nếu bản ghi thiếu mã cổ phiếu hoặc dữ liệu giá bất hợp lệ.
    """
    symbol = str(raw.get("symbol", "")).strip().upper()
    if not symbol:
        return None

    timestamp_vn = normalize_timestamp_vn(raw.get("timestamp_vn") or raw.get("timestamp"))
    trade_date = timestamp_vn[:10]

    last_price = safe_float(raw.get("last_price"), default=0.0)
    ref_price = safe_float(raw.get("ref_price"), default=0.0)
    if last_price < 0 or ref_price < 0:
        return None

    # Tính % thay đổi nếu API không cung cấp sẵn
    if raw.get("pct_change") is not None:
        pct_change = safe_float(raw.get("pct_change"), default=0.0)
    elif ref_price > 0 and last_price > 0:
        pct_change = ((last_price - ref_price) / ref_price) * 100.0
    else:
        pct_change = 0.0

    total_volume = max(0.0, safe_float(raw.get("total_volume"), default=0.0))
    total_value = max(0.0, safe_float(raw.get("total_value"), default=0.0))

    f_buy_vol = max(0.0, safe_float(raw.get("foreign_buy_vol"), default=0.0))
    f_sell_vol = max(0.0, safe_float(raw.get("foreign_sell_vol"), default=0.0))
    f_buy_val = max(0.0, safe_float(raw.get("foreign_buy_val"), default=0.0))
    f_sell_val = max(0.0, safe_float(raw.get("foreign_sell_val"), default=0.0))

    # Tự tính giá trị mua ròng để đảm bảo nhất quán tuyệt đối
    f_net_val = f_buy_val - f_sell_val

    total_room = max(0.0, safe_float(raw.get("total_room"), default=0.0))
    available_room = max(0.0, safe_float(raw.get("available_room"), default=0.0))
    # Chặn bất thường: room còn lại không thể lớn hơn tổng room (khi total_room > 0)
    if total_room > 0 and available_room > total_room:
        available_room = total_room

    return {
        "timestamp_vn": timestamp_vn,
        "trade_date": trade_date,
        "symbol": symbol,
        "last_price": round(last_price, 2),
        "ref_price": round(ref_price, 2),
        "pct_change": round(pct_change, 2),
        "total_volume": total_volume,
        "total_value": total_value,
        "foreign_buy_vol": f_buy_vol,
        "foreign_sell_vol": f_sell_vol,
        "foreign_buy_val": f_buy_val,
        "foreign_sell_val": f_sell_val,
        "foreign_net_val": f_net_val,
        "available_room": available_room,
        "total_room": total_room,
        "source": raw.get("source", source),
    }


def deduplicate_intraday_batch(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Khử trùng lặp trong cùng một lô theo khóa (timestamp_vn, symbol), giữ bản ghi mới nhất."""
    unique_map: dict[tuple[str, str], dict[str, Any]] = {}
    for rec in records:
        key = (rec["timestamp_vn"], rec["symbol"])
        unique_map[key] = rec
    return list(unique_map.values())