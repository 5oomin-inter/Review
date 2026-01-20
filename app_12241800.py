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
def _should_read_parenthetical(head, inner, context
