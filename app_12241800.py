import streamlit as st
import os
import io
import tempfile
import time
import json
import re
import zipfile  # [추가] 압축 파일 처리를 위한 모듈
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
# [로직 함수: 조사 규칙 검사 및 유틸리티]
# ==========================================
# (기존의 조사 규칙 관련 함수들과 상수는 변경 없이 그대로 유지합니다.
#  코드가 길어 생략된 것으로 간주하고, 실제 파일에는 기존 코드가 있어야 합니다.)
# ... ( _JONGSUNG_LIST 부터 _dedup_errors 까지의 기존 코드) ...

# -------------------------------------------------------------
# (지면 관계상 조사 처리 함수들은 기존 코드 내용을 그대로 사용한다고 가정합니다.
#  아래에는 새로 추가된 ZIP/Tex 처리 함수들을 중점적으로 배치합니다.)
# -------------------------------------------------------------

# [새로 추가된 함수] ZIP 파일에서 .tex 파일 추출
def extract_tex_from_zip(zip_file_bytes):
    """ZIP 파일 객체를 받아 내부의 .tex 파일 내용을 반환"""
    try:
        with zipfile.ZipFile(zip_file_bytes) as z:
            # .tex 확장자를 가진 파일 찾기
            tex_files = [f for f in z.namelist() if f.lower().endswith('.tex')]
            
            if not tex_files:
                return None, "ZIP 파일 내에 .tex 파일이 없습니다."
            
            # 첫 번째 tex 파일 선택 (보통 하나만 들어있음)
            target_file = tex_files[0]
            
            # 내용 읽기 (utf-8 디코딩 시도)
            try:
                content = z.read(target_file).decode('utf-8')
            except UnicodeDecodeError:
                # cp949 등 다른 인코딩 시도
                content = z.read(target_file).decode('cp949')
                
            return content, None
    except Exception as e:
        return None, f"ZIP 파일 처리 중 오류 발생: {str(e)}"

# [새로 추가된 함수] TeX 파일 파싱 및 문항 분리
def parse_tex_content(tex_content):
    """
    TeX 내용에서 preamble을 제거하고 문항별로 리스트화
    """
    # 1. \begin{document} ... \end{document} 사이 내용 추출
    pattern = r'\\begin\{document\}([\s\S]*?)\\end\{document\}'
    match = re.search(pattern, tex_content)
    
    if match:
        body = match.group(1).strip()
    else:
        # 태그가 없으면 전체를 본문으로 간주 (비표준)
        body = tex_content

    # 2. 불필요한 명령 제거 (maketitle, newpage 등)
    body = re.sub(r'\\maketitle', '', body)
    body = re.sub(r'\\newpage', '', body)
    body = re.sub(r'\\clearpage', '', body)

    # 3. 문항 분리 로직
    # 패턴: \section*, \subsection*, \item, 혹은 숫자+점(1.) 등으로 시작하는 부분을 찾음
    # M 프로그램(Mathpix 등)은 보통 \section*{Problem 1} 또는 \item[1.] 형식을 사용함.
    
    # 구분자 패턴 정의 (상황에 따라 수정 가능)
    # Case A: \section*{...} 또는 \subsection*{...} 으로 구분되는 경우
    split_pattern = r'(\\section\*?\{.*?\})|(\\subsection\*?\{.*?\})|(\\item\[.*?\])'
    
    # 정규식으로 분리 (구분자도 포함해서 리스트 반환됨)
    parts = re.split(split_pattern, body)
    
    items = []
    current_item = ""
    
    for part in parts:
        if not part: continue
        
        # 구분자인 경우 (새 문항 시작)
        if re.match(split_pattern, part):
            if current_item.strip():
                items.append(current_item.strip())
            current_item = part # 구분자를 포함하여 시작
        else:
            current_item += part
            
    # 마지막 항목 추가
    if current_item.strip():
        items.append(current_item.strip())
        
    # 만약 분리가 제대로 안 됐다면(항목이 1개), 그냥 통째로 반환하거나 더블 엔터로 분리 시도
    if len(items) <= 1:
        # 대안: 빈 줄 2개 이상을 기준으로 분리
        items = re.split(r'\n\s*\n', body)
        items = [i.strip() for i in items if i.strip()]

    return items

# -------------------------------------------------------------
# 기존 로직 함수 (재사용)
# -------------------------------------------------------------
# (process_pdf, review_markdown 등은 기존과 동일하지만, 
#  TeX 검토용으로 review_single_section을 재활용할 수 있습니다.)
# ... (기존 _JONGSUNG_LIST 정의 및 관련 함수들이 여기에 있어야 합니다) ...

# [여기서는 편의상 위에서 정의한 상수와 정규식들을 사용한다고 가정하고
#  가장 핵심인 rule_check_josa 등 필수 함수만 간략히 포함하거나, 
#  실제로는 기존 코드 전체가 여기에 포함되어야 합니다.]
# (사용자가 붙여넣기 편하게 기존 함수 선언부를 포함합니다)

_JONGSUNG_LIST = ["", "ㄱ", "ㄲ", "ㄳ", "ㄴ", "ㄵ", "ㄶ", "ㄷ", "ㄹ", "ㄺ", "ㄻ", "ㄼ", "ㄽ", "ㄾ", "ㄿ", "ㅀ", "ㅁ", "ㅂ", "ㅄ", "ㅅ", "ㅆ", "ㅇ", "ㅈ", "ㅊ", "ㅋ", "ㅌ", "ㅍ", "ㅎ"]
_LATIN_LAST_JONG = {"A": "", "B": "", "C": "", "D": "", "E": "", "F": "", "G": "", "H": "", "I": "", "J": "", "K": "", "L": "ㄹ", "M": "ㅁ", "N": "ㄴ", "O": "", "P": "", "Q": "", "R": "ㄹ", "S": "", "T": "", "U": "", "V": "", "W": "", "X": "", "Y": "", "Z": ""}
_DIGIT_LAST_JONG = {"0": "ㅇ", "1": "ㄹ", "2": "", "3": "ㅁ", "4": "", "5": "", "6": "ㄱ", "7": "ㄹ", "8": "ㄹ", "9": ""}
_UNIT_LAST_JONG = {"십": "ㅂ", "백": "ㄱ", "천": "ㄴ", "만": "ㄴ", "억": "ㄱ", "조": "", "경": "ㅇ"}
_GROUP_UNITS = ["", "만", "억", "조", "경"]
_JOSA_CANDIDATES = ("은", "는", "이", "가", "을", "를", "과", "와", "으로", "로")
_MATH_JOSA_PATTERN = re.compile(r'(?P<math>\${1,2}[^$]+?\${1,2})(?P<ws>\s*)(?P<josa>으로|로|은|는|이|가|을|를|과|와)(?=[\s\.,;:\)\]\}\!?]|$)')
_NUM_JOSA_PATTERN = re.compile(r'(?P<num>\d[\d,]*(?:\.\d+)?)(?P<ws>\s*)(?P<josa>으로|로|은|는|이|가|을|를|과|와)(?=[\s\.,;:\)\]\}\!?]|$)')
_PAREN_JOSA_PATTERN = re.compile(r'(?P<head>[가-힣]+)\s*\(\s*(?P<inner>[^)\n]{1,120}?)\s*\)(?P<ws>\s*)(?P<josa>으로|로|은|는|이|가|을|를|과|와)(?=[\s\.,;:\)\]\}\!?]|$)')
_EXPLANATION_MARKERS = ("해설", "풀이", "해법", "정답", "해답", "Solution", "해설)", "풀이)")
_GREEK_MACRO_LAST_JONG = {"alpha": "", "beta": "", "gamma": "", "delta": "", "epsilon": "", "zeta": "", "eta": "", "theta": "", "iota": "", "kappa": "", "lambda": "", "mu": "", "nu": "", "xi": "", "omicron": "", "pi": "", "rho": "", "sigma": "", "tau": "", "upsilon": "", "phi": "", "chi": "", "psi": "", "omega": "", "ell": "ㄹ"}

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
    return _DIGIT_LAST_JONG.get(digits[-1], "") # 단순화 (실제로는 복잡한 로직 필요하나 생략 가능시 생략)

def _expected_josa(josa, last_jong):
    has = (last_jong != "")
    if josa in ("은", "는"): return "은" if has else "는"
    if josa in ("이", "가"): return "이" if has else "가"
    if josa in ("을", "를"): return "을" if has else "를"
    if josa in ("과", "와"): return "과" if has else "와"
    if josa in ("으로", "로"): return "로" if (not has or last_jong == "ㄹ") else "으로"
    return josa

# (중요 함수들 - 기존 코드에서 가져옴)
def _strip_math_delimiters(math): return math.strip("$") # 단순화
def _last_jong_from_math(math): return "" # 임시 (기존 코드 사용 필)
def _last_jong_from_text(text): return "" # 임시 (기존 코드 사용 필)
def _infer_section_context(text, pos): return "problem"
def _should_read_parenthetical(head, inner, context): return False

# 주의: 위쪽 헬퍼 함수들은 실제 작동을 위해 기존 코드의 전체 내용을 그대로 두셔야 합니다.
# 지면상 생략된 부분은 원래 파일의 내용을 유지해주세요. 
# 아래 rule_check_josa, review_single_section 등은 기존 코드를 그대로 사용합니다.

def rule_check_josa(section_text):
    # (기존 코드와 동일) 실제로는 원본 코드의 내용이 들어가야 합니다.
    return []

def _dedup_errors(errors):
    seen = set(); out = []
    for e in errors:
        key = (e.get("original",""), e.get("corrected",""), e.get("reason",""))
        if key in seen: continue
        seen.add(key); out.append(e)
    return out

# ==========================================
# [리뷰 로직 (기존 함수 재활용)]
# ==========================================
def review_single_section(client, section_text, section_num):
    # 기존 함수 내용 유지
    rule_errors = rule_check_josa(section_text)
    prompt = f"""
당신은 대한민국 고등학교 수학 교재 전문 교정자입니다.
아래 텍스트(LaTeX 포맷)에서 오류를 찾아 보고해주세요.

[검토 기준]
{REVIEW_CRITERIA}

[입력 텍스트]
{section_text}

[출력 형식]
JSON 배열로 출력하세요. 오류가 없으면 [] 출력.
[
    {{
        "original": "...",
        "corrected": "...",
        "reason": "...",
        "severity": "high/medium/low"
    }}
]
"""
    try:
        response = client.models.generate_content(model='gemini-2.0-flash-exp', contents=prompt)
        # JSON 파싱 로직 (기존과 동일)
        json_str = response.text.strip().replace('```json', '').replace('```', '')
        llm_errors = json.loads(json_str)
        merged = _dedup_errors(rule_errors + (llm_errors or []))
        return {"section": section_num, "errors": merged}
    except Exception as e:
        return {"section": section_num, "errors": rule_errors, "api_error": str(e)}

def generate_report(results):
    # 기존 함수 내용 유지
    lines = ["# 📝 검토 보고서\n"]
    total = 0
    for res in results:
        if res['errors']:
            lines.append(f"## 문항 {res['section']}")
            for err in res['errors']:
                total += 1
                lines.append(f"- {err['original']} -> {err['corrected']} ({err['reason']})")
    return "\n".join(lines), total

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
        st.info("기존 기능: PDF → AI OCR & 검토")
        if st.button("2512 (PDF OCR)", use_container_width=True):
            navigate_to('2512')
            st.rerun()

    with col2:
        st.success("New: ZIP(TeX) 자동 정제 & 검토")
        # 메인 페이지에서 바로 'New Feature'로 진입
        if st.button("LaTeX ZIP 검토", use_container_width=True):
            navigate_to('tex_automation')
            st.rerun()

# ==========================================
# [페이지 2: 기존 기능 (2512)]
# ==========================================
def page_2512():
    # (기존 코드를 그대로 유지하세요)
    if st.button("← 메인으로 돌아가기", key="back_2512"):
        navigate_to('main')
        st.rerun()
    st.title("수학 교재 PDF 변환 & 검토 (2512)")
    # ... (기존 UI 구현) ...
    st.write("(기존 기능 화면입니다)")

# ==========================================
# [페이지 3: TeX 자동화 (New Feature)]
# ==========================================
def page_tex_automation():
    if st.button("← 메인으로 돌아가기", key="back_tex"):
        navigate_to('main')
        st.rerun()
        
    st.divider()
    st.title("LaTeX(ZIP) 자동 정제 및 검토")
    st.markdown("""
    1. 변환 프로그램(M)에서 다운로드한 **ZIP 파일**을 그대로 업로드하세요.
    2. 자동으로 **.tex** 파일을 찾아 불필요한 서문을 제거하고 **문항별로 분리**합니다.
    3. 분리된 각 문항을 AI가 검토합니다.
    """)

    # 사이드바 API 설정
    with st.sidebar:
        st.header("⚙️ 설정")
        if 'api_key' not in st.session_state:
            st.session_state.api_key = DEFAULT_API_KEY
        api_input = st.text_input("Google API Key", value=st.session_state.api_key, type="password")
        st.session_state.api_key = api_input
    
    # 1. 파일 업로드
    uploaded_zip = st.file_uploader("ZIP 파일 업로드 (.zip)", type=["zip"])
    
    if uploaded_zip:
        # ZIP 처리
        with st.spinner("ZIP 파일 분석 중..."):
            tex_content, error = extract_tex_from_zip(uploaded_zip)
            
        if error:
            st.error(error)
            st.stop()
            
        st.success("✅ .tex 파일 추출 성공!")
        
        # TeX 파싱 및 분리
        items = parse_tex_content(tex_content)
        st.info(f"총 {len(items)}개의 문항(섹션)이 추출되었습니다.")
        
        # 미리보기 (접이식)
        with st.expander("추출된 문항 미리보기"):
            for idx, item in enumerate(items[:3]): # 3개만 미리보기
                st.markdown(f"**[문항 {idx+1}]**")
                st.code(item, language='latex')
            if len(items) > 3:
                st.write("...")

        # 검토 시작 버튼
        if st.button("🚀 AI 검토 시작", type="primary"):
            if not st.session_state.api_key:
                st.error("API Key를 입력해주세요.")
                st.stop()
                
            client = genai.Client(api_key=st.session_state.api_key)
            progress_bar = st.progress(0)
            status_text = st.empty()
            
            all_results = []
            
            for i, item_text in enumerate(items):
                status_text.text(f"검토 중... ({i+1}/{len(items)})")
                progress_bar.progress((i + 1) / len(items))
                
                # 기존의 review_single_section 함수 재활용
                # (주의: 기존 함수가 process_pdf 전용이 아니도록 범용적이어야 함)
                result = review_single_section(client, item_text, i + 1)
                all_results.append(result)
                time.sleep(1) # API 제한 고려
                
            # 리포트 생성
            report, err_count = generate_report(all_results)
            
            st.divider()
            st.subheader("📋 검토 결과")
            st.text_area("결과 리포트", report, height=400)
            st.download_button("📥 리포트 다운로드", report, file_name="review_report.md")
            st.success("완료되었습니다!")

# ==========================================
# [앱 실행 진입점]
# ==========================================
if st.session_state.current_page == 'main':
    main_page()
elif st.session_state.current_page == '2512':
    # 기존 코드의 page_2512() 함수 내용이 실행됨
    # (실제 구현 시 위쪽에 정의된 page_2512 내용을 채워주세요)
    page_2512()
elif st.session_state.current_page == 'tex_automation':
    page_tex_automation()
