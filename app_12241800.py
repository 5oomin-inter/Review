import streamlit as st
import os
import io
import tempfile
import time
import json
import re
import zipfile
import google.generativeai as genai
from pdf2image import convert_from_path

# ==========================================
# [초기 설정]
# ==========================================
st.set_page_config(page_title="업무 자동화", layout="wide")

# ==========================================
# [CSS 스타일: 코드 블록 스크롤 제어 및 자동 줄바꿈]
# ==========================================
st.markdown("""
    <style>
    /* 1. 뷰어(st.code) 스타일 */
    [data-testid="stCodeBlock"] pre {
        white-space: pre-wrap !important;
        word-break: break-all !important;
        overflow-wrap: break-word !important;
        max-height: 400px !important; /* 개별 문항 박스 높이 제한 */
        overflow-y: auto !important;
        overflow-x: hidden !important;
    }
    
    [data-testid="stCodeBlock"] code {
        white-space: pre-wrap !important;
        word-break: break-all !important;
    }
    
    /* 2. 에디터(st.text_area) 스타일 */
    .stTextArea textarea {
        font-family: 'Courier New', Courier, monospace !important;
        font-size: 14px !important;
        line-height: 1.5 !important;
    }

    /* 3. 버튼 레이아웃 정렬 */
    div[data-testid="column"] {
        display: flex;
        align-items: center; 
    }
    </style>
""", unsafe_allow_html=True)

# ==========================================
# [상수 및 환경 설정]
# ==========================================
DEFAULT_API_KEY = ""

if os.name == 'nt':
    POPPLER_PATH = r"C:\Users\inter\Desktop\Review\poppler-25.12.0\Library\bin"
else:
    POPPLER_PATH = None

# ==========================================
# [프롬프트]
# ==========================================
PROMPT_FOR_TEX = """
# 🏆 종합 학술 감사관 (Scholarly Auditor v8.2)

## 1. 역할
고등 수학 교육 콘텐츠의 **최종 검증자**로서, 오류를 찾아내어 **깔끔한 표(Table)**로 보고합니다.

## 2. 출력 형식 (엄수)
서술형 줄글을 절대 쓰지 마십시오. 오직 **아래의 표 형식**으로만 출력하십시오.
오류가 없다면 표를 출력하지 말고 "✅ **발견된 오류 없음**"이라고만 쓰십시오.

### [Table A: 학술 감사 보고서] (치명적 오류)
* **기준:** 수학적 진리값, 정답, 부호, 개념 오류 (확신도 100%)
| 위치 | 오류 내용 | 원문 $\\to$ 수정 제안 | 근거 및 의견 |
| :--- | :--- | :--- | :--- |
| (예: 해설 3행) | (예: 부호 오류) | **[원문]** $f(t)$ <br> $\\downarrow$ <br> **[수정]** $f(-t)$ | y축 대칭이므로 -t 대입 필요 |

### [Table B: 변환 오류 클린업] (단순 수정)
* **기준:** 띄어쓰기, 오타, 문법, 단순 편집
| 위치 | 오류 내용 | 원문 $\\to$ 수정 제안 |
| :--- | :--- | :--- |
| (예: 문제 1행) | (예: 띄어쓰기) | 3 개를 $\\to$ 3개를 |

### [Table C: 개선 제안] (권장 사항)
* **기준:** 더 나은 풀이, 가독성, 교육적 제안
| 위치 | 제안 유형 | 내용 및 의견 |
| :--- | :--- | :--- |
| (예: 식 (나)) | (예: 풀이 개선) | 로피탈 정리보다 미분계수 정의를 사용하는 것이 좋습니다. |

## 3. 주의 사항
1. 각 표의 헤더(Table A, B, C)는 오류가 있을 때만 출력하세요.
2. 수식은 LaTeX 문법($$)을 유지하세요.
"""

PROMPT_FOR_PDF = """
당신은 대한민국 고등학교 수학 교재 전문 교정자입니다.
아래 텍스트에서 오류를 찾아 JSON으로 출력하세요.
(기존 프롬프트 생략...)
[
    {{
        "original": "문제가 있는 부분",
        "corrected": "수정 제안",
        "reason": "수정 이유",
        "severity": "high/medium/low"
    }}
]
"""

# ==========================================
# [공통 유틸리티]
# ==========================================
_JONGSUNG_LIST = ["", "ㄱ", "ㄲ", "ㄳ", "ㄴ", "ㄵ", "ㄶ", "ㄷ", "ㄹ", "ㄺ", "ㄻ", "ㄼ", "ㄽ", "ㄾ", "ㄿ", "ㅀ", "ㅁ", "ㅂ", "ㅄ", "ㅅ", "ㅆ", "ㅇ", "ㅈ", "ㅊ", "ㅋ", "ㅌ", "ㅍ", "ㅎ"]
_LATIN_LAST_JONG = {"A": "", "B": "", "C": "", "D": "", "E": "", "F": "", "G": "", "H": "", "I": "", "J": "", "K": "", "L": "ㄹ", "M": "ㅁ", "N": "ㄴ", "O": "", "P": "", "Q": "", "R": "ㄹ", "S": "", "T": "", "U": "", "V": "", "W": "", "X": "", "Y": "", "Z": ""}
_DIGIT_LAST_JONG = {"0": "ㅇ", "1": "ㄹ", "2": "", "3": "ㅁ", "4": "", "5": "", "6": "ㄱ", "7": "ㄹ", "8": "ㄹ", "9": ""}
_MATH_JOSA_PATTERN = re.compile(r'(?P<math>\${1,2}[^$]+?\${1,2})(?P<ws>\s*)(?P<josa>으로|로|은|는|이|가|을|를|과|와)(?=[\s\.,;:\)\]\}\!?]|$)')
_NUM_JOSA_PATTERN = re.compile(r'(?P<num>\d[\d,]*(?:\.\d+)?)(?P<ws>\s*)(?P<josa>으로|로|은|는|이|가|을|를|과|와)(?=[\s\.,;:\)\]\}\!?]|$)')

def _hangul_last_jong(text):
    if not text: return ""
    s = re.sub(r'[\s\.,;:!\?\)\]\}]+$', '', text.strip())
    for ch in reversed(s):
        code = ord(ch)
        if 0xAC00 <= code <= 0xD7A3: return _JONGSUNG_LIST[(code - 0xAC00) % 28]
    return ""
def _number_last_jong(num_raw):
    if not num_raw: return ""
    s = num_raw.strip().replace(",", "").replace(" ", "").lstrip("+")
    if s.startswith("-"): s = s[1:]
    if "." in s: return _DIGIT_LAST_JONG.get(s.split(".")[1][-1], "") if s.split(".")[1] else ""
    digits = re.sub(r"\D", "", s).lstrip("0") or "0"
    if digits == "0": return "ㅇ"
    return _DIGIT_LAST_JONG.get(digits[-1])
def _latin_last_jong(text):
    s = text.strip()
    if not s: return ""
    for ch in reversed(s):
        if ch.isalpha(): return _LATIN_LAST_JONG.get(ch.upper(), "")
    return ""
def _expected_josa(josa, last_jong):
    has = (last_jong != "")
    if josa in ("은", "는"): return "은" if has else "는"
    if josa in ("이", "가"): return "이" if has else "가"
    if josa in ("을", "를"): return "을" if has else "를"
    if josa in ("과", "와"): return "과" if has else "와"
    if josa in ("으로", "로"): return "로" if (not has or last_jong == "ㄹ") else "으로"
    return josa
def _last_jong_from_math(math): return "" 
def get_line_number(full_text, index): return full_text.count('\n', 0, index) + 1

def rule_check_josa(section_text):
    errors = []
    for m in _MATH_JOSA_PATTERN.finditer(section_text):
        math = m.group("math")
        ws = m.group("ws") or ""
        josa = m.group("josa")
        last_jong = _last_jong_from_math(math) 
        math_content = math.strip("$")
        last_char = math_content[-1] if math_content else ""
        if re.match(r'\d', last_char): derived_jong = _number_last_jong(last_char)
        elif re.match(r'[A-Za-z]', last_char): derived_jong = _latin_last_jong(last_char)
        else: derived_jong = "" 
        exp = _expected_josa(josa, derived_jong)
        original = f"{math}{ws}{josa}"
        corrected = f"{math}{exp}"
        line_num = get_line_number(section_text, m.start())
        if josa != exp and derived_jong != "":
            errors.append({"location": f"{line_num}행", "original": original, "corrected": corrected, "reason": "조사 오류(수식)", "severity": "medium"})
    for m in _NUM_JOSA_PATTERN.finditer(section_text):
        num = m.group("num")
        ws = m.group("ws") or ""
        josa = m.group("josa")
        if m.start() > 0 and section_text[m.start() - 1] == "$": continue
        last_jong = _number_last_jong(num)
        exp = _expected_josa(josa, last_jong)
        original = f"{num}{ws}{josa}"
        corrected = f"{num}{exp}"
        line_num = get_line_number(section_text, m.start())
        if josa != exp or ws:
            errors.append({"location": f"{line_num}행", "original": original, "corrected": corrected, "reason": "조사 오류(숫자)", "severity": "medium"})
    return errors

def _dedup_errors(errors):
    seen = set(); out = []
    for e in errors:
        key = (e.get("original",""), e.get("corrected",""), e.get("reason",""))
        if key in seen: continue
        seen.add(key); out.append(e)
    return out

# ==========================================
# [로직 A] LaTeX ZIP 처리 (메인용)
# ==========================================
def extract_tex_from_zip(zip_file_bytes):
    try:
        with zipfile.ZipFile(zip_file_bytes) as z:
            tex_files = [f for f in z.namelist() if f.lower().endswith('.tex')]
            if not tex_files: return None, "ZIP 파일 내에 .tex 파일이 없습니다."
            target_file = tex_files[0]
            try: content = z.read(target_file).decode('utf-8')
            except UnicodeDecodeError: content = z.read(target_file).decode('cp949')
            return content, None
    except Exception as e: return None, f"ZIP 처리 오류: {str(e)}"

# [메인 페이지용 구형 파서 - 유지]
def parse_tex_content(tex_content):
    pattern = r'\\begin\{document\}([\s\S]*?)\\end\{document\}'
    match = re.search(pattern, tex_content)
    body = match.group(1).strip() if match else tex_content
    body = re.sub(r'\\maketitle', '', body)
    body = re.sub(r'\\newpage', '', body)
    body = re.sub(r'\\clearpage', '', body)
    start_pattern = re.compile(r'\\section\*?\{')
    matches = list(start_pattern.finditer(body))
    if not matches: return [body]
    chunks = []
    for i in range(len(matches)):
        start_idx = matches[i].start()
        end_idx = matches[i+1].start() if i + 1 < len(matches) else len(body)
        chunks.append(body[start_idx:end_idx])
    final_items = []
    current_item_text = ""
    explanation_keywords = ["해법", "해설", "풀이", "정답", "Solution", "성질", "개념", "정리", "분석", "접근", "Note", "Tip", "Guide", "공식"]
    ignore_keywords = ["Day", "일차"] 
    for chunk in chunks:
        brace_open_index = chunk.find('{')
        title_content = ""
        if brace_open_index != -1:
            brace_count = 1
            for k, char in enumerate(chunk[brace_open_index+1:], 1):
                if char == '{': brace_count += 1
                elif char == '}': brace_count -= 1
                if brace_count == 0:
                    title_content = chunk[brace_open_index+1 : brace_open_index+k]
                    break
        is_ignore = any(kw in title_content for kw in ignore_keywords)
        is_explicit_explanation = any(kw in title_content for kw in explanation_keywords)
        has_korean_text = bool(re.search(r'[가-힣]', title_content))
        is_explanation = is_explicit_explanation or (has_korean_text and not is_ignore)
        if is_ignore:
            if current_item_text.strip(): final_items.append(current_item_text.strip())
            current_item_text = ""
            continue
        if is_explanation:
            if current_item_text: current_item_text += "\n" + chunk
            else:
                if final_items: final_items[-1] += "\n" + chunk
                else: current_item_text = chunk
        else:
            if current_item_text.strip(): final_items.append(current_item_text.strip())
            current_item_text = chunk
    if current_item_text.strip(): final_items.append(current_item_text.strip())
    return final_items

# ==========================================
# [NEW] 개발용 파서 (문항 번호 기준 엄격 분리)
# ==========================================
def parse_tex_content_dev(tex_content):
    """
    [개발용] TeX 내용을 줄 단위로 읽어 (문항 + 모든 해설) 세트로 분리.
    오직 '문항 번호'가 나올 때만 세트를 끊습니다.
    """
    # 1. 문서 본문 추출
    pattern = r'\\begin\{document\}([\s\S]*?)\\end\{document\}'
    match = re.search(pattern, tex_content)
    body = match.group(1).strip() if match else tex_content

    # 2. 불필요한 LaTeX 명령어 제거
    body = re.sub(r'\\maketitle', '', body)
    body = re.sub(r'\\newpage', '', body)
    body = re.sub(r'\\clearpage', '', body)
    
    # 3. 줄 단위로 처리
    lines = [line.strip() for line in body.split('\n') if line.strip()]
    
    items = []
    current_item_lines = []
    current_item_label = "서문/공통" 
    
    # [정규식 정의]
    # 1. 순수 숫자 (예: "28", "29.")
    regex_pure_num = re.compile(r'^\d+(\.\s*)?$')
    # 2. 섹션 내의 숫자 (예: \section*{28}, \section*{110 \\ 29})
    # 주의: \section*{해법} 같은 건 잡히면 안 됨. 오직 숫자, 공백, 줄바꿈(\\)만 허용
    regex_section_num = re.compile(r'^\\section\*?\{\s*(\d+(\s*\\\\)?\s*)+\}$')
    
    ignore_keywords = ["Day", "일차"] 

    for line in lines:
        is_ignore = any(kw in line for kw in ignore_keywords)
        if is_ignore: continue

        # --- 문항 시작 판별 로직 ---
        is_question_start = False
        new_label = ""

        if regex_pure_num.match(line):
            is_question_start = True
            new_label = line.replace('.', '').strip()
            
        elif regex_section_num.match(line):
            # 섹션 내부 텍스트 추출
            inner_text = re.sub(r'\\section\*?\{', '', line).rstrip('}')
            # 텍스트가 정말 숫자로만(또는 \\ 포함) 되어 있는지 확인
            # (이미 regex_section_num이 거르긴 했지만 안전장치)
            if re.fullmatch(r'[\d\s\\]+', inner_text):
                is_question_start = True
                # "110 \\ 29" 같은 경우 마지막 숫자 "29"를 라벨로 사용
                new_label = inner_text.split(r'\\')[-1].strip()

        # --- 분기 처리 ---
        if is_question_start:
            # 기존에 모으던 내용이 있으면 저장 (이전 문항 세트 완료)
            if current_item_lines:
                items.append({
                    "label": f"{current_item_label}번 문항",
                    "content": "\n".join(current_item_lines)
                })
                current_item_lines = []
            
            # 새 문항 시작
            current_item_label = new_label
            current_item_lines.append(line)
        else:
            # 문항 번호가 아니면 (해설, 개념, 지문 등) 무조건 현재 세트에 추가
            current_item_lines.append(line)

    # 마지막 문항 저장
    if current_item_lines:
        items.append({
            "label": f"문항 {current_item_label}",
            "content": "\n".join(current_item_lines)
        })

    # 후처리: 내용이 너무 짧은 항목 제거 (쓰레기 데이터)
    valid_items = []
    for item in items:
        if len(item['content']) > 5:
            valid_items.append(item)
            
    return valid_items

# ==========================================
# [공통] 리뷰 및 리포트 생성
# ==========================================
def review_tex_section(model, section_text, section_num):
    rule_errors = rule_check_josa(section_text)
    prompt = PROMPT_FOR_TEX + "\n\n---------------------------------------------------------\n[검토할 텍스트]\n" + section_text + "\n---------------------------------------------------------"
    try:
        response = model.generate_content(prompt)
        return {"section": section_num, "rule_errors": rule_errors, "ai_report_text": response.text}
    except Exception as e:
        return {"section": section_num, "rule_errors": rule_errors, "api_error": str(e)}

def generate_report_for_tex(results_grouped_by_file):
    lines = ["# 🏆 종합 학술 감사 보고서\n"]
    for filename, results in results_grouped_by_file.items():
        lines.append(f"\n# 📁 파일: {filename}")
        lines.append("---")
        for res in results:
            lines.append(f"\n## 📄 {res.get('label', '문항 세트 ' + str(res['section']))}")
            if res.get('rule_errors'):
                lines.append("### 🐍 [Python 규칙 감지] (참고용)")
                lines.append("| 위치 | 오류 내용 | 원문 $\\to$ 수정 제안 |")
                lines.append("| :--- | :--- | :--- |")
                for err in res['rule_errors']:
                    lines.append(f"| {err['location']} | {err['reason']} | {err['original']} $\\to$ {err['corrected']} |")
                lines.append("\n")
            if 'api_error' in res: 
                lines.append(f"⚠️ **API Error:** {res['api_error']}")
            else: 
                lines.append(res['ai_report_text'])
            lines.append("\n---")
    return "\n".join(lines)


# ==========================================
# [로직 B] 2512 PDF 처리
# ==========================================
def process_pdf(model, pdf_path, progress_callback=None):
    try:
        if POPPLER_PATH: pages = convert_from_path(pdf_path, dpi=300, poppler_path=POPPLER_PATH)
        else: pages = convert_from_path(pdf_path, dpi=300)
    except Exception as e: return None, f"오류: PDF 변환 실패 ({e})"
    full_text = ""
    prompt = "이미지 내용을 Markdown으로 변환(OCR)하세요. 수식은 LaTeX($$)사용, 한글 보존."
    total_pages = len(pages)
    for i, page in enumerate(pages):
        if progress_callback: progress_callback(i + 1, total_pages, "변환")
        try:
            response = model.generate_content([prompt, page])
            full_text += f"\n\n--- Page {i+1} ---\n\n" + response.text
            time.sleep(2)
        except Exception as e: full_text += f"\n\n--- Page {i+1} (Error: {e}) ---\n\n"
    return full_text, None

def split_pdf_sections(content):
    sections = re.split(r'\n(?=---\s*Page|\n---\n|\d+\.\s)', content)
    return [s.strip() for s in sections if s.strip()]

def review_pdf_section(model, section_text, section_num):
    rule_errors = rule_check_josa(section_text)
    prompt = PROMPT_FOR_PDF.format(section_text=section_text)
    try:
        response = model.generate_content(prompt)
        json_str = response.text.strip().replace('```json', '').replace('```', '')
        llm_errors = json.loads(json_str)
        merged = _dedup_errors(rule_errors + (llm_errors or []))
        return {"section": section_num, "errors": merged}
    except json.JSONDecodeError: return {"section": section_num, "errors": rule_errors, "parse_error": response.text}
    except Exception as e: return {"section": section_num, "errors": rule_errors, "api_error": str(e)}

def generate_report_for_pdf(results):
    report_lines = ["# 📝 검토 보고서 (2512)\n"]
    total_errors = 0
    for result in results:
        section_num = result["section"]
        errors = result.get("errors", [])
        if "parse_error" in result or "api_error" in result:
            report_lines.append(f"\n## 섹션 {section_num}\n⚠️ 오류 발생")
        if errors:
            report_lines.append(f"\n## 섹션 {section_num}\n")
            for err in errors:
                total_errors += 1
                icon = "🔴" if err.get("severity") == "high" else "🟡"
                report_lines.append(f"### {icon} 오류 {total_errors}")
                report_lines.append(f"- **원문**: {err.get('original', 'N/A')}")
                report_lines.append(f"- **수정**: {err.get('corrected', 'N/A')}")
                report_lines.append(f"- **이유**: {err.get('reason', 'N/A')}\n")
    return '\n'.join(report_lines)

# ==========================================
# [화면 전환 관리]
# ==========================================
if 'current_page' not in st.session_state:
    st.session_state.current_page = 'main'

def navigate_to(page):
    st.session_state.current_page = page

# ==========================================
# [화면 1] 메인 페이지 (운영용)
# ==========================================
def main_page():
    col_title, col_btns = st.columns([6, 4])
    with col_title: 
        st.title("업무 자동화 (LaTeX ZIP)")
    with col_btns:
        c1, c2, c3 = st.columns(3)
        with c1:
            st.link_button("⏱️ 타이머", "https://integrate-git.github.io/timer/timer_c3.html", use_container_width=True)
        with c2:
            if st.button("🛠️ 개발용", use_container_width=True):
                navigate_to('dev')
                st.rerun()
        with c3:
            if st.button("2512ver ▶", use_container_width=True): 
                navigate_to('2512')
                st.rerun()

    st.markdown("""
    **LaTeX ZIP 자동 정제 및 검토 시스템**입니다.
    여러 개의 **ZIP 파일**을 한 번에 업로드할 수 있습니다.
    """)

    with st.sidebar:
        st.header("⚙️ 설정")
        if 'api_key' not in st.session_state: st.session_state.api_key = DEFAULT_API_KEY
        api_input = st.text_input("Google API Key", value=st.session_state.api_key, type="password")
        st.session_state.api_key = api_input
    
    uploaded_zips = st.file_uploader("ZIP 파일 업로드 (.zip)", type=["zip"], accept_multiple_files=True)
    all_files_data = []

    if uploaded_zips:
        with st.status("파일 분석 및 추출 중...", expanded=True) as status:
            for i, uploaded_zip in enumerate(uploaded_zips):
                status.write(f"📂 분석 중: {uploaded_zip.name}")
                tex_content, error = extract_tex_from_zip(uploaded_zip)
                if error:
                    st.error(f"{uploaded_zip.name}: {error}")
                    continue
                # 메인 페이지는 기존 파서 사용 (통합 텍스트 출력)
                items = parse_tex_content(tex_content)
                full_text = "\n\n" + ("="*30) + "\n\n".join(items)
                all_files_data.append({"filename": uploaded_zip.name, "items": items, "full_text": full_text, "index": i})
            status.update(label="모든 파일 준비 완료!", state="complete", expanded=False)

        if all_files_data:
            st.divider()
            file_options = {f"{data['filename']}": data for data in all_files_data}
            selected_option = st.selectbox("📂 확인하고 싶은 파일을 선택하세요:", list(file_options.keys()))
            
            if selected_option:
                selected_data = file_options[selected_option]
                idx = selected_data['index']
                full_text = selected_data['full_text']
                items = selected_data['items']
                st.info(f"✅ '{selected_data['filename']}' 내용 (총 {len(items)}개 문항 세트)")
                tab1, tab2 = st.tabs(["👁️ 뷰어 (Color & Wrap)", "✏️ 에디터 (수정)"])
                with tab1: st.code(full_text, language='latex')
                with tab2: st.text_area(f"Editor_{idx}", value=full_text, height=600, label_visibility="collapsed")
            
            st.divider()
            if st.button("🚀 전체 파일 AI 학술 감사 시작", type="primary"):
                if not st.session_state.api_key: st.error("API Key를 입력해주세요."); st.stop()
                genai.configure(api_key=st.session_state.api_key)
                model = genai.GenerativeModel('gemini-1.5-flash')
                progress_bar = st.progress(0)
                status_text = st.empty()
                total_tasks = sum(len(f['items']) for f in all_files_data)
                current_task_idx = 0
                results_by_file = {}

                for file_data in all_files_data:
                    filename = file_data['filename']
                    items = file_data['items']
                    file_results = []
                    status_text.text(f"📂 {filename} 검토 중...")
                    for j, item_text in enumerate(items):
                        current_task_idx += 1
                        progress_bar.progress(current_task_idx / total_tasks)
                        max_retries = 3; retry_delay = 5
                        for attempt in range(max_retries):
                            result = review_tex_section(model, item_text, j + 1)
                            if "api_error" in result and "429" in str(result["api_error"]):
                                if attempt < max_retries - 1:
                                    time.sleep(retry_delay); retry_delay *= 2
                                    continue
                            file_results.append(result)
                            break
                        time.sleep(2) 
                    results_by_file[filename] = file_results
                
                report = generate_report_for_tex(results_by_file)
                st.divider()
                st.subheader("📋 통합 감사 결과 보고서")
                st.markdown(report)
                st.download_button("📥 리포트 다운로드", report, file_name="integrated_auditor_report.md")
                st.success("모든 파일의 검토가 완료되었습니다!")

# ==========================================
# [화면 3] 개발용 페이지 (Dev Mode)
# ==========================================
def page_dev():
    if st.button("← 메인으로 돌아가기"):
        navigate_to('main')
        st.rerun()
    st.divider()
    
    st.title("🛠️ 테스트 페이지")
    st.warning("""⚠️ 이곳은 기능 테스트 및 디버깅을 위한 공간입니다.  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;1. 문항별 분리: 문항끼리 분리되지 않을 수 있습니다. 문항 번호가 맞는지 확인하고, 필요한 부분만 복사하세요.""")

    with st.sidebar:
        st.header("⚙️ 설정 (Dev)")
        if 'api_key' not in st.session_state: st.session_state.api_key = DEFAULT_API_KEY
        api_input = st.text_input("Google API Key", value=st.session_state.api_key, type="password")
        st.session_state.api_key = api_input
    
    uploaded_zips = st.file_uploader("ZIP 파일 업로드", type=["zip"], accept_multiple_files=True, key="dev_uploader")
    all_files_data = []

    if uploaded_zips:
        with st.status("파일 분석 및 추출 중...", expanded=True) as status:
            for i, uploaded_zip in enumerate(uploaded_zips):
                status.write(f"📂 분석 중: {uploaded_zip.name}")
                tex_content, error = extract_tex_from_zip(uploaded_zip)
                if error:
                    st.error(f"{uploaded_zip.name}: {error}")
                    continue
                # [Dev] 개선된 파서 사용 -> items는 [{'label': '문항 28', 'content': '...'}, ...] 형태의 딕셔너리 리스트
                items = parse_tex_content_dev(tex_content)
                all_files_data.append({"filename": uploaded_zip.name, "items": items, "index": i})
            status.update(label="모든 파일 준비 완료!", state="complete", expanded=False)

        if all_files_data:
            st.divider()
            file_options = {f"{data['filename']}": data for data in all_files_data}
            selected_option = st.selectbox("📂 확인하고 싶은 파일 이름 선택", list(file_options.keys()), key="dev_selectbox")
            
            if selected_option:
                selected_data = file_options[selected_option]
                items = selected_data['items'] # 딕셔너리 리스트
                idx = selected_data['index']
                
                st.caption(f"✅ '{selected_data['filename']}' 내용 (총 {len(items)}개 문항 세트)")

                # [중요] 문항별 개별 박스 생성 (반복문)
                for j, item_data in enumerate(items):
                    item_label = item_data.get('label', f"{j+1}")
                    item_text = item_data.get('content', '')
                    
                    with st.expander(f"{item_label}", expanded=True):
                        # 각 문항마다 탭 생성
                        tab1, tab2 = st.tabs(["🦁LaTeX", "📝메모장st"])
                        with tab1:
                            st.code(item_text, language='latex')
                        with tab2:
                            st.text_area(f"Dev_Edit_{idx}_{j}", value=item_text, height=300, label_visibility="collapsed")
                    
                    st.divider()

            st.divider()
            if st.button("🚀 (Dev) AI 감사 시작", type="primary"):
                if not st.session_state.api_key: st.error("API Key를 입력해주세요."); st.stop()
                genai.configure(api_key=st.session_state.api_key)
                model = genai.GenerativeModel('gemini-1.5-flash')
                progress_bar = st.progress(0)
                status_text = st.empty()
                total_tasks = sum(len(f['items']) for f in all_files_data)
                current_task_idx = 0
                results_by_file = {}

                for file_data in all_files_data:
                    filename = file_data['filename']
                    items = file_data['items']
                    file_results = []
                    status_text.text(f"📂 {filename} 검토 중...")
                    for j, item_data in enumerate(items):
                        item_text = item_data.get('content', '')
                        item_label = item_data.get('label', f"문항 {j+1}")
                        
                        current_task_idx += 1
                        progress_bar.progress(current_task_idx / total_tasks)
                        max_retries = 3; retry_delay = 5
                        for attempt in range(max_retries):
                            # label도 넘겨주면 좋겠지만 review 함수 시그니처 유지 위해
                            result = review_tex_section(model, item_text, j + 1)
                            # 결과에 라벨 추가
                            result['label'] = item_label 
                            
                            if "api_error" in result and "429" in str(result["api_error"]):
                                if attempt < max_retries - 1:
                                    time.sleep(retry_delay); retry_delay *= 2
                                    continue
                            file_results.append(result)
                            break
                        time.sleep(2) 
                    results_by_file[filename] = file_results
                
                report = generate_report_for_tex(results_by_file)
                st.divider()
                st.subheader("📋 통합 감사 결과 보고서 (Dev)")
                st.markdown(report)
                st.download_button("📥 리포트 다운로드", report, file_name="dev_report.md")
                st.success("테스트 완료!")

# ==========================================
# [화면 2] 2512 페이지 (Legacy PDF)
# ==========================================
def page_2512():
    if st.button("← 메인으로 돌아가기"):
        navigate_to('main')
        st.rerun()
    st.divider()
    st.title("수학 교재 PDF 변환 & 검토 (2512)")

    with st.sidebar:
        st.header("⚙️ 설정 (2512)")
        if 'api_key' not in st.session_state: st.session_state.api_key = DEFAULT_API_KEY
        api_input = st.text_input("Google API Key", value=st.session_state.api_key, type="password")
        st.session_state.api_key = api_input
        st.divider()
        do_convert = st.checkbox("1단계: PDF → Markdown 변환", value=True)
        do_review = st.checkbox("2단계: Markdown 검토", value=True)

    uploaded_file = st.file_uploader("PDF 파일을 드래그하거나 선택하세요", type=["pdf"])

    if uploaded_file is not None:
        if st.button("🚀 시작하기", type="primary"):
            if not st.session_state.api_key: st.error("❌ API 키를 입력해주세요."); st.stop()
            
            genai.configure(api_key=st.session_state.api_key)
            model = genai.GenerativeModel('gemini-1.5-flash')

            progress_bar = st.progress(0)
            status_text = st.empty()
            
            def update_progress(current, total, stage):
                progress_bar.progress(current / total)
                status_text.text(f"[{stage}] {current}/{total} 처리 중...")
            
            with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_file:
                tmp_file.write(uploaded_file.getvalue())
                tmp_path = tmp_file.name
            
            try:
                converted_text = None
                if do_convert:
                    st.subheader("📄 1단계: PDF → Markdown 변환")
                    converted_text, error = process_pdf(model, tmp_path, update_progress)
                    if error: st.error(error); st.stop()
                    st.text_area("변환 결과", converted_text, height=300)
                    st.download_button("📥 변환 결과 다운로드", converted_text, file_name="converted.md")

                if do_review and converted_text:
                    st.subheader("📋 2단계: Markdown 검토")
                    sections = split_pdf_sections(converted_text)
                    all_results = []
                    total = len(sections)
                    for i, section in enumerate(sections):
                        update_progress(i+1, total, "검토")
                        res = review_pdf_section(model, section, i+1)
                        all_results.append(res)
                        time.sleep(2)
                    
                    report = generate_report_for_pdf(all_results)
                    st.text_area("검토 보고서", report, height=300)
                    st.download_button("📥 검토 보고서 다운로드", report, file_name="report_2512.md")
                    st.success("완료!")
            finally:
                if os.path.exists(tmp_path): os.remove(tmp_path)

# ==========================================
# [앱 실행 진입점]
# ==========================================
if st.session_state.current_page == 'main': main_page()
elif st.session_state.current_page == '2512': page_2512()
elif st.session_state.current_page == 'dev': page_dev()





