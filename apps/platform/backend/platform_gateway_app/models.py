"""Auth + entitlement tables for the gateway (SQLAlchemy 2.0).

Three small tables back per-user app visibility:
  users        — who can log in (+ role: user / admin / super-admin)
  entitlements — user -> app_id rows (which apps a non-admin user may see/reach)
  sessions     — opaque server-side session tokens (revocable, with expiry)

and two more back per-user data visibility INSIDE an app:
  workspaces        — a named set of users (e.g. one classroom)
  workspace_members — user -> workspace; co-members see each other's owner-scoped records

SQLite today (a single file on a mounted volume); the seam is a plain SQLAlchemy
URL, so it can point at Postgres later without code changes.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    # Super-admin: the platform root of trust (the seed owner). All-access to every app,
    # and the only role that can grant/revoke super-admin. A plain admin manages users but
    # reaches apps only via explicit entitlements (a host shell included).
    is_superadmin: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    entitlements: Mapped[list["Entitlement"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    # Same cascade as entitlements, and for a sharper reason: ids get reused. A membership row
    # outliving its user would make whoever next takes that id a peer of a classroom they were
    # never added to -- silently, and with student data on the other side of it. The FK carries
    # ondelete=CASCADE too, but SQLite only enforces that with PRAGMA foreign_keys=ON, so the
    # ORM cascade is what actually holds here. A test asserts the row is gone, because this
    # shipped without it and the test is what found that.
    workspace_memberships: Mapped[list["WorkspaceMember"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    sessions: Mapped[list["SessionRow"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class Entitlement(Base):
    __tablename__ = "entitlements"
    __table_args__ = (UniqueConstraint("user_id", "app_id", name="uq_user_app"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    app_id: Mapped[str] = mapped_column(String(64))

    user: Mapped["User"] = relationship(back_populates="entitlements")


class SessionRow(Base):
    __tablename__ = "sessions"

    token: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    user: Mapped["User"] = relationship(back_populates="sessions")


class Setting(Base):
    """Platform-wide key/value config (e.g. the default theme palette + mode)."""
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(String(255))


class GeneratedModelDesc(Base):
    """Cache of broker-LLM-generated category + blurb for models that have NO curated entry in
    model_catalog. Written by the scheduled 'Model pool scan'; read by the Models tab, which
    labels these 'auto'. Keyed by model name so each model is described once."""
    __tablename__ = "generated_model_desc"

    name: Mapped[str] = mapped_column(String(128), primary_key=True)
    category: Mapped[str] = mapped_column(String(24))
    blurb: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RailState(Base):
    """An admin's decision to switch a rail OFF, from the Rail Manager.

    Only DISABLED rails get a row — absence means enabled, so a fresh install and a new rail both
    default to on without a migration. Kept in the DB rather than in PLATFORM_ENABLED_APPS on
    purpose: that variable is read once at import (the gateway builds GatewaySettings while
    registering its static mounts), so changing it means editing a file AND restarting, while a
    DB row takes effect on the next request.

    This is availability control, not enforcement — the same line the model pool draws. A disabled
    rail disappears from the launcher, but an open session keeps working rather than 403-ing
    mid-task, and nothing is deleted. Re-enabling is one click.
    """
    __tablename__ = "rail_state"

    rail_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    disabled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    disabled_by: Mapped[str | None] = mapped_column(String(64), nullable=True)


class Workspace(Base):
    """A named set of users who see each other's owner-scoped data as their own.

    The case this exists for: a teacher and her classroom aide need to see and act on each
    other's work, and blanket-opening a rail is not an option because many unrelated teachers
    share the platform. So sharing is an explicit, admin-created membership rather than a
    property of a role or a rail.

    A GROUP rather than a pair, because a pair cannot express the situations that actually
    turn up -- a second aide, a co-teacher, a sub covering a week -- and widening a pair later
    is a schema change. Membership is SYMMETRIC: there is no direction to get backwards.

    A user may belong to several workspaces; their peer set is the union, minus themselves.
    This is deliberately NOT an entitlement: entitlements decide which APPS you can reach,
    this decides whose DATA you see inside one. Keeping them separate means granting an aide
    a new rail does not silently widen who can read the teacher.
    """
    __tablename__ = "workspaces"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    members: Mapped[list["WorkspaceMember"]] = relationship(
        back_populates="workspace", cascade="all, delete-orphan")


class WorkspaceMember(Base):
    """One user's membership of one workspace.

    ondelete=CASCADE on both sides plus delete-orphan: deleting a user or a workspace must not
    leave a membership row behind. A stale row here would be worse than an orphan elsewhere --
    ids get reused, and a resurrected membership silently grants somebody sight of student data.
    """
    __tablename__ = "workspace_members"
    __table_args__ = (UniqueConstraint("workspace_id", "user_id", name="uq_workspace_user"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True)

    workspace: Mapped["Workspace"] = relationship(back_populates="members")
    user: Mapped["User"] = relationship(back_populates="workspace_memberships")


class UserTheme(Base):
    """A user's personal theme override. A NULL column follows the platform default;
    no row at all means 'use the platform default for everything'."""
    __tablename__ = "user_theme"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    palette: Mapped[str | None] = mapped_column(String(32), nullable=True)
    mode: Mapped[str | None] = mapped_column(String(16), nullable=True)


class Schedule(Base):
    """One scheduled maintenance task for a rail (the central platform scheduler's source of
    truth). Seeded from the task registry with each task's default recurrence; an admin edits the
    recurrence/enabled from the Console. ``next_run`` is the cached computed fire time (UTC)."""
    __tablename__ = "schedules"
    __table_args__ = (UniqueConstraint("rail", "task_id", name="uq_rail_task"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    rail: Mapped[str] = mapped_column(String(64), index=True)
    task_id: Mapped[str] = mapped_column(String(64))
    recurrence: Mapped[str] = mapped_column(String(512))       # JSON recurrence dict
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # Anchor for "every N weeks/months" interval counting (set when the recurrence is saved), so a
    # multi-week/-month cadence is relative to when it was configured rather than a fixed epoch.
    anchor: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_run: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_run: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_status: Mapped[str | None] = mapped_column(String(255), nullable=True)
