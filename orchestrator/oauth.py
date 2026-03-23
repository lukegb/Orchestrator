from authlib.integrations.starlette_client import OAuth  # type: ignore

from orchestrator.config import AppConfig


oauth = OAuth()


def setup_oauth(config: AppConfig) -> None:
    oauth.register(
        name="github",
        client_id=config.oauth.client_id,
        client_secret=config.oauth.client_secret,
        access_token_url="https://github.com/login/oauth/access_token",
        access_token_params=None,
        authorize_url="https://github.com/login/oauth/authorize",
        authorize_params=None,
        api_base_url="https://api.github.com/",
        client_kwargs={"scope": "read:user"},
    )
