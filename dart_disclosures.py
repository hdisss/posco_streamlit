"""OpenDART corporate code and filing lookup. Credentials stay in session memory."""
from datetime import datetime, timedelta
from io import BytesIO
import re
from xml.etree import ElementTree as ET
from zipfile import ZipFile, is_zipfile
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import streamlit as st
from market_data import STOCKS

BASE = "https://opendart.fss.or.kr/api/"
TYPES = {"전체": "", "정기공시": "A", "주요사항보고": "B", "발행공시": "C", "지분공시": "D",
         "기타공시": "E", "외부감사관련": "F", "펀드공시": "G", "자산유동화": "H", "거래소공시": "I", "공정위공시": "J"}
ERRORS = {"010": "등록되지 않은 API 키입니다.", "011": "사용 중지된 API 키입니다.",
          "012": "접근할 수 없는 IP입니다. OpenDART의 허용 IP 설정을 확인하세요.",
          "014": "요청한 파일이 없습니다.", "020": "OpenDART 요청 한도를 초과했습니다.",
          "021": "조회 가능한 회사 개수를 초과했습니다.", "100": "조회 조건이 올바르지 않습니다.",
          "101": "OpenDART가 접근을 허용하지 않았습니다.", "800": "OpenDART 시스템 점검 중입니다.",
          "900": "OpenDART에서 오류가 발생했습니다.", "901": "계정 정보 보유기간이 만료된 키입니다. OpenDART에서 확인하세요."}


class DartError(Exception):
    pass


def check_status(status):
    if status not in {"000", "013"}:
        raise DartError(ERRORS.get(status, "OpenDART가 요청을 처리하지 못했습니다. 잠시 후 다시 조회하세요."))


def request_api(endpoint, key, **params):
    try:
        response = requests.get(BASE + endpoint, params={"crtfc_key": key, **params}, timeout=(5, 30))
        response.raise_for_status()
        return response
    except requests.RequestException:
        # Request exceptions can contain URLs with crtfc_key; never display them.
        raise DartError("OpenDART에 연결하지 못했습니다. 네트워크 상태를 확인하고 다시 조회하세요.") from None


def get_corporations(key):
    response = request_api("corpCode.xml", key)
    stream = BytesIO(response.content)
    try:
        if not is_zipfile(stream):
            root = ET.fromstring(response.content)
            check_status(root.findtext("status", "unknown"))
            raise DartError("기업 코드 파일을 받지 못했습니다.")
        with ZipFile(stream) as archive:
            member = next((i for i in archive.infolist() if i.filename.upper().endswith("CORPCODE.XML")), None)
            if member is None or member.file_size > 100_000_000:
                raise DartError("기업 코드 파일의 형식 또는 크기가 올바르지 않습니다.")
            root = ET.fromstring(archive.read(member))
        targets = {ticker.split(".")[0] for ticker in STOCKS}
        mapping = {}
        for item in root.findall("list"):
            stock = item.findtext("stock_code", "").strip()
            code = item.findtext("corp_code", "").strip()
            if stock in targets and re.fullmatch(r"\d{8}", code):
                mapping[stock] = {"corp_code": code, "corp_name": item.findtext("corp_name", "")}
        return mapping
    except DartError:
        raise
    except Exception:
        raise DartError("기업 코드 파일을 읽지 못했습니다. 다시 조회하세요.") from None


def get_filings(key, corp_code, start, end, disclosure_type="", final_only=False, page=1):
    params = dict(corp_code=corp_code, bgn_de=start.strftime("%Y%m%d"), end_de=end.strftime("%Y%m%d"),
                  last_reprt_at="Y" if final_only else "N", sort="date", sort_mth="desc", page_no=page, page_count=100)
    if disclosure_type:
        params["pblntf_ty"] = disclosure_type
    try:
        payload = request_api("list.json", key, **params).json()
        check_status(payload.get("status", "unknown"))
        if payload["status"] == "013":
            return [], 0, 0
        records = []
        for item in payload.get("list", []):
            receipt = str(item.get("rcept_no", ""))
            if not re.fullmatch(r"\d{14}", receipt):
                continue
            day = datetime.strptime(item["rcept_dt"], "%Y%m%d").date().isoformat()
            records.append({"접수일": day, "기업": item.get("corp_name", ""), "보고서명": item.get("report_nm", ""),
                            "제출인": item.get("flr_nm", ""), "접수번호": receipt, "비고": item.get("rm", ""),
                            "DART 원문": "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=" + receipt})
        return records, int(payload["total_count"]), int(payload["total_page"])
    except DartError:
        raise
    except Exception:
        raise DartError("공시 응답을 읽지 못했습니다. 잠시 후 다시 조회하세요.") from None


def clear_dart_result():
    for key in ["dart_result", "dart_corporations"]:
        st.session_state.pop(key, None)


def erase_dart_key():
    st.session_state.dart_api_key = ""
    clear_dart_result()


def render_disclosures():
    st.subheader("기업 공시 · OpenDART")
    st.caption("금융감독원 OpenDART에서 회사별 공시 목록을 조회합니다. OpenAI API 키와 별도의 OpenDART 인증키가 필요합니다.")
    st.text_input("OpenDART API 키", type="password", key="dart_api_key", on_change=clear_dart_result,
                  placeholder="40자리 인증키", help="현재 세션 메모리에만 유지하며 파일·공유 캐시에 저장하지 않습니다.")
    with st.container(horizontal=True):
        st.button("OpenDART 키 지우기", key="erase_dart_key", on_click=erase_dart_key)
        st.link_button("OpenDART 인증키 신청·관리", "https://opendart.fss.or.kr/")
    today = datetime.now(ZoneInfo("Asia/Seoul")).date()
    companies = {name: ticker.split(".")[0] for ticker, (name, _) in STOCKS.items()}
    with st.form("dart_search"):
        company = st.selectbox("공시 조회 기업", list(companies), key="dart_company")
        with st.container(horizontal=True):
            start = st.date_input("공시 시작일", today - timedelta(days=90), max_value=today, key="dart_start")
            end = st.date_input("공시 종료일", today, max_value=today, key="dart_end")
        kind = st.selectbox("공시 유형", list(TYPES), key="dart_type")
        final = st.checkbox("최종보고서만 조회", value=False, key="dart_final",
                            help="미선택 시 정정보고서를 포함해 제출된 보고서 전체를 검색합니다.")
        page = st.number_input("페이지 번호 (페이지당 최대 100건)", min_value=1, max_value=10000, value=1, step=1, key="dart_page")
        submit = st.form_submit_button("공시 조회", icon=":material/search:")
    st.caption("위 공시 기간은 왼쪽 주가 조회 기간과 별개입니다. 다른 페이지는 페이지 번호를 바꾸고 ‘공시 조회’를 누르세요.")
    api_key = st.session_state.dart_api_key.strip()
    if not api_key:
        st.info("OpenDART API 키를 입력하고 공시 조회를 누르세요.")
    if submit:
        st.session_state.pop("dart_result", None)
        if not re.fullmatch(r"[A-Za-z0-9]{40}", api_key):
            st.warning("OpenDART에서 발급받은 40자리 인증키를 입력하세요.")
            return
        if start > end:
            st.warning("공시 종료일은 시작일과 같거나 뒤여야 합니다.")
            return
        try:
            with st.spinner("기업 코드와 공시 목록을 조회하고 있습니다…"):
                if "dart_corporations" not in st.session_state:
                    st.session_state.dart_corporations = get_corporations(api_key)
                corporation = st.session_state.dart_corporations.get(companies[company])
                if not corporation:
                    raise DartError("해당 종목의 DART 기업 코드를 찾지 못했습니다. 키를 지우고 다시 입력해 기업 코드를 갱신하세요.")
                records, total, pages = get_filings(api_key, corporation["corp_code"], start, end, TYPES[kind], final, int(page))
                st.session_state.dart_result = dict(records=records, total=total, pages=pages, page=page,
                    company=company, start=start, end=end, kind=kind, final=final,
                    at=datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d %H:%M:%S KST"))
        except DartError as error:
            st.error(str(error))
            return
    result = st.session_state.get("dart_result")
    if result is None:
        return
    st.caption(f"조회 결과: {result['company']} · {result['start']} ~ {result['end']} · {result['kind']} · "
               f"{'최종보고서만' if result['final'] else '정정 포함'} · {result['at']}")
    st.caption(f"전체 {result['total']:,}건 · {result['page']} / {result['pages']}페이지 · 현재 페이지 {len(result['records'])}건")
    if not result["records"]:
        st.info("조회 조건 또는 해당 페이지에 공시가 없습니다. 기간·유형·페이지 번호를 확인하세요.")
        return
    frame = pd.DataFrame(result["records"])
    keyword = st.text_input("현재 페이지 보고서명 필터", key="dart_keyword", max_chars=100)
    frame = frame[frame["보고서명"].str.contains(keyword.strip(), case=False, regex=False)]
    st.dataframe(frame, hide_index=True, width="stretch", alt="기업 공시 접수일 보고서명 제출인 및 DART 원문 링크",
                 column_config={"DART 원문": st.column_config.LinkColumn("DART 원문", display_text="원문 열기")})
    st.download_button("현재 페이지 공시 CSV", frame.to_csv(index=False).encode("utf-8-sig"),
                       f"{result['company']}_dart_page{result['page']}.csv", "text/csv")
    st.caption("비고 ‘정’은 후속 정정, ‘철’은 철회 관련 공시를 뜻합니다. 보고서명·원문에서 정정 및 철회 여부를 함께 확인하세요.")
