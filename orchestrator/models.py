from typing import List
from datetime import datetime
from sqlalchemy import String, Integer, ForeignKey, Boolean, DateTime, and_
from sqlalchemy.orm import Mapped, mapped_column, DeclarativeBase, relationship

from orchestrator.config import AppConfig


class Base(DeclarativeBase):
    pass


class Repository(Base):
    __tablename__ = "repository"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, index=True)

    trusted_users: Mapped[List["TrustedUser"]] = relationship(
        back_populates="repository"
    )
    pull_requests: Mapped[List["PullRequest"]] = relationship(
        back_populates="repository"
    )
    open_pull_requests: Mapped[List["PullRequest"]] = relationship(
        back_populates="repository",
        viewonly=True,
        primaryjoin=lambda: and_(
            Repository.id == PullRequest.repository_id,
            PullRequest.is_open.is_(True),
        ),
        order_by=lambda: PullRequest.number.desc(),
    )


class TrustedUser(Base):
    __tablename__ = "trusted_user"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(255))
    repository_id: Mapped[int] = mapped_column(ForeignKey("repository.id"))

    repository: Mapped["Repository"] = relationship(back_populates="trusted_users")


class PullRequest(Base):
    __tablename__ = "pull_request"
    id: Mapped[int] = mapped_column(primary_key=True)
    number: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(255))
    repository_id: Mapped[int] = mapped_column(ForeignKey("repository.id"))
    head_sha: Mapped[str] = mapped_column(String(40))
    is_open: Mapped[bool] = mapped_column(Boolean, default=True)
    author: Mapped[str] = mapped_column(String(255))
    updated_at: Mapped[datetime] = mapped_column(DateTime)

    repository: Mapped["Repository"] = relationship(back_populates="pull_requests")
    deployments: Mapped[List["Deployment"]] = relationship(
        back_populates="pull_request"
    )


class Deployment(Base):
    __tablename__ = "deployment"
    id: Mapped[int] = mapped_column(primary_key=True)
    pull_request_id: Mapped[int] = mapped_column(ForeignKey("pull_request.id"))
    project_name: Mapped[str] = mapped_column(String(255), unique=True)
    status: Mapped[str] = mapped_column(
        String(50)
    )  # 'up', 'needs_update', 'needs_teardown'

    pull_request: Mapped["PullRequest"] = relationship(back_populates="deployments")
    ports: Mapped[List["PortAllocation"]] = relationship(back_populates="deployment")


class PortAllocation(Base):
    __tablename__ = "port_allocation"
    id: Mapped[int] = mapped_column(primary_key=True)
    deployment_id: Mapped[int] = mapped_column(ForeignKey("deployment.id"))
    port: Mapped[int] = mapped_column(Integer, unique=True)
    name: Mapped[str] = mapped_column(String(50))

    deployment: Mapped["Deployment"] = relationship(back_populates="ports")

    def hostname(self, app_config: AppConfig) -> str:
        repo_config = app_config.repositories[
            self.deployment.pull_request.repository.name
        ]
        for port in repo_config.ports:
            if port.name == self.name:
                return (
                    f"{self.deployment.project_name}.{app_config.domain_suffix}"
                    if port.is_default
                    else f"{port.name}-{self.deployment.project_name}.{app_config.domain_suffix}"
                )
        raise ValueError(f"Port {self.name} not found in repository config")
