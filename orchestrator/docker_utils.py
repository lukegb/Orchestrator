import asyncio
import os
from pathlib import Path
from typing import AsyncGenerator, Dict, IO, List, Optional


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


async def _run_compose_logged(
    worktree_path: Path,
    project_name: str,
    compose_args: List[str],
    compose_files: List[str],
    log_file: IO[str],
    env_vars: Optional[Dict[str, str]] = None,
) -> None:
    """Like _run_compose but streams stdout/stderr line-by-line to a log file."""
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
        stderr=asyncio.subprocess.STDOUT,
    )

    assert proc.stdout is not None
    while True:
        line = await proc.stdout.readline()
        if not line:
            break
        decoded = line.decode(errors="replace")
        log_file.write(decoded)
        log_file.flush()

    await proc.wait()

    if proc.returncode != 0:
        error_msg = (
            f"Docker compose failed (exit {proc.returncode}): {' '.join(args)}\n"
        )
        log_file.write(error_msg)
        log_file.flush()
        raise DockerError(error_msg.strip())


async def compose_up(
    worktree_path: Path,
    project_name: str,
    compose_files: List[str],
    env_vars: Optional[Dict[str, str]] = None,
    log_file: Optional[IO[str]] = None,
) -> None:
    if log_file is not None:
        await _run_compose_logged(
            worktree_path=worktree_path,
            project_name=project_name,
            compose_args=["up", "-d", "--build"],
            compose_files=compose_files,
            log_file=log_file,
            env_vars=env_vars,
        )
    else:
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
    log_file: Optional[IO[str]] = None,
) -> None:
    if not worktree_path.exists():
        return
    args = ["down"]
    if remove_volumes:
        args.append("-v")
    if log_file is not None:
        await _run_compose_logged(
            worktree_path=worktree_path,
            project_name=project_name,
            compose_args=args,
            compose_files=compose_files,
            log_file=log_file,
        )
    else:
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


async def compose_logs_stream(
    worktree_path: Path,
    project_name: str,
    compose_files: List[str],
) -> AsyncGenerator[str, None]:
    """Stream live container logs via docker compose logs --follow."""
    args = ["docker", "compose", "-p", project_name]
    for cfg in compose_files:
        args.extend(["-f", cfg])
    args.extend(["logs", "--follow", "--tail=100"])

    proc = await asyncio.create_subprocess_exec(
        *args,
        cwd=worktree_path,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )

    try:
        assert proc.stdout is not None
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            yield line.decode(errors="replace")
    finally:
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
