import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable, Mapping, Any

import pandas as pd


DEFAULT_DB_PATH = Path("data/foreign_radar.db")


class DatabaseManager:
    """Quản lý kết nối và thao tác lưu trữ SQLite cho Foreign Capital Radar."""

    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.init_db()

    @contextmanager
    def get_connection(self):
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def init_db(self) -> None:
        """Khởi tạo 4 bảng dữ liệu chuẩn theo kiến trúc hệ thống."""
        with self.get_connection() as conn:
            cursor = conn.cursor()

            # 1. Danh mục cổ phiếu & Phân ngành (Bổ trợ từ SSI API)
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS ticker_sector (
                    symbol TEXT PRIMARY KEY,
                    company_name TEXT,
                    sector TEXT NOT NULL,
                    exchange TEXT DEFAULT 'HOSE'
                )
                """
            )

            # 2. Lịch sử giao dịch khối ngoại cuối ngày (20 phiên nền từ SSI API + chốt phiên NEO)
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS daily_history_ssi (
                    symbol TEXT NOT NULL,
                    trade_date TEXT NOT NULL,
                    close_price REAL,
                    total_value REAL,
                    foreign_buy_val REAL,
                    foreign_sell_val REAL,
                    foreign_net_val REAL,
                    source TEXT DEFAULT 'SSI_API',
                    PRIMARY KEY (symbol, trade_date)
                )
                """
            )

            # 3. Dữ liệu khối ngoại & thanh khoản nội ngày (Cốt lõi từ NEO REST API)
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS stock_foreign_intraday (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp_vn TEXT NOT NULL,
                    trade_date TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    last_price REAL,
                    ref_price REAL,
                    pct_change REAL,
                    total_volume REAL,
                    total_value REAL,
                    foreign_buy_vol REAL,
                    foreign_sell_vol REAL,
                    foreign_buy_val REAL,
                    foreign_sell_val REAL,
                    foreign_net_val REAL,
                    available_room REAL,
                    total_room REAL,
                    source TEXT DEFAULT 'NEO_API',
                    UNIQUE (timestamp_vn, symbol)
                )
                """
            )

            # 4. Luồng chỉ số thị trường thời gian thực (Cốt lõi từ NEO WebSocket)
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS market_index_ws (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp_vn TEXT NOT NULL,
                    index_code TEXT NOT NULL,
                    index_value REAL,
                    pct_change REAL,
                    total_volume REAL,
                    total_value REAL,
                    advances INTEGER,
                    declines INTEGER,
                    unchanged INTEGER,
                    source TEXT DEFAULT 'NEO_WS',
                    UNIQUE (timestamp_vn, index_code)
                )
                """
            )

            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_intraday_symbol_date "
                "ON stock_foreign_intraday (symbol, trade_date, timestamp_vn)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_daily_symbol_date "
                "ON daily_history_ssi (symbol, trade_date)"
            )

    def upsert_ticker_sectors(self, records: Iterable[Mapping[str, Any]]) -> int:
        sql = """
            INSERT INTO ticker_sector (symbol, company_name, sector, exchange)
            VALUES (:symbol, :company_name, :sector, :exchange)
            ON CONFLICT(symbol) DO UPDATE SET
                company_name = excluded.company_name,
                sector = excluded.sector,
                exchange = excluded.exchange
        """
        rows = list(records)
        if not rows:
            return 0
        with self.get_connection() as conn:
            conn.executemany(sql, rows)
        return len(rows)

    def upsert_daily_history(self, records: Iterable[Mapping[str, Any]]) -> int:
        sql = """
            INSERT INTO daily_history_ssi (
                symbol, trade_date, close_price, total_value,
                foreign_buy_val, foreign_sell_val, foreign_net_val, source
            )
            VALUES (
                :symbol, :trade_date, :close_price, :total_value,
                :foreign_buy_val, :foreign_sell_val, :foreign_net_val, :source
            )
            ON CONFLICT(symbol, trade_date) DO UPDATE SET
                close_price = excluded.close_price,
                total_value = excluded.total_value,
                foreign_buy_val = excluded.foreign_buy_val,
                foreign_sell_val = excluded.foreign_sell_val,
                foreign_net_val = excluded.foreign_net_val,
                source = excluded.source
        """
        rows = list(records)
        if not rows:
            return 0
        with self.get_connection() as conn:
            conn.executemany(sql, rows)
        return len(rows)

    def insert_intraday_records(self, records: Iterable[Mapping[str, Any]]) -> int:
        """Lưu bản ghi nội ngày, tự động bỏ qua bản ghi trùng lặp (timestamp_vn, symbol)."""
        sql = """
            INSERT OR IGNORE INTO stock_foreign_intraday (
                timestamp_vn, trade_date, symbol, last_price, ref_price, pct_change,
                total_volume, total_value, foreign_buy_vol, foreign_sell_vol,
                foreign_buy_val, foreign_sell_val, foreign_net_val,
                available_room, total_room, source
            )
            VALUES (
                :timestamp_vn, :trade_date, :symbol, :last_price, :ref_price, :pct_change,
                :total_volume, :total_value, :foreign_buy_vol, :foreign_sell_vol,
                :foreign_buy_val, :foreign_sell_val, :foreign_net_val,
                :available_room, :total_room, :source
            )
        """
        rows = list(records)
        if not rows:
            return 0
        with self.get_connection() as conn:
            cursor = conn.executemany(sql, rows)
            return cursor.rowcount

    def fetch_historical_net_stats(self, lookback_days: int = 10) -> pd.DataFrame:
        """Tính trung bình và độ lệch chuẩn giá trị mua ròng N phiên gần nhất cho từng mã."""
        query = """
            WITH ranked AS (
                SELECT
                    symbol,
                    trade_date,
                    foreign_net_val,
                    ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY trade_date DESC) AS rn
                FROM daily_history_ssi
            )
            SELECT symbol, foreign_net_val
            FROM ranked
            WHERE rn <= ?
        """
        with self.get_connection() as conn:
            df = pd.read_sql_query(query, conn, params=(lookback_days,))

        if df.empty:
            return pd.DataFrame(columns=["symbol", "mean_net_val", "std_net_val", "hist_sessions"])

        stats = (
            df.groupby("symbol", as_index=False)["foreign_net_val"]
            .agg(
                mean_net_val="mean",
                std_net_val=lambda s: float(s.std(ddof=1)) if len(s) > 1 else 0.0,
                hist_sessions="count",
            )
        )
        return stats

    def fetch_latest_intraday_snapshot(self, trade_date: str | None = None) -> pd.DataFrame:
        """Lấy ảnh chụp (snapshot) mới nhất của từng cổ phiếu kèm thông tin ngành."""
        date_filter = "WHERE i.trade_date = ?" if trade_date else ""
        params = (trade_date,) if trade_date else ()

        query = f"""
            WITH latest_ts AS (
                SELECT symbol, MAX(timestamp_vn) AS max_ts
                FROM stock_foreign_intraday i
                {date_filter}
                GROUP BY symbol
            )
            SELECT
                i.*,
                COALESCE(t.sector, 'Khác') AS sector,
                COALESCE(t.company_name, i.symbol) AS company_name
            FROM stock_foreign_intraday i
            INNER JOIN latest_ts l
                ON i.symbol = l.symbol AND i.timestamp_vn = l.max_ts
            LEFT JOIN ticker_sector t
                ON i.symbol = t.symbol
        """
        with self.get_connection() as conn:
            return pd.read_sql_query(query, conn, params=params)

    def fetch_intraday_series(self, trade_date: str, symbol: str | None = None) -> pd.DataFrame:
        """Lấy chuỗi thời gian nội ngày phục vụ vẽ biểu đồ và phát hiện đảo chiều."""
        if symbol:
            query = """
                SELECT * FROM stock_foreign_intraday
                WHERE trade_date = ? AND symbol = ?
                ORDER BY timestamp_vn ASC
            """
            params = (trade_date, symbol)
        else:
            query = """
                SELECT * FROM stock_foreign_intraday
                WHERE trade_date = ?
                ORDER BY symbol ASC, timestamp_vn ASC
            """
            params = (trade_date,)

        with self.get_connection() as conn:
            return pd.read_sql_query(query, conn, params=params)