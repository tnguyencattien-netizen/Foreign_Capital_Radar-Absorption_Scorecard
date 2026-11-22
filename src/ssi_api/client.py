"""
Mô-đun kết nối SSI FastConnect Data API (REST + Streaming).
Chức năng:
1. Xác thực Access Token bằng ConsumerID & ConsumerSecret từ biến môi trường (.env).
2. Gọi REST API lấy danh sách rổ chỉ số (IndexComponents) và lịch sử cuối ngày (DailyStockPrice).
3. Chuyển đổi dữ liệu thô SSI sang chuẩn sạch của DatabaseManager.
"""

from dataclasses import dataclass
from datetime import date, timedelta
import logging
import os
import time
from typing import Any

from dotenv import load_dotenv
import requests

from src.data.cleaner import safe_float


load_dotenv()
logger = logging.getLogger(__name__)


@dataclass
class SSIConfig:
    """Cấu hình chuẩn tương thích với cả requests thuần lẫn SDK `ssi-fc-data`."""

    auth_type: str = os.getenv("SSI_AUTH_TYPE", "Bearer")
    consumerID: str = os.getenv("SSI_CONSUMER_ID", "")
    consumerSecret: str = os.getenv("SSI_CONSUMER_SECRET", "")
    url: str = os.getenv("SSI_API_BASE_URL", "https://fc-data.ssi.com.vn/")
    stream_url: str = os.getenv("SSI_STREAM_URL", "https://fc-data.ssi.com.vn/")


class SSIFastConnectClient:
    """Client REST API an toàn cho SSI FastConnect Data có xử lý lỗi và giới hạn tần suất."""

    def __init__(self, config: SSIConfig | None = None, timeout: int = 15) -> None:
        self.config = config or SSIConfig()
        self.base_url = self.config.url.rstrip("/")
        self.timeout = timeout
        self._access_token: str | None = None

    def authenticate(self) -> str:
        """Lấy JWT Access Token từ endpoint `/api/v2/Market/AccessToken`."""
        if not self.config.consumerID or not self.config.consumerSecret:
            raise ValueError(
                "Thiếu SSI_CONSUMER_ID hoặc SSI_CONSUMER_SECRET trong tệp `.env`."
            )

        endpoint = f"{self.base_url}/api/v2/Market/AccessToken"
        payload = {
            "consumerID": self.config.consumerID,
            "consumerSecret": self.config.consumerSecret,
        }
        resp = requests.post(endpoint, json=payload, timeout=self.timeout)
        resp.raise_for_status()
        body = resp.json()

        token = (body.get("data") or {}).get("accessToken")
        if not token:
            raise RuntimeError(f"Không lấy được AccessToken từ SSI API: {body}")

        self._access_token = token
        return token

    def _headers(self) -> dict[str, str]:
        if not self._access_token:
            self.authenticate()
        return {
            "Authorization": f"{self.config.auth_type} {self._access_token}",
            "Accept": "application/json",
        }

    def get_index_components(self, index_code: str = "VN30") -> list[str]:
        """Lấy danh sách mã cổ phiếu thuộc rổ chỉ số (mặc định VN30)."""
        endpoint = f"{self.base_url}/api/v2/Market/IndexComponents"
        params = {"indexCode": index_code, "pageIndex": 1, "pageSize": 100}
        resp = requests.get(endpoint, headers=self._headers(), params=params, timeout=self.timeout)
        resp.raise_for_status()
        data = resp.json().get("data") or []

        symbols: list[str] = []
        for item in data:
            for comp in item.get("IndexComponent", []):
                sym = str(comp.get("StockSymbol", "")).strip().upper()
                if sym:
                    symbols.append(sym)
        return sorted(set(symbols))

    def get_daily_stock_price(
        self,
        symbol: str,
        from_date: date,
        to_date: date,
    ) -> list[dict[str, Any]]:
        """
        Gọi `/api/v2/Market/DailyStockPrice` lấy dữ liệu lịch sử cuối ngày (định dạng DD/MM/YYYY)
        và chuẩn hóa sang lược đồ bảng `daily_history_ssi`.
        """
        endpoint = f"{self.base_url}/api/v2/Market/DailyStockPrice"
        params = {
            "Symbol": symbol.upper(),
            "FromDate": from_date.strftime("%d/%m/%Y"),
            "ToDate": to_date.strftime("%d/%m/%Y"),
            "PageIndex": 1,
            "PageSize": 100,
        }
        resp = requests.get(endpoint, headers=self._headers(), params=params, timeout=self.timeout)
        resp.raise_for_status()
        raw_rows = resp.json().get("data") or []

        normalized: list[dict[str, Any]] = []
        for row in raw_rows:
            raw_date = str(row.get("TradingDate", "")).strip()
            if len(raw_date) == 10 and "/" in raw_date:
                dd, mm, yyyy = raw_date.split("/")
                trade_date = f"{yyyy}-{mm}-{dd}"
            else:
                continue

            buy_val = safe_float(row.get("ForeignBuyValTotal"), 0.0)
            sell_val = safe_float(row.get("ForeignSellValTotal"), 0.0)
            net_val = safe_float(row.get("NetBuySellVal"), buy_val - sell_val)

            normalized.append(
                {
                    "symbol": symbol.upper(),
                    "trade_date": trade_date,
                    "close_price": safe_float(row.get("ClosePrice"), 0.0) / 1000.0,
                    "total_value": safe_float(row.get("TotalTradedValue"), 0.0),
                    "foreign_buy_val": buy_val,
                    "foreign_sell_val": sell_val,
                    "foreign_net_val": net_val,
                    "source": "SSI_API",
                }
            )
        return normalized

    def fetch_history_for_watchlist(
        self,
        symbols: list[str],
        lookback_calendar_days: int = 35,
        sleep_seconds: float = 0.5,
    ) -> list[dict[str, Any]]:
        """Thu thập lịch sử ~20 phiên giao dịch cho toàn bộ danh sách theo dõi (có giãn cách tránh rate limit)."""
        to_dt = date.today()
        from_dt = to_dt - timedelta(days=lookback_calendar_days)
        all_rows: list[dict[str, Any]] = []

        for sym in symbols:
            try:
                rows = self.get_daily_stock_price(sym, from_dt, to_dt)
                all_rows.extend(rows)
            except Exception as exc:
                logger.warning("Lỗi lấy DailyStockPrice cho %s: %s", sym, exc)
            time.sleep(sleep_seconds)

        return all_rows