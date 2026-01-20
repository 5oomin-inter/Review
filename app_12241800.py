import streamlit as st
import os
import io
import tempfile
import time
import json
import re
from google import genai
from google.genai import types
from pdf2image import convert_from_path

# ==========================================
# [초기 설정] - 반드시 스크립트 최상단에 위치
# ==========================================
st.set_page_config(page_title="니무네 방앗간", layout="wide")

# ==========================================
# [상수 및 환경 설정]
# ==========================================
DEFAULT_API_KEY = ""

# 환경에 따라 Poppler 경로 설정
if os.name == 'nt':  # Windows (로컬)
    POPPLER_PATH = r"C:\Users\inter\Desktop\Review\poppler-25.12.0\Library\bin"
else:  # Linux (Streamlit Cloud)
    POPPLER_PATH = None

REVIEW_CRITERIA = """
1. **조사 연결**: 수식 뒤의 조사($f(x)$는/은 등)가 자연스러운지
2. **맞춤법/띄어쓰기**: 기본적인 한국어 맞춤법 준수
3. **피동/사동**: '되어지다', '보여지다' 등 이중 피동 지양
4. **대등 연결**: 문장 나열 시 구조적 대등성 유지
5. **주술 호응**: 주어와 서술어의 관계가 명확한지
6. **중의성**: 해석이 모호한 문장 수정
7. **수학 용어**: 고교 과정에 맞는 정확한 용어 사용
8. **변수 일관성**: 정의된 변수가 끝까지 유지되는지
9. **오타**: 단순 오타 및 OCR 오류
"""

# ==========================================
# [로직 함수: 조사 규칙 검사]
# ==========================================

_JONGSUNG_LIST = [
    "", "ㄱ", "ㄲ", "ㄳ", "ㄴ", "ㄵ", "ㄶ", "ㄷ", "ㄹ", "ㄺ", "ㄻ", "ㄼ", "ㄽ", "ㄾ", "ㄿ", "ㅀ",
    "ㅁ", "ㅂ", "ㅄ", "ㅅ", "ㅆ", "ㅇ", "ㅈ", "ㅊ", "ㅋ", "ㅌ", "ㅍ", "ㅎ"
]

_LATIN_LAST_JONG = {
    "A": "", "B": "", "C": "", "D": "", "E": "", "F": "", "G": "", "H": "", "I": "", "J": "", "K": "",
    "L": "ㄹ", "M": "ㅁ", "N": "ㄴ", "O": "", "P": "", "Q": "", "R": "ㄹ", "S": "", "T": "", "U": "",
    "V": "", "W": "", "X": "", "Y": "", "Z": ""
}

_GREEK_MACRO_LAST_JONG = {
    "alpha": "", "beta": "", "gamma": "", "delta": "", "epsilon": "", "zeta": "", "eta": "", "theta": "",
    "iota": "", "kappa": "", "lambda": "", "mu": "", "nu": "", "xi": "", "omicron": "", "pi": "", "rho": "",
    "sigma": "", "tau": "", "upsilon": "", "phi": "", "chi": "", "psi": "", "omega": "",
    "ell": "ㄹ",
}

_DIGIT_LAST_JONG = {
    "0": "ㅇ", "1": "ㄹ", "2": "", "3": "ㅁ", "4": "",
    "5": "", "6": "ㄱ", "7": "ㄹ", "8": "ㄹ", "9": "",
}

_UNIT_LAST_JONG = {
    "십": "ㅂ", "백": "ㄱ", "천": "ㄴ", "만": "ㄴ", "억": "ㄱ", "조": "", "경": "ㅇ",
}
_GROUP_UNITS = ["", "만", "억", "조", "경"]
_JOSA_CANDIDATES = ("은", "는", "이", "가", "을", "를", "과", "와", "으로", "로")

_MATH_JOSA_PATTERN = re.compile(
    r'(?P<math>\${1,2}[^$]+?\${1,2})(?P<ws>\s*)(?P<josa>으로|로|은|는|이|가|을|를|과|와)'
    r'(?=[\s\.,;:\)\]\}\!?]|$)'
)
_NUM_JOSA_PATTERN = re.compile(
    r'(?P<num>\d[\d,]*(?:\.\d+)?)(?P<ws>\s*)(?P<josa>으로|로|은|는|이|가|을|를|과|와)'
    r'(?=[\s\.,;:\)\]\}\!?]|$)'
)
_PAREN_JOSA_PATTERN = re.compile(
    r'(?P<head>[가-힣]+)\s*\(\s*(?P<inner>[^)\n]{1,120}?)\s*\)(?P<ws>\s*)(?P<josa>으로|로|은|는|이|가|을|를|과|와)'
    r'(?=[\s\.,;:\)\]\}\!?]|$)'
)

_EXPLANATION_MARKERS = ("해설", "풀이", "해법", "정답", "해답", "Solution", "해설)", "풀이)")

def _hangul_last_jong(text: str) -> str:
    if not text:
        return ""
    s = re.sub(r'[\s\.,;:!\?\)\]\}]+$', '', text.strip())
    for ch in reversed(s):
        code = ord(ch)
        if 0xAC00 <= code <= 0xD7A3:
            jong = (code - 0xAC00) % 28
            return _JONGSUNG_LIST[jong]
    return ""

def _latin_last_jong(text: str) -> str:
    s = text.strip()
    if not s:
        return ""
    for ch in reversed(s):
        if ch.isalpha():
            return _LATIN_LAST_JONG.get(ch.upper(), "")
    return ""

def _number_last_jong(num_raw: str) -> str:
    if not num_raw:
        return ""
    s = num_raw.strip()
    if s.startswith("$") and s.endswith("$") and len(s) >= 3:
        s = s[1:-1].strip()
    s = s.replace(",", "").replace(" ", "")
    s = s.lstrip("+")
    if s.startswith(("-", "−")):
        s = s[1:]
    if not s:
        return ""
    if "." in s:
        left, right = s.split(".", 1)
        right_digits = re.sub(r"\D", "", right)
        if right_digits:
            return _DIGIT_LAST_JONG.get(right_digits[-1], "")
        s = left
    digits = re.sub(r"\D", "", s)
    if not digits:
        return ""
    if set(digits) == {"0"}:
        return _DIGIT_LAST_JONG["0"]
    digits = digits.lstrip("0") or "0"
    groups = []
    tmp = digits
    while tmp:
        groups.append(tmp[-4:].rjust(4, "0"))
        tmp = tmp[:-4]
    low = groups[0]
    if int(low) != 0:
        thousands, hundreds, tens, ones = low
        if ones != "0":
            return _DIGIT_LAST_JONG.get(ones, "")
        if tens != "0":
            return _UNIT_LAST_JONG["십"]
        if hundreds != "0":
            return _UNIT_LAST_JONG["백"]
        return _UNIT_LAST_JONG["천"]
    for idx in range(1, len(groups)):
        if int(groups[idx]) != 0:
            unit = _GROUP_UNITS[idx] if idx < len(_GROUP_UNITS) else _GROUP_UNITS[-1]
            return _UNIT_LAST_JONG.get(unit, "")
    return _DIGIT_LAST_JONG["0"]

def _expected_josa(josa: str, last_jong: str) -> str:
    has_batchim = (last_jong != "")
    if josa in ("은", "는"):
        return "은" if has_batchim else "는"
    if josa in ("이", "가"):
        return "이" if has_batchim else "가"
    if josa in ("을", "를"):
        return "을" if has_batchim else "를"
    if josa in ("과", "와"):
        return "과" if has_batchim else "와"
    if josa in ("으로", "로"):
        return "로" if (not has_batchim or last_jong == "ㄹ") else "으로"
    return josa

def _strip_math_delimiters(math: str) -> str:
    s = math.strip()
    if s.startswith("$$") and s.endswith("$$") and len(s) >= 4:
        return s[2:-2].strip()
    if s.startswith("$") and s.endswith("$") and len(s) >= 2:
        return s[1:-1].strip()
    return s

def _latex_extract_last_atom(latex: str) -> str:
    s = latex.strip()
    s = re.sub(r"\\(left|right)\b", "", s)
    while True:
        new_s = re.sub(r"[\s\.,;:!\?\)\]\}]+$", "", s)
        new_s = re.sub(r"(\\,|\\;|\\:|\\!|\\quad|\\qquad)\s*$", "", new_s)
        new_s = re.sub(r"(\\cdot|\\times|\\pm|\\mp|\\div)\s*$", "", new_s)
        if new_s == s:
            break
        s = new_s
    m = re.search(r"(?:_|\^)(\{([^{}]{1,40})\}|([A-Za-z0-9]))\s*$", s)
    if m:
        inner = m.group(2) or m.group(3) or ""
        return inner.strip()
    m = re.search(r"\\frac\s*\{([^{}]{1,80})\}\s*\{([^{}]{1,80})\}\s*$", s)
    if m:
        return (m.group(1) or "").strip()
    m = re.search(r"\\sqrt\s*\{([^{}]{1,80})\}\s*$", s)
    if m:
        return (m.group(1) or "").strip()
    m = re.search(r"\\([A-Za-z]+)\s*$", s)
    if m:
        return "\\" + m.group(1)
    if s.endswith((")", "]")):
        return _latex_extract_last_atom(s[:-1])
    if s.endswith("}"):
        return _latex_extract_last_atom(s[:-1])
    m = re.search(r"([0-9][0-9,]*(?:\.[0-9]+)?|[A-Za-z]+|[가-힣]+)\s*$", s)
    if m:
        return m.group(1)
    return ""

def _last_jong_from_math(math: str) -> str:
    latex = _strip_math_delimiters(math)
    atom = _latex_extract_last_atom(latex)
    if not atom:
        return ""
    if atom.startswith("\\"):
        name = atom[1:]
        return _GREEK_MACRO_LAST_JONG.get(name, "")
    if re.fullmatch(r"\d[\d,]*(?:\.\d+)?", atom):
        return _number_last_jong(atom)
    if re.fullmatch(r"[A-Za-z]+", atom):
        return _latin_last_jong(atom)
    if re.search(r"[가-힣]", atom):
        return _hangul_last_jong(atom)
    return _last_jong_from_text(atom)

def _last_jong_from_text(text: str) -> str:
    if not text:
        return ""
    s = text.strip()
    s = re.sub(r"[\s\.,;:!\?\)\]\}\>\"]+$", "", s)
    if not s:
        return ""
    m = re.search(r"(\${1,2}[^$]+?\${1,2})\s*$", s)
    if m:
        return _last_jong_from_math(m.group(1))
    m = re.search(r"(\d[\d,]*(?:\.\d+)?)\s*$", s)
    if m:
        return _number_last_jong(m.group(1))
    jong = _hangul_last_jong(s)
    if jong != "" or re.search(r"[가-힣]$", s):
        return jong
    m = re.search(r"([A-Za-z]+)\s*$", s)
    if m:
        return _latin_last_jong(m.group(1))
    return ""

def _is_internal_reference(inner: str) -> bool:
    if not inner:
        return False
    s = inner.strip()
    if re.search(r"\b\d+\s*p\b", s, flags=re.IGNORECASE):
        return True
    if re.search(r"\bp\.?\s*\d+\b", s, flags=re.IGNORECASE):
        return True
    if re.search(r"\b\d+\s*쪽\b", s):
        return True
    if re.search(r"\bpage\s*\d+\b", s, flags=re.IGNORECASE):
        return True
    keywords = ("평수능", "수능", "기출", "확통", "미적", "기하", "교재", "참고", "예제", "문항", "정답", "해설", "풀이")
    if any(k in s for k in keywords):
        return True
    return False

def _infer_section_context(section_text: str, pos: int) -> str:
    hits = [section_text.find(m) for m in _EXPLANATION_MARKERS if section_text.find(m) != -1]
    if hits:
        boundary = min(hits)
        return "explanation" if pos >= boundary else "problem"
    head_chunk = section_text[:300]
    problem_signals = ("보기", "①", "②", "③", "④", "⑤", "문제", "다음")
    if any(sig in head_chunk for sig in problem_signals):
        return "problem"
    return "explanation"

def _should_read_parenthetical(head: str, inner: str, context: str) -> bool:
    if _is_internal_reference(inner):
        return False
    if context == "problem":
        return True
    s = inner.strip()
    if re.fullmatch(r"=\s*[-+]?\d+(?:\.\d+)?", s):
        return False
    if re.search(r"(<=|>=|<|>|≤|≥|\\le|\\ge|\\lt|\\gt)", s):
        return True
    if re.search(r"[A-Za-z가-힣][^=]{0,10}=\s*[-+]?\d", s):
        return True
    if "$" in s or "\\" in s:
        return True
    return False

def rule_check_josa(section_text: str):
    errors = []
    for m in _MATH_JOSA_PATTERN.finditer(section_text):
        math = m.group("math")
        ws = m.group("ws") or ""
        josa = m.group("josa")
        last_jong = _last_jong_from_math(math)
        exp = _expected_josa(josa, last_jong)
        original = f"{math}{ws}{josa}"
        corrected = f"{math}{exp}"
        if josa != exp or ws:
            severity = "high" if josa != exp else "medium"
            reason = "1. 조사 연결: 수식의 마지막 발음 요소(숫자/변수/첨자 등) 기준으로 조사 선택 및 조사 붙여쓰기"
            errors.append({"original": original, "corrected": corrected, "reason": reason, "severity": severity})

    for m in _NUM_JOSA_PATTERN.finditer(section_text):
        num = m.group("num")
        ws = m.group("ws") or ""
        josa = m.group("josa")
        if m.start() > 0 and section_text[m.start() - 1] == "$":
            continue
        last_jong = _number_last_jong(num)
        exp = _expected_josa(josa, last_jong)
        original = f"{num}{ws}{josa}"
        corrected = f"{num}{exp}"
        if josa != exp or ws:
            severity = "high" if josa != exp else "medium"
            reason = "1. 조사 연결: 숫자는 한자음(1=일,2=이,3=삼...)으로 읽고 받침에 따라 조사 선택 및 조사 붙여쓰기"
            errors.append({"original": original, "corrected": corrected, "reason": reason, "severity": severity})

    for m in _PAREN_JOSA_PATTERN.finditer(section_text):
        head = m.group("head")
        inner = m.group("inner")
        ws = m.group("ws") or ""
        josa = m.group("josa")
        context = _infer_section_context(section_text, m.start())
        read_inner = _should_read_parenthetical(head, inner, context)
        basis_text = inner if read_inner else head
        last_jong = _last_jong_from_text(basis_text)
        exp = _expected_josa(josa, last_jong)
        original = f"{head}({inner}){ws}{josa}"
        corrected = f"{head}({inner}){exp}"
        if josa != exp or ws:
            severity = "high" if josa != exp else "medium"
            if _is_internal_reference(inner):
                why = "1. 조사 연결: 괄호 안 교재 참조/각주 표기는 읽지 않고 앞 단어 기준으로 조사 선택"
            elif context == "problem":
                why = "1. 조사 연결: [문제]에서는 괄호 안 내용도 읽는 것으로 보고 조사 선택"
            else:
                why = "1. 조사 연결: [해설]에서는 부연 설명 괄호는 읽지 않고, 조건/정의 괄호만 읽는 것으로 보고 조사 선택"
            errors.append({"original": original, "corrected": corrected, "reason": why, "severity": severity})
    return errors

def _dedup_errors(errors):
    seen = set()
    out = []
    for e in errors:
        key = (e.get("original",""), e.get("corrected",""), e.get("reason",""))
        if key in seen:
            continue
        seen.add(key)
        out.append(e)
    return out

# ==========================================
# [로직 함수: PDF 변환 및 검토]
# ==========================================
def process_pdf(client, pdf_path, progress_callback=None):
    try:
        if POPPLER_PATH:
            pages = convert_from_path(pdf_path, dpi=300, poppler_path=POPPLER_PATH)
        else:
            pages = convert_from_path(pdf_path, dpi=300)
    except Exception as e:
        return None, f"오류: PDF 변환 실패 ({e})"
    
    full_text = ""
    prompt = """
    당신은 수학 교재를 디지털화하는 전문가입니다. 
    제공된 이미지를 보고 내용을 텍스트로 정확하게 변환(OCR)하세요.

    [중요: 읽기 순서]
    이 문서는 **2단 레이아웃**입니다.
    반드시 다음 순서로 읽으세요:
    1. 왼쪽 단 전체 (상단 → 하단)
    2. 그 다음 오른쪽 단 전체 (상단 → 하단)

    절대로 왼쪽-오른쪽을 번갈아가며 읽지 마세요.

    [지침]
    1. **한글 보존**: 한글 지문은 오타 없이 그대로 옮기세요.
    2. **수식 변환**: 모든 수식, 기호, 숫자는 완벽한 LaTeX 포맷으로 작성하세요.
       - 문장 중간 수식: $ ... $ 사용
       - 독립된 수식: $$ ... $$ 사용
    3. **구조 유지**: 문제 번호, 보기(①, ②...), 박스 등 문제집의 구조를 Markdown 형식으로 유지하세요.
    4. **그림 처리**: 그래프나 도형이 있는 곳은 [그림: 설명]과 같이 위치만 표시하세요.
    5. **풀이 금지**: 문제를 풀지 말고, **적혀있는 그대로** 텍스트로 옮기기만 하세요.
    6. **단 구분 표시**: 왼쪽 단과 오른쪽 단 사이에 "---"를 넣어 구분하세요.
    """
    total_pages = len(pages)
    for i, page in enumerate(pages):
        if progress_callback:
            progress_callback(i + 1, total_pages, "변환")
        try:
            img_byte_arr = io.BytesIO()
            page.save(img_byte_arr, format='PNG')
            img_bytes = img_byte_arr.getvalue()
            image_part = types.Part.from_bytes(data=img_bytes, mime_type='image/png')
            response = client.models.generate_content(
                model='gemini-2.5-flash-lite',
                contents=[prompt, image_part]
            )
            full_text += f"\n\n--- Page {i+1} ---\n\n" + response.text
            time.sleep(2)
        except Exception as e:
            full_text += f"\n\n--- Page {i+1} (Error: {e}) ---\n\n"
    return full_text, None

def split_into_sections(content):
    sections = re.split(r'\n(?=---\s*Page|\n---\n|\d+\.\s)', content)
    return [s.strip() for s in sections if s.strip()]

def review_single_section(client, section_text, section_num):
    rule_errors = rule_check_josa(section_text)
    prompt = f"""
당신은 대한민국 고등학교 수학 교재 전문 교정자입니다.
아래 텍스트에서 오류를 찾아 보고해주세요.

[검토 기준]
{REVIEW_CRITERIA}

[조사 판단 규칙(매우 중요)]
1) **숫자(아라비아 숫자)는 한자음으로 읽습니다.** 예: 1[일], 2[이], 3[삼].
   - 따라서 '3를'은 [삼] + 목적격 조사이므로 '3을'이 자연스럽습니다.
2) **수식(LaTeX)은 마지막으로 발음되는 요소**(숫자/변수/첨자 등)를 기준으로 받침을 판단해 조사를 고르세요.
   - 예: $a_1$ 은 [에이 일]로 읽으므로 '은'이 자연스럽습니다.
3) **괄호(...)는 기본적으로 읽지 않습니다.** 다만 경우에 따라 괄호 안을 읽는 것으로 보고 조사 선택이 달라질 수 있습니다.
   - [문제] 괄호 안 수식/기호도 읽습니다.
   - [해설] 교재 내부 참조/각주(예: 218p, 10쪽, 평수능 등)는 읽지 않습니다.
   - [해설] 조건/정의(예: x>0, x=0, f(x)=0 등)를 제시하는 괄호는 읽고, 단순 부연 설명(예: 의미(평행이동), 등호(=0))은 읽지 않습니다.

[입력 텍스트]
{section_text}

[출력 형식]
오류가 있으면 JSON 배열로 출력하세요. 오류가 없으면 빈 배열 []을 출력하세요.
순수 JSON만 출력하고, 다른 설명은 하지 마세요.

[
    {{
        "original": "문제가 있는 부분",
        "corrected": "수정 제안",
        "reason": "수정 이유 (기준 번호 포함)",
        "severity": "high/medium/low"
    }}
]
"""
    try:
        response = client.models.generate_content(model='gemini-2.5-flash-lite', contents=prompt)
        json_str = response.text.strip()
        json_str = re.sub(r'^```json\s*', '', json_str)
        json_str = re.sub(r'\s*```$', '', json_str)
        llm_errors = json.loads(json_str)
        merged = _dedup_errors(rule_errors + (llm_errors or []))
        return {"section": section_num, "errors": merged}
    except json.JSONDecodeError:
        return {"section": section_num, "errors": rule_errors, "parse_error": response.text}
    except Exception as e:
        return {"section": section_num, "errors": rule_errors, "api_error": str(e)}

def review_markdown(client, content, progress_callback=None):
    sections = split_into_sections(content)
    total_sections = len(sections)
    all_results = []
    for i, section in enumerate(sections):
        if progress_callback:
            progress_callback(i + 1, total_sections, "검토")
        result = review_single_section(client, section, i + 1)
        all_results.append(result)
        time.sleep(2)
    return all_results

def generate_report(results):
    report_lines = ["# 📝 검토 보고서\n"]
    total_errors = 0
    high_count = 0
    medium_count = 0
    low_count = 0
    for result in results:
        section_num = result["section"]
        errors = result.get("errors", [])
        section_header_written = False
        if "parse_error" in result or "api_error" in result:
            report_lines.append(f"\n## 섹션 {section_num}\n")
            section_header_written = True
            if "parse_error" in result:
                report_lines.append("⚠️ JSON 파싱 오류 발생 (LLM 결과는 반영되지 않았습니다)\n")
            if "api_error" in result:
                report_lines.append(f"⚠️ API 오류: {result['api_error']} (LLM 결과는 반영되지 않았습니다)\n")
        if errors:
            if not section_header_written:
                report_lines.append(f"\n## 섹션 {section_num}\n")
            for err in errors:
                total_errors += 1
                severity = err.get("severity", "medium")
                if severity == "high":
                    high_count += 1
                    icon = "🔴"
                elif severity == "medium":
                    medium_count += 1
                    icon = "🟡"
                else:
                    low_count += 1
                    icon = "🟢"
                report_lines.append(f"### {icon} 오류 {total_errors}\n")
                report_lines.append(f"- **원문**: {err.get('original', 'N/A')}\n")
                report_lines.append(f"- **수정**: {err.get('corrected', 'N/A')}\n")
                report_lines.append(f"- **이유**: {err.get('reason', 'N/A')}\n")
                report_lines.append("")
    summary = f"""
## 📊 요약

| 구분 | 개수 |
|------|------|
| 🔴 높음 | {high_count} |
| 🟡 보통 | {medium_count} |
| 🟢 낮음 | {low_count} |
| **총계** | **{total_errors}** |

---
"""
    report_lines.insert(1, summary)
    return '\n'.join(report_lines), total_errors

# ==========================================
# [화면 전환 관리]
# ==========================================
if 'current_page' not in st.session_state:
    st.session_state.current_page = 'main'

def navigate_to(page):
    st.session_state.current_page = page

# ==========================================
# [페이지 1: 메인 페이지 (니무네 방앗간)]
# ==========================================
def main_page():
    st.title("니무네 방앗간 (Nimu's Mill)")
    st.markdown("### 작업 선택")
    
    col1, col2 = st.columns(2)
    
    with col1:
        st.info("수학 교재 검토 시스템")
        if st.button("2512", use_container_width=True):
            navigate_to('2512')
            st.rerun()

    with col2:
        st.success("새로운 기능 (준비 중)")
        if st.button("New Feature", disabled=True, use_container_width=True):
            pass

# ==========================================
# [페이지 2: 기존 기능 (2512)]
# ==========================================
def page_2512():
    # 네비게이션: 메인으로 돌아가기
    if st.button("← 메인으로 돌아가기"):
        navigate_to('main')
        st.rerun()
    
    st.divider()

    # --- 기존 UI 코드 시작 ---
    st.title("수학 교재 PDF 변환 & 검토")
    st.markdown("pdf를 업로드하면 md 텍스트로 변환하고, 문법/수학적 오류를 검토합니다.")
    st.markdown("⚠️Integrate 계정으로 google AI Studio에 접속해 발급받은 API Key를 왼쪽 사이드바에 입력해야 합니다.")

    # 사이드바 설정
    with st.sidebar:
        st.header("⚙️ 설정")
        
        # API Key 세션 관리
        if 'api_key' not in st.session_state:
            st.session_state.api_key = DEFAULT_API_KEY
        
        # 텍스트 인풋 값이 변경되면 세션에 저장
        api_input = st.text_input("Google API Key", value=st.session_state.api_key, type="password")
        st.session_state.api_key = api_input
        
        st.caption("[API 키 발급받기](https://aistudio.google.com/apikey)")
        
        st.divider()
        
        st.subheader("작업 선택")
        do_convert = st.checkbox("1단계: PDF → Markdown 변환", value=True)
        do_review = st.checkbox("2단계: Markdown 검토", value=True)
        
        st.divider()
        st.info("두 단계를 모두 선택하면 변환 후 자동으로 검토가 진행됩니다.")

    # 파일 업로드
    uploaded_file = st.file_uploader("PDF 파일을 드래그하거나 선택하세요", type=["pdf"])

    if uploaded_file is not None:
        # 파일명 자동 생성
        original_filename = uploaded_file.name
        file_name_only = os.path.splitext(original_filename)[0]
        convert_filename = f"convert_{file_name_only}.md"
        report_filename = f"report_{file_name_only}.md"
        
        col1, col2, col3 = st.columns(3)
        with col1:
            st.write(f"📂 입력: **{original_filename}**")
        with col2:
            st.write(f"📄 변환 결과: **{convert_filename}**")
        with col3:
            st.write(f"📋 검토 보고서: **{report_filename}**")
        
        if st.button("🚀 시작하기", type="primary"):
            # API 키 확인 (세션에서 가져옴)
            api_key = st.session_state.api_key
            if not api_key:
                st.error("❌ API 키를 입력해주세요.")
                st.stop()
            
            # 클라이언트 생성
            client = genai.Client(api_key=api_key)
            
            # 진행 상황 표시용
            progress_bar = st.progress(0)
            status_text = st.empty()
            
            def update_progress(current, total, stage):
                progress_bar.progress(current / total)
                status_text.text(f"[{stage}] {current}/{total} 처리 중...")
            
            # 임시 파일로 저장
            with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_file:
                tmp_file.write(uploaded_file.getvalue())
                tmp_path = tmp_file.name
            
            try:
                converted_text = None
                review_report = None
                
                # 1단계: PDF → Markdown 변환
                if do_convert:
                    st.subheader("📄 1단계: PDF → Markdown 변환")
                    converted_text, error = process_pdf(client, tmp_path, update_progress)
                    
                    if error:
                        st.error(error)
                        st.stop()
                    
                    st.text_area("변환 결과", converted_text, height=300)
                    st.download_button(
                        label="📥 변환 결과 다운로드",
                        data=converted_text,
                        file_name=convert_filename,
                        mime="text/markdown"
                    )
                    st.success("✅ 변환 완료!")
                
                # 2단계: Markdown 검토
                if do_review and converted_text:
                    st.subheader("📋 2단계: Markdown 검토")
                    status_text.text("검토를 시작합니다...")
                    
                    review_results = review_markdown(client, converted_text, update_progress)
                    review_report, error_count = generate_report(review_results)
                    
                    st.text_area("검토 보고서", review_report, height=300)
                    st.download_button(
                        label="📥 검토 보고서 다운로드",
                        data=review_report,
                        file_name=report_filename,
                        mime="text/markdown"
                    )
                    st.success(f"✅ 검토 완료! 총 {error_count}개 오류 발견")
                
                status_text.text("✅ 모든 작업이 완료되었습니다!")
                progress_bar.progress(100)
                
            finally:
                # 임시 파일 삭제
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)

    else:
        st.info("👆 PDF 파일을 업로드하면 시작할 수 있습니다.")

# ==========================================
# [앱 실행 진입점]
# ==========================================
if st.session_state.current_page == 'main':
    main_page()
elif st.session_state.current_page == '2512':
    page_2512()
