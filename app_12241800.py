import streamlit as st
import os
import io
import tempfile
import time
import json
import re
import zipfile
from google import genai
from google.genai import types
from pdf2image import convert_from_path

# ==========================================
# [초기 설정]
# ==========================================
st.set_page_config(page_title="업무 자동화", layout="wide")

# ==========================================
# [상수 및 환경 설정]
# ==========================================
DEFAULT_API_KEY = ""

if os.name == 'nt':
    POPPLER_PATH = r"C:\Users\inter\Desktop\Review\poppler-25.12.0\Library\bin"
else:
    POPPLER_PATH = None

# ==========================================
# [프롬프트 A] LaTeX ZIP용 (v8.1 - Table 형식)
# ==========================================
PROMPT_FOR_TEX = """
# 🏆 종합 학술 감사관 (Scholarly Auditor v8.1)

## 1. 🥇 핵심 정체성
귀하는 고등 수학 교육 콘텐츠의 **최종 검증자**이자 **기술적 편집자**입니다.

## 2. 📝 출력 표준 (Output format) - 중요!
**반드시 아래의 표(Table) 형식을 엄수하십시오.**

### 1. 🎓 [Table A: 학술 감사 보고서] (치명적 오류)
* **기준:** 수학적 진리값 오류, 정답 오류, 치명적 오개념 (확신도 100%)
* **양식:**
| 위치 | 오류 내용 | 원문 $\\to$ 수정 제안 | 근거 및 의견 |
| :--- | :--- | :--- | :--- |
| (예: 해설 3행) | (예: 부호 오류) | **[원문]** $f(t)$ <br> $\\downarrow$ <br> **[수정]** $f(-t)$ | (예: y축 대칭이므로 -t 대입 필요) |

### 2. 🧹 [Table B: 변환 오류 클린업] (단순 수정)
* **기준:** 띄어쓰기, 오타, LaTeX 문법, 단순 편집
* **양식:**
| 위치 | 오류 내용 | 원문 $\\to$ 수정 제안 |
| :--- | :--- | :--- |
| (예: 문제 1행) | (예: 띄어쓰기) | 3 개를 $\\to$ 3개를 |

### 3. 💡 [Table C: 개선 제안] (권장 사항)
* **기준:** 더 나은 풀이, 가독성, 교육적 제안
* **양식:**
| 위치 | 제안 유형 | 내용 및 의견 |
| :--- | :--- | :--- |
| (예: 식 (나)) | (예: 풀이 개선) | 현재 풀이보다 로피탈 정리를 이용하는 것이 더 직관적입니다. |

## 3. 🚫 출력 제어 규칙
1. **Perfect Score:** 수정 사항이 없으면 "✅ [무결점 인증]"만 출력.
2. **Table Integrity:** 내용이 있는 표만 출력하고, 빈 표는 출력하지 마십시오.

## 4. 📊 <FINAL REPORT>
(위 양식에 맞춰 출력)
<FINAL REPORT>
... (AI Report Content) ...
</FINAL REPORT>
"""

# ==========================================
# [프롬프트 B] 2512 PDF용 (Legacy - JSON 형식)
# ==========================================
PROMPT_FOR_PDF = """
당신은 대한민국 고등학교 수학 교재 전문 교정자입니다.
아래 텍스트에서 오류를 찾아 보고해주세요.

[검토 기준]
1. 조사 연결: 수식 뒤의 조사($f(x)$는/은 등)가 자연스러운지
2. 맞춤법/띄어쓰기: 기본적인 한국어 맞춤법 준수
3. 피동/사동: '되어지다', '보여지다' 등 이중 피동 지양
4. 대등 연결: 문장 나열 시 구조적 대등성 유지
5. 주술 호응: 주어와 서술어의 관계가 명확한지
6. 중의성: 해석이 모호한 문장 수정
7. 수학 용어: 고교 과정에 맞는 정확한 용어 사용
8. 변수 일관성: 정의된 변수가 끝까지 유지되는지
9. 오타: 단순 오타 및 OCR 오류

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

# ==========================================
# [공통 유틸리티: 조사 규칙 검사 등]
# ==========================================
_JONGSUNG_LIST = ["", "ㄱ", "ㄲ", "ㄳ", "ㄴ", "ㄵ", "ㄶ", "ㄷ", "ㄹ", "ㄺ", "ㄻ", "ㄼ", "ㄽ", "ㄾ", "ㄿ", "ㅀ", "ㅁ", "ㅂ", "ㅄ", "ㅅ", "ㅆ", "ㅇ", "ㅈ", "ㅊ", "ㅋ", "ㅌ", "ㅍ", "ㅎ"]
_LATIN_LAST_JONG = {"A": "", "B": "", "C": "", "D": "", "E": "", "F": "", "G": "", "H": "", "I": "", "J": "", "K": "", "L": "ㄹ", "M": "ㅁ", "N": "ㄴ", "O": "", "P": "", "Q": "", "R": "ㄹ", "S": "", "T": "", "U": "", "V": "", "W": "", "X": "", "Y": "", "Z": ""}
_DIGIT_LAST_JONG = {"0": "ㅇ", "1": "ㄹ", "2": "", "3": "ㅁ", "4": "", "5": "", "6": "ㄱ", "7": "ㄹ", "8": "ㄹ", "9": ""}
_JOSA_CANDIDATES = ("은", "는", "이", "가", "을", "를", "과", "와", "으로", "로")
_MATH_JOSA_PATTERN = re.compile(r'(?P<math>\${1,2}[^$]+?\${1,2})(?P<ws>\s*)(?P<josa>으로|로|은|는|이|가|을|를|과|와)(?=[\s\.,;:\)\]\}\!?]|$)')
_NUM_JOSA_PATTERN = re.compile(r'(?P<num>\d[\d,]*(?:\.\d+)?)(?P<ws>\s*)(?P<josa>으로|로|은|는|이|가|을|를|과|와)(?=[\s\.,;:\)\]\}\!?]|$)')

def _hangul_last_jong(text):
    if not text: return ""
    s = re.sub(r'[\s\.,;:!\?\)\]\}]+$', '', text.strip())
    for ch in reversed(s):
        code = ord(ch)
        if 0xAC00 <= code <= 0xD7A3: return _JONGSUNG_LIST[(code - 0xAC00) % 28]
    return ""
def _latin_last_jong(text):
    s = text.strip()
    if not s: return ""
    for ch in reversed(s):
        if ch.isalpha(): return _LATIN_LAST_JONG.get(ch.upper(), "")
    return ""
def _number_last_jong(num_raw):
    if not num_raw: return ""
    s = num_raw.strip().replace(",", "").replace(" ", "").lstrip("+")
    if s.startswith("-"): s = s[1:]
    if "." in s: return _DIGIT_LAST_JONG.get(s.split(".")[1][-1], "") if s.split(".")[1] else ""
    digits = re.sub(r"\D", "", s).lstrip("0") or "0"
    if digits == "0": return "ㅇ"
    return _DIGIT_LAST_JONG.get(digits[-1], "")

def _expected_josa(josa, last_jong):
    has = (last_jong != "")
    if josa in ("은", "는"): return "은" if has else "는"
    if josa in ("이", "가"): return "이" if has else "가"
    if josa in ("을", "를"): return "을" if has else "를"
    if josa in ("과", "와"): return "과" if has else "와"
    if josa in ("으로", "로"): return "로" if (not has or last_jong == "ㄹ") else "으로"
    return josa

def _strip_math_delimiters(math): return math.strip("$")
def _last_jong_from_math(math): return "" 
def _last_jong_from_text(text): return "" 

def get_line_number(full_text, index):
    return full_text.count('\n', 0, index) + 1

def rule_check_josa(section_text):
    errors = []
    # 1. 수식 뒤 조사
    for m in _MATH_JOSA_PATTERN.finditer(section_text):
        math = m.group("math")
        ws = m.group("ws") or ""
        josa = m.group("josa")
        last_jong = _last_jong_from_math(math)
        exp = _expected_josa(josa, last_jong)
        original = f"{math}{ws}{josa}"
        corrected = f"{math}{exp}"
        line_num = get_line_number(section_text, m.start())
        if josa != exp or ws:
            errors.append({"location": f"{line_num}행", "original": original, "corrected": corrected, "reason": "조사 오류(수식)", "severity": "medium"})
    # 2. 숫자 뒤 조사
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
# [로직 A] LaTeX ZIP 처리 전용
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

def review_tex_section(client, section_text, section_num):
    """[LaTeX ZIP 전용] Markdown Table 형식 리턴"""
    rule_errors = rule_check_josa(section_text)
    prompt = PROMPT_FOR_TEX + "\n\n---------------------------------------------------------\n[검토할 텍스트]\n" + section_text + "\n---------------------------------------------------------"
    try:
        response = client.models.generate_content(model='gemini-1.5-flash', contents=prompt)
        return {"section": section_num, "rule_errors": rule_errors, "ai_report_text": response.text}
    except Exception as e:
        return {"section": section_num, "rule_errors": rule_errors, "api_error": str(e)}

def generate_report_for_tex(results):
    lines = ["# 🏆 종합 학술 감사 보고서\n"]
    for res in results:
        lines.append(f"\n---")
        lines.append(f"## 📄 문항 세트 {res['section']}\n")
        if res.get('rule_errors'):
            lines.append("### 🐍 [Python 규칙 감지] (참고용)")
            lines.append("| 위치 | 오류 내용 | 원문 $\\to$ 수정 제안 |")
            lines.append("| :--- | :--- | :--- |")
            for err in res['rule_errors']:
                lines.append(f"| {err['location']} | {err['reason']} | {err['original']} $\\to$ {err['corrected']} |")
            lines.append("\n")
        if 'api_error' in res: lines.append(f"⚠️ **API Error:** {res['api_error']}")
        else: lines.append(res['ai_report_text'])
    return "\n".join(lines)


# ==========================================
# [로직 B] 2512 PDF 처리 전용
# ==========================================
def process_pdf(client, pdf_path, progress_callback=None):
    try:
        if POPPLER_PATH: pages = convert_from_path(pdf_path, dpi=300, poppler_path=POPPLER_PATH)
        else: pages = convert_from_path(pdf_path, dpi=300)
    except Exception as e: return None, f"오류: PDF 변환 실패 ({e})"
    
    full_text = ""
    prompt = """
    이미지 내용을 Markdown으로 변환(OCR)하세요.
    수식은 LaTeX($$)를 사용하고, 한글은 정확히 보존하세요.
    문항 번호와 구조를 유지하세요.
    """
    total_pages = len(pages)
    for i, page in enumerate(pages):
        if progress_callback: progress_callback(i + 1, total_pages, "변환")
        try:
            img_byte_arr = io.BytesIO()
            page.save(img_byte_arr, format='PNG')
            img_bytes = img_byte_arr.getvalue()
            image_part = types.Part.from_bytes(data=img_bytes, mime_type='image/png')
            response = client.models.generate_content(model='gemini-1.5-flash', contents=[prompt, image_part])
            full_text += f"\n\n--- Page {i+1} ---\n\n" + response.text
            time.sleep(2)
        except Exception as e: full_text += f"\n\n--- Page {i+1} (Error: {e}) ---\n\n"
    return full_text, None

def split_pdf_sections(content):
    sections = re.split(r'\n(?=---\s*Page|\n---\n|\d+\.\s)', content)
    return [s.strip() for s in sections if s.strip()]

def review_pdf_section(client, section_text, section_num):
    """[2512 PDF 전용] JSON 형식 리턴"""
    rule_errors = rule_check_josa(section_text)
    prompt = PROMPT_FOR_PDF.format(section_text=section_text)
    
    try:
        response = client.models.generate_content(model='gemini-1.5-flash', contents=prompt)
        json_str = response.text.strip().replace('```json', '').replace('```', '')
        llm_errors = json.loads(json_str)
        merged = _dedup_errors(rule_errors + (llm_errors or []))
        return {"section": section_num, "errors": merged}
    except json.JSONDecodeError:
        return {"section": section_num, "errors": rule_errors, "parse_error": response.text}
    except Exception as e:
        return {"section": section_num, "errors": rule_errors, "api_error": str(e)}

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
# [화면 1] 메인 페이지 (LaTeX ZIP 자동화)
# ==========================================
def main_page():
    # 상단 헤더 영역 (우측에 2512 버튼 배치)
    col_title, col_btn = st.columns([8, 1])
    with col_title:
        st.title("업무 자동화 (LaTeX ZIP)")
    with col_btn:
        # 우상단 구석 2512 버튼
        if st.button("2512 ▶"):
            navigate_to('2512')
            st.rerun()

    st.markdown("""
    **LaTeX ZIP 자동 정제 및 검토 시스템**입니다.
    1. 변환 프로그램의 **ZIP 파일**을 업로드하세요.
    2. 'Day' 헤더 제거, **[문제+해설]** 자동 그룹화가 수행됩니다.
    3. **표(Table) 형식**의 정밀 리포트를 제공합니다.
    """)

    # 사이드바 (API Key)
    with st.sidebar:
        st.header("⚙️ 설정")
        if 'api_key' not in st.session_state: st.session_state.api_key = DEFAULT_API_KEY
        api_input = st.text_input("Google API Key", value=st.session_state.api_key, type="password")
        st.session_state.api_key = api_input
    
    # ZIP 업로드 및 처리 로직
    uploaded_zip = st.file_uploader("ZIP 파일 업로드 (.zip)", type=["zip"])
    
    if uploaded_zip:
        with st.spinner("ZIP 파일 분석 중..."):
            tex_content, error = extract_tex_from_zip(uploaded_zip)
        
        if error:
            st.error(error)
            st.stop()
            
        st.success("✅ .tex 파일 추출 성공!")
        items = parse_tex_content(tex_content)
        st.info(f"총 {len(items)}개의 문항 세트가 추출되었습니다.")
        
        st.subheader("🔎 문항 전체 보기")
        for idx, item in enumerate(items):
            preview_title = item[:50].replace('\n', ' ') + "..."
            with st.expander(f"문항 {idx+1}: {preview_title}"):
                st.code(item, language='latex')

        if st.button("🚀 AI 학술 감사 시작", type="primary"):
            if not st.session_state.api_key:
                st.error("API Key를 입력해주세요.")
                st.stop()
            
            client = genai.Client(api_key=st.session_state.api_key)
            progress_bar = st.progress(0)
            status_text = st.empty()
            
            all_results = []
            for i, item_text in enumerate(items):
                status_text.text(f"감사관 검토 중... ({i+1}/{len(items)})")
                progress_bar.progress((i + 1) / len(items))
                
                # 재시도 로직
                max_retries = 3; retry_delay = 5
                for attempt in range(max_retries):
                    result = review_tex_section(client, item_text, i + 1)
                    if "api_error" in result and "429" in str(result["api_error"]):
                        if attempt < max_retries - 1:
                            time.sleep(retry_delay); retry_delay *= 2
                            continue
                    all_results.append(result)
                    break
                time.sleep(2) 
            
            report = generate_report_for_tex(all_results)
            st.divider()
            st.subheader("📋 감사 결과 보고서")
            st.markdown(report)
            st.download_button("📥 리포트 다운로드", report, file_name="auditor_report_v8.1.md")
            st.success("완료되었습니다!")

# ==========================================
# [화면 2] 2512 페이지 (Legacy PDF)
# ==========================================
def page_2512():
    if st.button("← 메인으로 돌아가기"):
        navigate_to('main')
        st.rerun()
    st.divider()
    
    st.title("수학 교재 PDF 변환 & 검토 (2512)")
    st.info("기존 PDF OCR 및 JSON 기반 검토 기능입니다.")

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
            if not st.session_state.api_key:
                st.error("❌ API 키를 입력해주세요.")
                st.stop()
            
            client = genai.Client(api_key=st.session_state.api_key)
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
                    converted_text, error = process_pdf(client, tmp_path, update_progress)
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
                        # 2512 전용 검토 함수 사용
                        res = review_pdf_section(client, section, i+1)
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
if st.session_state.current_page == 'main':
    main_page()
elif st.session_state.current_page == '2512':
    page_2512()
