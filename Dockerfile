# 1. Python 3.12 기반 슬림 이미지 사용
FROM python:3.12-slim

# 2. 작업 디렉토리 생성
WORKDIR /app

# 3. requirements.txt 먼저 복사하고 설치
COPY requirements.txt .
COPY app/agent/requirements.txt ./agent_requirements.txt
RUN pip install --no-cache-dir -r agent_requirements.txt && \
    pip install --no-cache-dir -r requirements.txt

# 4. 전체 코드 복사
COPY . .

# 5. 컨테이너가 열 포트 설정
EXPOSE 8000

# 6. FastAPI 실행
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
