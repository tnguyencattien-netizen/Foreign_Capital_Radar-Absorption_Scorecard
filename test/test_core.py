import pytest

from src.analytics.alerts import detect_intraday_reversals, generate_snapshot_alerts
from src.data.cleaner import (
    clean_intraday_record,
    deduplicate_intraday_batch,
    normalize_timestamp_vn,
)
from src.data.database import DatabaseManager
from src.data.mock_generator import seed_mock_database
from src.finance.indicators import (
    calculate_fms,
    calculate_fpr,
    calculate_room_used_pct,
    classify_absorption_state,
    compute_scorecard,
)


def test_normalize_timestamp_utc_to_vn():
    # 02:30:00 UTC phải chuyển thành 09:30:00+07:00
    ts_vn = normalize_timestamp_vn("2026-11-23T02:30:00Z")
    assert ts_vn == "2026-11-23T09:30:00+07:00"


def test_clean_intraday_handles_nulls_and_invalid_room():
    raw = {
        "timestamp_vn": "2026-11-23T09:35:00+07:00",
        "symbol": " fpt ",
        "last_price": "138.5",
        "ref_price": "135.0",
        "total_value": "500,000,000,000",
        "foreign_buy_val": None,
        "foreign_sell_val": "NaN",
        "available_room": 1200,
        "total_room": 1000,  # Bất thường: available > total
    }
    cleaned = clean_intraday_record(raw)
    assert cleaned is not None
    assert cleaned["symbol"] == "FPT"
    assert cleaned["foreign_buy_val"] == 0.0
    assert cleaned["foreign_sell_val"] == 0.0
    assert cleaned["available_room"] == 1000.0
    assert cleaned["pct_change"] == pytest.approx(2.59, abs=0.01)


def test_deduplicate_batch():
    recs = [
        {"timestamp_vn": "2026-11-23T09:30:00+07:00", "symbol": "VCB", "last_price": 92.0},
        {"timestamp_vn": "2026-11-23T09:30:00+07:00", "symbol": "VCB", "last_price": 92.5},
    ]
    deduped = deduplicate_intraday_batch(recs)
    assert len(deduped) == 1
    assert deduped[0]["last_price"] == 92.5


def test_financial_indicators_formulas_and_edge_cases():
    # FPR chuẩn & biên chia cho 0
    assert calculate_fpr(100e9, 50e9, 500e9) == 15.0
    assert calculate_fpr(100e9, 50e9, 0.0) == 0.0

    # FMS chuẩn & biên std = 0
    assert calculate_fms(50e9, 10e9, 20e9) == 2.0
    assert calculate_fms(10e9, 10e9, 0.0) == 0.0

    # Room used %
    assert calculate_room_used_pct(500, 10000) == 95.0
    assert calculate_room_used_pct(0, 0) == 0.0

    # Phân loại Gom âm thầm
    assert classify_absorption_state(pct_change=0.2, fms=2.1, fpr=20.0) == "GOM_AM_THAM"
    assert classify_absorption_state(pct_change=1.8, fms=-2.2, fpr=18.0) == "XA_KEO_GIA"


def test_end_to_end_pipeline_with_mock_data(tmp_path):
    db_file = tmp_path / "test_radar.db"
    seed_mock_database(str(db_file), seed=42)

    db = DatabaseManager(db_file)
    df_snap = db.fetch_latest_intraday_snapshot(trade_date="2026-11-20")
    df_stats = db.fetch_historical_net_stats(lookback_days=10)
    df_series = db.fetch_intraday_series(trade_date="2026-11-20")

    scorecard = compute_scorecard(df_snap, df_stats)
    assert len(scorecard) == 10
    assert {"fpr_pct", "fms_score", "room_used_pct", "absorption_state", "composite_score"}.issubset(
        scorecard.columns
    )

    alerts = generate_snapshot_alerts(scorecard)
    alert_types = {(a.symbol, a.alert_type) for a in alerts}
    assert ("FPT", "ROOM_SQUEEZE") in alert_types
    assert ("MBB", "STEALTH_ACCUMULATION") in alert_types

    reversals = detect_intraday_reversals(df_series, lookback_steps=3)
    assert any(r.symbol == "HPG" and r.alert_type == "INTRADAY_REVERSAL" for r in reversals)