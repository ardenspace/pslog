import uuid
from datetime import datetime

from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.workspace import WorkspaceRole


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))

    name: Mapped[str]
    description: Mapped[str | None]
    discord_webhook_url: Mapped[str | None] = mapped_column(default=None)

    # Phase 1 — task-automation 설계서 §4.1
    git_repo_url: Mapped[str | None] = mapped_column(default=None)
    git_default_branch: Mapped[str] = mapped_column(default="main")
    plan_path: Mapped[str] = mapped_column(default="PLAN.md")
    decisions_path: Mapped[str] = mapped_column(default="DECISIONS.md", server_default="DECISIONS.md")
    handoff_dir: Mapped[str] = mapped_column(default="handoffs/")
    tasks_dir: Mapped[str] = mapped_column(default="docs/tasks/", server_default="docs/tasks/")
    last_synced_commit_sha: Mapped[str | None] = mapped_column(default=None)
    webhook_secret_encrypted: Mapped[bytes | None] = mapped_column(default=None)
    # Phase 4 — task-automation 설계서 §9 (GitHub PAT Fernet 암호화 저장)
    github_pat_encrypted: Mapped[bytes | None] = mapped_column(default=None)

    # Phase 6 — Discord 알림 cooldown / auto-disable
    discord_consecutive_failures: Mapped[int] = mapped_column(default=0, nullable=False)
    discord_disabled_at: Mapped[datetime | None] = mapped_column(default=None)

    # handoff 누락 알림 스킵 브랜치 — 쉼표/줄바꿈 split, main 은 코드 레벨 하드코드 추가
    handoff_skip_branches: Mapped[str] = mapped_column(default="", nullable=False)

    def __init__(self, **kwargs: object) -> None:
        kwargs.setdefault("git_default_branch", "main")
        kwargs.setdefault("plan_path", "PLAN.md")
        kwargs.setdefault("decisions_path", "DECISIONS.md")
        kwargs.setdefault("handoff_dir", "handoffs/")
        kwargs.setdefault("tasks_dir", "docs/tasks/")
        kwargs.setdefault("discord_consecutive_failures", 0)
        kwargs.setdefault("handoff_skip_branches", "")
        super().__init__(**kwargs)

    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    workspace: Mapped["Workspace"] = relationship(back_populates="projects")
    members: Mapped[list["ProjectMember"]] = relationship(back_populates="project", cascade="all, delete-orphan")
    tasks: Mapped[list["Task"]] = relationship(back_populates="project", cascade="all, delete-orphan")
    share_links: Mapped[list["ShareLink"]] = relationship(back_populates="project", cascade="all, delete-orphan")


class ProjectMember(Base):
    __tablename__ = "project_members"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    role: Mapped[WorkspaceRole] = mapped_column(default=WorkspaceRole.VIEWER)

    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)

    # Relationships
    project: Mapped["Project"] = relationship(back_populates="members")
    user: Mapped["User"] = relationship(back_populates="project_memberships")
