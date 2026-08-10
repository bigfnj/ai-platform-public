"""Auth + entitlement tables for the gateway (SQLAlchemy 2.0).

Three small tables back per-user app visibility:
  users        — who can log in (+ role: user / admin / super-admin)
  entitlements — user -> app_id rows (which apps a non-admin user may see/reach)
  sessions     — opaque server-side session tokens (revocable, with expiry)

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
