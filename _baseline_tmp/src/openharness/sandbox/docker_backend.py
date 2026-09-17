"""Docker-based sandbox backend for isolated tool execution."""
#基于Docker的沙箱后端，用于隔离的工具执行

from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from openharness.config import Settings
from openharness.platforms import get_platform, get_platform_capabilities
from openharness.sandbox.adapter import SandboxAvailability, SandboxUnavailableError

logger = logging.getLogger(__name__)


#这是一个用于检测 Docker 是否可用作为沙箱后端的检查函数
def get_docker_availability(settings: Settings) -> SandboxAvailability:
    """Check whether Docker can be used as a sandbox backend."""
    if not settings.sandbox.enabled or settings.sandbox.backend != "docker":
        return SandboxAvailability(
            enabled=False, available=False, reason="Docker sandbox is not enabled"
        )

    platform_name = get_platform()  #获取当前操作系统/平台名称（如 linux、windows、darwin 等）
    capabilities = get_platform_capabilities(platform_name)#获取该平台的能力集（capabilities），看看它是否明确支持 Docker 沙箱。
    if not capabilities.supports_docker_sandbox:
        return SandboxAvailability(
            enabled=True,
            available=False,
            reason=f"Docker sandbox is not supported on platform {platform_name}",
        )

    #Docker CLI 是否存在
    docker = shutil.which("docker")#在系统的 PATH 环境变量中查找 docker 可执行文件的路径
    if not docker:
        return SandboxAvailability(
            enabled=True,
            available=False,
            reason="Docker CLI not found; install Docker Desktop or Docker Engine",
        )

    #Docker 守护进程是否运行
    try:
        subprocess.run(
            [docker, "info"],
            capture_output=True,
            timeout=5,
            check=True,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return SandboxAvailability(
            enabled=True,
            available=False,
            reason="Docker daemon is not running",
            command=docker,
        )

    return SandboxAvailability(enabled=True, available=True, command=docker)


@dataclass
class DockerSandboxSession:
    """Manages a long-running Docker container for one OpenHarness session."""

    settings: Settings
    session_id: str
    cwd: Path
    _container_name: str = field(init=False)
    _running: bool = field(init=False, default=False)

    def __post_init__(self) -> None:
        self._container_name = f"openharness-sandbox-{self.session_id}"

    @property
    def container_name(self) -> str:
        return self._container_name

    @property
    def is_running(self) -> bool:
        return self._running

# 读配置：从 sandbox.docker 里取出CPU、内存、挂载目录、环境变量等设置
# 转参数：把每个配置项翻译成Docker认识的参数（--cpus、-v、-e等）
# 组装返回：把所有参数放进列表，交给后续代码去执行
    def _build_run_argv(self) -> list[str]:
        """Build the ``docker run`` argv for container creation."""
        docker = shutil.which("docker") or "docker"#在系统PATH中查找docker可执行文件的绝对路径
        sandbox = self.settings.sandbox
        docker_cfg = sandbox.docker
        cwd_str = str(self.cwd.resolve())

        argv = [
            docker,
            "run",
            "-d",
            "--rm",
            "--name",
            self._container_name,
        ]

        # Docker backend currently supports only fully disabled networking.
        # Domain-level allow/deny policies exist for the srt backend, but Docker
        # does not enforce them yet. Fail closed instead of silently widening
        # egress to unrestricted bridge networking.
        #检查是否配置了域名限制策略
        if sandbox.network.allowed_domains or sandbox.network.denied_domains:
            logger.warning(
                "Docker sandbox does not enforce allowed_domains/denied_domains yet; "
                "keeping network disabled"
            )
        argv.extend(["--network", "none"])

        # Resource limits
        if docker_cfg.cpu_limit > 0:
            argv.extend(["--cpus", str(docker_cfg.cpu_limit)])
        if docker_cfg.memory_limit:
            argv.extend(["--memory", docker_cfg.memory_limit])

        # Bind-mount project directory at the same path
        argv.extend(["-v", f"{cwd_str}:{cwd_str}"])#-v /home/user/proj:/home/user/proj：将宿主机的当前项目目录挂载到容器内的完全相同路径
        argv.extend(["-w", cwd_str])#-w /home/user/proj：将容器的工作目录（WORKDIR）设置为该路径

        # Extra mounts
        for mount in docker_cfg.extra_mounts:
            argv.extend(["-v", mount])

        # Extra environment variables
        #环境变量传递
        for key, value in docker_cfg.extra_env.items():
            argv.extend(["-e", f"{key}={value}"])

        #这样容器启动后就会一直存活在后台，等待后续通过docker exec来执行真正的代码命令
        argv.extend([docker_cfg.image, "tail", "-f", "/dev/null"])
        return argv

#检查镜像是否存在 → 组装启动命令 → 执行命令启动容器 → 标记运行状态
    async def start(self) -> None:
        """Create and start the sandbox container."""
        from openharness.sandbox.docker_image import ensure_image_available

        docker_cfg = self.settings.sandbox.docker
        available = await ensure_image_available(
            docker_cfg.image, docker_cfg.auto_build_image
        )
        #查看镜像是否存在
        if not available:
            raise SandboxUnavailableError(
                f"Docker image {docker_cfg.image!r} is not available and "
                "auto_build_image is disabled"
            )

        argv = self._build_run_argv()
        logger.info("Starting Docker sandbox: %s", " ".join(argv))

        process = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        #如果出错
        if process.returncode != 0:
            msg = stderr.decode("utf-8", errors="replace").strip()
            raise SandboxUnavailableError(f"Failed to start Docker sandbox: {msg}")

        self._running = True
        logger.info("Docker sandbox started: %s", self._container_name)

    #这个函数是start()的反向操作——启动容器时做了什么，停止时就反向做一遍
    async def stop(self) -> None:
        """Stop and remove the sandbox container."""
        if not self._running:
            return
        docker = shutil.which("docker") or "docker"
        try:
            process = await asyncio.create_subprocess_exec(
                docker,
                "stop",
                "-t",
                "5",
                self._container_name,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(process.communicate(), timeout=15)
        except (asyncio.TimeoutError, OSError) as exc:
            logger.warning("Error stopping Docker sandbox: %s", exc)
        finally:
            self._running = False
            logger.info("Docker sandbox stopped: %s", self._container_name)

    #同步版本
    def stop_sync(self) -> None:
        """Synchronous stop for use in atexit handlers."""
        if not self._running:
            return
        docker = shutil.which("docker") or "docker"
        try:
            subprocess.run(
                [docker, "stop", "-t", "3", self._container_name],
                capture_output=True,
                timeout=10,
            )
        except (subprocess.TimeoutExpired, OSError):
            pass
        finally:
            self._running = False

    async def exec_command(
        self,
        argv: list[str],
        *,
        cwd: str | Path,
        stdin: int | None = None,
        stdout: int | None = None,
        stderr: int | None = None,
        env: dict[str, str] | None = None,
    ) -> asyncio.subprocess.Process:
        """Execute a command inside the sandbox container.

        Returns an ``asyncio.subprocess.Process`` with the same interface as
        ``asyncio.create_subprocess_exec``.
        """
        if not self._running:
            raise SandboxUnavailableError("Docker sandbox session is not running")

        docker = shutil.which("docker") or "docker"
        cmd: list[str] = [docker, "exec"]
        cmd.extend(["-w", str(Path(cwd).resolve())])

        if env:
            for key, value in env.items():
                cmd.extend(["-e", f"{key}={value}"])

        cmd.append(self._container_name)
        cmd.extend(argv)

        return await asyncio.create_subprocess_exec(
            *cmd,
            stdin=stdin,
            stdout=stdout,
            stderr=stderr,
        )
