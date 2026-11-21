"""
Bộ sinh dữ liệu mô phỏng (SIMULATED_MOCK) phục vụ kiểm thử kiến trúc và giao diện
khi chưa có kết nối tài khoản NEO API / SSI API.
Tuân thủ Mục 13 (Liêm chính học thuật): Mọi bản ghi đều gắn nhãn source='SIMULATED_MOCK'.
"""

from datetime import date, datetime, timedelta
import random
from zoneinfo import ZoneInfo

from src.data.cleaner import clean_intraday_record
from src.data.database import DatabaseManager


VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")

SAMPLE_WATCHLIST = [
    {"symbol": "VCB", "company_name": "Vietcombank", "sector": "Ngân hàng", "exchange": "HOSE", "ref": 92.5, "room": 1_676_000_000},
    {"symbol": "MBB", "company_name": "MB Bank", "sector": "Ngân hàng", "exchange": "HOSE", "ref": 25.0, "room": 1_230_000_000},
    {"symbol": "ACB", "company_name": "Ngân hàng Á Châu", "sector": "Ngân hàng", "exchange": "HOSE", "ref": 25.8, "room": 1_340_000_000},
    {"symbol": "FPT", "company_name": "Tập đoàn FPT", "sector": "Công nghệ", "exchange": "HOSE", "ref": 136.0, "room": 715_000_000},
    {"symbol": "MWG", "company_name": "Thế Giới Di Động", "sector": "Bán lẻ", "exchange": "HOSE", "ref": 64.5, "room": 716_000_000},
    {"symbol": "HPG", "company_name": "Tập đoàn Hòa Phát", "sector": "Thép", "exchange": "HOSE", "ref": 27.2, "room": 3_134_000_000},
    {"symbol": "SSI", "company_name": "Chứng khoán SSI", "sector": "Chứng khoán", "exchange": "HOSE", "ref": 26.5, "room": 1_810_000_000},
    {"symbol": "VHM", "company_name": "Vinhomes", "sector": "Bất động sản", "exchange": "HOSE", "ref": 42.0, "room": 2_177_000_000},
    {"symbol": "VNM", "company_name": "Vinamilk", "sector": "Thực phẩm", "exchange": "HOSE", "ref": 67.0, "room": 2_089_000_000},
    {"symbol": "MSN", "company_name": "Tập đoàn Masan", "sector": "Bán lẻ", "exchange": "HOSE", "ref": 75.5, "room": 702_000_000},
]


def seed_mock_database(db_path: str = "data/mock_foreign_radar.db", seed: int = 42) -> str:
    """Khởi tạo DB mô phỏng gồm 15 phiên lịch sử + 1 phiên nội ngày có sẵn các kịch bản cảnh báo."""
    rng = random.Random(seed)
    db = DatabaseManager(db_path)

    # 1. Nạp danh mục ngành
    db.upsert_ticker_sectors(
        [
            {
                "symbol": item["symbol"],
                "company_name": item["company_name"],
                "sector": item["sector"],
                "exchange": item["exchange"],
            }
            for item in SAMPLE_WATCHLIST
        ]
    )

    # 2. Nạp 15 phiên lịch sử nền (giả lập dữ liệu từ SSI API)
    base_date = date(2026, 11, 20)
    history_rows = []
    for item in SAMPLE_WATCHLIST:
        for d in range(1, 16):
            trade_day = (base_date - timedelta(days=d)).isoformat()
            buy_val = rng.uniform(25e9, 90e9)
            sell_val = rng.uniform(25e9, 90e9)
            history_rows.append(
                {
                    "symbol": item["symbol"],
                    "trade_date": trade_day,
                    "close_price": round(item["ref"] * rng.uniform(0.96, 1.04), 2),
                    "total_value": rng.uniform(250e9, 900e9),
                    "foreign_buy_val": buy_val,
                    "foreign_sell_val": sell_val,
                    "foreign_net_val": buy_val - sell_val,
                    "source": "SIMULATED_MOCK",
                }
            )
    db.upsert_daily_history(history_rows)

    # 3. Nạp 12 mốc thời gian nội ngày (mỗi 5 phút từ 09:30 đến 10:25)
    start_dt = datetime(2026, 11, 20, 9, 30, 0, tzinfo=VN_TZ)
    intraday_rows = []
    for step in range(12):
        ts = start_dt + timedelta(minutes=5 * step)
        progress = (step + 1) / 12.0

        for item in SAMPLE_WATCHLIST:
            sym = item["symbol"]
            ref = item["ref"]
            total_room = item["room"]

            # Mặc định ngẫu nhiên có kiểm soát
            pct_chg = rng.uniform(-1.2, 1.2)
            total_val = 400e9 * progress * rng.uniform(0.8, 1.2)
            f_buy = total_val * rng.uniform(0.08, 0.16)
            f_sell = total_val * rng.uniform(0.08, 0.16)
            avail_room = total_room * rng.uniform(0.15, 0.45)

            # Cài sẵn 3 kịch bản kiểm chứng rõ ràng
            if sym == "FPT":
                # Kịch bản 1: Room tới hạn (98.5% đã lấp đầy) + mua ròng đột biến
                pct_chg = 1.8 * progress
                f_buy = 240e9 * progress
                f_sell = 30e9 * progress
                avail_room = total_room * (0.018 - 0.004 * progress)
            elif sym == "MBB":
                # Kịch bản 2: Gom âm thầm (giá đi ngang ~0.2%, nhưng khối ngoại gom mạnh)
                pct_chg = 0.2
                total_val = 420e9 * progress
                f_buy = 170e9 * progress
                f_sell = 15e9 * progress
            elif sym == "HPG":
                # Kịch bản 3: Đảo chiều đột ngột từ bán ròng sang mua ròng ở 3 mốc cuối
                if step < 9:
                    f_buy, f_sell = 40e9 * progress, 95e9 * progress
                else:
                    f_buy, f_sell = 180e9 * progress, 80e9 * progress

            raw = {
                "timestamp_vn": ts.isoformat(timespec="seconds"),
                "symbol": sym,
                "last_price": round(ref * (1 + pct_chg / 100.0), 2),
                "ref_price": ref,
                "pct_change": pct_chg,
                "total_volume": total_val / (ref * 1000.0),
                "total_value": total_val,
                "foreign_buy_vol": f_buy / (ref * 1000.0),
                "foreign_sell_vol": f_sell / (ref * 1000.0),
                "foreign_buy_val": f_buy,
                "foreign_sell_val": f_sell,
                "available_room": avail_room,
                "total_room": total_room,
                "source": "SIMULATED_MOCK",
            }
            cleaned = clean_intraday_record(raw, source="SIMULATED_MOCK")
            if cleaned:
                intraday_rows.append(cleaned)

    db.insert_intraday_records(intraday_rows)
    return str(db.db_path)


if __name__ == "__main__":
    path = seed_mock_database()
    print(f"Đã khởi tạo dữ liệu mô phỏng thành công tại: {path}")