import asyncio
import os
from pathlib import Path
from typing import Dict, List, Optional


class DockerError(Exception):
    pass


async def _run_compose(
    worktree_path: Path,
    project_name: str,
    compose_args: List[str],
    compose_files: List[str],
    env_vars: Optional[Dict[str, str]] = None,
) -> str:
    args = ["docker", "compose", "-p", project_name]
    for cfg in compose_files:
        args.extend(["-f", cfg])

    args.extend(compose_args)

    env = os.environ.copy()
    if env_vars:
        for k, v in env_vars.items():
            env[k] = v

    proc = await asyncio.create_subprocess_exec(
        *args,
        cwd=worktree_path,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()

    if proc.returncode != 0:
        err_msg = stderr.decode().strip()
        raise DockerError(f"Docker compose failed: {' '.join(args)}\nError: {err_msg}")

    return stdout.decode().strip()


async def compose_up(
    worktree_path: Path,
    project_name: str,
    compose_files: List[str],
    env_vars: Optional[Dict[str, str]] = None,
) -> None:
    await _run_compose(
        worktree_path=worktree_path,
        project_name=project_name,
        compose_args=["up", "-d", "--build"],
        compose_files=compose_files,
        env_vars=env_vars,
    )


async def compose_down(
    worktree_path: Path,
    project_name: str,
    compose_files: List[str],
    remove_volumes: bool = True,
) -> None:
    if not worktree_path.exists():
        return
    args = ["down"]
    if remove_volumes:
        args.append("-v")
    await _run_compose(
        worktree_path=worktree_path,
        project_name=project_name,
        compose_args=args,
        compose_files=compose_files,
    )


async def compose_exec(
    worktree_path: Path,
    project_name: str,
    compose_files: List[str],
    service_name: str,
    exec_cmd: List[str],
    env_vars: Optional[Dict[str, str]] = None,
) -> str:
    args = ["exec", service_name] + exec_cmd
    return await _run_compose(
        worktree_path=worktree_path,
        project_name=project_name,
        compose_args=args,
        compose_files=compose_files,
        env_vars=env_vars,
    )
