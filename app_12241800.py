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
# [기본 설정]
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
# [PDF → Markdown 변환 함수]
# ==========================================
def process_pdf(client, pdf_path, progress_callback=None):
    """PDF를 이미지로 변환하고 Gemini로 OCR 수행"""
    
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
            
            image_part = types.Part.from_bytes(
                data=img_bytes,
                mime_type='image/png'
            )
            
            response = client.models.generate_content(
                model='gemini-2.5-flash',
                contents=[prompt, image_part]
            )
            
            full_text += f"\n\n--- Page {i+1} ---\n\n" + response.text
            time.sleep(2)
            
        except Exception as e:
            full_text += f"\n\n--- Page {i+1} (Error: {e}) ---\n\n"
    
    return full_text, None

# ==========================================
# [Markdown 검토 함수]
# ==========================================
def split_into_sections(content):
    """문제 단위로 분리"""
    sections = re.split(r'\n(?=---\s*Page|\n---\n|\d+\.\s)', content)
    return [s.strip() for s in sections if s.strip()]

def review_single_section(client, section_text, section_num):
    """단일 섹션 검토"""
    
    prompt = f"""
당신은 대한민국 고등학교 수학 교재 전문 교정자입니다.
아래 텍스트에서 오류를 찾아 보고해주세요.

[검토 기준]
{REVIEW_CRITERIA}

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
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt
        )
        
        json_str = response.text.strip()
        json_str = re.sub(r'^```json\s*', '', json_str)
        json_str = re.sub(r'\s*```$', '', json_str)
        
        errors = json.loads(json_str)
        return {"section": section_num, "errors": errors}
        
    except json.JSONDecodeError:
        return {"section": section_num, "errors": [], "parse_error": response.text}
    except Exception as e:
        return {"section": section_num, "errors": [], "api_error": str(e)}

def review_markdown(client, content, progress_callback=None):
    """Markdown 텍스트 검토"""
    
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
    """검토 결과 보고서 생성"""
    
    report_lines = ["# 📝 검토 보고서\n"]
    total_errors = 0
    high_count = 0
    medium_count = 0
    low_count = 0
    
    for result in results:
        section_num = result["section"]
        errors = result.get("errors", [])
        
        if "parse_error" in result:
            report_lines.append(f"\n## 섹션 {section_num}\n")
            report_lines.append(f"⚠️ JSON 파싱 오류 발생\n")
            continue
        
        if "api_error" in result:
            report_lines.append(f"\n## 섹션 {section_num}\n")
            report_lines.append(f"⚠️ API 오류: {result['api_error']}\n")
            continue
        
        if errors:
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
# [메인 UI]
# ==========================================
st.set_page_config(page_title="수학 교재 PDF 변환 & 검토", layout="wide")

st.title("📄 수학 교재 PDF → Markdown 변환 & 검토")
st.markdown("PDF를 업로드하면 텍스트로 변환하고, 문법/수학적 오류까지 검토합니다.")

# 사이드바 설정
with st.sidebar:
    st.header("⚙️ 설정")
    api_key = st.text_input("Google API Key", value=DEFAULT_API_KEY, type="password")
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
        # API 키 확인
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