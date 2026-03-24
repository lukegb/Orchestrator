import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional


@dataclass
class GitHubConfig:
    token: Optional[str] = None
    app_id: Optional[str] = None
    installation_id: Optional[str] = None
    private_key: Optional[str] = None


@dataclass
class OAuthConfig:
    client_id: str
    client_secret: str
    session_secret: str


@dataclass
class RepoPort:
    name: str
    environment_variable: str
    is_http: bool = True
    is_default: bool = False


@dataclass
class RepoConfig:
    name: str
    trusted_users: List[str] = field(default_factory=list)
    ports: List[RepoPort] = field(default_factory=list)
    compose_files: List[str] = field(default_factory=list)
    custom_actions: Dict[str, str] = field(default_factory=dict)


@dataclass
class AppConfig:
    github: GitHubConfig
    oauth: OAuthConfig
    database_url: str
    port_pool_start: int
    port_pool_end: int
    bare_clones_path: Path
    worktrees_path: Path
    nginx_config_path: Path
    logs_path: Path
    app_secret_seed: str
    domain_suffix: str
    repositories: Dict[str, RepoConfig]


def load_config(path: Path | str) -> AppConfig:
    with open(path, "rb") as f:
        data = tomllib.load(f)

    github_data = data.get("github", {})
    github = GitHubConfig(
        token=github_data.get("token"),
        app_id=github_data.get("app_id"),
        installation_id=github_data.get("installation_id"),
        private_key=github_data.get("private_key"),
    )

    oauth_data = data.get("oauth", {})
    oauth = OAuthConfig(
        client_id=oauth_data.get("client_id", ""),
        client_secret=oauth_data.get("client_secret", ""),
        session_secret=oauth_data.get("session_secret", ""),
    )

    port_pool = data.get("port_pool", {})
    start = port_pool.get("start", 10000)
    end = port_pool.get("end", 20000)

    paths = data.get("paths", {})
    bare_clones = Path(paths.get("bare_clones", "/var/lib/orchestrator/bare"))
    worktrees = Path(paths.get("worktrees", "/var/lib/orchestrator/worktrees"))
    nginx_config_path = Path(
        paths.get("nginx_config", "/etc/nginx/sites-available/orchestrator")
    )
    logs = Path(paths.get("logs", "/var/lib/orchestrator/logs"))

    repositories: Dict[str, RepoConfig] = {}
    for repo_name, repo_data in data.get("repositories", {}).items():
        ports: list[RepoPort] = []
        for port in repo_data.get("ports", []):
            ports.append(
                RepoPort(
                    name=port.get("name"),
                    environment_variable=port.get("environment_variable"),
                    is_http=port.get("is_http", True),
                    is_default=port.get("is_default", False),
                )
            )

        repositories[repo_name] = RepoConfig(
            name=repo_name,
            trusted_users=repo_data.get("trusted_users", []),
            ports=ports,
            compose_files=repo_data.get("compose_files", ["docker-compose.yml"]),
            custom_actions=repo_data.get("custom_actions", {}),
        )

    database_data = data.get("database", {})
    database_url = database_data.get("url", "sqlite+aiosqlite:///orchestrator.db")

    return AppConfig(
        github=github,
        oauth=oauth,
        database_url=database_url,
        domain_suffix=data.get("domain_suffix", "emfcamp-test.org"),
        app_secret_seed=data.get("app_secret_seed", ""),
        port_pool_start=start,
        port_pool_end=end,
        bare_clones_path=bare_clones,
        worktrees_path=worktrees,
        nginx_config_path=nginx_config_path,
        logs_path=logs,
        repositories=repositories,
    )
