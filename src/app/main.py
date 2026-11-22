"""
Giao diện chính (Dashboard) cho hệ thống:
Ra-đa dòng tiền khối ngoại & Hệ thống chấm điểm sức mạnh dòng vốn ngoại
(Foreign Capital Radar & Absorption Scorecard)

Nguồn dữ liệu: SSI FastConnect Data API (REST + Streaming)
Chạy ứng dụng bằng lệnh:
    streamlit run src/app/main.py
"""

from pathlib import Path
import sys

# Đảm bảo Python nhận diện được gói `src` dù chạy từ thư mục gốc hay thư mục con
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

from src.analytics.alerts import detect_intraday_reversals, generate_snapshot_alerts
from src.data.database import DatabaseManager
from src.data.mock_generator import seed_mock_database
from src.finance.indicators import compute_scorecard


# --- CẤU HÌNH TRANG ---
st.set_page_config(
    page_title="Foreign Capital Radar | Ra-đa Dòng Tiền Khối Ngoại",
    page_icon="📡",
    layout="wide",
    initial_sidebar_state="expanded",
)

MOCK_DB_PATH = PROJECT_ROOT / "data" / "mock_foreign_radar.db"
LIVE_DB_PATH = PROJECT_ROOT / "data" / "foreign_radar.db"

STATE_LABELS = {
    "GOM_AM_THAM": "🟢 Gom âm thầm (Stealth Accumulation)",
    "XA_KEO_GIA": "🔴 Xả kéo giá (Distribution)",
    "DONG_THUAN_MUA": "🔵 Đồng thuận mua",
    "DONG_THUAN_BAN": "🟠 Đồng thuận bán",
    "TRUNG_TINH": "⚪ Trung tính",
}


def get_available_trade_dates(db: DatabaseManager) -> list[str]:
    """Truy vấn danh sách các phiên giao dịch hiện có trong bảng intraday."""
    try:
        with db.get_connection() as conn:
            rows = conn.execute(
                "SELECT DISTINCT trade_date FROM stock_foreign_intraday ORDER BY trade_date DESC"
            ).fetchall()
            return [row["trade_date"] for row in rows]
    except Exception:
        return []


def render_sidebar() -> tuple[Path, str]:
    """Hiển thị thanh điều hướng chọn nguồn dữ liệu và cấu hình bộ lọc."""
    st.sidebar.title("📡 Foreign Capital Radar")
    st.sidebar.caption("Đồ án FinTech • SSI FastConnect Data API")
    st.sidebar.divider()

    mode = st.sidebar.radio(
        "Nguồn dữ liệu hiển thị",
        options=["Dữ liệu mô phỏng (SIMULATED_MOCK)", "Dữ liệu thực tế (SSI FastConnect Live DB)"],
        index=0,
        help="Chế độ mô phỏng giúp kiểm thử đầy đủ giao diện và công thức khi chưa kết nối tài khoản SSI API.",
    )

    if mode.startswith("Dữ liệu mô phỏng"):
        db_path = MOCK_DB_PATH
        if not db_path.exists():
            st.sidebar.warning("Chưa có tệp dữ liệu mô phỏng.")
        if st.sidebar.button("🔄 Khởi tạo / Làm mới dữ liệu mô phỏng", use_container_width=True):
            try:
                seed_mock_database(str(db_path), seed=42)
                st.sidebar.success("Đã tạo dữ liệu mô phỏng thành công!")
                st.rerun()
            except Exception as exc:
                st.sidebar.error(f"Lỗi khi tạo dữ liệu mô phỏng: {exc}")
    else:
        db_path = LIVE_DB_PATH

    return db_path, mode


def render_sector_heatmap(df_scorecard: pd.DataFrame) -> None:
    """Trực quan hóa 1: Bản đồ nhiệt (Treemap) dòng tiền ròng khối ngoại theo ngành."""
    df_plot = df_scorecard.copy()
    df_plot["net_bil"] = df_plot["foreign_net_val"] / 1e9
    df_plot["total_val_bil"] = (df_plot["total_value"] / 1e9).clip(lower=1.0)

    fig = px.treemap(
        df_plot,
        path=[px.Constant("Toàn rổ theo dõi"), "sector", "symbol"],
        values="total_val_bil",
        color="net_bil",
        color_continuous_scale=["#d9534f", "#f7f7f7", "#2e7d32"],
        color_continuous_midpoint=0,
        custom_data=["net_bil", "fpr_pct", "fms_score", "room_used_pct"],
    )
    fig.update_traces(
        hovertemplate=(
            "<b>%{label}</b><br>"
            "Mua/Bán ròng: %{customdata[0]:+.2f} tỷ VND<br>"
            "Tỷ trọng ngoại (FPR): %{customdata[1]:.1f}%<br>"
            "Động lượng (FMS): %{customdata[2]:+.2f}σ<br>"
            "Room đã dùng: %{customdata[3]:.1f}%<extra></extra>"
        ),
        textinfo="label+value",
    )
    fig.update_layout(
        margin=dict(t=20, l=5, r=5, b=5),
        height=400,
        coloraxis_colorbar_title="Ròng (tỷ VND)",
    )
    st.plotly_chart(fig, use_container_width=True)


def render_absorption_scatter(df_scorecard: pd.DataFrame) -> None:
    """Trực quan hóa 2: Ma trận hành vi Giá (% thay đổi) vs Động lượng dòng vốn ngoại (FMS)."""
    df_plot = df_scorecard.copy()
    df_plot["bubble_size"] = df_plot["fpr_pct"].clip(lower=5.0)
    df_plot["state_display"] = df_plot["absorption_state"].map(STATE_LABELS).fillna("Khác")
    df_plot["net_bil"] = df_plot["foreign_net_val"] / 1e9

    fig = px.scatter(
        df_plot,
        x="pct_change",
        y="fms_score",
        size="bubble_size",
        color="state_display",
        text="symbol",
        hover_data={
            "sector": True,
            "net_bil": ":.2f",
            "fpr_pct": ":.1f",
            "room_used_pct": ":.1f",
            "bubble_size": False,
        },
        labels={
            "pct_change": "Biến động giá trong phiên (%)",
            "fms_score": "Foreign Momentum Score (FMS - σ)",
            "state_display": "Trạng thái hành vi",
        },
    )
    fig.add_hline(y=1.5, line_dash="dot", line_color="green", annotation_text="Ngưỡng gom (+1.5σ)")
    fig.add_hline(y=-1.5, line_dash="dot", line_color="red", annotation_text="Ngưỡng xả (-1.5σ)")
    fig.add_vline(x=0.0, line_dash="dash", line_color="gray")

    fig.update_traces(textposition="top center")
    fig.update_layout(margin=dict(t=20, l=5, r=5, b=5), height=400)
    st.plotly_chart(fig, use_container_width=True)


def render_intraday_dual_chart(df_symbol_series: pd.DataFrame, symbol: str) -> None:
    """Trực quan hóa 3: Diễn biến nội ngày giữa Giá cổ phiếu và Lũy kế mua/bán ròng ngoại."""
    if df_symbol_series.empty:
        st.info(f"Không có dữ liệu chuỗi thời gian nội ngày cho mã {symbol}.")
        return

    df_s = df_symbol_series.copy()
    df_s["time_hhmm"] = df_s["timestamp_vn"].str.slice(11, 16)
    df_s["net_bil"] = df_s["foreign_net_val"] / 1e9
    bar_colors = ["#2e7d32" if v >= 0 else "#d9534f" for v in df_s["net_bil"]]

    fig = make_subplots(specs=[[{"secondary_y": True}]])
    fig.add_trace(
        go.Bar(
            x=df_s["time_hhmm"],
            y=df_s["net_bil"],
            name="Mua/Bán ròng lũy kế (tỷ VND)",
            marker_color=bar_colors,
            opacity=0.65,
        ),
        secondary_y=False,
    )
    fig.add_trace(
        go.Scatter(
            x=df_s["time_hhmm"],
            y=df_s["last_price"],
            name="Giá khớp gần nhất (nghìn VND)",
            mode="lines+markers",
            line=dict(color="#1565c0", width=2.5),
        ),
        secondary_y=True,
    )

    fig.update_layout(
        title=f"Tương quan Giá & Tích lũy Dòng vốn ngoại nội ngày — {symbol}",
        xaxis_title="Thời gian (UTC+7)",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        margin=dict(t=50, l=10, r=10, b=10),
        height=380,
    )
    fig.update_yaxes(title_text="Giá trị ròng (tỷ VND)", secondary_y=False)
    fig.update_yaxes(title_text="Giá cổ phiếu (nghìn VND)", secondary_y=True)
    st.plotly_chart(fig, use_container_width=True)


def main() -> None:
    db_path, mode = render_sidebar()

    # --- TIÊU ĐỀ & ĐỊNH VỊ SẢN PHẨM ---
    st.title("📡 Ra-đa Dòng Tiền Khối Ngoại & Hệ Thống Chấm Điểm Sức Mạnh Vốn Ngoại")
    st.markdown(
        "**Mục tiêu:** Giám sát dòng vốn ngoại theo thời gian thực trong phiên qua SSI FastConnect Data API, "
        "phát hiện hành vi gom âm thầm, đảo chiều đột ngột và cảnh báo cạn kiệt room ngoại.  \n"
        "**Đối tượng người dùng:** Nhà đầu tư cá nhân theo trường phái dòng tiền lớn & Chuyên viên phân tích ngành."
    )

    if not db_path.exists():
        st.warning(
            f"Không tìm thấy cơ sở dữ liệu tại `{db_path.name}`. "
            "Nếu bạn đang ở chế độ mô phỏng, hãy bấm nút **Khởi tạo dữ liệu mô phỏng** ở thanh bên trái. "
            "Nếu ở chế độ thực tế, hãy chạy bộ thu thập SSI FastConnect API trước."
        )
        return

    try:
        db = DatabaseManager(db_path)
        trade_dates = get_available_trade_dates(db)
    except Exception as exc:
        st.error(f"Lỗi kết nối cơ sở dữ liệu SQLite: {exc}")
        return

    if not trade_dates:
        st.info("Cơ sở dữ liệu hiện chưa có bản ghi nội ngày nào. Vui lòng nạp dữ liệu trước khi phân tích.")
        return

    # --- BỘ LỌC TRÊN SIDEBAR ---
    st.sidebar.divider()
    st.sidebar.subheader("Bộ lọc phân tích")
    selected_date = st.sidebar.selectbox("Chọn phiên giao dịch", options=trade_dates, index=0)

    df_snap = db.fetch_latest_intraday_snapshot(trade_date=selected_date)
    df_stats = db.fetch_historical_net_stats(lookback_days=10)
    df_series_all = db.fetch_intraday_series(trade_date=selected_date)

    if df_snap.empty:
        st.warning(f"Không có dữ liệu snapshot cho phiên {selected_date}.")
        return

    df_scorecard = compute_scorecard(df_snap, df_stats)

    latest_ts = str(df_scorecard["timestamp_vn"].max())
    sources = sorted(df_scorecard["source"].dropna().unique().tolist())
    is_mock = "SIMULATED_MOCK" in sources

    status_col1, status_col2 = st.columns([2, 1])
    with status_col1:
        if is_mock:
            st.warning(
                f"⚠️ **TRẠNG THÁI:** Đang chạy trên dữ liệu mô phỏng (`SIMULATED_MOCK`) để kiểm thử kỹ thuật. "
                f"Cập nhật gần nhất: **{latest_ts} (UTC+7)**."
            )
        else:
            st.success(
                f"🟢 **TRẠNG THÁI KẾT NỐI:** Dữ liệu thực tế SSI FastConnect ({', '.join(sources)}) • "
                f"Cập nhật gần nhất: **{latest_ts} (UTC+7)**."
            )
    with status_col2:
        st.info(f"📅 Phiên: **{selected_date}** | Số mã giám sát: **{len(df_scorecard)}**")

    all_sectors = sorted(df_scorecard["sector"].unique().tolist())
    selected_sectors = st.sidebar.multiselect("Lọc theo ngành", options=all_sectors, default=all_sectors)

    filtered_sc = df_scorecard[df_scorecard["sector"].isin(selected_sectors)].copy()
    if filtered_sc.empty:
        st.info("Không có cổ phiếu nào khớp với bộ lọc ngành đã chọn.")
        return

    snapshot_alerts = generate_snapshot_alerts(filtered_sc)
    sector_map = dict(zip(df_snap["symbol"], df_snap["sector"]))
    df_series_filtered = df_series_all[df_series_all["symbol"].isin(filtered_sc["symbol"])].copy()
    df_series_filtered["sector"] = df_series_filtered["symbol"].map(sector_map).fillna("Khác")
    reversal_alerts = detect_intraday_reversals(df_series_filtered, lookback_steps=3)
    all_alerts = snapshot_alerts + reversal_alerts

    # --- HÀNG KPI TỔNG HỢP ---
    total_net_bil = filtered_sc["foreign_net_val"].sum() / 1e9
    avg_fpr = filtered_sc["fpr_pct"].mean()
    high_alerts_cnt = sum(1 for a in all_alerts if a.severity == "HIGH")
    top_row = filtered_sc.iloc[0]

    kpi1, kpi2, kpi3, kpi4 = st.columns(4)
    kpi1.metric(
        "Tổng mua/bán ròng ngoại",
        f"{total_net_bil:+.2f} tỷ",
        delta="Dòng vốn vào" if total_net_bil >= 0 else "Dòng vốn rút",
    )
    kpi2.metric(
        "Tỷ trọng tham gia TB (FPR)",
        f"{avg_fpr:.1f}%",
        help="Tỷ trọng giao dịch khối ngoại trên tổng thanh khoản cổ phiếu.",
    )
    kpi3.metric(
        "Cảnh báo mức CAO (High)",
        f"{high_alerts_cnt} tín hiệu",
        delta=f"Tổng {len(all_alerts)} cảnh báo",
        delta_color="off",
    )
    kpi4.metric(
        "Dẫn đầu sức mạnh vốn ngoại",
        f"{top_row['symbol']} ({top_row['composite_score']:+.2f})",
        delta=f"FMS: {top_row['fms_score']:+.2f}σ | Room: {top_row['room_used_pct']:.1f}%",
    )

    st.divider()

    # --- KHU VỰC TRỰC QUAN HÓA CHÍNH ---
    tab_radar, tab_alerts, tab_scorecard, tab_method = st.tabs(
        [
            "🗺️ Ra-đa & Biểu đồ phân tích",
            f"🚨 Hệ thống cảnh báo ({len(all_alerts)})",
            "📊 Bảng điểm Scorecard chi tiết",
            "📘 Phương pháp & Giới hạn",
        ]
    )

    with tab_radar:
        col_left, col_right = st.columns(2)
        with col_left:
            st.subheader("Bản đồ nhiệt dòng tiền ròng theo ngành")
            render_sector_heatmap(filtered_sc)
        with col_right:
            st.subheader("Ma trận Hành vi Giá vs Động lượng Vốn ngoại (FMS)")
            render_absorption_scatter(filtered_sc)

        st.divider()
        st.subheader("Soi chi tiết diễn biến nội ngày theo mã cổ phiếu")
        symbol_list = filtered_sc["symbol"].tolist()
        selected_symbol = st.selectbox("Chọn mã cổ phiếu phân tích sâu", options=symbol_list, index=0)
        df_sym = df_series_filtered[df_series_filtered["symbol"] == selected_symbol]
        render_intraday_dual_chart(df_sym, selected_symbol)

    with tab_alerts:
        st.subheader("Thẻ cảnh báo định lượng theo thời gian thực")
        if not all_alerts:
            st.success("Hiện tại chưa ghi nhận tín hiệu bất thường nào vượt ngưỡng thống kê.")
        else:
            for alert in all_alerts:
                if alert.severity == "HIGH":
                    st.error(f"**[{alert.severity}] {alert.title}** — {alert.detail} *(Ngành: {alert.sector} | {alert.timestamp_vn})*")
                else:
                    st.warning(f"**[{alert.severity}] {alert.title}** — {alert.detail} *(Ngành: {alert.sector} | {alert.timestamp_vn})*")

    with tab_scorecard:
        st.subheader("Bảng xếp hạng Foreign Capital Absorption Scorecard")
        display_df = filtered_sc[
            [
                "symbol",
                "sector",
                "last_price",
                "pct_change",
                "foreign_net_val",
                "fpr_pct",
                "fms_score",
                "room_used_pct",
                "absorption_state",
                "composite_score",
            ]
        ].copy()
        display_df["foreign_net_val"] = (display_df["foreign_net_val"] / 1e9).round(2)
        display_df["absorption_state"] = display_df["absorption_state"].map(STATE_LABELS)
        display_df.columns = [
            "Mã CK",
            "Ngành",
            "Giá khớp",
            "% Giá",
            "Ròng ngoại (tỷ)",
            "FPR (%)",
            "FMS (σ)",
            "Room đã dùng (%)",
            "Trạng thái hấp thụ",
            "Điểm tổng hợp",
        ]
        st.dataframe(display_df, use_container_width=True, hide_index=True)

    with tab_method:
        st.subheader("Phương pháp tính toán & Cơ sở tài chính")
        st.markdown(
            """
            1. **Foreign Participation Rate (FPR - %):**  
               $$\\text{FPR} = \\frac{V_{\\text{buy,foreign}} + V_{\\text{sell,foreign}}}{2 \\times V_{\\text{total}}} \\times 100\\%$$  
               Đo lường mức độ chi phối thanh khoản thực tế của nhà đầu tư nước ngoài đối với từng cổ phiếu.
            2. **Foreign Momentum Score (FMS - Z-score):**  
               $$\\text{FMS} = \\frac{\\text{NetVal}_t - \\mu_{10}}{\\sigma_{10} + \\epsilon}$$  
               Chuẩn hóa giá trị mua/bán ròng hiện tại theo trung bình và độ lệch chuẩn 10 phiên lịch sử gần nhất (lấy từ SSI `DailyStockPrice`). Ngưỡng $|\\text{FMS}| \\ge 2.0$ tương ứng mức bất thường thống kê ($p < 0.05$).
            3. **Cảnh báo Room Tới hạn (Foreign Room Squeeze):**  
               Kích hoạt khi tỷ lệ lấp đầy room đạt $\\ge 95\\%$ (theo dõi qua kênh SSI `R:symbols`) đồng thời khối ngoại tiếp tục duy trì mua ròng dương.
            4. **Phân loại Gom âm thầm / Xả kéo giá:**  
               Nhận diện phân kỳ giữa biến động giá thấp ($\\le 0.5\\%$) nhưng dòng vốn ngoại mua ròng đột biến ($\\text{FMS} \\ge 1.5, \\text{FPR} \\ge 15\\%$) và ngược lại.
            """
        )
        st.subheader("Giới hạn mô hình & Tuyên bố trách nhiệm")
        st.info(
            "- **Tuyên bố học thuật:** Sản phẩm này là công cụ phân tích tài chính phục vụ mục đích học thuật, "
            "**không phải khuyến nghị mua/bán chứng khoán** và tuyệt đối không phát sinh lệnh giao dịch thật.  \n"
            "- **Giới hạn dữ liệu:** Giao dịch thỏa thuận (put-through) nội khối ngoại có thể làm biến động cục bộ chỉ số FPR/FMS; "
            "cơ chế thu thập sử dụng SSI FastConnect Streaming (kênh `R:`, `X:`, `MI:`) kết hợp REST API (`DailyStockPrice`) nhằm tối ưu giới hạn tần suất (rate limit)."
        )


if __name__ == "__main__":
    main()