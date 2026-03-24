import hashlib
import hmac
import logging
from datetime import datetime, timezone
from typing import IO, Sequence
from pathlib import Path
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from orchestrator.config import AppConfig
from orchestrator.models import Deployment, PullRequest, PortAllocation
from orchestrator.db import get_session
from orchestrator.git_utils import clone_bare, fetch_bare, add_worktree, remove_worktree
from orchestrator.docker_utils import compose_up, compose_down
from orchestrator.ports import allocate_ports, release_ports
from orchestrator.nginx import generate_nginx_config, reload_nginx

logger = logging.getLogger(__name__)


def _log(log_file: IO[str], message: str) -> None:
    """Write a timestamped message to the deployment log file."""
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    log_file.write(f"[{ts}] {message}\n")
    log_file.flush()


async def execute_deployments(config: AppConfig) -> None:
    # Ensure logs directory exists
    config.logs_path.mkdir(parents=True, exist_ok=True)

    async with get_session() as session:
        query = (
            select(Deployment)
            .where(
                Deployment.status.in_(
                    ["needs_bringup", "needs_update", "needs_teardown"]
                )
            )
            .options(
                selectinload(Deployment.pull_request).selectinload(
                    PullRequest.repository
                ),
                selectinload(Deployment.ports),
            )
        )
        result = await session.execute(query)
        pending_deployments = result.scalars().all()
        logger.info("Found %d pending deployments", len(pending_deployments))

        if not pending_deployments:
            return

        for dep in pending_deployments:
            logger.info("Processing deployment %s", dep.project_name)
            repo = dep.pull_request.repository
            repo_config = config.repositories.get(repo.name)
            if not repo_config:
                logger.error(f"Config missing for repo {repo.name}")
                continue

            bare_path = config.bare_clones_path / repo.name
            worktree_path = config.worktrees_path / dep.project_name
            repo_url = f"https://github.com/{repo.name}.git"
            log_path = config.logs_path / f"{dep.project_name}.log"

            # Truncate log file for this deployment action
            with open(log_path, "w") as log_file:
                _log(log_file, f"=== Deployment action: {dep.status} ===")
                _log(log_file, f"Project: {dep.project_name}")
                _log(log_file, f"Repository: {repo.name}")
                _log(
                    log_file, f"PR #{dep.pull_request.number}: {dep.pull_request.title}"
                )
                _log(log_file, "")

                try:
                    if dep.status in ("needs_bringup", "needs_update"):
                        _log(log_file, f"Ensuring bare repo exists at {bare_path}")
                        await clone_bare(repo_url, bare_path)

                        _log(log_file, f"Fetching {dep.pull_request.head_sha}")
                        await fetch_bare(bare_path, dep.pull_request.head_sha)

                        _log(
                            log_file, f"Stopping old deployment for {dep.project_name}"
                        )
                        await compose_down(
                            worktree_path,
                            dep.project_name,
                            repo_config.compose_files,
                            False,
                            log_file=log_file,
                        )

                        _log(log_file, f"Removing old worktree {worktree_path}")
                        await remove_worktree(bare_path, worktree_path)

                        _log(log_file, f"Provisioning new worktree {worktree_path}")
                        await add_worktree(
                            bare_path, worktree_path, dep.pull_request.head_sha
                        )

                        ports_by_name = {p.name: p for p in repo_config.ports}
                        ports: Sequence[PortAllocation] = dep.ports
                        env_vars: dict[str, str] = {}
                        env_vars["ORCHESTRATOR_SECRET_SEED"] = hmac.new(
                            config.app_secret_seed.encode(),
                            dep.project_name.encode(),
                            hashlib.sha256,
                        ).hexdigest()
                        if len(dep.ports) != len(repo_config.ports):
                            _log(log_file, "Ports changed, reallocating")
                            await release_ports(session, dep.id)
                            allocated = await allocate_ports(
                                session,
                                dep.id,
                                repo_config.ports,
                                config.port_pool_start,
                                config.port_pool_end,
                            )
                            ports = allocated
                        for p in ports:
                            port_config = ports_by_name[p.name]
                            env_vars[port_config.environment_variable] = str(p.port)
                            env_vars[f"{port_config.environment_variable}_HOSTNAME"] = (
                                p.hostname(config)
                            )

                        _log(
                            log_file,
                            f"Building and starting containers at {worktree_path}",
                        )
                        await compose_up(
                            worktree_path,
                            dep.project_name,
                            repo_config.compose_files,
                            env_vars,
                            log_file=log_file,
                        )
                        _log(log_file, "")
                        _log(log_file, "=== Deployment complete ===")
                        dep.status = "up"

                    elif dep.status == "needs_teardown":
                        _log(log_file, f"Stopping containers for {dep.project_name}")
                        await compose_down(
                            worktree_path,
                            dep.project_name,
                            repo_config.compose_files,
                            True,
                            log_file=log_file,
                        )

                        _log(log_file, f"Removing worktree {worktree_path}")
                        await remove_worktree(bare_path, worktree_path)

                        _log(log_file, f"Releasing ports for {dep.project_name}")
                        await release_ports(session, dep.id)

                        _log(log_file, "")
                        _log(log_file, "=== Teardown complete ===")
                        dep.status = "torn_down"

                except Exception as e:
                    _log(log_file, "")
                    _log(log_file, f"!!! ERROR: {e}")
                    logger.error(
                        f"Failed to execute {dep.status} for {dep.project_name}: {e}"
                    )
                    dep.status = "error"

        await session.commit()

        logging.info("Regenerating NGINX config")
        # Afterwards, regenerate NGINX config
        query_all = (
            select(Deployment)
            .where(Deployment.status == "up")
            .options(
                selectinload(Deployment.pull_request).selectinload(
                    PullRequest.repository
                ),
                selectinload(Deployment.ports),
            )
        )
        all_up = (await session.execute(query_all)).scalars().all()

        try:
            await generate_nginx_config(
                list(all_up),
                config,
                Path(__file__).parent / "templates" / "nginx.conf.j2",
                config.nginx_config_path,
            )
        except Exception as e:
            logger.error(f"Failed to generate nginx config: {e}")

        try:
            await reload_nginx()
        except Exception as e:
            logger.error(f"Failed to reload nginx: {e}")
