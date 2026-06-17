# pslog backend — production-ish image (always-on / docker compose 용)
# dev iteration 은 venv + `make backend` 로 hot reload (Makefile 참조).

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# 시스템 의존성 — psycopg/asyncpg 빌드용 최소 set
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# 의존성 먼저 설치 — 코드 변경 시 캐시 적중률 높임
COPY requirements.txt ./
RUN pip install -r requirements.txt

# 애플리케이션 + alembic
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./

EXPOSE 8081

# entrypoint 가 alembic upgrade head → uvicorn 순으로 실행 (compose 가 마운트)
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8081"]
