"""
Mô-đun lắng nghe luồng dữ liệu thời gian thực (SignalR Streaming) từ SSI FastConnect Data API.
Khai thác đồng thời 3 kênh:
- R:{symbols} : Dữ liệu dòng tiền & room khối ngoại (Foreign Room)
- X:{symbols} : Dữ liệu giá khớp & tổng thanh khoản (Market Data)
- MI:ALL      : Dữ liệu chỉ số thị trường (VN-Index, VN30, độ rộng thị trường)

Cơ chế an toàn:
- Gom trạng thái mới nhất trong bộ nhớ (In-memory Aggregation) và ghi xuống SQLite định kỳ (mặc định 45s)
  để tránh lỗi khóa DB (database locked) và tối ưu dung lượng lưu trữ.
- Tự động chuẩn hóa múi giờ UTC+7, ghi nhật ký (log) và hỗ trợ tái kết nối khi rớt mạng.
"""

from datetime import datetime
import json
import logging
import threading
import time
from typing import Any
from zoneinfo import ZoneInfo

from ssi_fc_data.fc_md_client import MarketDataClient
from ssi_fc_data.fc_md_stream import MarketDataStream

from src.data.cleaner import clean_intraday_record, normalize_timestamp_vn, safe_float
from src.data.database import DatabaseManager
from src.ssi_api.client import SSIConfig


VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
logger = logging.getLogger(__name__)


def _parse_json_safe(payload: Any) -> dict[str, Any]:
    """Giải mã an toàn thông điệp từ SignalR (có thể là dict hoặc chuỗi JSON lồng nhau)."""
    if isinstance(payload, dict):
        return payload
    if isinstance(payload, str):
        try:
            parsed = json.loads(payload)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _to_thousand_vnd(price: float) -> float:
    """Chuẩn hóa đơn vị giá về nghìn VND (ví dụ 92500 -> 92.5) cho đồng nhất với Dashboard."""
    return price / 1000.0 if price > 1000.0 else price


class SSIRealtimeCollector:
    """Quản lý luồng SSI Streaming và ghi định kỳ vào SQLite."""

    def __init__(
        self,
        config: SSIConfig,
        db: DatabaseManager,
        watchlist: list[str],
        flush_interval: int = 45,
    ) -> None:
        self.config = config
        self.db = db
        self.watchlist = sorted({s.strip().upper() for s in watchlist if s.strip()})
        self.flush_interval = max(15, int(flush_interval))

        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._stock_state: dict[str, dict[str, Any]] = {}
        self._index_state: dict[str, dict[str, Any]] = {}
        self._msg_count = 0

    def _build_channel_string(self) -> str:
        """Tạo chuỗi đăng ký 3 kênh R, X và MI theo quy ước của SSI FastConnect Data."""
        joined = "-".join(self.watchlist)
        return f"X:{joined},R:{joined},MI:ALL"

    def _on_message(self, message: Any) -> None:
        """Hàm callback xử lý mọi thông điệp đẩy về từ SSI SignalR."""
        outer = _parse_json_safe(message)
        if not outer:
            return

        data_type = str(outer.get("dataType") or outer.get("DataType") or "").upper()
        content = _parse_json_safe(outer.get("Content") or outer.get("content") or outer)
        if not content:
            return

        if not data_type:
            data_type = str(content.get("RType") or "").upper()

        with self._lock:
            self._msg_count += 1
            if data_type.startswith("R"):
                self._update_foreign_room(content)
            elif data_type.startswith("X"):
                self._update_market_trade(content)
            elif data_type.startswith("MI"):
                self._update_market_index(content)

    def _update_foreign_room(self, content: dict[str, Any]) -> None:
        """Cập nhật dòng tiền & room khối ngoại từ kênh R."""
        symbol = str(content.get("Symbol") or content.get("symbol") or "").strip().upper()
        if not symbol or (self.watchlist and symbol not in self.watchlist):
            return

        state = self._stock_state.setdefault(symbol, {"symbol": symbol})
        state["foreign_buy_vol"] = safe_float(
            content.get("BuyVol") or content.get("FBuyVol") or state.get("foreign_buy_vol"), 0.0
        )
        state["foreign_sell_vol"] = safe_float(
            content.get("SellVol") or content.get("FSellVol") or state.get("foreign_sell_vol"), 0.0
        )
        state["foreign_buy_val"] = safe_float(
            content.get("BuyVal") or content.get("FBuyVal") or state.get("foreign_buy_val"), 0.0
        )
        state["foreign_sell_val"] = safe_float(
            content.get("SellVal") or content.get("FSellVal") or state.get("foreign_sell_val"), 0.0
        )
        state["available_room"] = safe_float(
            content.get("CurrentRoom") or content.get("AvailableRoom") or state.get("available_room"), 0.0
        )
        state["total_room"] = safe_float(
            content.get("TotalRoom") or state.get("total_room"), 0.0
        )

    def _update_market_trade(self, content: dict[str, Any]) -> None:
        """Cập nhật giá khớp & tổng thanh khoản từ kênh X."""
        symbol = str(content.get("Symbol") or content.get("symbol") or "").strip().upper()
        if not symbol or (self.watchlist and symbol not in self.watchlist):
            return

        state = self._stock_state.setdefault(symbol, {"symbol": symbol})

        raw_last = safe_float(content.get("LastPrice") or content.get("Close"), 0.0)
        if raw_last > 0:
            state["last_price"] = _to_thousand_vnd(raw_last)

        raw_ref = safe_float(content.get("RefPrice") or content.get("PriorVal"), 0.0)
        if raw_ref > 0:
            state["ref_price"] = _to_thousand_vnd(raw_ref)

        if content.get("RatioChange") is not None:
            state["pct_change"] = safe_float(content.get("RatioChange"), 0.0)

        tot_vol = safe_float(content.get("TotalVol") or content.get("TotalTradedVol"), 0.0)
        if tot_vol > 0:
            state["total_volume"] = tot_vol

        tot_val = safe_float(content.get("TotalVal") or content.get("TotalTradedValue"), 0.0)
        if tot_val > 0:
            state["total_value"] = tot_val

    def _update_market_index(self, content: dict[str, Any]) -> None:
        """Cập nhật chỉ số VN-Index / VN30 từ kênh MI."""
        index_code = str(content.get("IndexId") or content.get("IndexName") or "").strip().upper()
        if index_code not in {"VNINDEX", "VN30", "HNXINDEX", "UPCOMINDEX"}:
            return

        self._index_state[index_code] = {
            "index_code": index_code,
            "index_value": safe_float(content.get("IndexValue"), 0.0),
            "pct_change": safe_float(content.get("RatioChange"), 0.0),
            "total_volume": safe_float(content.get("TotalQtty") or content.get("TotalVolume"), 0.0),
            "total_value": safe_float(content.get("TotalValue"), 0.0),
            "advances": int(safe_float(content.get("Advances"), 0.0)),
            "declines": int(safe_float(content.get("Declines"), 0.0)),
            "unchanged": int(safe_float(content.get("Nochanges"), 0.0)),
            "source": "SSI_STREAM",
        }

    def _on_error(self, error: Any) -> None:
        """Ghi nhận nhật ký khi kết nối SignalR gặp lỗi."""
        logger.error("Lỗi luồng SSI Streaming: %s", error)

    def _flush_to_database(self) -> None:
        """Chụp trạng thái bộ đệm hiện tại và lưu an toàn xuống SQLite."""
        now_vn = normalize_timestamp_vn(datetime.now(VN_TZ))

        with self._lock:
            stock_snapshots = [dict(v) for v in self._stock_state.values()]
            index_snapshots = [dict(v) for v in self._index_state.values()]
            msg_count = self._msg_count
            self._msg_count = 0

        cleaned_stocks = []
        for raw in stock_snapshots:
            raw["timestamp_vn"] = now_vn
            rec = clean_intraday_record(raw, source="SSI_STREAM")
            if rec:
                cleaned_stocks.append(rec)

        inserted_stocks = self.db.insert_intraday_records(cleaned_stocks) if cleaned_stocks else 0
        inserted_indices = self._insert_index_records(index_snapshots, now_vn)

        logger.info(
            "[FLUSH %s] Đã nhận %d gói tin | Lưu %d mã cổ phiếu & %d chỉ số vào SQLite.",
            now_vn,
            msg_count,
            inserted_stocks,
            inserted_indices,
        )

    def _insert_index_records(self, records: list[dict[str, Any]], timestamp_vn: str) -> int:
        if not records:
            return 0
        sql = """
            INSERT OR IGNORE INTO market_index_ws (
                timestamp_vn, index_code, index_value, pct_change,
                total_volume, total_value, advances, declines, unchanged, source
            )
            VALUES (
                :timestamp_vn, :index_code, :index_value, :pct_change,
                :total_volume, :total_value, :advances, :declines, :unchanged, :source
            )
        """
        rows = [{**rec, "timestamp_vn": timestamp_vn} for rec in records]
        with self.db.get_connection() as conn:
            cursor = conn.executemany(sql, rows)
            return cursor.rowcount

    def _flush_loop(self) -> None:
        """Luồng nền ghi dữ liệu định kỳ theo `flush_interval` giây."""
        while not self._stop_event.wait(self.flush_interval):
            try:
                self._flush_to_database()
            except Exception as exc:
                logger.exception("Lỗi khi ghi bộ đệm vào SQLite: %s", exc)

    def run_forever(self) -> None:
        """Khởi động bộ lắng nghe SSI SignalR kèm cơ chế tự tái kết nối khi rớt mạng."""
        if not self.watchlist:
            raise ValueError("Danh sách cổ phiếu theo dõi (watchlist) đang rỗng.")

        flush_thread = threading.Thread(target=self._flush_loop, daemon=True, name="SQLiteFlusher")
        flush_thread.start()

        channels = self._build_channel_string()
        backoff_sec = 5

        logger.info("Bắt đầu kết nối SSI Streaming cho %d mã (chu kỳ lưu: %ds)...", len(self.watchlist), self.flush_interval)

        try:
            while not self._stop_event.is_set():
                try:
                    md_client = MarketDataClient(self.config)
                    stream = MarketDataStream(self.config, md_client)
                    logger.info("Đăng ký kênh SSI: %s", channels)
                    stream.start(self._on_message, self._on_error, channels)
                    while not self._stop_event.is_set():
                        time.sleep(1.0)
                except KeyboardInterrupt:
                    raise
                except Exception as exc:
                    logger.warning("Mất kết nối SSI Streaming (%s). Thử kết nối lại sau %ds...", exc, backoff_sec)
                    if self._stop_event.wait(backoff_sec):
                        break
                    backoff_sec = min(backoff_sec * 2, 60)
        except KeyboardInterrupt:
            logger.info("Nhận tín hiệu dừng (Ctrl+C). Đang ghi lần cuối trước khi thoát...")
        finally:
            self._stop_event.set()
            try:
                self._flush_to_database()
            except Exception as exc:
                logger.error("Lỗi khi ghi lần cuối: %s", exc)
            logger.info("Đã dừng SSIRealtimeCollector an toàn.")