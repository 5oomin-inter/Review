# 파이썬 3.9 사용
FROM python:3.9

# 작업 폴더 설정
WORKDIR /code

# 파일 복사
COPY . .

# 라이브러리 설치
RUN pip install --no-cache-dir --upgrade -r requirements.txt

# ★중요★: 포트를 7860으로 설정하여 실행
CMD ["streamlit", "run", "app_12241800.py", "--server.port", "7860", "--server.address", "0.0.0.0"]
