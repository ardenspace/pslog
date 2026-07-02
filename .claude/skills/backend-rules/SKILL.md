---
name: backend-rules
description: pslog backend(FastAPI/SQLAlchemy) 코드를 작성하거나 수정할 때 반드시 사용. 라우터→서비스 분리, 응답/에러 표준, DB/보안 규칙을 담는다.
---

# Backend Development Rules

Backend 코드를 작성하거나 수정할 때 이 가이드를 따릅니다.

## File Structure
```
backend/app/
├── api/v1/
│   ├── endpoints/      # 라우터 (auth.py, tasks.py, projects.py)
│   └── router.py
├── models/             # SQLAlchemy 모델
├── schemas/            # Pydantic 스키마 (request/response)
├── services/           # 비즈니스 로직 (재사용 가능)
├── core/
│   ├── security.py     # JWT, 비밀번호 해싱
│   ├── permissions.py  # 권한 체크
│   └── config.py
├── utils/              # 공통 유틸리티
└── constants/          # 상수 관리
```

## API Design Patterns

### Router → Service 분리
```python
# ✅ Router는 요청/응답만 처리
@router.get("/tasks/week", response_model=list[TaskSchema])
async def get_week_tasks(
    week_start: date,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    return await TaskService.get_week_tasks(db, week_start, current_user.id)

# ❌ Router에 비즈니스 로직 금지
@router.get("/tasks/week")
async def get_week_tasks(...):
    tasks = db.query(Task).filter(...).all()
```

### Response Format (표준화)
```python
# 성공: {"data": [...], "message": "Success"}
# 에러: {"detail": "Error message", "code": "ERROR_CODE"}
```

### Error Handling
```python
# utils/exceptions.py에 공통 예외 정의
class NotFoundException(HTTPException):
    def __init__(self, resource: str):
        super().__init__(status_code=404, detail=f"{resource} not found")

# 사용: raise NotFoundException("Task")
```

## Database Rules
- 관계 설정: `back_populates` 양방향 사용
- 삭제 정책: `ondelete="CASCADE"` 명시
- 모든 ID는 UUID
- `created_at`, `updated_at` 필수
- 변경사항 발생 시 마이그레이션 즉시 생성

## Security Rules
```python
# 모든 엔드포인트는 인증 필요 (공개 제외)
current_user: User = Depends(get_current_user)

# 권한 체크는 데코레이터 사용
@require_permission("project:write")
async def update_project(...): ...

# SQL Injection 방지 - 직접 쿼리 금지, ORM만 사용
```
