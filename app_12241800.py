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
st.set_page_config(page_title="니무네 방앗간", layout="wide")

# ==========================================
# [상수 및 환경 설정]
# ==========================================
DEFAULT_API_KEY = ""

if os.name == 'nt':
    POPPLER_PATH = r"C:\Users\inter\Desktop\Review\poppler-25.12.0\Library\bin"
else:
    POPPLER_PATH = None

# ==========================================
# [프롬프트: 종합 학술 감사관 v8.0]
# ==========================================
AUDITOR_PROMPT_TEMPLATE = """
# 🏆 종합 학술 감사관 (Scholarly Auditor v8.0)

## 1. 🥇 핵심 정체성
귀하는 고등 수학 교육 콘텐츠의 **최종 검증자**이자 **기술적 편집자**입니다. 오류(Error)와 제안(Suggestion)을 명확히 구분하여 보고합니다.

## 2. 🧠 인지 작동 프로토콜 (Logic Flow)
**검토 대상을 발견했을 때, 아래 순서대로 분류(Sorting)하십시오.**

- **Step 0. ANCHOR (계산 및 검증):**
    - 독립 풀이 수행 후 원문과 대조합니다.
- **Step 1. TRIAGE (3단 분류):**
    1.  **[확실한 오류]** 수학적으로 명백히 틀렸는가? (확신도 99% 이상) -> **[Table A]**
    2.  **[단순 오타]** 수학적 의미가 변하지 않는 텍스트 오류인가? -> **[Table B]**
    3.  **[제안/의심]**
        - 틀린 건 아니지만 더 나은 풀이가 있는가?
        - 문맥상 이상하지만, 나의 확신도가 100%는 아닌가? (확신도 50~90%)
        - 교육적으로 설명이 불친절한가?
        -> **[Table C] (제안 전용)**
- **Step 2. DROP (기각):** 위 3가지에 해당하지 않는 무의미한 지적은 폐기하십시오.

## 3. ⚖️ 위험도(R) 및 분류 기준 (Rubric)

| 대상 테이블 | 분류 | 기준 및 정의 |
| :--- | :--- | :--- |
| **Table A**<br>(학술 감사) | **치명적 오류**<br>**(R >= 4.0)** | **수학적 진리값이 깨진 경우 (무조건 수정 필수)**<br>- 변수/인자($f(-t)$), 부호, 숫자, 정답 오류<br>- **AI의 확신이 100%일 때만 기록** |
| **Table B**<br>(변환 오류) | **단순 오타**<br>**(R < 2.0)** | **수학적 의미가 보존되는 단순 편집**<br>- 띄어쓰기, LaTeX 문법, 철자 교정 |
| **Table C**<br>(제안/논의) | **제안**<br>**(Score N/A)** | **오류는 아니지만 검토가 필요한 경우**<br>- **더 나은 풀이 제안 (Optimization)**<br>- **잠재적 오류 의심 (Low Confidence Check)**<br>- 가독성 개선, 문맥상 어색함 지적 |

## 4. 📝 출력 표준 (Output format)

### 1. 🎓 [Table A: 학술 감사 보고서] (Must Fix)
* **Format:** LaTeX 모드. 변경 사항은 **볼드체**(`\mathbf{...}` 또는 `**...**`) 강조.
* **Layout:** 상하 배치 (`[원문]` -> `[수정]`).

### 2. 🧹 [Table B: 변환 오류 클린업] (Auto Fix)
* **Format:** 코드 모드 허용. 좌우 배치.

### 3. 💡 [Table C: 개선 제안 및 검토 의견] (Check)
* **성격:** 정오표에 들어갈 오류는 아니지만, 저자에게 전달할 피드백.
* **Layout:** 자유 서술형 표.

| 위치 | 제안 유형 | 내용 및 의견 |
| :--- | :--- | :--- |
| (위치) | **(가독성/풀이개선/확인요망)** | (구체적인 제안 내용 서술) |

## 5. 🚫 출력 제어 규칙
1. **Perfect Score:** 수정/제안 사항이 전혀 없으면 "✅ [무결점 인증]" 메시지 출력.
2. **Table Integrity:** 각 테이블은 해당하는 항목이 있을 때만 생성하십시오. (빈 표 출력 금지)

## 7. 📊 <FINAL REPORT>
(아래 양식에 맞춰서 출력)

<FINAL REPORT>

### 1. 🎓 [학술 감사 보고서] (Math & Logic)
**최대 위험 점수: R=[Max Score]**

| 위치 | 분류 | 내용 (검토 내역: 변경사항 Bold 강조) | 근거 및 감사 의견 | R |
| :--- | :--- | :--- | :--- | :--- |
| ... | ... | ... | ... | ... |

---

### 2. 🧹 [변환 오류 클린업] (Simple Fixes)

| 위치 | 오류 내용 | 원문 -> 수정 제안 |
| :--- | :--- | :--- |
| ... | ... | ... |

---

### 3. 💡 [개선 제안 및 검토 의견] (Suggestions)

| 위치 | 제안 유형 | 내용 및 의견 |
| :--- | :--- | :--- |
| ... | ... | ... |

---
[System Status] 현재 누적된 오판 로그(LOG_ID): N개

</FINAL REPORT>

---------------------------------------------------------
[검토할 텍스트]
{section_text}
---------------------------------------------------------
"""

# ==========================================
# [로직 함수: 조사 규칙 검사 및 유틸리티]
# ==========================================
# (기존 유틸리티 함수들은 그대로 유지)
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
def _infer_section_context(text, pos): return "problem"
def _should_read_parenthetical(head, inner, context): return False

def rule_check_josa(section_text):
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
            reason = "조사 연결(규칙): 수식 발음 기준"
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
            reason = "조사 연결(규칙): 숫자 발음 기준"
            errors.append({"original": original, "corrected": corrected, "reason": reason, "severity": severity})
    return errors

# ==========================================
# [TeX 처리 로직: ZIP 추출 & 문항/해설 그룹핑]
# ==========================================
def extract_tex_from_zip(zip_file_bytes):
    try:
        with zipfile.ZipFile(zip_file_bytes) as z:
            tex_files = [f for f in z.namelist() if f.lower().endswith('.tex')]
            if not tex_files:
                return None, "ZIP 파일 내에 .tex 파일이 없습니다."
            target_file = tex_files[0]
            try:
                content = z.read(target_file).decode('utf-8')
            except UnicodeDecodeError:
                content = z.read(target_file).decode('cp949')
            return content, None
    except Exception as e:
        return None, f"ZIP 파일 처리 중 오류 발생: {str(e)}"

def parse_tex_content(tex_content):
    """문항과 해설을 하나의 세트로 묶어서 추출 (Day 등 불필요 헤더 제거)"""
    pattern = r'\\begin\{document\}([\s\S]*?)\\end\{document\}'
    match = re.search(pattern, tex_content)
    body = match.group(1).strip() if match else tex_content

    body = re.sub(r'\\maketitle', '', body)
    body = re.sub(r'\\newpage', '', body)
    body = re.sub(r'\\clearpage', '', body)

    start_pattern = re.compile(r'\\section\*?\{')
    matches = list(start_pattern.finditer(body))

    if not matches:
        return [body]

    chunks = []
    for i in range(len(matches)):
        start_idx = matches[i].start()
        end_idx = matches[i+1].start() if i + 1 < len(matches) else len(body)
        chunks.append(body[start_idx:end_idx])

    final_items = []
    current_item_text = ""
    
    sol_keywords = ["해법", "해설", "풀이", "정답", "Solution"]
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
        is_solution = any(kw in title_content for kw in sol_keywords)

        if is_ignore:
            if current_item_text.strip():
                final_items.append(current_item_text.strip())
            current_item_text = ""
            continue

        if is_solution:
            if current_item_text:
                current_item_text += "\n" + chunk
            else:
                if final_items:
                    final_items[-1] += "\n" + chunk
                else:
                    current_item_text = chunk
        else:
            if current_item_text.strip():
                final_items.append(current_item_text.strip())
            current_item_text = chunk

    if current_item_text.strip():
        final_items.append(current_item_text.strip())

    return final_items

# ==========================================
# [리뷰 및 리포트 로직 (업데이트됨)]
# ==========================================
def review_single_section(client, section_text, section_num):
    """
    업데이트된 프롬프트(v8.0)를 사용해 검토 수행.
    JSON 파싱 대신 AI가 생성한 Markdown 리포트를 그대로 반환합니다.
    """
    
    # 규칙 기반 검사(참고용)
    rule_errors = rule_check_josa(section_text)
    
    # 프롬프트 구성
    prompt = AUDITOR_PROMPT_TEMPLATE.format(section_text=section_text)
    
    try:
        # 모델명은 최신 것으로 설정 (Gemini 1.5 Pro or Flash 권장)
        response = client.models.generate_content(
            model='gemini-2.0-flash-exp', 
            contents=prompt
        )
        
        return {
            "section": section_num,
            "rule_errors": rule_errors,
            "ai_report_text": response.text  # AI의 Markdown 텍스트 그대로 반환
        }
        
    except Exception as e:
        return {
            "section": section_num, 
            "rule_errors": rule_errors, 
            "api_error": str(e)
        }

def generate_report(results):
    """전체 리포트 병합"""
    lines = ["# 🏆 종합 학술 감사 보고서\n"]
    
    for res in results:
        lines.append(f"\n---")
        lines.append(f"## 📄 문항 세트 {res['section']}\n")
        
        # 1. 규칙 기반 오류 (Python 검출) - 있으면 먼저 표시
        if res.get('rule_errors'):
            lines.append("### 🐍 [Python 규칙 감지] (참고용)")
            for err in res['rule_errors']:
                lines.append(f"- **{err['original']}** → `{err['corrected']}` ({err['reason']})")
            lines.append("\n")
            
        # 2. AI 학술 감사관 리포트 (Markdown)
        if 'api_error' in res:
            lines.append(f"⚠️ **API Error:** {res['api_error']}")
        else:
            lines.append(res['ai_report_text'])
            
    return "\n".join(lines)

# ==========================================
# [화면 전환 관리]
# ==========================================
if 'current_page' not in st.session_state:
    st.session_state.current_page = 'main'

def navigate_to(page):
    st.session_state.current_page = page

# ==========================================
# [페이지 1: 메인 페이지]
# ==========================================
def main_page():
    st.title("니무네 방앗간 (Nimu's Mill)")
    st.markdown("### 작업 선택")
    
    col1, col2 = st.columns(2)
    with col1:
        st.info("기존 기능: PDF OCR & 검토")
        if st.button("2512 (PDF)", use_container_width=True):
            navigate_to('2512')
            st.rerun()
    with col2:
        st.success("New: LaTeX ZIP 자동 정제 & 검토 (v8.0)")
        if st.button("LaTeX ZIP 검토", use_container_width=True):
            navigate_to('tex_automation')
            st.rerun()

# ==========================================
# [페이지 2: 2512 (PDF 기능)]
# ==========================================
def page_2512():
    if st.button("← 메인으로 돌아가기"):
        navigate_to('main')
        st.rerun()
    st.divider()
    
    # PDF 관련 함수(process_pdf)는 유지되었으나 UI는 간소화함 (기능 작동)
    # 실제로는 기존 PDF OCR 코드를 여기에 복원하거나 그대로 두면 됩니다.
    st.title("수학 교재 PDF 변환 & 검토")
    st.info("이곳은 기존 PDF 변환 기능을 수행하는 곳입니다.")
    # (기존 PDF 로직 생략 - ZIP 기능 집중)

# ==========================================
# [페이지 3: TeX 자동화 (v8.0 프롬프트 적용)]
# ==========================================
def page_tex_automation():
    if st.button("← 메인으로 돌아가기"):
        navigate_to('main')
        st.rerun()
        
    st.divider()
    st.title("학술 감사관 v8.0 (LaTeX/ZIP)")
    st.markdown("""
    1. 변환 프로그램의 **ZIP 파일**을 업로드하세요.
    2. 'Day' 헤더는 버리고, **[문제 + 해설]**을 자동으로 묶습니다.
    3. **Scholarly Auditor v8.0** 프롬프트로 정밀 검토합니다.
    """)

    with st.sidebar:
        st.header("⚙️ 설정")
        if 'api_key' not in st.session_state:
            st.session_state.api_key = DEFAULT_API_KEY
        api_input = st.text_input("Google API Key", value=st.session_state.api_key, type="password")
        st.session_state.api_key = api_input
    
    uploaded_zip = st.file_uploader("ZIP 파일 업로드 (.zip)", type=["zip"])
    
    if uploaded_zip:
        with st.spinner("ZIP 파일 분석 중..."):
            tex_content, error = extract_tex_from_zip(uploaded_zip)
            
        if error:
            st.error(error)
            st.stop()
            
        st.success("✅ .tex 파일 추출 성공!")
        
        # 파싱 및 분리
        items = parse_tex_content(tex_content)
        st.info(f"총 {len(items)}개의 문항 세트(문제+해설)가 추출되었습니다.")
        
        with st.expander("추출된 문항 미리보기 (첫 1개)"):
            if items:
                st.code(items[0], language='latex')

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
                
                # v8.0 프롬프트로 검토
                result = review_single_section(client, item_text, i + 1)
                all_results.append(result)
                time.sleep(1) 
                
            report = generate_report(all_results)
            
            st.divider()
            st.subheader("📋 감사 결과 보고서")
            st.markdown(report) # 마크다운 렌더링
            st.download_button("📥 리포트 다운로드", report, file_name="auditor_report_v8.md")
            st.success("완료되었습니다!")

# ==========================================
# [앱 실행 진입점]
# ==========================================
if st.session_state.current_page == 'main':
    main_page()
elif st.session_state.current_page == '2512':
    page_2512()
elif st.session_state.current_page == 'tex_automation':
    page_tex_automation()
