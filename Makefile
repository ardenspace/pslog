# pslog dev 환경 자동화
# app-chak (운영 서버, 8000/5432) 는 절대 안 건드림 — 모든 target 은 pslog 자원만 대상.
#
# 두 가지 실행 모드:
#  (1) always-on / production-ish:  `make up`           (docker compose: postgres + backend)
#  (2) dev iteration (hot reload):  `make db-up` + `make backend`  (compose postgres + venv uvicorn)
# 한 시점에 backend 는 둘 중 하나만 — 8081 포트 충돌.

PG_USER := pslog
PG_PASSWORD := pslog123
PG_DB := pslog
PG_PORT ?= 5433
BACKEND_PORT ?= 8081

BACKEND_HOST := http://localhost:$(BACKEND_PORT)
BACKEND_API_URL := $(BACKEND_HOST)/api/v1

VENV := backend/venv
VENV_PIP := $(VENV)/bin/pip
VENV_ALEMBIC := $(VENV)/bin/alembic
VENV_PYTEST := $(VENV)/bin/pytest
VENV_UVICORN := $(VENV)/bin/uvicorn

COMPOSE := docker compose

.PHONY: help setup env venv up down restart logs ps backend frontend db-up db-down stop clean migrate test test-backend test-frontend

help:
	@echo "pslog dev targets:"
	@echo "  make setup        # 첫 setup: venv + deps + .env + .env.local + db-up + migrate"
	@echo ""
	@echo "Always-on (compose):"
	@echo "  make up           # postgres + backend 컨테이너 기동 (alembic 자동 적용)"
	@echo "  make down         # 컨테이너 stop+remove (volume 보존)"
	@echo "  make restart      # backend 만 rebuild + 재기동"
	@echo "  make logs         # backend 로그 follow"
	@echo "  make ps           # compose 서비스 상태"
	@echo ""
	@echo "Dev iteration:"
	@echo "  make db-up        # postgres 컨테이너만 기동 ($(PG_PORT))"
	@echo "  make db-down      # postgres stop"
	@echo "  make backend      # venv uvicorn --reload ($(BACKEND_PORT)) — db-up 선행 필요"
	@echo "  make frontend     # vite (5173 -> backend $(BACKEND_PORT))"
	@echo ""
	@echo "Misc:"
	@echo "  make migrate      # alembic upgrade head (host venv 기준)"
	@echo "  make test         # backend pytest + frontend build/lint"
	@echo "  make clean        # 컨테이너 + volume 삭제 (데이터 날아감)"

# 첫 setup — 한 번만
setup: env venv db-up migrate
	cd frontend && bun install
	@echo ""
	@echo "✓ setup 완료. 'make up' (always-on) 또는 'make backend'+'make frontend' (dev)."

env:
	@if [ ! -f backend/.env ]; then \
		echo "DATABASE_URL=postgresql+asyncpg://$(PG_USER):$(PG_PASSWORD)@localhost:$(PG_PORT)/$(PG_DB)" > backend/.env; \
		echo "SECRET_KEY=$$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')" >> backend/.env; \
		echo "pslog_FERNET_KEY=$$(python3 -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')" >> backend/.env; \
		echo "pslog_PUBLIC_URL=$(BACKEND_HOST)" >> backend/.env; \
		echo "ALLOWED_ORIGINS=http://localhost:5173" >> backend/.env; \
		echo "✓ backend/.env 생성 (SECRET_KEY / pslog_FERNET_KEY 자동 생성)"; \
	else \
		echo "ℹ backend/.env 이미 있음 — 보존"; \
	fi
	@if [ ! -f frontend/.env.local ]; then \
		echo "VITE_API_URL=$(BACKEND_API_URL)" > frontend/.env.local; \
		echo "✓ frontend/.env.local 생성"; \
	else \
		echo "ℹ frontend/.env.local 이미 있음 — 보존"; \
	fi

venv:
	@if [ ! -d $(VENV) ]; then \
		python3.12 -m venv $(VENV); \
		echo "✓ venv 생성"; \
	fi
	@$(VENV_PIP) install --quiet --upgrade pip
	@$(VENV_PIP) install --quiet -r backend/requirements-dev.txt
	@echo "✓ backend deps 설치"

# Always-on stack
up:
	$(COMPOSE) up -d --build
	@echo "✓ stack up — http://localhost:$(BACKEND_PORT)"

down:
	$(COMPOSE) down
	@echo "✓ stack down (volume 보존)"

restart:
	$(COMPOSE) up -d --build backend
	@echo "✓ backend 재빌드/재기동"

logs:
	$(COMPOSE) logs -f --tail=100 backend

ps:
	$(COMPOSE) ps

# Dev iteration (host venv 사용)
backend:
	$(VENV_UVICORN) --app-dir backend app.main:app --reload --port $(BACKEND_PORT)

frontend:
	cd frontend && bun run dev

db-up:
	$(COMPOSE) up -d postgres
	@echo "✓ postgres ready ($(PG_PORT))"

db-down:
	$(COMPOSE) stop postgres
	@echo "✓ postgres stopped"

stop: down
	@echo "ℹ frontend(vite) / dev backend 는 ctrl-c 로 직접 종료"

clean:
	$(COMPOSE) down -v
	@echo "✓ 컨테이너 + volume 삭제됨 (데이터 날아감 — 백업 확인했는지 ?)"

migrate:
	cd backend && ../$(VENV_ALEMBIC) upgrade head

test: test-backend test-frontend

test-backend:
	cd backend && ../$(VENV_PYTEST) -q

test-frontend:
	cd frontend && bun run build
	-cd frontend && bun run lint
