import os
from contextlib import asynccontextmanager
from pathlib import Path

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response
from starlette.routing import Route
from starlette.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from orchestrator.config import load_config
from orchestrator.db import init_db, get_session
from orchestrator.git_utils import generate_project_name
from orchestrator.github import GitHubClient
from orchestrator.models import Deployment, PullRequest, Repository, TrustedUser
from orchestrator.oauth import oauth, setup_oauth
from orchestrator.sync import sync_all


config_path = os.environ.get("ORCHESTRATOR_CONFIG", "config.toml")
app_config = load_config(config_path)
setup_oauth(app_config)

# Starlette templates
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


@asynccontextmanager
async def lifespan(app: Starlette):
    # Initialize DB
    init_db(app_config.database_url)
    yield


async def index(request: Request) -> Response:
    user = request.session.get("user")

    if user is None:
        return templates.TemplateResponse(request, "login.html", {})

    login_name = user["login"]
    repositories = []
    error = request.query_params.get("error")

    async with get_session() as session:
        # Check user access (simplified: get all repos where user is trusted)
        repo_query = select(TrustedUser.repository_id).where(
            TrustedUser.username == login_name
        )
        repo_result = await session.execute(repo_query)
        trusted_repo_ids = {rid for rid in repo_result.scalars().all()}

        if trusted_repo_ids:
            query = (
                select(Repository)
                .where(Repository.id.in_(trusted_repo_ids))
                .options(
                    selectinload(Repository.open_pull_requests)
                    .selectinload(PullRequest.deployments)
                    .selectinload(Deployment.ports)
                )
            )
            result = await session.execute(query)
            repositories = list(result.scalars().all())

        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "user": user,
                "repositories": repositories,
                "app_config": app_config,
                "error": error,
            },
        )


async def login(request: Request) -> Response:
    redirect_uri = request.url_for("auth")
    return await oauth.github.authorize_redirect(request, redirect_uri)


async def auth(request: Request) -> Response:
    token = await oauth.github.authorize_access_token(request)
    resp = await oauth.github.get("user", token=token)
    user = resp.json()
    request.session["user"] = user
    return RedirectResponse(url="/")


async def logout(request: Request) -> Response:
    request.session.pop("user", None)
    return RedirectResponse(url="/")


async def sync_endpoint(request: Request) -> Response:
    # Trigger a manual sync inline
    # Should authenticate or only allow trusted users
    if not request.session.get("user"):
        return Response("Unauthorized", status_code=401)

    await sync_all(app_config)
    return RedirectResponse(url="/", status_code=303)


async def deployment_action(request: Request) -> Response:
    if not request.session.get("user"):
        return Response("Unauthorized", status_code=401)

    dep_id = request.path_params["id"]
    action_type = request.query_params.get("type")

    async with get_session() as session:
        query = (
            select(Deployment)
            .where(Deployment.id == dep_id)
            .options(
                selectinload(Deployment.pull_request).selectinload(
                    PullRequest.repository
                )
            )
        )
        result = await session.execute(query)
        dep = result.scalars().first()

        if not dep:
            return Response("Not found", status_code=404)

        if action_type == "update":
            if dep.status == "ok":
                dep.status = "needs_update"
            else:
                dep.status = "needs_bringup"
        elif action_type == "teardown":
            dep.status = "needs_teardown"
        elif action_type == "custom":
            # For now, custom actions are fire-and-forget or handled separately.
            # Storing them as a need isn't supported yet in the models without a dedicated field,
            # so we'll just log it or pass.
            pass

        await session.commit()
    return RedirectResponse(url="/", status_code=303)


async def manual_deploy(request: Request) -> Response:
    if not request.session.get("user"):
        return Response("Unauthorized", status_code=401)

    pr_id = request.path_params["id"]
    form = await request.form()
    expected_head_sha = form.get("expected_head_sha", "")

    if not expected_head_sha:
        return Response("Missing expected_head_sha", status_code=400)

    async with get_session() as session:
        query = (
            select(PullRequest)
            .where(PullRequest.id == pr_id)
            .options(
                selectinload(PullRequest.repository),
                selectinload(PullRequest.deployments),
            )
        )
        result = await session.execute(query)
        pr = result.scalars().first()

        if not pr:
            return Response("Pull request not found", status_code=404)

        if not pr.is_open:
            return RedirectResponse(url="/?error=pr_closed", status_code=303)

        if pr.deployments:
            return RedirectResponse(
                url="/?error=already_deployed", status_code=303
            )

        # Verify the commit hasn't changed by checking GitHub
        owner, repo = pr.repository.name.split("/")
        client = GitHubClient(app_config)
        try:
            live_head_sha = await client.get_pull_request_head_sha(
                owner, repo, pr.number
            )
        finally:
            await client.close()

        if live_head_sha != expected_head_sha:
            return RedirectResponse(
                url="/?error=commit_changed", status_code=303
            )

        # Create deployment
        project_name = generate_project_name()
        deployment = Deployment(
            pull_request_id=pr.id,
            project_name=project_name,
            status="needs_bringup",
        )
        session.add(deployment)
        await session.commit()

    return RedirectResponse(url="/", status_code=303)


routes = [
    Route("/", index, methods=["GET"]),
    Route("/login", login, methods=["GET"]),
    Route("/auth", auth, methods=["GET"]),
    Route("/logout", logout, methods=["GET", "POST"]),
    Route("/api/sync", sync_endpoint, methods=["POST"]),
    Route("/api/deployments/{id:int}/action", deployment_action, methods=["POST"]),
    Route(
        "/api/pull-requests/{id:int}/deploy", manual_deploy, methods=["POST"]
    ),
]

middleware = [Middleware(SessionMiddleware, secret_key=app_config.oauth.session_secret)]

app = Starlette(
    routes=routes,
    middleware=middleware,
    lifespan=lifespan,
)
