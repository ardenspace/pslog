# MVP Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** MVP 구현 - Kanban 보드 기반 협업 태스크 툴 (Workspace → Project → Task 계층, 권한 시스템, 공유 링크)

**Architecture:**
- Backend: FastAPI + SQLAlchemy (async) + PostgreSQL
- Frontend: React 19 + Vite + TanStack Query + Zustand + Tailwind
- 중첩 리소스 API 구조 (`/workspaces/.../projects/.../tasks`)

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.0, React 19, TanStack Query v5, Zustand v5

**현재 상태:**
- Auth API 구현됨 (register, login, me)
- Tasks API 부분 구현됨 (리팩토링 필요)
- 모델 6개 정의됨 (ProjectMember.role, ShareLink.created_by 추가 필요)
- Frontend: Login, Register, Dashboard(placeholder) 구현됨

---

## Phase 1: Backend Foundation

### Task 1.1: DB Migration - ProjectMember.role 추가

**Files:**
- Create: `backend/alembic/versions/xxxx_add_project_member_role.py`
- Modify: `backend/app/models/project.py`

**Step 1: 모델 수정**

`backend/app/models/project.py` 수정:

```python
# 기존 import에 추가
from app.models.workspace import WorkspaceRole  # Role enum 재사용

class ProjectMember(Base):
    __tablename__ = "project_members"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    role: Mapped[WorkspaceRole] = mapped_column(default=WorkspaceRole.VIEWER)  # 추가

    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)

    # Relationships
    project: Mapped["Project"] = relationship(back_populates="members")
    user: Mapped["User"] = relationship(back_populates="project_memberships")
```

**Step 2: 마이그레이션 생성**

```bash
cd backend
source .venv/bin/activate
alembic revision --autogenerate -m "add_project_member_role"
```

**Step 3: 마이그레이션 실행**

```bash
alembic upgrade head
```

**Step 4: 커밋**

```bash
git add backend/app/models/project.py backend/alembic/versions/
git commit -m "feat: add role field to ProjectMember model"
```

---

### Task 1.2: DB Migration - ShareLink.created_by 추가

**Files:**
- Modify: `backend/app/models/share_link.py`

**Step 1: 모델 수정**

`backend/app/models/share_link.py`에 created_by 추가:

```python
class ShareLink(Base):
    __tablename__ = "share_links"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))  # 추가

    token: Mapped[str] = mapped_column(String, unique=True, index=True)
    scope: Mapped[ShareLinkScope] = mapped_column(default=ShareLinkScope.PROJECT_READ)

    is_active: Mapped[bool] = mapped_column(default=True)
    expires_at: Mapped[datetime]  # NOT NULL - 항상 30일 후로 설정

    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)

    # Relationships
    project: Mapped["Project"] = relationship(back_populates="share_links")
    creator: Mapped["User"] = relationship()  # 추가
```

**Step 2: 마이그레이션 생성 및 실행**

```bash
alembic revision --autogenerate -m "add_share_link_created_by"
alembic upgrade head
```

**Step 3: 커밋**

```bash
git add backend/app/models/share_link.py backend/alembic/versions/
git commit -m "feat: add created_by field to ShareLink model"
```

---

### Task 1.3: 회원가입 시 Workspace 자동 생성

**Files:**
- Modify: `backend/app/services/auth_service.py`

**Step 1: auth_service.register() 수정**

회원가입 시 Workspace + WorkspaceMember를 자동 생성하도록 수정:

```python
# backend/app/services/auth_service.py
from app.models.workspace import Workspace, WorkspaceMember, WorkspaceRole

async def register(db: AsyncSession, data: RegisterRequest) -> AuthResponse:
    # 1. 기존 사용자 중복 체크
    existing = await db.execute(
        select(User).where(User.email == data.email)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Email already exists")

    # 2. 사용자 생성
    hashed_password = get_password_hash(data.password)
    user = User(
        email=data.email,
        name=data.name,
        password_hash=hashed_password,
    )
    db.add(user)
    await db.flush()

    # 3. 기본 Workspace 자동 생성
    workspace = Workspace(
        name=f"{data.name}의 워크스페이스",
        slug=f"ws-{user.id.hex}",  # 전체 UUID hex 사용 (충돌 방지)
        description=None,
    )
    db.add(workspace)
    await db.flush()

    # 4. 사용자를 Workspace Owner로 추가
    member = WorkspaceMember(
        workspace_id=workspace.id,
        user_id=user.id,
        role=WorkspaceRole.OWNER,
    )
    db.add(member)

    await db.commit()
    await db.refresh(user)

    # 5. 토큰 생성 및 반환
    token = create_access_token(data={"sub": str(user.id)})
    return AuthResponse(
        token=TokenResponse(access_token=token),
        user=UserResponse.model_validate(user),
    )
```

**Step 2: 테스트**

```bash
# 서버 실행 후 회원가입 테스트
curl -X POST http://localhost:8000/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email":"test@example.com","name":"테스트","password":"test1234"}'

# 워크스페이스 목록 확인
curl http://localhost:8000/api/v1/workspaces \
  -H "Authorization: Bearer {token}"
```

**Step 3: 커밋**

```bash
git add backend/app/services/auth_service.py
git commit -m "feat: auto-create workspace on user registration"
```

---

### Task 1.4: Permission Service 구현

**Files:**
- Create: `backend/app/services/permission_service.py`
- Create: `backend/tests/services/test_permission_service.py`

**Step 1: 테스트 작성**

```python
# backend/tests/services/test_permission_service.py
import pytest
from uuid import uuid4
from app.services.permission_service import get_effective_role
from app.models.workspace import WorkspaceRole

@pytest.mark.asyncio
async def test_get_effective_role_from_project_member(db_session, user, project):
    """ProjectMember가 있으면 그 role 반환"""
    # Given: user가 project의 editor 멤버
    await add_project_member(db_session, project.id, user.id, WorkspaceRole.EDITOR)

    # When
    role = await get_effective_role(db_session, user.id, project.id)

    # Then
    assert role == WorkspaceRole.EDITOR

@pytest.mark.asyncio
async def test_get_effective_role_inherits_from_workspace(db_session, user, project, workspace):
    """ProjectMember 없으면 WorkspaceMember role 상속"""
    # Given: user가 workspace의 owner (project member 아님)
    await add_workspace_member(db_session, workspace.id, user.id, WorkspaceRole.OWNER)

    # When
    role = await get_effective_role(db_session, user.id, project.id)

    # Then
    assert role == WorkspaceRole.OWNER

@pytest.mark.asyncio
async def test_get_effective_role_no_membership(db_session, user, project):
    """어디에도 멤버가 아니면 None"""
    # When
    role = await get_effective_role(db_session, user.id, project.id)

    # Then
    assert role is None
```

**Step 2: 테스트 실행 (실패 확인)**

```bash
pytest tests/services/test_permission_service.py -v
```

**Step 3: 구현**

```python
# backend/app/services/permission_service.py
from uuid import UUID
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.workspace import WorkspaceRole, WorkspaceMember
from app.models.project import Project, ProjectMember


async def get_effective_role(
    db: AsyncSession,
    user_id: UUID,
    project_id: UUID
) -> WorkspaceRole | None:
    """
    사용자의 프로젝트에 대한 실효 권한을 반환한다.
    1. ProjectMember에 있으면 그 role 반환
    2. 없으면 WorkspaceMember role 상속
    3. 둘 다 없으면 None
    """
    # 1. Project 레벨 확인
    stmt = select(ProjectMember).where(
        ProjectMember.project_id == project_id,
        ProjectMember.user_id == user_id
    )
    result = await db.execute(stmt)
    project_member = result.scalar_one_or_none()

    if project_member:
        return project_member.role

    # 2. Workspace 레벨 상속
    stmt = select(Project).where(Project.id == project_id)
    result = await db.execute(stmt)
    project = result.scalar_one_or_none()

    if not project:
        return None

    stmt = select(WorkspaceMember).where(
        WorkspaceMember.workspace_id == project.workspace_id,
        WorkspaceMember.user_id == user_id
    )
    result = await db.execute(stmt)
    workspace_member = result.scalar_one_or_none()

    if workspace_member:
        return workspace_member.role

    return None


def can_edit(role: WorkspaceRole | None) -> bool:
    """Editor 이상만 편집 가능"""
    return role in (WorkspaceRole.OWNER, WorkspaceRole.EDITOR)


def can_manage(role: WorkspaceRole | None) -> bool:
    """Owner만 관리 가능 (멤버 관리, 삭제, 공유 링크)"""
    return role == WorkspaceRole.OWNER
```

**Step 4: 테스트 실행 (성공 확인)**

```bash
pytest tests/services/test_permission_service.py -v
```

**Step 5: 커밋**

```bash
git add backend/app/services/permission_service.py backend/tests/services/
git commit -m "feat: implement permission service with role inheritance"
```

---

### Task 1.5: Workspace API 구현

**Files:**
- Create: `backend/app/schemas/workspace.py`
- Create: `backend/app/services/workspace_service.py`
- Create: `backend/app/api/v1/endpoints/workspaces.py`
- Modify: `backend/app/api/v1/router.py`

**Step 1: Schema 작성**

```python
# backend/app/schemas/workspace.py
from datetime import datetime
from uuid import UUID
from pydantic import BaseModel
from app.models.workspace import WorkspaceRole


class WorkspaceCreate(BaseModel):
    name: str
    slug: str
    description: str | None = None


class WorkspaceUpdate(BaseModel):
    name: str | None = None
    description: str | None = None


class WorkspaceResponse(BaseModel):
    id: UUID
    name: str
    slug: str
    description: str | None
    my_role: WorkspaceRole
    member_count: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class WorkspaceMemberResponse(BaseModel):
    id: UUID
    user_id: UUID
    role: WorkspaceRole
    created_at: datetime
    user: "UserBrief"

    model_config = {"from_attributes": True}


class AddMemberRequest(BaseModel):
    email: str
    role: WorkspaceRole = WorkspaceRole.VIEWER


# Forward reference
from app.schemas.task import UserBrief
WorkspaceMemberResponse.model_rebuild()
```

**Step 2: Service 작성**

```python
# backend/app/services/workspace_service.py
from uuid import UUID
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.user import User
from app.models.workspace import Workspace, WorkspaceMember, WorkspaceRole
from app.schemas.workspace import WorkspaceCreate, WorkspaceUpdate


async def create_workspace(
    db: AsyncSession,
    user_id: UUID,
    data: WorkspaceCreate
) -> Workspace:
    workspace = Workspace(
        name=data.name,
        slug=data.slug,
        description=data.description,
    )
    db.add(workspace)
    await db.flush()

    # 생성자를 Owner로 추가
    member = WorkspaceMember(
        workspace_id=workspace.id,
        user_id=user_id,
        role=WorkspaceRole.OWNER,
    )
    db.add(member)
    await db.commit()
    await db.refresh(workspace)

    return workspace


async def get_user_workspaces(db: AsyncSession, user_id: UUID) -> list[dict]:
    """사용자가 속한 워크스페이스 목록 (my_role, member_count 포함)"""
    stmt = (
        select(Workspace, WorkspaceMember.role)
        .join(WorkspaceMember)
        .where(WorkspaceMember.user_id == user_id)
    )
    result = await db.execute(stmt)
    rows = result.all()

    workspaces = []
    for workspace, role in rows:
        # member_count 조회
        count_stmt = select(func.count()).where(
            WorkspaceMember.workspace_id == workspace.id
        )
        count_result = await db.execute(count_stmt)
        member_count = count_result.scalar()

        workspaces.append({
            **workspace.__dict__,
            "my_role": role,
            "member_count": member_count,
        })

    return workspaces


async def get_workspace(db: AsyncSession, workspace_id: UUID) -> Workspace | None:
    stmt = select(Workspace).where(Workspace.id == workspace_id)
    result = await db.execute(stmt)
    return result.scalar_one_or_none()
```

**Step 3: API Endpoint 작성**

```python
# backend/app/api/v1/endpoints/workspaces.py
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import CurrentUser
from app.models.workspace import WorkspaceRole
from app.schemas.workspace import (
    WorkspaceCreate,
    WorkspaceResponse,
    WorkspaceMemberResponse,
    AddMemberRequest,
)
from app.services import workspace_service
from app.services.permission_service import can_manage

router = APIRouter(prefix="/workspaces", tags=["workspaces"])


@router.get("", response_model=list[WorkspaceResponse])
async def list_workspaces(
    db: AsyncSession = Depends(get_db),
    user: CurrentUser,
):
    """내 워크스페이스 목록"""
    return await workspace_service.get_user_workspaces(db, user.id)


@router.post("", response_model=WorkspaceResponse, status_code=status.HTTP_201_CREATED)
async def create_workspace(
    data: WorkspaceCreate,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser,
):
    """워크스페이스 생성"""
    workspace = await workspace_service.create_workspace(db, user.id, data)
    return {
        **workspace.__dict__,
        "my_role": WorkspaceRole.OWNER,
        "member_count": 1,
    }


@router.get("/{workspace_id}", response_model=WorkspaceResponse)
async def get_workspace(
    workspace_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser,
):
    """워크스페이스 상세"""
    workspace = await workspace_service.get_workspace(db, workspace_id)
    if not workspace:
        raise HTTPException(status_code=404, detail="Workspace not found")
    # TODO: 멤버 여부 확인
    return workspace
```

**Step 4: Router 등록**

`backend/app/api/v1/router.py` 수정:

```python
from app.api.v1.endpoints.workspaces import router as workspaces_router

api_v1_router.include_router(workspaces_router)
```

**Step 5: 테스트 및 커밋**

```bash
# 서버 실행 후 수동 테스트 또는 pytest
pytest tests/api/test_workspaces.py -v

git add backend/app/schemas/workspace.py backend/app/services/workspace_service.py \
        backend/app/api/v1/endpoints/workspaces.py backend/app/api/v1/router.py
git commit -m "feat: implement workspace API (list, create, get)"
```

---

### Task 1.6: Project API 구현

**Files:**
- Create: `backend/app/schemas/project.py`
- Create: `backend/app/services/project_service.py`
- Create: `backend/app/api/v1/endpoints/projects.py`
- Modify: `backend/app/api/v1/router.py`

**Step 1: Schema 작성**

```python
# backend/app/schemas/project.py
from datetime import datetime
from uuid import UUID
from pydantic import BaseModel
from app.models.workspace import WorkspaceRole


class ProjectCreate(BaseModel):
    name: str
    description: str | None = None


class ProjectUpdate(BaseModel):
    name: str | None = None
    description: str | None = None


class ProjectResponse(BaseModel):
    id: UUID
    workspace_id: UUID
    name: str
    description: str | None
    my_role: WorkspaceRole
    task_count: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
```

**Step 2: Service 작성**

```python
# backend/app/services/project_service.py
from uuid import UUID
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.project import Project, ProjectMember
from app.models.task import Task
from app.models.workspace import WorkspaceRole
from app.schemas.project import ProjectCreate
from app.services.permission_service import get_effective_role


async def create_project(
    db: AsyncSession,
    workspace_id: UUID,
    user_id: UUID,
    data: ProjectCreate,
) -> Project:
    project = Project(
        workspace_id=workspace_id,
        name=data.name,
        description=data.description,
    )
    db.add(project)
    await db.flush()

    # 생성자를 Project Owner로 추가
    member = ProjectMember(
        project_id=project.id,
        user_id=user_id,
        role=WorkspaceRole.OWNER,
    )
    db.add(member)

    await db.commit()
    await db.refresh(project)
    return project


async def get_workspace_projects(
    db: AsyncSession,
    workspace_id: UUID,
    user_id: UUID,
) -> list[dict]:
    """워크스페이스의 프로젝트 목록"""
    stmt = select(Project).where(Project.workspace_id == workspace_id)
    result = await db.execute(stmt)
    projects = result.scalars().all()

    project_list = []
    for project in projects:
        role = await get_effective_role(db, user_id, project.id)

        # task_count
        count_stmt = select(func.count()).where(Task.project_id == project.id)
        count_result = await db.execute(count_stmt)
        task_count = count_result.scalar()

        project_list.append({
            **project.__dict__,
            "my_role": role,
            "task_count": task_count,
        })

    return project_list


async def get_project(db: AsyncSession, project_id: UUID) -> Project | None:
    stmt = select(Project).where(Project.id == project_id)
    result = await db.execute(stmt)
    return result.scalar_one_or_none()
```

**Step 3: API Endpoint 작성**

```python
# backend/app/api/v1/endpoints/projects.py
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import CurrentUser
from app.models.workspace import WorkspaceRole
from app.schemas.project import ProjectCreate, ProjectResponse
from app.services import project_service
from app.services.permission_service import get_effective_role, can_edit

router = APIRouter(tags=["projects"])


@router.get("/workspaces/{workspace_id}/projects", response_model=list[ProjectResponse])
async def list_projects(
    workspace_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser,
):
    """워크스페이스의 프로젝트 목록"""
    return await project_service.get_workspace_projects(db, workspace_id, user.id)


@router.post(
    "/workspaces/{workspace_id}/projects",
    response_model=ProjectResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_project(
    workspace_id: UUID,
    data: ProjectCreate,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser,
):
    """프로젝트 생성 (editor 이상)"""
    # TODO: workspace 멤버 권한 체크
    project = await project_service.create_project(db, workspace_id, user.id, data)
    return {
        **project.__dict__,
        "my_role": WorkspaceRole.OWNER,
        "task_count": 0,
    }


@router.get("/projects/{project_id}", response_model=ProjectResponse)
async def get_project(
    project_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser,
):
    """프로젝트 상세"""
    project = await project_service.get_project(db, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    role = await get_effective_role(db, user.id, project_id)
    if not role:
        raise HTTPException(status_code=403, detail="Permission denied")

    return {
        **project.__dict__,
        "my_role": role,
        "task_count": 0,  # TODO
    }
```

**Step 4: Router 등록 및 커밋**

```python
# backend/app/api/v1/router.py
from app.api.v1.endpoints.projects import router as projects_router
api_v1_router.include_router(projects_router)
```

```bash
git add backend/app/schemas/project.py backend/app/services/project_service.py \
        backend/app/api/v1/endpoints/projects.py backend/app/api/v1/router.py
git commit -m "feat: implement project API (list, create, get)"
```

---

### Task 1.7: Tasks API 리팩토링 (중첩 리소스)

**Files:**
- Modify: `backend/app/api/v1/endpoints/tasks.py`
- Modify: `backend/app/services/task_service.py`
- Modify: `backend/app/schemas/task.py`

**Step 1: Schema에 필터 추가**

```python
# backend/app/schemas/task.py 에 추가
from app.models.task import TaskStatus

class TaskFilters(BaseModel):
    status: TaskStatus | None = None
    assignee_id: UUID | None = None
    mine_only: bool = False
```

**Step 2: Service 수정 - 단일 태스크 조회 헬퍼**

```python
# backend/app/services/task_service.py 에 추가
async def get_task(db: AsyncSession, task_id: UUID) -> Task | None:
    """태스크 단일 조회"""
    stmt = select(Task).where(Task.id == task_id)
    result = await db.execute(stmt)
    return result.scalar_one_or_none()
```

**Step 3: Service 수정 - 프로젝트별 목록 조회**

```python
# backend/app/services/task_service.py 에 추가
async def get_project_tasks(
    db: AsyncSession,
    project_id: UUID,
    user_id: UUID,
    filters: TaskFilters | None = None,
) -> list[Task]:
    stmt = select(Task).where(Task.project_id == project_id)

    if filters:
        if filters.status:
            stmt = stmt.where(Task.status == filters.status)
        if filters.assignee_id:
            stmt = stmt.where(Task.assignee_id == filters.assignee_id)
        if filters.mine_only:
            stmt = stmt.where(Task.assignee_id == user_id)

    stmt = stmt.options(
        selectinload(Task.assignee),
        selectinload(Task.reporter),
    )

    result = await db.execute(stmt)
    return result.scalars().all()
```

**Step 4: API 엔드포인트 수정**

```python
# backend/app/api/v1/endpoints/tasks.py

@router.get("/projects/{project_id}/tasks", response_model=list[TaskResponse])
async def list_project_tasks(
    project_id: UUID,
    status: TaskStatus | None = None,
    assignee_id: UUID | None = None,
    mine_only: bool = False,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser,
):
    """프로젝트의 태스크 목록 (Board용)"""
    from app.services.permission_service import get_effective_role

    role = await get_effective_role(db, user.id, project_id)
    if not role:
        raise HTTPException(status_code=403, detail="Permission denied")

    filters = TaskFilters(status=status, assignee_id=assignee_id, mine_only=mine_only)
    return await task_service.get_project_tasks(db, project_id, user.id, filters)


@router.post("/projects/{project_id}/tasks", ...)  # 기존 유지, path만 변경
```

**Step 5: 커밋**

```bash
git add backend/app/api/v1/endpoints/tasks.py backend/app/services/task_service.py \
        backend/app/schemas/task.py
git commit -m "refactor: migrate tasks API to nested resource structure"
```

---

### Task 1.8: TaskEvent 자동 기록

**Files:**
- Create: `backend/app/services/task_event_service.py`
- Modify: `backend/app/services/task_service.py`
- Modify: `backend/app/models/task_event.py` (DELETED enum 추가)

**Step 1: TaskEventAction에 DELETED 추가**

```python
# backend/app/models/task_event.py
class TaskEventAction(str, Enum):
    CREATED = "created"
    STATUS_CHANGED = "status_changed"
    UPDATED = "updated"
    DELETED = "deleted"  # 추가
```

**Step 2: TaskEvent Service 작성**

```python
# backend/app/services/task_event_service.py
from uuid import UUID
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.task_event import TaskEvent, TaskEventAction


async def record_event(
    db: AsyncSession,
    task_id: UUID,
    user_id: UUID,
    action: TaskEventAction,
    changes: dict | None = None,
) -> TaskEvent:
    event = TaskEvent(
        task_id=task_id,
        user_id=user_id,
        action=action,
        changes=changes,
    )
    db.add(event)
    return event
```

**Step 3: Task Service에 이벤트 기록 추가**

```python
# backend/app/services/task_service.py 수정

async def create_task(...):
    # ... 기존 로직 ...
    db.add(task)
    await db.flush()

    # TaskEvent 기록
    await task_event_service.record_event(
        db, task.id, user_id, TaskEventAction.CREATED
    )

    await db.commit()
    return task


async def update_task(...):
    # 상태 변경 감지
    old_status = task.status

    # ... 기존 업데이트 로직 ...

    # 상태가 변경되었으면 이벤트 기록
    if data.status and data.status != old_status:
        await task_event_service.record_event(
            db, task_id, user_id, TaskEventAction.STATUS_CHANGED,
            changes={"from": old_status.value, "to": data.status.value}
        )

    await db.commit()
    return task
```

**Step 4: 커밋**

```bash
git add backend/app/models/task_event.py backend/app/services/task_event_service.py \
        backend/app/services/task_service.py
git commit -m "feat: auto-record TaskEvent on task creation and status change"
```

---

### Task 1.9: Task 삭제 API 구현

**Files:**
- Modify: `backend/app/api/v1/endpoints/tasks.py`
- Modify: `backend/app/services/task_service.py`

**Step 1: Service에 삭제 함수 추가**

```python
# backend/app/services/task_service.py 에 추가

async def delete_task(
    db: AsyncSession,
    task_id: UUID,
    user_id: UUID,
) -> bool:
    """태스크 삭제 (Owner만 가능)"""
    stmt = select(Task).where(Task.id == task_id)
    result = await db.execute(stmt)
    task = result.scalar_one_or_none()

    if not task:
        return False

    # TaskEvent 기록 (삭제 전)
    await task_event_service.record_event(
        db, task_id, user_id, TaskEventAction.DELETED
    )

    await db.delete(task)
    await db.commit()
    return True
```

**Step 2: API Endpoint 추가**

```python
# backend/app/api/v1/endpoints/tasks.py 에 추가

@router.delete("/tasks/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_task(
    task_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser,
):
    """태스크 삭제 (Owner만)"""
    from app.services.permission_service import get_effective_role, can_manage

    # 태스크 조회하여 project_id 확인
    task = await task_service.get_task(db, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    role = await get_effective_role(db, user.id, task.project_id)
    if not can_manage(role):
        raise HTTPException(status_code=403, detail="Only owner can delete tasks")

    await task_service.delete_task(db, task_id, user.id)
```

**Step 3: 커밋**

```bash
git add backend/app/api/v1/endpoints/tasks.py backend/app/services/task_service.py
git commit -m "feat: implement task delete API (owner only)"
```

---

### Task 1.10: Workspace 멤버 초대 API 구현

**Files:**
- Modify: `backend/app/services/workspace_service.py`
- Modify: `backend/app/api/v1/endpoints/workspaces.py`

**Step 1: Service에 멤버 관련 함수 추가**

```python
# backend/app/services/workspace_service.py 에 추가

async def get_user_membership(
    db: AsyncSession,
    workspace_id: UUID,
    user_id: UUID,
) -> WorkspaceMember | None:
    """사용자의 워크스페이스 멤버십 조회 (권한 체크용)"""
    stmt = select(WorkspaceMember).where(
        WorkspaceMember.workspace_id == workspace_id,
        WorkspaceMember.user_id == user_id
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def add_member(
    db: AsyncSession,
    workspace_id: UUID,
    email: str,
    role: WorkspaceRole,
) -> WorkspaceMember | None:
    """이메일로 사용자를 워크스페이스에 초대"""
    # 사용자 조회
    stmt = select(User).where(User.email == email)
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()

    if not user:
        return None  # 사용자 없음

    # 이미 멤버인지 확인
    stmt = select(WorkspaceMember).where(
        WorkspaceMember.workspace_id == workspace_id,
        WorkspaceMember.user_id == user.id
    )
    result = await db.execute(stmt)
    existing = result.scalar_one_or_none()

    if existing:
        # 이미 멤버면 role 업데이트
        existing.role = role
        await db.commit()
        return existing

    # 새 멤버 추가
    member = WorkspaceMember(
        workspace_id=workspace_id,
        user_id=user.id,
        role=role,
    )
    db.add(member)
    await db.commit()
    await db.refresh(member)
    return member


async def get_workspace_members(
    db: AsyncSession,
    workspace_id: UUID,
) -> list[WorkspaceMember]:
    """워크스페이스 멤버 목록"""
    stmt = (
        select(WorkspaceMember)
        .where(WorkspaceMember.workspace_id == workspace_id)
        .options(selectinload(WorkspaceMember.user))
    )
    result = await db.execute(stmt)
    return result.scalars().all()


async def remove_member(
    db: AsyncSession,
    workspace_id: UUID,
    user_id: UUID,
) -> bool:
    """워크스페이스에서 멤버 제거"""
    stmt = select(WorkspaceMember).where(
        WorkspaceMember.workspace_id == workspace_id,
        WorkspaceMember.user_id == user_id
    )
    result = await db.execute(stmt)
    member = result.scalar_one_or_none()

    if not member:
        return False

    await db.delete(member)
    await db.commit()
    return True
```

**Step 2: API Endpoint 추가**

```python
# backend/app/api/v1/endpoints/workspaces.py 에 추가

@router.get("/{workspace_id}/members", response_model=list[WorkspaceMemberResponse])
async def list_workspace_members(
    workspace_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser,
):
    """워크스페이스 멤버 목록"""
    # TODO: 멤버 여부 확인
    return await workspace_service.get_workspace_members(db, workspace_id)


@router.post("/{workspace_id}/members", response_model=WorkspaceMemberResponse, status_code=status.HTTP_201_CREATED)
async def add_workspace_member(
    workspace_id: UUID,
    data: AddMemberRequest,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser,
):
    """멤버 초대 (Owner만)"""
    # 권한 확인
    my_membership = await workspace_service.get_user_membership(db, workspace_id, user.id)
    if not my_membership or my_membership.role != WorkspaceRole.OWNER:
        raise HTTPException(status_code=403, detail="Only owner can add members")

    member = await workspace_service.add_member(db, workspace_id, data.email, data.role)
    if not member:
        raise HTTPException(status_code=404, detail="User not found")

    return member


@router.delete("/{workspace_id}/members/{member_user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_workspace_member(
    workspace_id: UUID,
    member_user_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser,
):
    """멤버 제거 (Owner만)"""
    my_membership = await workspace_service.get_user_membership(db, workspace_id, user.id)
    if not my_membership or my_membership.role != WorkspaceRole.OWNER:
        raise HTTPException(status_code=403, detail="Only owner can remove members")

    # 자기 자신 제거 방지
    if member_user_id == user.id:
        raise HTTPException(status_code=400, detail="Cannot remove yourself")

    success = await workspace_service.remove_member(db, workspace_id, member_user_id)
    if not success:
        raise HTTPException(status_code=404, detail="Member not found")
```

**Step 3: 커밋**

```bash
git add backend/app/services/workspace_service.py backend/app/api/v1/endpoints/workspaces.py
git commit -m "feat: implement workspace member management API (owner only)"
```

---

## Phase 2: Frontend Board UI

### Task 2.1: UI Store 추가

**Files:**
- Create: `frontend/src/stores/uiStore.ts`
- Modify: `frontend/src/stores/index.ts`

**Step 1: uiStore 작성**

```typescript
// frontend/src/stores/uiStore.ts
import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import type { TaskStatus } from '@/types';

interface TaskFilters {
  mineOnly: boolean;
  status: TaskStatus | null;
}

interface UIState {
  selectedWorkspaceId: string | null;
  selectedProjectId: string | null;
  taskFilters: TaskFilters;

  setSelectedWorkspace: (id: string | null) => void;
  setSelectedProject: (id: string | null) => void;
  setTaskFilters: (filters: Partial<TaskFilters>) => void;
  resetTaskFilters: () => void;
}

const defaultFilters: TaskFilters = {
  mineOnly: false,
  status: null,
};

export const useUIStore = create<UIState>()(
  persist(
    (set) => ({
      selectedWorkspaceId: null,
      selectedProjectId: null,
      taskFilters: defaultFilters,

      setSelectedWorkspace: (id) =>
        set({ selectedWorkspaceId: id, selectedProjectId: null }),
      setSelectedProject: (id) =>
        set({ selectedProjectId: id }),
      setTaskFilters: (filters) =>
        set((state) => ({
          taskFilters: { ...state.taskFilters, ...filters },
        })),
      resetTaskFilters: () =>
        set({ taskFilters: defaultFilters }),
    }),
    {
      name: 'ui-storage',
      partialize: (state) => ({
        selectedWorkspaceId: state.selectedWorkspaceId,
        selectedProjectId: state.selectedProjectId,
      }),
    }
  )
);
```

**Step 2: 커밋**

```bash
git add frontend/src/stores/uiStore.ts
git commit -m "feat: add uiStore for workspace/project selection and filters"
```

---

### Task 2.2: API Service 확장

**Files:**
- Modify: `frontend/src/services/api.ts`
- Create: `frontend/src/types/workspace.ts`
- Create: `frontend/src/types/project.ts`

**Step 1: Types 추가**

```typescript
// frontend/src/types/workspace.ts
export type WorkspaceRole = 'owner' | 'editor' | 'viewer';

export interface Workspace {
  id: string;
  name: string;
  slug: string;
  description: string | null;
  my_role: WorkspaceRole;
  member_count: number;
  created_at: string;
  updated_at: string;
}

export interface WorkspaceCreate {
  name: string;
  slug: string;
  description?: string;
}
```

```typescript
// frontend/src/types/project.ts
import type { WorkspaceRole } from './workspace';

export interface Project {
  id: string;
  workspace_id: string;
  name: string;
  description: string | null;
  my_role: WorkspaceRole;
  task_count: number;
  created_at: string;
  updated_at: string;
}

export interface ProjectCreate {
  name: string;
  description?: string;
}
```

**Step 2: API 함수 추가**

```typescript
// frontend/src/services/api.ts 에 추가
import type { Workspace, WorkspaceCreate } from '@/types/workspace';
import type { Project, ProjectCreate } from '@/types/project';

export const api = {
  // ... 기존 auth, tasks ...

  workspaces: {
    list: () => apiClient.get<Workspace[]>('/workspaces'),
    create: (data: WorkspaceCreate) => apiClient.post<Workspace>('/workspaces', data),
    get: (id: string) => apiClient.get<Workspace>(`/workspaces/${id}`),
  },

  projects: {
    list: (workspaceId: string) =>
      apiClient.get<Project[]>(`/workspaces/${workspaceId}/projects`),
    create: (workspaceId: string, data: ProjectCreate) =>
      apiClient.post<Project>(`/workspaces/${workspaceId}/projects`, data),
    get: (id: string) => apiClient.get<Project>(`/projects/${id}`),
  },

  tasks: {
    // 기존 메서드 수정
    list: (projectId: string, filters?: { mineOnly?: boolean; status?: string }) =>
      apiClient.get<Task[]>(`/projects/${projectId}/tasks`, { params: filters }),
    create: (projectId: string, data: TaskCreate) =>
      apiClient.post<Task>(`/projects/${projectId}/tasks`, data),
    // ... 나머지 유지
  },
};
```

**Step 3: 커밋**

```bash
git add frontend/src/types/workspace.ts frontend/src/types/project.ts \
        frontend/src/services/api.ts frontend/src/types/index.ts
git commit -m "feat: add workspace and project API types and methods"
```

---

### Task 2.3: Query Hooks 추가

**Files:**
- Create: `frontend/src/hooks/useWorkspaces.ts`
- Create: `frontend/src/hooks/useProjects.ts`
- Create: `frontend/src/hooks/useTasks.ts`

**Step 1: useWorkspaces hook**

```typescript
// frontend/src/hooks/useWorkspaces.ts
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '@/services/api';
import type { WorkspaceCreate } from '@/types/workspace';

export function useWorkspaces() {
  return useQuery({
    queryKey: ['workspaces'],
    queryFn: () => api.workspaces.list().then(r => r.data),
  });
}

export function useCreateWorkspace() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (data: WorkspaceCreate) => api.workspaces.create(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['workspaces'] });
    },
  });
}
```

**Step 2: useProjects hook**

```typescript
// frontend/src/hooks/useProjects.ts
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '@/services/api';
import type { ProjectCreate } from '@/types/project';

export function useProjects(workspaceId: string | null) {
  return useQuery({
    queryKey: ['workspaces', workspaceId, 'projects'],
    queryFn: () => api.projects.list(workspaceId!).then(r => r.data),
    enabled: !!workspaceId,
  });
}

export function useCreateProject(workspaceId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (data: ProjectCreate) => api.projects.create(workspaceId, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['workspaces', workspaceId, 'projects'] });
    },
  });
}
```

**Step 3: useTasks hook**

```typescript
// frontend/src/hooks/useTasks.ts
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '@/services/api';
import type { TaskCreate, TaskUpdate } from '@/types/task';

interface TaskFilters {
  mineOnly?: boolean;
  status?: string;
}

export function useTasks(projectId: string | null, filters?: TaskFilters) {
  return useQuery({
    queryKey: ['projects', projectId, 'tasks', filters],
    queryFn: () => api.tasks.list(projectId!, filters).then(r => r.data),
    enabled: !!projectId,
  });
}

export function useCreateTask(projectId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (data: TaskCreate) => api.tasks.create(projectId, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['projects', projectId, 'tasks'] });
    },
  });
}

export function useUpdateTask() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ taskId, data }: { taskId: string; data: TaskUpdate }) =>
      api.tasks.update(taskId, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ predicate: q => q.queryKey[2] === 'tasks' });
    },
  });
}
```

**Step 4: 커밋**

```bash
git add frontend/src/hooks/useWorkspaces.ts frontend/src/hooks/useProjects.ts \
        frontend/src/hooks/useTasks.ts
git commit -m "feat: add React Query hooks for workspaces, projects, tasks"
```

---

### Task 2.4: Board 컴포넌트 구현

**Files:**
- Create: `frontend/src/components/board/KanbanBoard.tsx`
- Create: `frontend/src/components/board/KanbanColumn.tsx`
- Create: `frontend/src/components/board/TaskCard.tsx`

**Step 1: TaskCard 컴포넌트**

```typescript
// frontend/src/components/board/TaskCard.tsx
import { Card, CardContent } from '@/components/ui/card';
import type { Task } from '@/types/task';

interface TaskCardProps {
  task: Task;
  onClick: () => void;
}

export function TaskCard({ task, onClick }: TaskCardProps) {
  return (
    <Card
      className="cursor-pointer hover:shadow-md transition-shadow"
      onClick={onClick}
    >
      <CardContent className="p-3">
        <h4 className="font-medium text-sm">{task.title}</h4>
        {task.assignee && (
          <p className="text-xs text-muted-foreground mt-1">
            {task.assignee.name}
          </p>
        )}
        {task.due_date && (
          <p className="text-xs text-muted-foreground">
            {new Date(task.due_date).toLocaleDateString()}
          </p>
        )}
      </CardContent>
    </Card>
  );
}
```

**Step 2: KanbanColumn 컴포넌트**

```typescript
// frontend/src/components/board/KanbanColumn.tsx
import type { Task, TaskStatus } from '@/types/task';
import { TaskCard } from './TaskCard';

interface KanbanColumnProps {
  status: TaskStatus;
  title: string;
  tasks: Task[];
  onTaskClick: (task: Task) => void;
}

const statusColors: Record<TaskStatus, string> = {
  todo: 'bg-slate-100',
  doing: 'bg-blue-100',
  done: 'bg-green-100',
  blocked: 'bg-red-100',
};

export function KanbanColumn({ status, title, tasks, onTaskClick }: KanbanColumnProps) {
  return (
    <div className={`flex-1 min-w-[250px] rounded-lg p-3 ${statusColors[status]}`}>
      <div className="flex items-center justify-between mb-3">
        <h3 className="font-semibold text-sm">{title}</h3>
        <span className="text-xs text-muted-foreground bg-white px-2 py-0.5 rounded">
          {tasks.length}
        </span>
      </div>
      <div className="space-y-2">
        {tasks.map((task) => (
          <TaskCard key={task.id} task={task} onClick={() => onTaskClick(task)} />
        ))}
        {tasks.length === 0 && (
          <p className="text-xs text-muted-foreground text-center py-4">
            태스크 없음
          </p>
        )}
      </div>
    </div>
  );
}
```

**Step 3: KanbanBoard 컴포넌트**

```typescript
// frontend/src/components/board/KanbanBoard.tsx
import type { Task, TaskStatus } from '@/types/task';
import { KanbanColumn } from './KanbanColumn';

interface KanbanBoardProps {
  tasks: Task[];
  onTaskClick: (task: Task) => void;
}

const columns: { status: TaskStatus; title: string }[] = [
  { status: 'todo', title: 'To Do' },
  { status: 'doing', title: 'Doing' },
  { status: 'done', title: 'Done' },
  { status: 'blocked', title: 'Blocked' },
];

export function KanbanBoard({ tasks, onTaskClick }: KanbanBoardProps) {
  const tasksByStatus = columns.map((col) => ({
    ...col,
    tasks: tasks.filter((t) => t.status === col.status),
  }));

  return (
    <div className="flex gap-4 overflow-x-auto pb-4">
      {tasksByStatus.map((col) => (
        <KanbanColumn
          key={col.status}
          status={col.status}
          title={col.title}
          tasks={col.tasks}
          onTaskClick={onTaskClick}
        />
      ))}
    </div>
  );
}
```

**Step 4: 커밋**

```bash
git add frontend/src/components/board/
git commit -m "feat: implement Kanban board components (Board, Column, Card)"
```

---

### Task 2.5: Board 페이지 통합

**Files:**
- Modify: `frontend/src/pages/DashboardPage.tsx`
- Create: `frontend/src/components/board/BoardHeader.tsx`
- Create: `frontend/src/components/board/CreateTaskModal.tsx`
- Create: `frontend/src/components/board/TaskDetailModal.tsx`

**Step 1: BoardHeader 컴포넌트**

```typescript
// frontend/src/components/board/BoardHeader.tsx
import { Button } from '@/components/ui/button';
import { useUIStore } from '@/stores/uiStore';

interface BoardHeaderProps {
  projectName: string;
  onCreateTask: () => void;
}

export function BoardHeader({ projectName, onCreateTask }: BoardHeaderProps) {
  const { taskFilters, setTaskFilters } = useUIStore();

  return (
    <div className="flex items-center justify-between mb-4">
      <h2 className="text-xl font-bold">{projectName}</h2>
      <div className="flex items-center gap-3">
        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={taskFilters.mineOnly}
            onChange={(e) => setTaskFilters({ mineOnly: e.target.checked })}
            className="rounded"
          />
          내 태스크만
        </label>
        <Button onClick={onCreateTask}>새 태스크</Button>
      </div>
    </div>
  );
}
```

**Step 2: TaskDetailModal 컴포넌트**

```typescript
// frontend/src/components/board/TaskDetailModal.tsx
import { useState } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { useUpdateTask } from '@/hooks/useTasks';
import type { Task, TaskStatus } from '@/types/task';
import type { WorkspaceRole } from '@/types/workspace';

interface TaskDetailModalProps {
  task: Task | null;
  myRole: WorkspaceRole;
  isOpen: boolean;
  onClose: () => void;
  onDelete?: (taskId: string) => void;
}

const statusOptions: { value: TaskStatus; label: string }[] = [
  { value: 'todo', label: 'To Do' },
  { value: 'doing', label: 'Doing' },
  { value: 'done', label: 'Done' },
  { value: 'blocked', label: 'Blocked' },
];

export function TaskDetailModal({ task, myRole, isOpen, onClose, onDelete }: TaskDetailModalProps) {
  const updateTask = useUpdateTask();
  const [status, setStatus] = useState<TaskStatus>(task?.status ?? 'todo');

  const canEdit = myRole === 'owner' || myRole === 'editor';
  const canDelete = myRole === 'owner';

  if (!isOpen || !task) return null;

  const handleStatusChange = async (newStatus: TaskStatus) => {
    setStatus(newStatus);
    await updateTask.mutateAsync({
      taskId: task.id,
      data: { status: newStatus },
    });
  };

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
      <div className="bg-white rounded-lg p-6 w-full max-w-lg">
        <div className="flex items-start justify-between mb-4">
          <h2 className="text-lg font-bold">{task.title}</h2>
          {!canEdit && (
            <span className="text-xs bg-gray-100 text-gray-600 px-2 py-1 rounded">
              읽기 전용
            </span>
          )}
        </div>

        {task.description && (
          <p className="text-sm text-muted-foreground mb-4">{task.description}</p>
        )}

        <div className="space-y-3 mb-6">
          <div className="flex items-center gap-2">
            <span className="text-sm font-medium w-20">상태:</span>
            {canEdit ? (
              <select
                value={status}
                onChange={(e) => handleStatusChange(e.target.value as TaskStatus)}
                className="border rounded px-2 py-1 text-sm"
              >
                {statusOptions.map((opt) => (
                  <option key={opt.value} value={opt.value}>{opt.label}</option>
                ))}
              </select>
            ) : (
              <span className="text-sm">{status}</span>
            )}
          </div>
          {task.assignee && (
            <div className="flex items-center gap-2">
              <span className="text-sm font-medium w-20">담당자:</span>
              <span className="text-sm">{task.assignee.name}</span>
            </div>
          )}
          {task.due_date && (
            <div className="flex items-center gap-2">
              <span className="text-sm font-medium w-20">마감일:</span>
              <span className="text-sm">{new Date(task.due_date).toLocaleDateString()}</span>
            </div>
          )}
        </div>

        <div className="flex justify-between">
          <div>
            {canDelete && onDelete && (
              <Button
                variant="destructive"
                size="sm"
                onClick={() => {
                  if (confirm('정말 삭제하시겠습니까?')) {
                    onDelete(task.id);
                    onClose();
                  }
                }}
              >
                삭제
              </Button>
            )}
          </div>
          <Button variant="ghost" onClick={onClose}>
            닫기
          </Button>
        </div>
      </div>
    </div>
  );
}
```

**Step 3: CreateTaskModal 컴포넌트**

```typescript
// frontend/src/components/board/CreateTaskModal.tsx
import { useState } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { useCreateTask } from '@/hooks/useTasks';
import type { TaskStatus } from '@/types/task';

interface CreateTaskModalProps {
  projectId: string;
  isOpen: boolean;
  onClose: () => void;
}

export function CreateTaskModal({ projectId, isOpen, onClose }: CreateTaskModalProps) {
  const [title, setTitle] = useState('');
  const [description, setDescription] = useState('');
  const [status, setStatus] = useState<TaskStatus>('todo');
  const [dueDate, setDueDate] = useState('');
  const createTask = useCreateTask(projectId);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    await createTask.mutateAsync({
      title,
      description: description || undefined,
      status,
      due_date: dueDate || undefined,
    });
    setTitle('');
    setDescription('');
    setStatus('todo');
    setDueDate('');
    onClose();
  };

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
      <div className="bg-white rounded-lg p-6 w-full max-w-md">
        <h2 className="text-lg font-bold mb-4">새 태스크</h2>
        <form onSubmit={handleSubmit}>
          <div className="space-y-4">
            <div>
              <label className="text-sm font-medium">제목 *</label>
              <Input
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                placeholder="태스크 제목"
                required
              />
            </div>
            <div>
              <label className="text-sm font-medium">설명</label>
              <textarea
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                placeholder="태스크 설명 (선택)"
                className="w-full border rounded px-3 py-2 text-sm min-h-[80px]"
              />
            </div>
            <div>
              <label className="text-sm font-medium">상태</label>
              <select
                value={status}
                onChange={(e) => setStatus(e.target.value as TaskStatus)}
                className="w-full border rounded px-3 py-2"
              >
                <option value="todo">To Do</option>
                <option value="doing">Doing</option>
                <option value="done">Done</option>
                <option value="blocked">Blocked</option>
              </select>
            </div>
            <div>
              <label className="text-sm font-medium">마감일</label>
              <Input
                type="date"
                value={dueDate}
                onChange={(e) => setDueDate(e.target.value)}
              />
            </div>
          </div>
          <div className="flex justify-end gap-2 mt-6">
            <Button type="button" variant="ghost" onClick={onClose}>
              취소
            </Button>
            <Button type="submit" disabled={createTask.isPending}>
              {createTask.isPending ? '생성 중...' : '생성'}
            </Button>
          </div>
        </form>
      </div>
    </div>
  );
}
```

**Step 4: DashboardPage 수정**

```typescript
// frontend/src/pages/DashboardPage.tsx
import { useState } from 'react';
import { useAuth } from '@/hooks/useAuth';
import { useWorkspaces } from '@/hooks/useWorkspaces';
import { useProjects } from '@/hooks/useProjects';
import { useTasks } from '@/hooks/useTasks';
import { useUIStore } from '@/stores/uiStore';
import { Button } from '@/components/ui/button';
import { KanbanBoard } from '@/components/board/KanbanBoard';
import { BoardHeader } from '@/components/board/BoardHeader';
import { CreateTaskModal } from '@/components/board/CreateTaskModal';

export function DashboardPage() {
  const { user, logout } = useAuth();
  const {
    selectedWorkspaceId,
    selectedProjectId,
    setSelectedWorkspace,
    setSelectedProject,
    taskFilters,
  } = useUIStore();

  const { data: workspaces } = useWorkspaces();
  const { data: projects } = useProjects(selectedWorkspaceId);
  const { data: tasks, isLoading } = useTasks(selectedProjectId, {
    mineOnly: taskFilters.mineOnly,
  });

  const [isCreateModalOpen, setCreateModalOpen] = useState(false);

  const selectedProject = projects?.find((p) => p.id === selectedProjectId);

  return (
    <div className="min-h-screen bg-background">
      {/* Header */}
      <header className="border-b bg-card">
        <div className="container mx-auto px-4 py-4 flex items-center justify-between">
          <div className="flex items-center gap-4">
            <h1 className="text-xl font-bold">pslog</h1>
            {/* Workspace selector */}
            <select
              value={selectedWorkspaceId || ''}
              onChange={(e) => setSelectedWorkspace(e.target.value || null)}
              className="border rounded px-2 py-1 text-sm"
            >
              <option value="">워크스페이스 선택</option>
              {workspaces?.map((ws) => (
                <option key={ws.id} value={ws.id}>{ws.name}</option>
              ))}
            </select>
            {/* Project selector */}
            {selectedWorkspaceId && (
              <select
                value={selectedProjectId || ''}
                onChange={(e) => setSelectedProject(e.target.value || null)}
                className="border rounded px-2 py-1 text-sm"
              >
                <option value="">프로젝트 선택</option>
                {projects?.map((p) => (
                  <option key={p.id} value={p.id}>{p.name}</option>
                ))}
              </select>
            )}
          </div>
          <div className="flex items-center gap-4">
            <span className="text-sm text-muted-foreground">{user?.name}</span>
            <Button variant="ghost" size="sm" onClick={logout}>
              로그아웃
            </Button>
          </div>
        </div>
      </header>

      {/* Main */}
      <main className="container mx-auto px-4 py-6">
        {!selectedProjectId ? (
          <div className="text-center py-12">
            <p className="text-muted-foreground">
              워크스페이스와 프로젝트를 선택해주세요.
            </p>
          </div>
        ) : isLoading ? (
          <div className="text-center py-12">로딩 중...</div>
        ) : (
          <>
            <BoardHeader
              projectName={selectedProject?.name || ''}
              onCreateTask={() => setCreateModalOpen(true)}
            />
            <KanbanBoard
              tasks={tasks || []}
              onTaskClick={(task) => console.log('Task clicked:', task)}
            />
          </>
        )}
      </main>

      {/* Modals */}
      {selectedProjectId && (
        <CreateTaskModal
          projectId={selectedProjectId}
          isOpen={isCreateModalOpen}
          onClose={() => setCreateModalOpen(false)}
        />
      )}
    </div>
  );
}
```

**Step 5: 커밋**

```bash
git add frontend/src/pages/DashboardPage.tsx frontend/src/components/board/BoardHeader.tsx \
        frontend/src/components/board/CreateTaskModal.tsx frontend/src/components/board/TaskDetailModal.tsx
git commit -m "feat: integrate Kanban board into Dashboard with workspace/project selection"
```

---

### Task 2.6: 태스크 삭제 기능 연결

**Files:**
- Modify: `frontend/src/hooks/useTasks.ts`
- Modify: `frontend/src/services/api.ts`
- Modify: `frontend/src/pages/DashboardPage.tsx`

> Note: TaskDetailModal은 Task 2.5에서 이미 삭제 버튼을 포함하여 생성됨. 이 Task에서는 API 연결만 추가.

**Step 1: API 함수 추가**

```typescript
// frontend/src/services/api.ts 의 tasks에 추가
tasks: {
  // ... 기존 ...
  delete: (taskId: string) => apiClient.delete(`/tasks/${taskId}`),
},
```

**Step 2: Hook 추가**

```typescript
// frontend/src/hooks/useTasks.ts 에 추가
export function useDeleteTask() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (taskId: string) => api.tasks.delete(taskId),
    onSuccess: () => {
      queryClient.invalidateQueries({ predicate: q => q.queryKey[2] === 'tasks' });
    },
  });
}
```

**Step 3: DashboardPage에서 삭제 기능 연결**

```typescript
// frontend/src/pages/DashboardPage.tsx 에 추가
import { useTasks, useCreateTask, useDeleteTask } from '@/hooks/useTasks';
import { TaskDetailModal } from '@/components/board/TaskDetailModal';

// ... 컴포넌트 내부 ...
const [selectedTask, setSelectedTask] = useState<Task | null>(null);
const deleteTaskMutation = useDeleteTask();

const selectedProject = projects?.find((p) => p.id === selectedProjectId);
const myRole = selectedProject?.my_role ?? 'viewer';

const handleDeleteTask = (taskId: string) => {
  deleteTaskMutation.mutate(taskId);
};

// ... return 내부에 추가 ...
<KanbanBoard
  tasks={tasks || []}
  onTaskClick={(task) => setSelectedTask(task)}
/>

<TaskDetailModal
  task={selectedTask}
  myRole={myRole}
  isOpen={!!selectedTask}
  onClose={() => setSelectedTask(null)}
  onDelete={handleDeleteTask}
/>
```

**Step 4: 커밋**

```bash
git add frontend/src/services/api.ts frontend/src/hooks/useTasks.ts \
        frontend/src/pages/DashboardPage.tsx
git commit -m "feat: connect task delete functionality (owner only)"
```

---

### Task 2.7: 멤버 초대 UI 구현

**Files:**
- Modify: `frontend/src/types/workspace.ts`
- Modify: `frontend/src/services/api.ts`
- Create: `frontend/src/hooks/useWorkspaceMembers.ts`
- Create: `frontend/src/components/workspace/MemberList.tsx`
- Create: `frontend/src/components/workspace/InviteMemberModal.tsx`

**Step 1: WorkspaceMember 타입 추가**

```typescript
// frontend/src/types/workspace.ts 에 추가
export interface WorkspaceMember {
  id: string;
  user_id: string;
  role: WorkspaceRole;
  created_at: string;
  user: {
    id: string;
    name: string;
    email: string;
  };
}
```

**Step 2: API 함수 추가**

```typescript
// frontend/src/services/api.ts 의 workspaces에 추가
import type { WorkspaceMember } from '@/types/workspace';

workspaces: {
  // ... 기존 ...
  members: {
    list: (workspaceId: string) =>
      apiClient.get<WorkspaceMember[]>(`/workspaces/${workspaceId}/members`),
    add: (workspaceId: string, data: { email: string; role: WorkspaceRole }) =>
      apiClient.post<WorkspaceMember>(`/workspaces/${workspaceId}/members`, data),
    remove: (workspaceId: string, userId: string) =>
      apiClient.delete(`/workspaces/${workspaceId}/members/${userId}`),
  },
},
```

**Step 3: Hook 작성**

```typescript
// frontend/src/hooks/useWorkspaceMembers.ts
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '@/services/api';
import type { WorkspaceRole } from '@/types/workspace';

export function useWorkspaceMembers(workspaceId: string | null) {
  return useQuery({
    queryKey: ['workspaces', workspaceId, 'members'],
    queryFn: () => api.workspaces.members.list(workspaceId!).then(r => r.data),
    enabled: !!workspaceId,
  });
}

export function useAddMember(workspaceId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (data: { email: string; role: WorkspaceRole }) =>
      api.workspaces.members.add(workspaceId, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['workspaces', workspaceId, 'members'] });
    },
  });
}

export function useRemoveMember(workspaceId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (userId: string) =>
      api.workspaces.members.remove(workspaceId, userId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['workspaces', workspaceId, 'members'] });
    },
  });
}
```

**Step 4: InviteMemberModal 컴포넌트**

```typescript
// frontend/src/components/workspace/InviteMemberModal.tsx
import { useState } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { useAddMember } from '@/hooks/useWorkspaceMembers';
import type { WorkspaceRole } from '@/types/workspace';

interface InviteMemberModalProps {
  workspaceId: string;
  isOpen: boolean;
  onClose: () => void;
}

export function InviteMemberModal({ workspaceId, isOpen, onClose }: InviteMemberModalProps) {
  const [email, setEmail] = useState('');
  const [role, setRole] = useState<WorkspaceRole>('viewer');
  const [error, setError] = useState<string | null>(null);
  const addMember = useAddMember(workspaceId);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    try {
      await addMember.mutateAsync({ email, role });
      setEmail('');
      onClose();
    } catch (err: any) {
      if (err.response?.status === 404) {
        setError('해당 이메일의 사용자를 찾을 수 없습니다.');
      } else {
        setError('초대에 실패했습니다. 다시 시도해주세요.');
      }
    }
  };

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
      <div className="bg-white rounded-lg p-6 w-full max-w-md">
        <h2 className="text-lg font-bold mb-4">멤버 초대</h2>
        <form onSubmit={handleSubmit}>
          <div className="space-y-4">
            <div>
              <label className="text-sm font-medium">이메일</label>
              <Input
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="user@example.com"
                required
              />
            </div>
            <div>
              <label className="text-sm font-medium">권한</label>
              <select
                value={role}
                onChange={(e) => setRole(e.target.value as WorkspaceRole)}
                className="w-full border rounded px-3 py-2"
              >
                <option value="viewer">Viewer (읽기 전용)</option>
                <option value="editor">Editor (편집 가능)</option>
                <option value="owner">Owner (관리자)</option>
              </select>
            </div>
            {error && (
              <p className="text-sm text-red-500">{error}</p>
            )}
          </div>
          <div className="flex justify-end gap-2 mt-6">
            <Button type="button" variant="ghost" onClick={onClose}>
              취소
            </Button>
            <Button type="submit" disabled={addMember.isPending}>
              {addMember.isPending ? '초대 중...' : '초대'}
            </Button>
          </div>
        </form>
      </div>
    </div>
  );
}
```

**Step 5: 커밋**

```bash
git add frontend/src/services/api.ts frontend/src/hooks/useWorkspaceMembers.ts \
        frontend/src/components/workspace/
git commit -m "feat: implement workspace member invite UI (owner only)"
```

---

## Phase 3: ShareLink (Epic 6)

### Task 3.1: ShareLink API 구현

**Files:**
- Create: `backend/app/schemas/share_link.py`
- Create: `backend/app/services/share_link_service.py`
- Create: `backend/app/api/v1/endpoints/share_links.py`

(상세 구현은 Phase 1-2와 동일한 패턴)

---

### Task 3.2: Share View 페이지 구현

**Files:**
- Create: `frontend/src/pages/SharePage.tsx`
- Modify: `frontend/src/App.tsx` (라우트 추가)

---

## Phase 4: Week Tab (Epic 5)

### Task 4.1: Week 뷰 컴포넌트 구현

**Files:**
- Create: `frontend/src/components/week/WeekView.tsx`
- Create: `frontend/src/components/week/WeekColumn.tsx`

---

## 체크포인트

각 Phase 완료 후 검증 (2026-02 코드 기준으로 구현 확인된 항목 반영):

### Phase 1 완료 체크
- [ ] `alembic upgrade head` 성공
- [x] `GET /workspaces` 엔드포인트 구현
- [x] `POST /workspaces` 엔드포인트 구현
- [x] `GET /workspaces/{id}/projects` 엔드포인트 구현
- [x] `GET /projects/{id}/tasks` 엔드포인트 구현
- [x] TaskEvent 생성/상태변경 기록 로직 구현
- [x] `DELETE /tasks/{id}` Owner 권한 체크 로직 구현
- [x] `POST /workspaces/{id}/members` Owner 권한 체크 로직 구현
- [x] `GET /workspaces/{id}/members` 엔드포인트 구현

### Phase 2 완료 체크
- [x] 워크스페이스 선택 시 프로젝트 목록 표시
- [x] 프로젝트 선택 시 Kanban 보드 표시
- [x] 4컬럼(todo/doing/done/blocked) 렌더링
- [x] "내 태스크만" 토글 동작 (서버 필터링)
- [x] 새 태스크 생성 후 보드에 표시
- [x] Owner만 태스크 삭제 버튼 표시/동작
- [ ] Owner만 멤버 초대 UI 표시/동작

### Phase 3 완료 체크
- [ ] Owner만 공유 링크 생성 가능
- [x] 공유 URL 접속 시 read-only 보드 표시
- [x] 철회된 토큰 접근 시 에러 표시

### Phase 4 완료 체크
- [x] Week 탭 전환 동작
- [x] due_date 기준 주간 그룹핑
- [ ] "마감일 미지정" 그룹 표시
