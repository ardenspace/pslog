# 데이터 모델 설계

## 개요

이 문서는 pslog 프로젝트의 데이터 모델(ERD)과 권한 체계를 정의한다.

## 결정 사항

| 항목 | 결정 | 이유 |
|------|------|------|
| 권한 체계 | Workspace + Project 둘 다 | 프로젝트별 세분화된 권한 부여 필요 |
| ProjectMember.role | 추가 | Project 레벨 권한 지원 |
| Comment 모델 | 유지 (MVP API/UI 미구현) | DB 스키마 안정성, MVP+1 대비 |
| ShareLink.created_by | 추가 | 감사/추적 목적 |
| ShareLink.scope | PROJECT_READ만 사용 | MVP 단순화 |
| Task.reporter_id | 유지 | 태스크 생성자 추적 |
| 권한 상속 규칙 | Project 우선 | Project 멤버십 있으면 사용, 없으면 Workspace 상속 |

## 엔티티 관계도

```
┌─────────────────────────────────────────────────────────────────┐
│                            User                                  │
│  id, email, name, password_hash, created_at, updated_at         │
└─────────────────────────────────────────────────────────────────┘
        │                           │
        │ 1:N                       │ 1:N
        ▼                           ▼
┌───────────────────┐       ┌───────────────────┐
│  WorkspaceMember  │       │   ProjectMember   │
│  id               │       │   id              │
│  workspace_id(FK) │       │   project_id(FK)  │
│  user_id(FK)      │       │   user_id(FK)     │
│  role             │       │   role            │
│  created_at       │       │   created_at      │
└───────────────────┘       └───────────────────┘
        │                           │
        │ N:1                       │ N:1
        ▼                           ▼
┌───────────────────┐       ┌───────────────────────────────────┐
│    Workspace      │ 1:N   │            Project                │
│  id, name, slug   │──────▶│  id, workspace_id(FK)             │
│  description      │       │  name, description                │
│  created/updated  │       │  created_at, updated_at           │
└───────────────────┘       └───────────────────────────────────┘
                                    │
                    ┌───────────────┼───────────────┐
                    │ 1:N           │ 1:N           │ 1:N
                    ▼               ▼               ▼
            ┌─────────────┐  ┌────────────┐  ┌─────────────────┐
            │    Task     │  │ ShareLink  │  │    Comment      │
            │ id          │  │ id         │  │ id              │
            │ project_id  │  │ project_id │  │ task_id         │
            │ title       │  │ token      │  │ user_id         │
            │ description │  │ scope      │  │ content         │
            │ status      │  │ is_active  │  │ created_at      │
            │ assignee_id │  │ expires_at │  │ updated_at      │
            │ reporter_id │  │ created_by │  │ (MVP+1)         │
            │ due_date    │  │ created_at │  └─────────────────┘
            │ created_at  │  └────────────┘
            │ updated_at  │
            └─────────────┘
                    │
                    │ 1:N
                    ▼
            ┌─────────────┐
            │  TaskEvent  │
            │ id          │
            │ task_id     │
            │ user_id     │
            │ action      │
            │ changes     │
            │ created_at  │
            └─────────────┘
```

## 엔티티 상세

### User

| 필드 | 타입 | 설명 |
|------|------|------|
| id | UUID | PK |
| email | String | unique, indexed |
| name | String | 표시 이름 |
| password_hash | String | 암호화된 비밀번호 |
| created_at | DateTime | 생성 시각 |
| updated_at | DateTime | 수정 시각 |

### Workspace

| 필드 | 타입 | 설명 |
|------|------|------|
| id | UUID | PK |
| name | String | 워크스페이스 이름 |
| slug | String | unique, indexed, URL용 |
| description | String? | 설명 |
| created_at | DateTime | 생성 시각 |
| updated_at | DateTime | 수정 시각 |

### WorkspaceMember

| 필드 | 타입 | 설명 |
|------|------|------|
| id | UUID | PK |
| workspace_id | UUID | FK → Workspace |
| user_id | UUID | FK → User |
| role | Enum | owner / editor / viewer |
| created_at | DateTime | 생성 시각 |

**Unique constraint**: (workspace_id, user_id)

### Project

| 필드 | 타입 | 설명 |
|------|------|------|
| id | UUID | PK |
| workspace_id | UUID | FK → Workspace |
| name | String | 프로젝트 이름 |
| description | String? | 설명 |
| created_at | DateTime | 생성 시각 |
| updated_at | DateTime | 수정 시각 |

### ProjectMember

| 필드 | 타입 | 설명 |
|------|------|------|
| id | UUID | PK |
| project_id | UUID | FK → Project |
| user_id | UUID | FK → User |
| role | Enum | owner / editor / viewer |
| created_at | DateTime | 생성 시각 |

**Unique constraint**: (project_id, user_id)

### Task

| 필드 | 타입 | 설명 |
|------|------|------|
| id | UUID | PK |
| project_id | UUID | FK → Project |
| title | String | 태스크 제목 (필수) |
| description | Text? | 상세 설명 |
| status | Enum | todo / doing / done / blocked |
| assignee_id | UUID? | FK → User (담당자) |
| reporter_id | UUID? | FK → User (생성자) |
| due_date | DateTime? | 마감일 |
| created_at | DateTime | 생성 시각 |
| updated_at | DateTime | 수정 시각 |

### TaskEvent

| 필드 | 타입 | 설명 |
|------|------|------|
| id | UUID | PK |
| task_id | UUID | FK → Task |
| user_id | UUID? | FK → User (수행자) |
| action | Enum | created / updated / status_changed / assigned / commented / deleted |
| changes | JSON? | 변경 내역 (예: {"from": "todo", "to": "doing"}) |
| created_at | DateTime | 발생 시각 |

### ShareLink

| 필드 | 타입 | 설명 |
|------|------|------|
| id | UUID | PK |
| project_id | UUID | FK → Project |
| token | String | unique, indexed, URL용 토큰 |
| scope | Enum | project_read (MVP에서 이것만 사용) |
| is_active | Boolean | 활성 여부 (철회 시 false) |
| expires_at | DateTime | 만료 시각 (NOT NULL, 생성 시 30일 후 자동 설정) |
| created_by | UUID | FK → User (생성자) ← **추가** |
| created_at | DateTime | 생성 시각 |

### Comment (MVP+1)

| 필드 | 타입 | 설명 |
|------|------|------|
| id | UUID | PK |
| task_id | UUID | FK → Task |
| user_id | UUID | FK → User |
| content | Text | 댓글 내용 |
| created_at | DateTime | 생성 시각 |
| updated_at | DateTime | 수정 시각 |

## 권한 체계

### Role 정의

| Role | 설명 |
|------|------|
| owner | 전체 관리 권한 (멤버 관리, 삭제, 공유 링크 등) |
| editor | 태스크 생성/수정/상태 변경 |
| viewer | 읽기 전용 |

### 권한 해석 로직

```python
def get_effective_role(user_id: UUID, project_id: UUID) -> Role | None:
    """
    사용자의 특정 프로젝트에 대한 실효 권한을 반환한다.

    규칙:
    1. ProjectMember에 해당 사용자가 있으면 그 role 반환
    2. 없으면 WorkspaceMember의 role 상속
    3. 둘 다 없으면 None (접근 불가)
    """
    # 1. Project 레벨 확인
    project_member = db.query(ProjectMember).filter(
        ProjectMember.project_id == project_id,
        ProjectMember.user_id == user_id
    ).first()

    if project_member:
        return project_member.role

    # 2. Workspace 레벨 상속
    project = db.query(Project).get(project_id)
    workspace_member = db.query(WorkspaceMember).filter(
        WorkspaceMember.workspace_id == project.workspace_id,
        WorkspaceMember.user_id == user_id
    ).first()

    if workspace_member:
        return workspace_member.role

    # 3. 접근 불가
    return None
```

### 권한별 허용 액션

| 액션 | owner | editor | viewer |
|------|-------|--------|--------|
| 프로젝트 조회 | O | O | O |
| 태스크 조회 | O | O | O |
| 태스크 생성 | O | O | X |
| 태스크 수정 | O | O | X |
| 태스크 상태 변경 | O | O | X |
| 태스크 삭제 | O | X | X |
| 공유 링크 생성 | O | X | X |
| 공유 링크 철회 | O | X | X |
| 멤버 관리 | O | X | X |
| 프로젝트 삭제 | O | X | X |

## 마이그레이션 필요 항목

현재 구현 대비 변경이 필요한 부분:

1. **ProjectMember.role 추가**
   - `role: Mapped[ProjectRole]` 필드 추가
   - default는 `viewer` 권장

2. **ShareLink.created_by 추가**
   - `created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))`

3. **ShareLink.scope 단순화** (선택)
   - `TASK_READ` enum 제거하거나 미사용으로 둠

## 관련 문서

- PRD.md: 제품 요구사항
- SCOPE.md: MVP 범위
- DECISIONS.md: 결정 로그
