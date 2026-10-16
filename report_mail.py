"""Build reviewable reports and send only on explicit form submission."""
from datetime import datetime
from email.message import EmailMessage
from email.utils import format_datetime, make_msgid
import hashlib
import re
import smtplib
import ssl
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st
from company_news import fetch_news
from market_data import summarize


def build_report(data, selected, news_days, news_limit, collected, source):
    subset = data[data["Name"].isin(selected)]
    if subset.empty:
        raise ValueError("선택한 기업의 주식 데이터가 없습니다.")
    summary = summarize(subset).sort_values("기간 수익률(%)", ascending=False)
    now = datetime.now(ZoneInfo("Asia/Seoul"))
    lines = ["국내 주식·기업 뉴스 보고서", f"작성: {now:%Y-%m-%d %H:%M:%S KST}",
             f"대상: {', '.join(selected)}", f"주가 범위: {subset['Date'].min():%Y-%m-%d} ~ {subset['Date'].max():%Y-%m-%d}",
             f"주가 출처: {source} / 수집: {collected}", f"뉴스: Google 뉴스 RSS / 최근 {news_days}일 / 기업별 최대 {news_limit}건", "",
             "1. 주가 성과 요약", "가격은 배당·분할을 반영한 수정 종가이며, 기간 수익률은 각 기업의 첫 종가 대비 마지막 종가입니다."]
    best = summary.iloc[0]
    lines += [f"조회 기업 중 기간 수익률 1위: {best['종목']} ({best['기간 수익률(%)']:+.2f}%)", ""]
    for row in summary.to_dict("records"):
        change = row["전일 대비(%)"]
        daily = f"{change:+.2f}%" if pd.notna(change) else "자료 없음"
        volume = f"{row['거래량(주)']:,.0f}주" if pd.notna(row["거래량(주)"]) else "자료 없음"
        lines.append(f"- {row['종목']} ({row['티커']}) | 거래일 {row['최근 거래일']} | 수정 종가 {row['수정 종가(원)']:,.0f}원 | 전일 대비 {daily} | 기간 수익률 {row['기간 수익률(%)']:+.2f}% | 거래량 {volume}")
    missing = [name for name in selected if name not in set(summary["종목"])]
    if missing:
        lines += ["주가 미수집 기업: " + ", ".join(missing)]
    lines += ["", "2. 기업별 관련 뉴스", "뉴스 제목과 메타데이터를 정리했으며 기사 본문 분석이나 주가 변동 원인 추정은 포함하지 않습니다."]
    news_rows, failures = [], []
    for company in selected:
        lines += ["", f"[{company}]"]
        try:
            articles, fetched = fetch_news(company, news_days)
            lines.append(f"뉴스 수집: {fetched} / 검색 반환 {len(articles)}건")
            if not articles:
                lines.append("해당 기간 뉴스 검색 결과 없음")
            for article in articles[:news_limit]:
                day = article["발행 시각"].strftime("%Y-%m-%d %H:%M KST") if article["발행 시각"] else "발행일 미상"
                lines += [f"- {article['제목']} | {article['언론사']} | {day}", f"  {article['원문 링크']}"]
                news_rows.append({"기업": company, "제목": article["제목"], "언론사": article["언론사"], "발행 시각": day,
                                  "원문 링크": article["원문 링크"], "수집 시각": fetched})
        except Exception:
            failures.append(company)
            lines.append("뉴스 수집 실패: 네트워크 또는 뉴스 제공자 응답을 확인하세요.")
    lines += ["", "3. 해석 및 확인 사항", "- 기업별 최근 거래일이 다를 수 있습니다. 당일 일봉은 장중 변경될 수 있습니다.",
              "- 뉴스 검색 결과는 전체 기사를 포함하지 않으며 간접 관련 기사도 포함될 수 있습니다.",
              "- 뉴스 검색 기간과 주가 조회 기간은 다를 수 있습니다. 기사 세부 내용은 원문을 확인하세요.",
              "- 자동 집계 자료이며 투자 권유 또는 미래 수익 보장이 아닙니다."]
    return {"body": "\n".join(lines), "summary_csv": summary.to_csv(index=False).encode("utf-8-sig"),
            "news_csv": pd.DataFrame(news_rows, columns=["기업", "제목", "언론사", "발행 시각", "원문 링크", "수집 시각"]).to_csv(index=False).encode("utf-8-sig"),
            "failures": failures, "at": now.isoformat(timespec="seconds"), "companies": selected}


def validate_address(address):
    address = address.strip()
    if not address.isascii() or not re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,}", address):
        raise ValueError("발신 계정과 수신 주소는 각각 하나의 올바른 이메일 주소로 입력하세요.")
    return address


def make_message(account, recipient, subject, body, attachments):
    account, recipient = validate_address(account), validate_address(recipient)
    if not subject.strip() or "\r" in subject or "\n" in subject:
        raise ValueError("메일 제목은 줄바꿈 없이 입력하세요.")
    if not body.strip():
        raise ValueError("보고서 본문이 비어 있습니다.")
    message = EmailMessage()
    message["From"], message["To"], message["Subject"] = account, recipient, subject.strip()
    message["Date"] = format_datetime(datetime.now(ZoneInfo("Asia/Seoul")))
    message["Message-ID"] = make_msgid()
    message.set_content(body)
    for name, content in attachments.items():
        message.add_attachment(content, maintype="text", subtype="csv", filename=name)
    return message


class SMTPStageError(smtplib.SMTPException):
    def __init__(self, stage):
        self.stage = stage
        super().__init__(stage)


def send_report(host, port, security, account, password, recipient, subject, body, attachments, dry_run=False):
    message = make_message(account, recipient, subject, body, attachments)
    if not re.fullmatch(r"[A-Za-z0-9.-]+", host.strip()) or not 1 <= int(port) <= 65535:
        raise ValueError("SMTP 서버 주소와 포트를 확인하세요.")
    if not password:
        raise ValueError("SMTP 앱 비밀번호를 입력하세요.")
    if security not in {"SSL/TLS", "STARTTLS"}:
        raise ValueError("암호화 연결을 선택하세요.")
    context = ssl.create_default_context()
    server = None
    stage = "서버 연결"
    try:
        if security == "SSL/TLS":
            server = smtplib.SMTP_SSL(host.strip(), int(port), local_hostname="localhost", timeout=30, context=context)
        else:
            server = smtplib.SMTP(host.strip(), int(port), local_hostname="localhost", timeout=30)
            stage = "STARTTLS 암호화"
            code, _ = server.ehlo()
            if code != 250:
                raise smtplib.SMTPHeloError(code, b"SMTP greeting rejected")
            server.starttls(context=context)
        stage = "SMTP 인사(EHLO)"
        code, _ = server.ehlo()
        if code != 250:
            raise smtplib.SMTPHeloError(code, b"SMTP greeting rejected")
        stage = "계정 인증"
        if host.strip().lower() == "smtp.gmail.com":
            password = re.sub(r"\s+", "", password)
        server.login(account.strip(), password)
        if dry_run:
            return "인증 성공"
        stage = "메일 본문 전송"
        refused = server.send_message(message, from_addr=account.strip(), to_addrs=[recipient.strip()])
        if refused:
            raise smtplib.SMTPRecipientsRefused(refused)
        return message["Message-ID"]
    except (smtplib.SMTPServerDisconnected, TimeoutError):
        raise SMTPStageError(stage) from None
    finally:
        if server is not None:
            try:
                server.quit()
            except Exception:
                try:
                    server.close()
                except Exception:
                    pass


def clear_smtp_credentials():
    st.session_state.smtp_account = ""
    st.session_state.smtp_password = ""


def render_report_mail(data, collected, source):
    st.subheader("주식·뉴스 보고서와 메일 발송")
    st.caption("현재 주가 조회 범위의 수치와 기업별 뉴스 제목을 자동 집계합니다. 보고서 작성 후 본문을 수정하고 직접 발송할 수 있습니다.")
    with st.form("report_generate"):
        selected = st.multiselect("보고서 대상 기업", list(data["Name"].unique()), default=list(data["Name"].unique())[:3], key="report_companies")
        days = st.selectbox("보고서 뉴스 기간", [1, 7, 30], index=1, format_func=lambda n: f"최근 {n}일", key="report_news_days")
        limit = st.select_slider("기업별 뉴스 개수", [3, 5, 10], value=5, key="report_news_limit")
        generate = st.form_submit_button("보고서 작성", icon=":material/description:")
    if generate:
        if not selected:
            st.warning("보고서 대상 기업을 선택하세요.")
        else:
            with st.spinner("선택한 기업의 뉴스를 수집하고 보고서를 작성하고 있습니다…"):
                report = build_report(data, selected, days, limit, collected, source)
            st.session_state.mail_report = report
            st.session_state.report_body = report["body"]
            st.session_state.report_subject = "주식·뉴스 보고서 - " + ", ".join(selected)
            st.session_state.pop("mail_send_status", None)
    report = st.session_state.get("mail_report")
    if report is None:
        st.info("대상 기업을 선택하고 ‘보고서 작성’을 누르세요. 작성만으로 메일이 발송되지는 않습니다.")
        return
    st.caption(f"작성된 보고서: {', '.join(report['companies'])} · {report['at']} · 조회 조건 변경은 ‘보고서 작성’을 눌러 반영하세요.")
    if report["failures"]:
        st.warning("뉴스 수집 실패 기업을 본문에 표시했습니다: " + ", ".join(report["failures"]))
    st.text_input("메일 제목", key="report_subject", max_chars=200)
    st.text_area("보고서 본문 (수정 가능)", key="report_body", height=430, max_chars=100000)
    st.download_button("보고서 TXT 다운로드", st.session_state.report_body.encode("utf-8-sig"), "stock_news_report.txt", "text/plain")
    with st.expander("발송할 보고서 미리보기", expanded=False):
        st.text(st.session_state.report_body)
    st.subheader("SMTP 메일 설정")
    st.caption("발신 계정과 앱 비밀번호는 세션 메모리에만 유지하며 파일·공유 캐시에 저장하지 않습니다. 메일은 아래 발송 버튼을 누를 때만 전송합니다.")
    st.button("SMTP 계정·비밀번호 지우기", on_click=clear_smtp_credentials, key="clear_smtp_credentials")
    with st.form("smtp_send"):
        host = st.text_input("SMTP 서버", value="smtp.gmail.com", key="smtp_host", help="사용하는 메일 서비스의 SMTP 서버 주소를 입력하세요.")
        security = st.selectbox("SMTP 암호화", ["SSL/TLS", "STARTTLS"], key="smtp_security")
        port = st.number_input("SMTP 포트", min_value=1, max_value=65535, value=465, key="smtp_port", help="메일 서비스 설정에 맞추세요. 일반적으로 SSL/TLS는 465, STARTTLS는 587을 사용합니다.")
        account = st.text_input("발신 계정 (이메일 주소)", key="smtp_account")
        password = st.text_input("SMTP 앱 비밀번호", type="password", key="smtp_password")
        recipient = st.text_input("수신 이메일 주소", key="smtp_recipient")
        attach = st.checkbox("주가 요약·뉴스 목록 CSV 첨부", value=True, key="smtp_attach")
        probe = st.form_submit_button("연결·인증 테스트 (메일 미발송)")
        send = st.form_submit_button("이 보고서를 메일로 발송", icon=":material/send:")
    if probe:
        try:
            with st.spinner("SMTP 연결과 계정 인증을 확인하고 있습니다. 메일은 보내지 않습니다…"):
                send_report(host, port, security, account, password, account, "연결 테스트", "연결 테스트", {}, dry_run=True)
            st.success("SMTP 연결·암호화·계정 인증이 정상입니다. 메일은 발송하지 않았습니다.")
        except ValueError as error:
            st.error(str(error))
        except smtplib.SMTPAuthenticationError:
            st.error("연결은 됐지만 계정 인증에 실패했습니다. 일반 비밀번호 대신 해당 계정에서 발급한 앱 비밀번호를 입력하세요.")
        except SMTPStageError as error:
            st.error(f"‘{error.stage}’ 단계에서 응답이 끊겼거나 시간이 초과됐습니다. 메일은 발송하지 않았습니다.")
        except (smtplib.SMTPException, OSError):
            st.error("SMTP 연결·인증 테스트에 실패했습니다. 서버·포트·암호화 설정과 계정의 SMTP 사용 권한을 확인하세요. 메일은 발송하지 않았습니다.")
    attachments = {"stock_summary.csv": report["summary_csv"], "company_news.csv": report["news_csv"]} if attach else {}
    if send:
        st.session_state.pop("mail_send_status", None)
        digest = hashlib.sha256((host + str(port) + security + account.strip() + recipient.strip() + st.session_state.report_subject + st.session_state.report_body + str(attach)).encode() + b"".join(attachments.values())).hexdigest()
        if st.session_state.get("mail_last_digest") == digest:
            st.info("같은 수신자에게 동일한 보고서를 이미 SMTP 서버에 전달했습니다. 중복 발송하지 않았습니다.")
        else:
            try:
                with st.spinner("SMTP 서버에 메일을 전달하고 있습니다…"):
                    message_id = send_report(host, port, security, account, password, recipient,
                                             st.session_state.report_subject, st.session_state.report_body, attachments)
                st.session_state.mail_last_digest = digest
                st.session_state.mail_send_status = f"{recipient.strip()} 앞으로 SMTP 서버가 메일을 접수했습니다. 실제 수신 여부는 수신함·스팸함을 확인하세요."
            except ValueError as error:
                st.error(str(error))
            except smtplib.SMTPAuthenticationError:
                st.error("SMTP 인증에 실패했습니다. 계정·앱 비밀번호와 메일 서비스의 SMTP 사용 설정을 확인하세요.")
            except smtplib.SMTPRecipientsRefused:
                st.error("SMTP 서버가 수신 주소를 거부했습니다. 주소를 확인하세요.")
            except SMTPStageError as error:
                if error.stage == "메일 본문 전송":
                    st.error("메일 본문 전송 중 응답이 끊겼습니다. 발송 여부가 불확실하므로 수신함·보낸편지함을 확인한 뒤 다시 시도하세요. 자동 재시도하지 않았습니다.")
                else:
                    st.error(f"‘{error.stage}’ 단계에서 연결이 끊겼거나 시간이 초과됐습니다. 메일 전송 단계에 도달하지 않았습니다.")
            except (smtplib.SMTPException, OSError):
                st.error("SMTP 전송에 실패했습니다. 서버·포트·암호화 설정과 네트워크를 확인하세요. 다시 시도하기 전 수신 여부도 확인하세요.")
    if st.session_state.get("mail_send_status"):
        st.success(st.session_state.mail_send_status)
