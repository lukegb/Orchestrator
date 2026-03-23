import asyncio
import os
from pathlib import Path
from typing import List

from jinja2 import Environment, FileSystemLoader

from orchestrator.config import AppConfig
from orchestrator.models import Deployment


async def generate_nginx_config(
    deployments: List[Deployment],
    app_config: AppConfig,
    template_path: Path,
    output_path: Path,
) -> None:
    """Generates an Nginx config file from a Jinja template and a list of deployments."""
    if not template_path.exists():
        raise FileNotFoundError(f"Template not found at {template_path}")

    env = Environment(loader=FileSystemLoader(str(template_path.parent)))
    template = env.get_template(template_path.name)

    config_content = template.render(deployments=deployments, app_config=app_config)

    # Write atomically
    tmp_path = output_path.with_name(f"{output_path.name}.tmp")

    # Make sure output dir exists
    if not output_path.parent.exists():
        output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(tmp_path, "w") as f:
        f.write(config_content)

    os.rename(tmp_path, output_path)


async def reload_nginx() -> None:
    process = await asyncio.create_subprocess_exec(
        "systemctl",
        "reload",
        "nginx",
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await process.wait()
