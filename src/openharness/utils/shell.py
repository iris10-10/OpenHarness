"""Shared shell and subprocess helpers."""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
from collections.abc import Mapping
from pathlib import Path

from openharness.config import Settings, load_settings
from openharness.platforms import PlatformName, get_platform
from openharness.sandbox import wrap_command_for_sandbox


def resolve_shell_command(
    command: str,
    *,
    platform_name: PlatformName | None = None,
    prefer_pty: bool = False,
) -> list[str]:
    """Return argv for the best available shell on the current platform."""
    resolved_platform = platform_name or get_platform()
    if resolved_platform == "windows":
        bash = shutil.which("bash")
        if bash and (bash.startswith("/") or _bash_is_usable(bash)):
            return [bash, "-lc", command]
        # WSL's system32 bash.exe can be present without a configured
        # distribution. Prefer an installed Git Bash in that case so POSIX
        # commands used by hooks and local tasks keep working on Windows.
        git_bash = _find_usable_git_bash(bash)
        if git_bash:
            return [git_bash, "-lc", command]
        powershell = shutil.which("pwsh") or shutil.which("powershell")
        if powershell:
            return [powershell, "-NoLogo", "-NoProfile", "-Command", command]
        return [shutil.which("cmd.exe") or "cmd.exe", "/d", "/s", "/c", command]

    bash = shutil.which("bash")
    if bash:
        argv = [bash, "-lc", command]
        if prefer_pty:
            wrapped = _wrap_command_with_script(argv, platform_name=resolved_platform)
            if wrapped is not None:
                return wrapped
        return argv
    shell = shutil.which("sh") or os.environ.get("SHELL") or "/bin/sh"
    argv = [shell, "-lc", command]
    if prefer_pty:
        wrapped = _wrap_command_with_script(argv, platform_name=resolved_platform)
        if wrapped is not None:
            return wrapped
    return argv


def _find_usable_git_bash(discovered_bash: str | None) -> str | None:
    """Find Git Bash when the first PATH bash is an unusable WSL shim."""

    if discovered_bash and not Path(discovered_bash).exists():
        return None
    if discovered_bash and Path(discovered_bash).exists():
        normalized = str(Path(discovered_bash)).lower().replace("/", "\\")
        if "\\git\\" in normalized:
            return None
    candidates: list[Path] = []
    for variable in ("ProgramFiles", "ProgramFiles(x86)"):
        root = os.environ.get(variable)
        if root:
            candidates.append(Path(root) / "Git" / "bin" / "bash.exe")
    for candidate in candidates:
        if candidate.exists() and _bash_is_usable(str(candidate)):
            return str(candidate)
    return None


async def create_shell_subprocess(
    command: str,
    *,
    cwd: str | Path,
    settings: Settings | None = None,
    prefer_pty: bool = False,
    stdin: int | None = asyncio.subprocess.DEVNULL,
    stdout: int | None = None,
    stderr: int | None = None,
    env: Mapping[str, str] | None = None,
) -> asyncio.subprocess.Process:
    """Spawn a shell command with platform-aware shell selection and sandboxing."""
    resolved_settings = settings or load_settings()

    # Docker backend: route through docker exec
    if resolved_settings.sandbox.enabled and resolved_settings.sandbox.backend == "docker":
        from openharness.sandbox.session import get_docker_sandbox

        session = get_docker_sandbox()
        if session is not None and session.is_running:
            argv = resolve_shell_command(command)
            return await session.exec_command(
                argv,
                cwd=cwd,
                stdin=stdin,
                stdout=stdout,
                stderr=stderr,
                env=dict(env) if env is not None else None,
            )
        if resolved_settings.sandbox.fail_if_unavailable:
            from openharness.sandbox import SandboxUnavailableError

            raise SandboxUnavailableError("Docker sandbox session is not running")

    # Existing srt path
    argv = resolve_shell_command(command, prefer_pty=prefer_pty)
    if (
        len(argv) >= 3
        and argv[0].lower().replace("/", "\\").find("\\git\\") >= 0
        and argv[1] == "-lc"
    ):
        argv = [argv[0], "--noprofile", "--norc", "-lc", argv[2]]
    argv, cleanup_path = wrap_command_for_sandbox(argv, settings=resolved_settings)

    try:
        process = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(Path(cwd).resolve()),
            stdin=stdin,
            stdout=stdout,
            stderr=stderr,
            env=dict(env) if env is not None else None,
        )
    except Exception:
        if cleanup_path is not None:
            cleanup_path.unlink(missing_ok=True)
        raise

    if cleanup_path is not None:
        asyncio.create_task(_cleanup_after_exit(process, cleanup_path))
    return process


def _wrap_command_with_script(
    argv: list[str],
    *,
    platform_name: PlatformName | None = None,
) -> list[str] | None:
    resolved_platform = platform_name or get_platform()
    if resolved_platform == "macos":
        return None
    script = shutil.which("script")
    if script is None:
        return None
    if len(argv) >= 3 and argv[1] == "-lc":
        return [script, "-qefc", argv[2], "/dev/null"]
    return None


def _bash_is_usable(bash_path: str) -> bool:
    """Return True when a discovered bash executable can run commands.

    On Windows, ``shutil.which("bash")`` can find WSL's ``bash.exe`` even when no
    WSL distribution is installed. In that case the executable exists but every
    command fails, so fall back to PowerShell/cmd instead of selecting it.
    """
    try:
        result = subprocess.run(
            [bash_path, "-lc", "exit 0"],
            capture_output=True,
            timeout=5,
            check=False,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


async def _cleanup_after_exit(process: asyncio.subprocess.Process, cleanup_path: Path) -> None:
    try:
        await process.wait()
    finally:
        cleanup_path.unlink(missing_ok=True)
