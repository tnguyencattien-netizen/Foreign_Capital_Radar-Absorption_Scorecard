from dataclasses import dataclass, asdict
from typing import Any

import numpy as np
import pandas as pd


@dataclass
class AlertItem:
    symbol: str
    sector: str
    alert_type: str
    severity: str  # HIGH | MEDIUM | LOW
    title: str
    detail: str
    metric_value: float
    timestamp_vn: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def generate_snapshot_alerts(df_scorecard: pd.DataFrame) -> list[AlertItem]:
    """
    Sinh các cảnh báo dựa trên ngưỡng thống kê và ngưỡng nghiệp vụ từ bảng Scorecard:
    1. Dòng tiền đột biến: |FMS| >= 2.0 (vượt 2 độ lệch chuẩn).
    2. Room tới hạn (Room Squeeze): RoomUsed >= 95% và mua ròng dương.
    3. Gom âm thầm / Xả kéo giá (Phân kỳ giá - dòng vốn).
    """
    alerts: list[AlertItem] = []
    if df_scorecard.empty:
        return alerts

    for _, row in df_scorecard.iterrows():
        symbol = str(row["symbol"])
        sector = str(row.get("sector", "Khác"))
        ts = str(row.get("timestamp_vn", ""))
        fms = float(row.get("fms_score", 0.0))
        fpr = float(row.get("fpr_pct", 0.0))
        room_used = float(row.get("room_used_pct", 0.0))
        net_val_bil = float(row.get("foreign_net_val", 0.0)) / 1e9
        pct_change = float(row.get("pct_change", 0.0))
        state = str(row.get("absorption_state", "TRUNG_TINH"))

        # 1. Cảnh báo đột biến dòng vốn ngoại (|Z-score| >= 2.0)
        if fms >= 2.0:
            alerts.append(
                AlertItem(
                    symbol=symbol,
                    sector=sector,
                    alert_type="FOREIGN_SURGE_BUY",
                    severity="HIGH",
                    title=f"{symbol}: Khối ngoại mua ròng đột biến",
                    detail=(
                        f"FMS = +{fms:.2f}σ (vượt ngưỡng 2.0σ), mua ròng {net_val_bil:+.2f} tỷ VND, "
                        f"chiếm {fpr:.1f}% thanh khoản."
                    ),
                    metric_value=fms,
                    timestamp_vn=ts,
                )
            )
        elif fms <= -2.0:
            alerts.append(
                AlertItem(
                    symbol=symbol,
                    sector=sector,
                    alert_type="FOREIGN_SURGE_SELL",
                    severity="HIGH",
                    title=f"{symbol}: Khối ngoại bán ròng đột biến",
                    detail=(
                        f"FMS = {fms:.2f}σ (dưới ngưỡng -2.0σ), bán ròng {net_val_bil:+.2f} tỷ VND, "
                        f"chiếm {fpr:.1f}% thanh khoản."
                    ),
                    metric_value=fms,
                    timestamp_vn=ts,
                )
            )

        # 2. Cảnh báo Room Tới hạn (Foreign Room Squeeze)
        if room_used >= 95.0 and net_val_bil > 0:
            alerts.append(
                AlertItem(
                    symbol=symbol,
                    sector=sector,
                    alert_type="ROOM_SQUEEZE",
                    severity="HIGH",
                    title=f"{symbol}: Cảnh báo Room ngoại tới hạn ({room_used:.2f}%)",
                    detail=(
                        f"Room đã lấp đầy {room_used:.2f}% (ngưỡng >= 95%) trong khi khối ngoại "
                        f"tiếp tục mua ròng {net_val_bil:+.2f} tỷ VND."
                    ),
                    metric_value=room_used,
                    timestamp_vn=ts,
                )
            )

        # 3. Cảnh báo Gom âm thầm / Xả kéo giá
        if state == "GOM_AM_THAM":
            alerts.append(
                AlertItem(
                    symbol=symbol,
                    sector=sector,
                    alert_type="STEALTH_ACCUMULATION",
                    severity="MEDIUM",
                    title=f"{symbol}: Tín hiệu gom âm thầm",
                    detail=(
                        f"Giá biến động thấp ({pct_change:+.2f}%) nhưng FMS đạt +{fms:.2f}σ "
                        f"và FPR đạt {fpr:.1f}%."
                    ),
                    metric_value=fms,
                    timestamp_vn=ts,
                )
            )
        elif state == "XA_KEO_GIA":
            alerts.append(
                AlertItem(
                    symbol=symbol,
                    sector=sector,
                    alert_type="DISTRIBUTION_INTO_STRENGTH",
                    severity="MEDIUM",
                    title=f"{symbol}: Tín hiệu xả khi giá tăng",
                    detail=(
                        f"Giá tăng {pct_change:+.2f}% nhưng khối ngoại bán mạnh (FMS = {fms:.2f}σ, "
                        f"FPR = {fpr:.1f}%)."
                    ),
                    metric_value=fms,
                    timestamp_vn=ts,
                )
            )

    severity_rank = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    alerts.sort(key=lambda a: (severity_rank[a.severity], -abs(a.metric_value)))
    return alerts


def detect_intraday_reversals(
    df_intraday_series: pd.DataFrame,
    lookback_steps: int = 3,
) -> list[AlertItem]:
    """
    Phát hiện đảo chiều dòng tiền khối ngoại đột ngột trong phiên:
    So sánh giá trị mua ròng hiện tại với mốc cách đó `lookback_steps` bước (~15 phút).
    Kích hoạt khi đổi dấu mua/bán ròng VÀ biên độ thay đổi vượt phân vị 90 của toàn rổ.
    """
    if df_intraday_series.empty:
        return []

    df = df_intraday_series.sort_values(["symbol", "timestamp_vn"]).copy()
    df["prev_net_val"] = df.groupby("symbol")["foreign_net_val"].shift(lookback_steps)
    df["delta_net_val"] = df["foreign_net_val"] - df["prev_net_val"]

    latest = df.groupby("symbol", as_index=False).tail(1).dropna(subset=["prev_net_val"])
    if latest.empty:
        return []

    abs_deltas = latest["delta_net_val"].abs()
    p90_threshold = float(np.percentile(abs_deltas, 90)) if len(abs_deltas) >= 3 else 0.0

    reversals: list[AlertItem] = []
    for _, row in latest.iterrows():
        prev_val = float(row["prev_net_val"])
        curr_val = float(row["foreign_net_val"])
        delta = float(row["delta_net_val"])

        # Kiểm tra đổi dấu thực sự (từ âm sang dương hoặc ngược lại)
        crossed_zero = (prev_val < 0 < curr_val) or (prev_val > 0 > curr_val)
        if crossed_zero and abs(delta) >= p90_threshold and abs(delta) > 0:
            direction = "Bán ròng → Mua ròng" if curr_val > 0 else "Mua ròng → Bán ròng"
            reversals.append(
                AlertItem(
                    symbol=str(row["symbol"]),
                    sector=str(row.get("sector", "Khác")),
                    alert_type="INTRADAY_REVERSAL",
                    severity="HIGH",
                    title=f"{row['symbol']}: Đảo chiều dòng tiền ngoại ({direction})",
                    detail=(
                        f"Dòng tiền ròng chuyển từ {prev_val / 1e9:+.2f} tỷ sang {curr_val / 1e9:+.2f} tỷ VND "
                        f"(biến động {delta / 1e9:+.2f} tỷ, vượt ngưỡng P90)."
                    ),
                    metric_value=round(delta / 1e9, 2),
                    timestamp_vn=str(row["timestamp_vn"]),
                )
            )

    return reversals