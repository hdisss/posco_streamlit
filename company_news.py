"""Collect Korean company headlines from Google News RSS."""
from datetime import datetime
from email.utils import parsedate_to_datetime
import re
from urllib.parse import urlencode, urlparse
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import streamlit as st
from market_data import STOCKS
from stock_chat import render_news_chat

KST = ZoneInfo("Asia/Seoul")


def parse_news(content):
    if len(content) > 2_000_000:
        raise ValueError("뉴스 응답 크기를 초과했습니다.")
    root = ElementTree.fromstring(content)
    if root.tag != "rss":
        raise ValueError("뉴스 피드 형식이 올바르지 않습니다.")
    records, seen = [], set()
    for item in root.findall("./channel/item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        parsed = urlparse(link)
        if not title or parsed.scheme not in {"http", "https"} or not parsed.netloc:
            continue
        publisher = (item.findtext("source") or "출처 미상").strip()
        suffix = " - " + publisher
        if title.endswith(suffix):
            title = title[:-len(suffix)]
        identity = re.sub(r"\s+", "", title).casefold()
        if identity in seen:
            continue
        seen.add(identity)
        try:
            published = parsedate_to_datetime(item.findtext("pubDate") or "")
            if published.tzinfo is None:
                published = published.replace(tzinfo=ZoneInfo("UTC"))
            published = published.astimezone(KST)
        except (ValueError, TypeError, OverflowError):
            published = None
        records.append({"제목": title, "언론사": publisher, "발행 시각": published, "원문 링크": link})
    return sorted(records, key=lambda item: item["발행 시각"] or datetime.min.replace(tzinfo=KST), reverse=True)


@st.cache_data(ttl=600, max_entries=40, show_spinner=False)
def fetch_news(company, days):
    query = f'"{company}" when:{days}d'
    url = "https://news.google.com/rss/search?" + urlencode({"q": query, "hl": "ko", "gl": "KR", "ceid": "KR:ko"})
    response = requests.get(url, timeout=(5, 15), headers={"User-Agent": "KoreaEquityMonitor/1.0"})
    response.raise_for_status()
    return parse_news(response.content), datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S KST")


def render_news():
    st.subheader("기업 뉴스")
    st.caption("Google 뉴스 RSS에서 기업명으로 검색한 한국어 기사 목록입니다. 기사 제목·언론사·발행 시각을 확인하고 원문을 열어보세요.")
    with st.container(horizontal=True, vertical_alignment="bottom"):
        company = st.selectbox("기업 선택", [name for name, _ in STOCKS.values()], key="news_company")
        period = st.segmented_control("검색 기간", ["1일", "7일", "30일"], default="7일", key="news_period")
        if st.button("뉴스 새로고침", icon=":material/refresh:", key="refresh_company_news"):
            fetch_news.clear()
    days = {"1일": 1, "7일": 7, "30일": 30}.get(period, 7)
    try:
        with st.spinner(f"{company} 관련 뉴스를 수집하고 있습니다…"):
            articles, fetched = fetch_news(company, days)
    except Exception:
        st.error("뉴스를 불러오지 못했습니다. 네트워크 또는 Google 뉴스 응답 상태를 확인한 뒤 새로고침하세요.")
        return
    st.caption(f"{company} · 최근 {days}일 검색 · {len(articles)}건 수집 · {fetched} · 10분 캐시")
    st.caption("뉴스 검색 기간은 왼쪽 주가 조회 기간과 별개입니다. 검색 제공자의 반환 범위 내 결과이며 모든 기사를 포함하지 않을 수 있습니다.")
    if not articles:
        st.info("해당 기간의 기사가 없습니다. 기간을 늘리거나 다른 기업을 선택하세요.")
        return
    keyword = st.text_input("제목 내 키워드 필터", placeholder="예: 실적, 배터리, 투자", max_chars=100, key="news_keyword")
    publishers = sorted({article["언론사"] for article in articles})
    chosen = st.multiselect("언론사 필터 (미선택 시 전체)", publishers, key=f"news_publishers_{company}_{days}")
    filtered = [a for a in articles if keyword.strip().casefold() in a["제목"].casefold() and (not chosen or a["언론사"] in chosen)]
    limit = st.select_slider("표시 개수", [10, 20, 50, 100], value=20, key="news_limit")
    st.caption(f"필터 결과 {len(filtered)}건 · 최신순으로 최대 {limit}건 표시")
    if not filtered:
        st.info("필터에 맞는 기사가 없습니다. 키워드나 언론사 필터를 변경하세요.")
        return
    export = pd.DataFrame(filtered)
    export["발행 시각"] = export["발행 시각"].map(lambda d: d.strftime("%Y-%m-%d %H:%M:%S KST") if pd.notna(d) else "미상")
    st.download_button("뉴스 목록 CSV", export.to_csv(index=False).encode("utf-8-sig"),
                       f"{company}_news.csv", "text/csv", icon=":material/download:")
    if st.toggle("수집된 뉴스에 AI로 질문하기", value=False, key="show_news_chat"):
        render_news_chat(filtered, company, days, fetched, keyword.strip(), chosen)
        st.divider()
    for number, article in enumerate(filtered[:limit], 1):
        with st.container(border=True):
            published = article["발행 시각"].strftime("%Y.%m.%d %H:%M KST") if article["발행 시각"] else "발행 시각 미상"
            st.caption(f"[뉴스 {number}] {article['언론사']} · {published}")
            st.markdown(f"### {article['제목']}")
            st.link_button("기사 원문 열기", article["원문 링크"], icon=":material/open_in_new:")
    st.caption("기업명 검색 결과에는 동명이인·간접 관련 기사가 포함될 수 있습니다. 링크는 Google 뉴스를 거쳐 기사 원문으로 연결됩니다.")
