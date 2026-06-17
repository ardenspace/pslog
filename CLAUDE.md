# pslog (B2B Task Management & Collaboration Tool)

## Tech Stack
- **Backend**: FastAPI 0.115+, PostgreSQL, SQLAlchemy 2.0+ (async), Alembic, Pydantic v2
- **Frontend**: React 19, TypeScript 5+, Vite, Tailwind CSS, shadcn/ui, React Router v7, TanStack Query, Zustand, Axios
- **Package Manager**: bun

## Core Principles
- DRY: 같은 코드 2번 이상 반복 금지, 공통 로직은 즉시 분리
- 모듈화: 한 파일은 하나의 책임만, 작은 단위로 쪼개서 조합
- 타입 안전성: `any` 사용 금지, API 응답 타입 정의 필수

## Naming Conventions

### Backend (Python)
- 파일/모듈: `snake_case` — 클래스: `PascalCase` — 함수/변수: `snake_case` — 상수: `UPPER_SNAKE_CASE`

### Frontend (TypeScript)
- 컴포넌트 파일: `PascalCase.tsx` — 훅/유틸 파일: `camelCase.ts`
- 컴포넌트: `PascalCase` — 함수/변수: `camelCase` — 상수: `UPPER_SNAKE_CASE` — 타입: `PascalCase`

## Prohibitions
- 라우터에 비즈니스 로직 작성 금지 (Service 레이어 사용)
- 직접 SQL 쿼리 금지 (ORM 사용)
- 컴포넌트에서 직접 API 호출 금지 (커스텀 훅 사용)
- 하드코딩 URL/상수 금지 (constants 파일에 정의)
- 인라인 스타일 금지 (Tailwind 사용)
- props drilling 금지 (Context/Zustand 사용)
- console.log 커밋 금지
