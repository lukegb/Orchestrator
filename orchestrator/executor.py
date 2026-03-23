import hashlib
import hmac
import logging
from typing import Sequence
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


async def execute_deployments(config: AppConfig) -> None:
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
            repo_url = f"https://github.com/{repo.name}.git"  # Using public checkout, or should use auth if private

            try:
                if dep.status in ("needs_bringup", "needs_update"):
                    logger.info("Ensuring bare repo exists at %s", bare_path)
                    await clone_bare(repo_url, bare_path)
                    logger.info(
                        "Fetching %s for %s", dep.pull_request.head_sha, repo_url
                    )
                    await fetch_bare(bare_path, dep.pull_request.head_sha)
                    logger.info("Downing old deployment for %s", dep.project_name)
                    await compose_down(
                        worktree_path,
                        dep.project_name,
                        repo_config.compose_files,
                        False,
                    )
                    logger.info("Removing old worktree %s", worktree_path)
                    await remove_worktree(bare_path, worktree_path)
                    logger.info("Provisioning new worktree %s", worktree_path)
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
                        logger.info("Ports changed, reallocating")
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

                    logger.info("Bringing up environment at %s", worktree_path)
                    await compose_up(
                        worktree_path,
                        dep.project_name,
                        repo_config.compose_files,
                        env_vars,
                    )
                    logger.info("Done! Setting as up.")
                    dep.status = "up"

                elif dep.status == "needs_teardown":
                    logger.info("Bringing down environment %s", worktree_path)
                    await compose_down(
                        worktree_path, dep.project_name, repo_config.compose_files, True
                    )
                    logger.info("Removing worktree %s", worktree_path)
                    await remove_worktree(bare_path, worktree_path)
                    logger.info("Releasing ports for %s", dep.project_name)
                    await release_ports(session, dep.id)
                    logger.info("Done! Setting as torn down.")
                    dep.status = "torn_down"

            except Exception as e:
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
