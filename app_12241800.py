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
def _latex_extract_last_atom(latex): return "" # 실제로는 위쪽의 복잡한 로직이 필요하나 생략시 오류 가능성 있음. 기존 코드 유지 권장.
def _last_jong_from_math(math): return "" 
def _last_jong_from_text(text): return "" 
def _infer_section_context(text, pos): return "problem"
def _should_read_parenthetical(head, inner, context): return False

# 중요: 이 코드 블록에서는 지면상 생략했지만, 실제 구동을 위해선 
# `_latex_extract_last_atom` 등 기존의 모든 헬퍼 함수가 온전히 있어야 합니다.
# (사용자분이 기존 파일 내용을 보존하면서 parse_tex_content만 교체하길 원하시면 아래 함수만 보시면 됩니다.)

def rule_check_josa(section_text):
    # 실제 구현은 기존 코드 내용과 동일해야 함
    return []

def _dedup_errors(errors):
    seen = set(); out = []
    for e in errors:
        key = (e.get("original",""), e.get("corrected",""), e.get("reason",""))
        if key in seen: continue
        seen.add(key); out.append(e)
    return out

# ==========================================
# [TeX 처리 로직 (핵심 수정 부분)]
# ==========================================
def extract_tex_from_zip(zip_file_bytes):
    """ZIP 파일 객체를 받아 내부의 .tex 파일 내용을 반환"""
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
    """
    TeX 내용에서 문항과 해설을 하나의 세트로 묶어서 추출
    - 'Day' 헤더는 무시
    - '해법', '해설' 등은 앞선 문제에 병합
    """
    # 1. \begin{document} ... \end{document} 추출
    pattern = r'\\begin\{document\}([\s\S]*?)\\end\{document\}'
    match = re.search(pattern, tex_content)
    body = match.group(1).strip() if match else tex_content

    # 2. 불필요 명령 제거
    body = re.sub(r'\\maketitle', '', body)
    body = re.sub(r'\\newpage', '', body)
    body = re.sub(r'\\clearpage', '', body)

    # 3. \section으로 시작하는 청크들의 시작 위치 찾기
    # 정규식으로 찾되, 내용은 나중에 파싱
    start_pattern = re.compile
