"""
Kịch bản chạy thu thập dữ liệu tập trung cho đồ án Foreign Capital Radar (SSI FastConnect Data).

Cách dùng:
1. Chỉ kiểm tra kết nối & nạp sẵn 20 phiên lịch sử nền (chạy trước 09:00 hoặc sau 15:00):
   python scripts/run_collector.py --history-only

2. Nạp lịch sử nền + chạy lắng nghe luồng thời gian thực xuyên phiên (chạy lúc 08:55 - 15:05):
   python scripts/run_collector.py
"""

import argparse
import logging
import os
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv

from src.data.database import DatabaseManager
from src.ssi_api.client import SSIConfig, SSIFastConnectClient
from src.ssi_api.streamer import SSIRealtimeCollector


load_dotenv()

VN30_SECTOR_MAP: dict[str, str] = {
    "ACB": "Ngân hàng", "BCM": "Bất động sản", "BID": "Ngân hàng", "BVH": "Bảo hiểm",
    "CTG": "Ngân hàng", "FPT": "Công nghệ", "GAS": "Dầu khí", "GVR": "Hóa chất & Cao su",
    "HDB": "Ngân hàng", "HPG": "Thép", "LPB": "Ngân hàng", "MBB": "Ngân hàng",
    "MSN": "Bán lẻ & Tiêu dùng", "MWG": "Bán lẻ & Tiêu dùng", "PLX": "Dầu khí",
    "POW": "Điện & Năng lượng", "SAB": "Thực phẩm & Đồ uống", "SHB": "Ngân hàng",
    "SSB": "Ngân hàng", "SSI": "Chứng khoán", "STB": "Ngân hàng", "TCB": "Ngân hàng",
    "TPB": "Ngân hàng", "VCB": "Ngân hàng", "VHM": "Bất động sản", "VIB": "Ngân hàng",
    "VIC": "Bất động sản", "VJC": "Hàng không", "VNM": "Thực phẩm & Đồ uống",
    "VPB": "Ngân hàng", "VRE": "Bất động sản",
}


def setup_logging() -> None:
    log_dir = PROJECT_ROOT / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "collector.log"

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Thu thập dữ liệu SSI FastConnect Data cho Foreign Capital Radar.")
    parser.add_argument(
        "--history-only",
        action="store_true",
        help="Chỉ tải danh mục VN30 và 20 phiên lịch sử DailyStockPrice, không mở luồng Streaming.",
    )
    parser.add_argument(
        "--skip-history",
        action="store_true",
        help="Bỏ qua bước tải lịch sử 20 phiên, vào thẳng luồng Streaming (dùng khi khởi động lại trong phiên).",
    )
    args = parser.parse_args()

    setup_logging()
    logger = logging.getLogger("run_collector")

    config = SSIConfig()
    if not config.consumerID or not config.consumerSecret:
        logger.error(
            "CHƯA CẤU HÌNH KHÓA API: Vui lòng tạo tệp `.env` từ `.env.example` và điền "
            "`SSI_CONSUMER_ID`, `SSI_CONSUMER_SECRET` trước khi chạy."
        )
        sys.exit(1)

    db_path = Path(os.getenv("DB_PATH", "data/foreign_radar.db"))
    index_code = os.getenv("WATCHLIST_INDEX", "VN30")
    flush_interval = int(os.getenv("POLL_INTERVAL_SECONDS", "45"))

    db = DatabaseManager(db_path)
    client = SSIFastConnectClient(config)

    logger.info("Bước 1/3: Đang xác thực Access Token với SSI FastConnect Data API...")
    try:
        client.authenticate()
        logger.info("Xác thực SSI thành công!")
    except Exception as exc:
        logger.error("Xác thực SSI thất bại: %s", exc)
        sys.exit(1)

    logger.info("Bước 2/3: Đang tải danh mục chỉ số %s...", index_code)
    try:
        watchlist = client.get_index_components(index_code=index_code)
        if not watchlist:
            raise RuntimeError("Danh sách mã trả về rỗng.")
    except Exception as exc:
        logger.warning("Không lấy được danh mục %s từ API (%s). Dùng danh sách VN30 dự phòng.", index_code, exc)
        watchlist = sorted(VN30_SECTOR_MAP.keys())

    sector_rows = [
        {
            "symbol": sym,
            "company_name": sym,
            "sector": VN30_SECTOR_MAP.get(sym, "Khác"),
            "exchange": "HOSE",
        }
        for sym in watchlist
    ]
    db.upsert_ticker_sectors(sector_rows)
    logger.info("Đã cập nhật %d mã vào bảng `ticker_sector`.", len(sector_rows))

    if not args.skip_history:
        logger.info("Bước 3/3: Đang tải lịch sử ~20 phiên gần nhất cho %d mã (giãn cách 0.5s/mã)...", len(watchlist))
        history_rows = client.fetch_history_for_watchlist(watchlist, lookback_calendar_days=35, sleep_seconds=0.5)
        saved = db.upsert_daily_history(history_rows)
        logger.info("Đã lưu %d bản ghi lịch sử vào bảng `daily_history_ssi`.", saved)

    if args.history_only:
        logger.info("Hoàn tất chế độ `--history-only`. Hệ thống sẵn sàng cho phiên giao dịch!")
        return

    collector = SSIRealtimeCollector(
        config=config,
        db=db,
        watchlist=watchlist,
        flush_interval=flush_interval,
    )
    collector.run_forever()


if __name__ == "__main__":
    main()