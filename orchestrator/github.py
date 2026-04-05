import time
from typing import Any, Dict, List, Optional
import httpx
from authlib.jose import jwt  # type: ignore

from orchestrator.config import AppConfig


class GitHubAPIError(Exception):
    pass


class GitHubClient:
    def __init__(self, config: AppConfig) -> None:
        self.config = config.github
        self.client = httpx.AsyncClient(
            base_url="https://api.github.com",
            headers={"Accept": "application/vnd.github.v3+json"},
        )
        self._installation_token: Optional[str] = None
        self._token_expires_at: float = 0

    async def _get_auth_header(self) -> str:
        if self.config.token:
            return f"Bearer {self.config.token}"

        if (
            not self.config.app_id
            or not self.config.private_key
            or not self.config.installation_id
        ):
            raise ValueError(
                "GitHub config must contain either token or app_id/private_key/installation_id"
            )

        now = time.time()
        if self._installation_token and now < self._token_expires_at:
            return f"Bearer {self._installation_token}"

        # Generate JWT
        header = {"alg": "RS256"}
        payload = {
            "iat": int(now) - 60,
            "exp": int(now) + (3 * 60),
            "iss": self.config.app_id,
        }
        jwt_token = jwt.encode(header, payload, self.config.private_key).decode("utf-8")

        # Request installation token
        resp = await self.client.post(
            f"/app/installations/{self.config.installation_id}/access_tokens",
            headers={
                "Authorization": f"Bearer {jwt_token}",
                "Accept": "application/vnd.github.v3+json",
            },
        )

        if resp.status_code != 201:
            raise GitHubAPIError(f"Failed to get installation token: {resp.text}")

        data = resp.json()
        self._installation_token = data["token"]
        # Subtract some buffer margin
        self._token_expires_at = now + 3000

        return f"Bearer {self._installation_token}"

    async def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        auth_header = await self._get_auth_header()
        headers = kwargs.pop("headers", {})
        headers["Authorization"] = auth_header
        headers["X-Github-Next-Global-ID"] = "1"

        resp = await self.client.request(method, url, headers=headers, **kwargs)
        if resp.status_code >= 400:
            raise GitHubAPIError(f"API request failed: {resp.status_code} {resp.text}")
        return resp

    async def graphql(
        self, query: str, variables: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        resp = await self._request(
            "POST",
            "https://api.github.com/graphql",
            json={"query": query, "variables": variables or {}},
        )
        data = resp.json()
        if "errors" in data:
            raise GitHubAPIError(f"GraphQL errors: {data['errors']}")
        return data["data"]

    async def get_pull_requests(self, owner: str, repo: str) -> List[Dict[str, Any]]:
        query = """
        query($owner: String!, $repo: String!) {
          repository(owner: $owner, name: $repo) {
            pullRequests(first: 100, states: [OPEN, CLOSED, MERGED], orderBy: {field: CREATED_AT, direction: DESC}) {
              nodes {
                number
                title
                state
                author {
                  login
                }
                baseRefName
                headRefOid
                headRef {
                  id
                }
                updatedAt
              }
            }
          }
        }
        """
        data = await self.graphql(query, {"owner": owner, "repo": repo})
        prs = data["repository"]["pullRequests"]["nodes"]
        return prs

    async def get_collaborators(self, owner: str, repo: str) -> List[str]:
        # Need pull/push/admin permissions to be considered a trusted user usually.
        # We fetch all with push access
        resp = await self._request(
            "GET", f"/repos/{owner}/{repo}/collaborators", params={"permission": "push"}
        )
        users = resp.json()
        return [user["login"] for user in users]

    async def get_pull_request_head_sha(
        self, owner: str, repo: str, number: int
    ) -> str:
        """Fetch the current head SHA for a specific pull request from GitHub."""
        query = """
        query($owner: String!, $repo: String!, $number: Int!) {
          repository(owner: $owner, name: $repo) {
            pullRequest(number: $number) {
              headRefOid
            }
          }
        }
        """
        data = await self.graphql(
            query, {"owner": owner, "repo": repo, "number": number}
        )
        return data["repository"]["pullRequest"]["headRefOid"]

    async def create_deployment(
        self, owner: str, repo: str, ref: str, environment: str
    ) -> str:
        get_data = """
        query($owner: String!, $repo: String!) {
          repository(owner: $owner, name: $repo) {
            id
          }
        }
        """
        data = await self.graphql(get_data, {"owner": owner, "repo": repo})

        create_deployment_input = {
            "autoMerge": False,
            "requiredContexts": [],
            "task": "deploy",
            "environment": environment,
            "refId": ref,
            "repositoryId": data["repository"]["id"],
        }
        mutation = """
        mutation CreateDeployment($input: CreateDeploymentInput!) {
          createDeployment(input: $input) {
            deployment {
              id
            }
          }
        }
        """
        mutation_data = await self.graphql(
            mutation,
            {"input": create_deployment_input},
        )
        return mutation_data["createDeployment"]["deployment"]["id"]

    async def create_deployment_status(
        self,
        deployment_id: str,
        *,
        log_url: str,
        environment_url: str,
        state: str,
        description: str | None = None,
    ) -> str:
        create_status_input = {
            "deploymentId": deployment_id,
            "logUrl": log_url,
            "environmentUrl": environment_url,
            "state": state,
            "autoInactive": True,
        }
        if description is not None:
            create_status_input["description"] = description
        mutation = """
        mutation CreateDeploymentStatus($input: CreateDeploymentStatusInput!) {
          createDeploymentStatus(input: $input) {
            deploymentStatus {
              id
            }
          }
        }
        """
        mutation_data = await self.graphql(
            mutation,
            {"input": create_status_input},
        )
        return mutation_data["createDeploymentStatus"]["deploymentStatus"]["id"]

    async def delete_deployment(self, deployment_id: str) -> None:
        mutation = """
        mutation DeleteDeployment($id: ID!) {
          deleteDeployment(id: $id)
        }
        """
        await self.graphql(
            mutation,
            {"id": deployment_id},
        )

    async def close(self) -> None:
        await self.client.aclose()
