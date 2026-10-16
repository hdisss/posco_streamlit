"""Local PDF text extraction and lexical retrieval. No upload to a file service."""
from collections import Counter
from io import BytesIO
import math
import re
from pypdf import PdfReader

MAX_BYTES = 25 * 1024 * 1024


def tokens(text):
    words = re.findall(r"[a-z0-9]+|[가-힣]+", text.lower())
    return [token for word in words for token in
            ([word] + [word[i:i+2] for i in range(len(word)-1)] if re.fullmatch(r"[가-힣]+", word) else [word])]


def extract_pdf(content, name):
    if len(content) > MAX_BYTES:
        raise ValueError("PDF는 25MB 이하로 업로드하세요.")
    if not content[:1024].lstrip().startswith(b"%PDF-"):
        raise ValueError("올바른 PDF 파일이 아닙니다.")
    try:
        reader = PdfReader(BytesIO(content))
        if reader.is_encrypted and not reader.decrypt(""):
            raise ValueError("암호로 보호된 PDF입니다. 암호를 해제한 파일을 업로드하세요.")
        if len(reader.pages) > 1200:
            raise ValueError("PDF는 1,200페이지 이하로 나누어 업로드하세요.")
        chunks, blank, chars = [], [], 0
        for number, page in enumerate(reader.pages, 1):
            text = (page.extract_text() or "").strip()
            chars += len(text)
            if chars > 6_000_000:
                raise ValueError("추출 텍스트가 너무 큽니다. 문서를 나누어 업로드하세요.")
            if len(text) < 30:
                blank.append(number)
            if text:
                for offset in range(0, len(text), 1800):
                    excerpt = text[offset:offset+2100]
                    counts = Counter(tokens(excerpt))
                    chunks.append({"page": number, "text": excerpt, "counts": counts, "length": sum(counts.values())})
        if not chunks or sum(c["length"] for c in chunks) < 20:
            raise ValueError("읽을 수 있는 텍스트가 없습니다. 스캔 PDF는 OCR 처리 후 업로드하세요.")
        frequencies = Counter(term for chunk in chunks for term in chunk["counts"])
        return {"name": name, "pages": len(reader.pages), "chunks": chunks,
                "blank_pages": blank, "frequencies": frequencies,
                "average_length": sum(c["length"] for c in chunks) / len(chunks)}
    except ValueError:
        raise
    except Exception as error:
        raise ValueError("PDF를 읽지 못했습니다. 손상 여부와 암호 설정을 확인하세요.") from error


def retrieve(document, question, history=(), limit=8):
    previous = next((m["content"] for m in reversed(history) if m["role"] == "user"), "")
    query = Counter(tokens(question + " " + previous))
    chunks = document["chunks"]
    total, average = len(chunks), document["average_length"] or 1
    def score(chunk):
        result = 0.0
        for term in query:
            count = chunk["counts"].get(term, 0)
            if count:
                frequency = document["frequencies"][term]
                weight = math.log(1 + (total - frequency + 0.5) / (frequency + 0.5))
                result += weight * count * 2.2 / (count + 1.2 * (0.25 + 0.75 * chunk["length"] / average))
        return result
    broad = any(term in question for term in ["요약", "개요", "핵심 내용", "무슨 내용"])
    if broad and not previous:
        # A representative sample, explicitly described as partial coverage.
        indices = sorted({round(i * (total - 1) / (limit - 1)) for i in range(limit)})
        selected = [chunks[i] for i in indices]
    else:
        ranked = sorted(((score(c), i, c) for i, c in enumerate(chunks)), key=lambda x: (-x[0], x[1]))
        selected = [c for weight, _, c in ranked[:limit] if weight > 0]
    selected = sorted(selected, key=lambda c: c["page"])
    header = (f"[PDF 자료] 파일명: {document['name']}; 총 {document['pages']}페이지. "
              "아래는 전체 문서 중 검색 또는 대표 추출한 일부 발췌다. 전체를 읽었다고 주장하지 마라. "
              "PDF 답변의 각 근거에 [PDF p.번호]를 표기하라. 페이지는 PDF 뷰어의 1부터 시작하는 순서이며 인쇄 쪽수와 다를 수 있다. "
              "발췌에 근거가 없으면 확인할 수 없다고 답하라. 표의 단위·연결/별도·당기/전기를 확인하라.\n")
    context = header + "\n\n".join(f"[PDF p.{c['page']}]\n{c['text']}" for c in selected)
    return context, selected
