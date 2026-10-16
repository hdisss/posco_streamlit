"""Yahoo Finance collection and calculations, shared by UI and CLI."""
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf

ROOT = Path(__file__).resolve().parent
STOCKS = {
    "005930.KS": ("삼성전자", "반도체"),
    "000660.KS": ("SK하이닉스", "반도체"),
    "373220.KS": ("LG에너지솔루션", "배터리"),
    "005380.KS": ("현대차", "자동차"),
    "000270.KS": ("기아", "자동차"),
    "035420.KS": ("NAVER", "인터넷"),
    "035720.KS": ("카카오", "인터넷"),
    "005490.KS": ("POSCO홀딩스", "소재"),
    "105560.KS": ("KB금융", "금융"),
    "068270.KS": ("셀트리온", "바이오"),
}
FIELDS = ["Open", "High", "Low", "Close", "Volume"]


def collect(start, end):
    """End is inclusive. Fetch preceding bars to calculate first-day changes."""
    yf.set_tz_cache_location(str(ROOT / ".cache" / "yfinance"))
    raw = yf.download(
        list(STOCKS), start=start - timedelta(days=14),
        end=end + timedelta(days=1), interval="1d", auto_adjust=True,
        group_by="ticker", threads=False, progress=False, timeout=20,
    )
    frames, failed = [], []
    for ticker, (name, sector) in STOCKS.items():
        try:
            frame = raw[ticker].copy()
            frame = frame.reindex(columns=FIELDS).dropna(subset=["Close"])
            frame = frame[frame["Close"] > 0].sort_index()
            frame["DailyReturn"] = frame["Close"].pct_change(fill_method=None) * 100
            frame.index = pd.to_datetime(frame.index).tz_localize(None)
            frame = frame.loc[str(start):str(end)].copy()
            if frame.empty:
                failed.append(name)
                continue
            frame.index.name = "Date"
            frame = frame.reset_index()
            frame["Ticker"], frame["Name"], frame["Sector"] = ticker, name, sector
            frames.append(frame)
        except (KeyError, TypeError, AttributeError):
            failed.append(name)
    data = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    return data, failed, datetime.now(ZoneInfo("Asia/Seoul")).isoformat(timespec="seconds")


def summarize(data):
    records = []
    for ticker, frame in data.groupby("Ticker", sort=False):
        frame = frame.sort_values("Date")
        first, last = frame.iloc[0], frame.iloc[-1]
        records.append({
            "종목": last["Name"], "티커": ticker, "업종": last["Sector"],
            "최근 거래일": last["Date"].strftime("%Y-%m-%d"),
            "수정 종가(원)": last["Close"], "전일 대비(%)": last["DailyReturn"],
            "기간 수익률(%)": (last["Close"] / first["Close"] - 1) * 100,
            "거래량(주)": last["Volume"], "거래일 수": len(frame),
        })
    return pd.DataFrame(records)


if __name__ == "__main__":
    today = datetime.now(ZoneInfo("Asia/Seoul")).date()
    data, failed, collected = collect(today - timedelta(days=365), today)
    if data.empty:
        raise SystemExit("데이터 수집 실패: Yahoo Finance 연결 또는 요청 제한을 확인하세요.")
    output = ROOT / "data"
    output.mkdir(exist_ok=True)
    data.to_csv(output / "stocks.csv", index=False, encoding="utf-8-sig")
    summarize(data).to_csv(output / "summary.csv", index=False, encoding="utf-8-sig")
    print(f"Collected {data['Ticker'].nunique()}/10 tickers, {len(data)} rows at {collected}")
    print(f"Missing: {failed}")
