import asyncio
import random
import string
from pathlib import Path


class GitError(Exception):
    pass


async def _run_git(*args: str, cwd: Path | None = None) -> None:
    proc = await asyncio.create_subprocess_exec(
        "git",
        *args,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        err_msg = stderr.decode().strip()
        raise GitError(f"Git command failed: git {' '.join(args)}\nError: {err_msg}")


async def clone_bare(repo_url: str, dest_path: Path) -> None:
    if not dest_path.exists():
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        await _run_git("clone", "--bare", repo_url, str(dest_path))


async def fetch_bare(bare_path: Path, commit_sha: str) -> None:
    # Need to fetch all heads
    await _run_git("fetch", "origin", commit_sha, cwd=bare_path)


async def add_worktree(bare_path: Path, worktree_path: Path, commit_sha: str) -> None:
    if not worktree_path.parent.exists():
        worktree_path.parent.mkdir(parents=True, exist_ok=True)
    # Add a worktree detached at the specific commit
    await _run_git(
        "worktree", "add", "--detach", str(worktree_path), commit_sha, cwd=bare_path
    )


async def remove_worktree(bare_path: Path, worktree_path: Path) -> None:
    if not worktree_path.exists():
        return
    # Force remove worktree
    await _run_git("worktree", "remove", "--force", str(worktree_path), cwd=bare_path)


def generate_project_name() -> str:
    adjectives = [
        "swift",
        "calm",
        "bright",
        "dark",
        "loud",
        "quiet",
        "fast",
        "slow",
        "cool",
        "warm",
        "bold",
        "shy",
    ]
    nouns = [
        "lion",
        "tiger",
        "bear",
        "wolf",
        "fox",
        "owl",
        "hawk",
        "eagle",
        "shark",
        "whale",
        "frog",
        "toad",
    ]
    suffix = "".join(random.choices(string.digits, k=4))
    return f"{random.choice(adjectives)}-{random.choice(nouns)}-{suffix}"
