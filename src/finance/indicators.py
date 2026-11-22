import numpy as np
import pandas as pd


EPSILON = 1e-6


def calculate_fpr(foreign_buy_val: float, foreign_sell_val: float, total_value: float) -> float:
    """
    Foreign Participation Rate (FPR - %):
    Tỷ trọng tham gia của khối ngoại trên tổng thanh khoản cổ phiếu.
    FPR = (V_buy_foreign + V_sell_foreign) / (2 * V_total) * 100
    """
    if total_value <= 0:
        return 0.0
    fpr = ((foreign_buy_val + foreign_sell_val) / (2.0 * total_value)) * 100.0
    return round(min(max(fpr, 0.0), 100.0), 2)


def calculate_fms(
    foreign_net_val: float,
    mean_10d: float,
    std_10d: float,
    eps: float = EPSILON,
) -> float:
    """
    Foreign Momentum Score (FMS - Z-score):
    Chuẩn hóa giá trị mua/bán ròng hiện tại theo phân phối 10 phiên gần nhất.
    FMS = (NetVal - mu_10) / (sigma_10 + eps)
    """
    if pd.isna(mean_10d) or pd.isna(std_10d):
        return 0.0
    z_score = (foreign_net_val - mean_10d) / (std_10d + eps)
    # Giới hạn hiển thị trong [-10, 10] để tránh nhiễu ngoại lai cực đoan khi std quá nhỏ
    return round(float(np.clip(z_score, -10.0, 10.0)), 2)


def calculate_room_used_pct(available_room: float, total_room: float) -> float:
    """Tỷ lệ lấp đầy room ngoại (%): (1 - AvailableRoom / TotalRoom) * 100."""
    if total_room <= 0:
        return 0.0
    used_pct = (1.0 - (available_room / total_room)) * 100.0
    return round(min(max(used_pct, 0.0), 100.0), 2)


def calculate_depletion_rate_pct(
    delta_net_vol: float,
    prev_available_room: float,
) -> float:
    """Tốc độ cạn kiệt room ngoại (%): Delta_NetForeignVol / (PrevAvailableRoom + 1) * 100."""
    if prev_available_room < 0:
        return 0.0
    rate = (delta_net_vol / (prev_available_room + 1.0)) * 100.0
    return round(rate, 2)


def classify_absorption_state(pct_change: float, fms: float, fpr: float) -> str:
    """
    Phân loại hành vi hấp thụ / phân kỳ giữa biến động giá và dòng vốn ngoại.
    """
    if pct_change <= 0.5 and fms >= 1.5 and fpr >= 15.0:
        return "GOM_AM_THAM"  # Stealth Accumulation
    if pct_change >= 0.5 and fms <= -1.5 and fpr >= 15.0:
        return "XA_KEO_GIA"  # Distribution into Strength
    if pct_change > 0.5 and fms >= 1.5:
        return "DONG_THUAN_MUA"  # Bullish Confluence
    if pct_change < -0.5 and fms <= -1.5:
        return "DONG_THUAN_BAN"  # Bearish Confluence
    return "TRUNG_TINH"


def compute_scorecard(
    df_snapshot: pd.DataFrame,
    df_hist_stats: pd.DataFrame,
) -> pd.DataFrame:
    """
    Tính toán toàn bộ bảng điểm Foreign Capital Scorecard cho ảnh chụp hiện tại.
    """
    if df_snapshot.empty:
        return pd.DataFrame()

    df = df_snapshot.merge(df_hist_stats, on="symbol", how="left")
    df["mean_net_val"] = df["mean_net_val"].fillna(0.0)
    df["std_net_val"] = df["std_net_val"].fillna(0.0)
    df["hist_sessions"] = df["hist_sessions"].fillna(0).astype(int)

    df["fpr_pct"] = [
        calculate_fpr(b, s, t)
        for b, s, t in zip(df["foreign_buy_val"], df["foreign_sell_val"], df["total_value"])
    ]

    df["fms_score"] = [
        calculate_fms(net, mu, sigma)
        for net, mu, sigma in zip(df["foreign_net_val"], df["mean_net_val"], df["std_net_val"])
    ]

    df["room_used_pct"] = [
        calculate_room_used_pct(avail, tot)
        for avail, tot in zip(df["available_room"], df["total_room"])
    ]

    df["absorption_state"] = [
        classify_absorption_state(pct, fms, fpr)
        for pct, fms, fpr in zip(df["pct_change"], df["fms_score"], df["fpr_pct"])
    ]

    # Điểm tổng hợp sức mạnh dòng vốn ngoại (Composite Strength Score) phục vụ xếp hạng
    # Trọng số: 60% động lượng FMS + 40% mức độ chi phối thanh khoản FPR (chuẩn hóa dấu theo FMS)
    sign_fms = np.where(df["fms_score"] >= 0, 1.0, -1.0)
    df["composite_score"] = np.round(
        0.6 * df["fms_score"] + 0.4 * sign_fms * (df["fpr_pct"] / 10.0),
        2,
    )

    return df.sort_values(by="composite_score", ascending=False).reset_index(drop=True)