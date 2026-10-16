from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

from market_data import ROOT, STOCKS, collect, summarize
from stock_chat import render_chat
from company_news import render_news
from dart_disclosures import render_disclosures
from report_mail import render_report_mail

st.set_page_config(page_title="국내 주식 한눈에", page_icon=":material/monitoring:", layout="wide")


@st.cache_data(ttl=900, max_entries=20, show_spinner=False)
def load_prices(start, end):
    data, failed, collected = collect(start, end)
    if data.empty:
        raise ValueError("Yahoo Finance에서 가격 데이터를 받지 못했습니다.")
    return data, failed, collected


st.caption("KOREA EQUITY MONITOR  /  YAHOO FINANCE")
st.title("국내 주식 한눈에")
st.markdown("주요 **10개 종목**의 흐름을 살펴보고, 같은 기간의 성과를 비교하세요.")
today = datetime.now(ZoneInfo("Asia/Seoul")).date()
with st.sidebar:
    st.header("조회 설정", divider="gray")
    st.caption("KOSPI · 주요 업종의 10종목을 고정 선정했습니다. 시가총액 순위는 아닙니다.")
    with st.form("period"):
        start = st.date_input("시작일", today - timedelta(days=365), max_value=today)
        end = st.date_input("종료일", today, max_value=today)
        st.form_submit_button("기간 적용", icon=":material/date_range:", width="stretch")
    snapshot = st.toggle("저장된 수집 데이터 보기", value=False,
                         help="market_data.py로 수집한 CSV를 사용합니다. 최신 시세가 아닐 수 있습니다.")
    if st.button("데이터 새로고침", icon=":material/refresh:", width="stretch"):
        load_prices.clear()
    st.divider()
    st.caption("일봉 · KRW · 15분 캐시\n\n출처: Yahoo Finance / yfinance")
    st.caption("배당과 분할을 반영한 수정주가입니다. 실시간 호가와 다를 수 있으며, 당일 일봉은 장중 변경됩니다.")

if st.session_state.pop("stock_clear_key", False):
    st.session_state.stock_api_key = ""
view = st.segmented_control("화면", ["수익률 비교", "종목 상세", "데이터 다운로드", "기업 뉴스", "기업 공시", "보고서·메일", "AI 챗봇", "PDF 챗봇"], default="수익률 비교", key="dashboard_view")
if view == "기업 공시":
    render_disclosures()
    st.stop()
if view == "기업 뉴스":
    render_news()
    st.stop()
if view == "PDF 챗봇":
    render_chat()
    st.stop()

if start >= end:
    st.warning("종료일을 시작일보다 뒤로 설정하세요.")
    st.stop()

try:
    if snapshot:
        path = ROOT / "data" / "stocks.csv"
        if not path.exists():
            st.info("저장된 데이터가 없습니다. 먼저 python market_data.py를 실행하세요.")
            st.stop()
        data = pd.read_csv(path, parse_dates=["Date"])
        data = data[data["Date"].between(pd.Timestamp(start), pd.Timestamp(end))]
        failed = [name for ticker, (name, _) in STOCKS.items() if ticker not in data["Ticker"].unique()]
        collected = datetime.fromtimestamp(path.stat().st_mtime, ZoneInfo("Asia/Seoul")).isoformat(timespec="seconds")
        st.info("저장된 CSV를 표시 중입니다. 실시간 수집 결과가 아닙니다.")
    else:
        with st.spinner("Yahoo Finance에서 10종목의 일봉을 수집하고 있습니다…"):
            data, failed, collected = load_prices(start, end)
except Exception:
    st.error("시세를 수집하지 못했습니다. 네트워크 또는 Yahoo Finance 요청 제한을 확인하고 새로고침하세요. 저장된 데이터가 있다면 왼쪽에서 확인할 수 있습니다.")
    st.stop()

if data.empty:
    st.warning("선택한 기간에 거래 데이터가 없습니다. 주말·휴장일 또는 저장 데이터의 범위를 확인하세요.")
    st.stop()
if failed:
    st.warning("데이터 미수집: " + ", ".join(failed) + " · 나머지 종목만 표시합니다.")

summary = summarize(data)
latest_date = data["Date"].max()
st.caption(f"조회 {start:%Y.%m.%d} – {end:%Y.%m.%d}  ·  최근 거래일 {latest_date:%Y.%m.%d}  ·  수집 {collected.replace('T', ' ')}")
latest = summary[summary["최근 거래일"] == latest_date.strftime("%Y-%m-%d")]
best = summary.loc[summary["기간 수익률(%)"].idxmax()]
with st.container(horizontal=True):
    st.metric("수집 종목", f"{len(summary)} / 10", border=True)
    st.metric("최근일 상승 종목", f"{(latest['전일 대비(%)'] > 0).sum()} / {len(latest)}", border=True)
    st.metric("기간 최고 수익 종목", best["종목"], border=True)
    st.metric("최고 기간 수익률", f"{best['기간 수익률(%)']:+.2f}%", border=True)

if view == "수익률 비교":
    with st.container(border=True):
        st.subheader("같은 출발점에서 보는 주가 흐름")
        chosen = st.multiselect("비교 종목", list(summary["종목"]), default=list(summary["종목"]))
        subset = data[data["Name"].isin(chosen)].sort_values(["Ticker", "Date"]).copy()
        if not subset.empty:
            subset["수익률(%)"] = (subset["Close"] / subset.groupby("Ticker")["Close"].transform("first") - 1) * 100
            st.line_chart(subset, x="Date", y="수익률(%)", color="Name", x_label="거래일", height=390,
                          alt="종목별 첫 거래일 수정 종가 대비 누적 수익률")
        else:
            st.info("비교할 종목을 선택하세요.")
        st.caption("각 종목의 조회 기간 첫 거래일 종가를 0%로 계산합니다. 거래일이 다른 경우 표의 최근 거래일을 확인하세요.")
    st.subheader("10종목 시세 보드")
    st.dataframe(summary, hide_index=True, width="stretch", alt="국내 10종목 수정 종가와 수익률 요약",
                 column_config={
                     "수정 종가(원)": st.column_config.NumberColumn(format="localized", step=1),
                     "전일 대비(%)": st.column_config.NumberColumn(format="%+.2f%%"),
                     "기간 수익률(%)": st.column_config.NumberColumn(format="%+.2f%%"),
                     "거래량(주)": st.column_config.NumberColumn(format="localized", step=1),
                 })
elif view == "종목 상세":
    name = st.selectbox("종목 선택", list(summary["종목"]))
    frame = data[data["Name"] == name].sort_values("Date")
    last = frame.iloc[-1]
    st.subheader(f"{name} · {last['Ticker']}")
    with st.container(horizontal=True):
        st.metric("수정 종가", f"{last['Close']:,.0f}원", border=True)
        change = last["DailyReturn"]
        st.metric("전일 대비", f"{change:+.2f}%" if pd.notna(change) else "—", border=True)
        st.metric("거래량", f"{last['Volume']:,.0f}주" if pd.notna(last["Volume"]) else "—", border=True)
    ma = st.toggle("20·60 거래일 이동평균", value=True)
    figure = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.06, row_heights=[0.75, 0.25])
    figure.add_trace(go.Candlestick(x=frame["Date"], open=frame["Open"], high=frame["High"], low=frame["Low"], close=frame["Close"],
                                   name="수정주가", increasing_line_color="#e05260", decreasing_line_color="#377fea"), row=1, col=1)
    if ma:
        for window, color in [(20, "#0d9488"), (60, "#b08bdb")]:
            figure.add_trace(go.Scatter(x=frame["Date"], y=frame["Close"].rolling(window).mean(),
                                       name=f"{window}일 평균", line=dict(color=color, width=1.5)), row=1, col=1)
    figure.add_trace(go.Bar(x=frame["Date"], y=frame["Volume"], name="거래량", marker_color="#9aacc4"), row=2, col=1)
    figure.update_layout(height=560, margin=dict(l=10, r=10, t=20, b=10), template="plotly_white", xaxis_rangeslider_visible=False,
                         legend=dict(orientation="h", y=1.08), hovermode="x unified")
    figure.update_yaxes(title_text="원", row=1, col=1)
    figure.update_yaxes(title_text="주", row=2, col=1)
    st.plotly_chart(figure, width="stretch", alt=f"{name} 수정주가 캔들, 이동평균, 거래량")
    st.caption("이동평균은 조회 기간 안의 종가로 계산합니다. 20·60개 관측치가 쌓인 뒤 표시됩니다.")
elif view == "데이터 다운로드":
    st.subheader("분석에 바로 쓰는 데이터")
    st.caption("CSV는 Excel에서 한글이 보이도록 UTF-8 BOM으로 저장됩니다. 가격은 배당·분할을 반영한 수정주가입니다.")
    with st.container(horizontal=True):
        st.download_button("전체 일봉 CSV", data.to_csv(index=False).encode("utf-8-sig"),
                           f"korea_stocks_{start}_{end}.csv", "text/csv", icon=":material/download:")
        st.download_button("종목 요약 CSV", summary.to_csv(index=False).encode("utf-8-sig"),
                           f"korea_summary_{start}_{end}.csv", "text/csv", icon=":material/download:")
    st.dataframe(data.sort_values(["Date", "Name"], ascending=[False, True]), hide_index=True,
                 alt="전체 종목의 일별 수정 시가 고가 저가 종가 거래량", width="stretch")

elif view == "AI 챗봇":
    render_chat(data, collected, "저장된 Yahoo Finance CSV" if snapshot else "Yahoo Finance / yfinance")
elif view == "보고서·메일":
    render_report_mail(data, collected, "저장된 Yahoo Finance CSV" if snapshot else "Yahoo Finance / yfinance")

st.divider()
st.caption("Yahoo Finance 제공 데이터 · 개인 학습·분석용 · 투자 권유가 아닙니다.")
