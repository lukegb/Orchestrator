import asyncio
from datetime import datetime
import logging

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from orchestrator.config import AppConfig
from orchestrator.db import get_session
from orchestrator.models import Repository, TrustedUser, PullRequest, Deployment
from orchestrator.github import GitHubClient


logger = logging.getLogger(__name__)


async def sync_repository(
    session, client: GitHubClient, repo_name: str, config: AppConfig
) -> None:
    owner, repo = repo_name.split("/")

    # Get or create Repo
    db_repo = (
        (await session.execute(select(Repository).where(Repository.name == repo_name)))
        .scalars()
        .first()
    )
    if not db_repo:
        db_repo = Repository(name=repo_name)
        session.add(db_repo)
        await session.flush()

    # Sync trusted users
    repo_config = config.repositories.get(repo_name)
    static_trusted = repo_config.trusted_users if repo_config else []

    try:
        github_trusted = await client.get_collaborators(owner, repo)
    except Exception:
        logger.exception(f"Failed to fetch collaborators for {repo_name}")
        github_trusted = []

    all_trusted = set(static_trusted + github_trusted)

    # Update DB TrustedUsers
    tu_query = select(TrustedUser).where(TrustedUser.repository_id == db_repo.id)
    tu_result = await session.execute(tu_query)
    existing_users = {tu.username: tu for tu in tu_result.scalars().all()}

    for username in all_trusted:
        if username not in existing_users:
            tu = TrustedUser(username=username, repository_id=db_repo.id)
            session.add(tu)

    for username, tu in existing_users.items():
        if username not in all_trusted:
            await session.delete(tu)

    await session.commit()

    # Sync PRs
    try:
        prs = await client.get_pull_requests(owner, repo)
    except Exception:
        logger.exception(f"Failed to fetch PRs for {repo_name}")
        return

    prs_query = (
        select(PullRequest)
        .where(PullRequest.repository_id == db_repo.id)
        .options(selectinload(PullRequest.deployments))
    )
    prs_result = await session.execute(prs_query)
    existing_prs = {pr.number: pr for pr in prs_result.scalars().all()}

    for pr_data in prs:
        number = pr_data["number"]
        is_open = pr_data["state"] == "OPEN"
        author = pr_data["author"]["login"] if pr_data.get("author") else "unknown"
        head_sha = pr_data["headRefOid"]
        head_ref = pr_data["headRef"] or {}
        head_ref_github_graphql_id = head_ref.get("id", "")
        base_ref = pr_data.get("baseRefName")
        updated_at = datetime.fromisoformat(pr_data["updatedAt"].replace("Z", "+00:00"))

        pr = existing_prs.get(number)

        needs_update = False

        if not pr:
            pr = PullRequest(
                number=number,
                title=pr_data["title"],
                repository_id=db_repo.id,
                head_sha=head_sha,
                head_ref_github_graphql_id=head_ref_github_graphql_id,
                base_ref=base_ref,
                is_open=is_open,
                author=author,
                updated_at=updated_at,
                deployments=[],
            )
            session.add(pr)
            await session.flush()
            existing_prs[number] = pr
        else:
            pr.title = pr_data["title"]
            pr.base_ref = base_ref
            if pr.head_sha != head_sha:
                pr.head_sha = head_sha
                pr.head_ref_github_graphql_id = head_ref_github_graphql_id
                needs_update = True
            if pr.is_open != is_open:
                pr.is_open = is_open
            pr.updated_at = updated_at

        # Handle deployments
        is_trusted = author in all_trusted
        deployment = pr.deployments[0] if pr.deployments else None

        if not pr.is_open:
            if deployment and deployment.status not in ("needs_teardown", "torn_down"):
                deployment.status = "needs_teardown"
        else:
            if deployment:
                if needs_update and deployment.status in ("up",):
                    deployment.status = "needs_update"
            elif is_trusted:
                # new PR by trusted user -> bring up
                # generate project name
                from orchestrator.git_utils import generate_project_name

                project_name = generate_project_name()
                deployment = Deployment(
                    pull_request_id=pr.id,
                    project_name=project_name,
                    status="needs_bringup",
                )
                session.add(deployment)
                pr.deployments.append(deployment)

    await session.commit()


async def sync_all(config: AppConfig, client: GitHubClient) -> None:
    async with get_session() as session:
        for repo_name in config.repositories.keys():
            await sync_repository(session, client, repo_name, config)


async def run_sync_loop(
    config: AppConfig, interval: int = 60, once: bool = False
) -> None:
    from orchestrator.executor import execute_deployments

    while True:
        try:
            client = GitHubClient(config)
            try:
                logger.info("Beginning sync of data from GitHub")
                await sync_all(config, client)

                logger.info("Executing pending deployments")
                await execute_deployments(config, client)
            finally:
                await client.close()
        except Exception:
            logger.exception("Sync loop error")
        if once:
            return
        await asyncio.sleep(interval)
