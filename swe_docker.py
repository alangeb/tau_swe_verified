#!/usr/bin/env python3
"""
Docker container management for SWE-bench Verified adapter.

Handles image naming, container lifecycle, file operations.
Adapted from ../swe/docker_manager.py for SWE-bench Verified format.

Key differences from SWE-bench:
- Image namespace: swebench/
- Image naming: sweb.eval.x86_64.{instance_id} (no repo prefix transformation)
- __ → _1776_ conversion is the same
"""
import io
import logging
import os
import select
import socket as _socket_mod
import sys
import tarfile
import time
import uuid
from pathlib import Path
from typing import Any, Optional, Tuple

from config import (
    CONTAINER_NETWORK,
    DEFAULT_LLM_GROUP,
    DISK_MIN_FREE_GB,
    DISK_WARN_FREE_GB,
    IMAGE_ARCH,
    IMAGE_NAMESPACE,
    IMAGE_PREFIX,
    TAU_CONTAINER_PATH,
    TESTBED_PATH,
    TESTBED_PYTHON,
)
import docker

logger = logging.getLogger(__name__)


class DiskSpaceError(RuntimeError):
    """Raised when disk space is critically low."""

    pass


def get_docker_data_root() -> str | None:
    """Get Docker's data root directory from docker info.

    Returns the DockerRootDir if available, None if Docker is not reachable.
    This is important because Docker stores images/containers here, which
    may be on a different mount than /.
    """
    client = None
    try:
        client = docker.from_env()
        info = client.info()
        return info.get("DockerRootDir", "/var/lib/docker")
    except Exception:
        return None
    finally:
        if client is not None:
            client.close()


def _resolve_disk_path(path: str | None = None) -> str:
    """Resolve the filesystem path to check for disk space.

    Priority: explicit path → Docker data root → /.
    Falls back to / if Docker root is inaccessible.

    Args:
        path: Explicit path to check (None = auto-detect).

    Returns:
        Resolved filesystem path.
    """
    if path is not None:
        return path
    # Try Docker data root first
    docker_root = get_docker_data_root()
    if docker_root is not None and os.path.exists(docker_root):
        return docker_root
    # Fallback to root filesystem
    return "/"


def get_disk_space_gb(path: str | None = None) -> dict[str, Any]:
    """Get disk space statistics for a filesystem path.

    If path is None, checks Docker data root (or / as fallback).

    Args:
        path: Filesystem path to check (None = auto-detect).

    Returns:
        Dict with total, used, free, available, and percent_used (all in GB).
        Note: 'available' accounts for root reserved blocks — use this, not 'free'.
    """
    target = _resolve_disk_path(path)
    stat = os.statvfs(target)
    total = stat.f_frsize * stat.f_blocks / (1024 ** 3)
    free = stat.f_frsize * stat.f_bfree / (1024 ** 3)
    available = stat.f_frsize * stat.f_bavail / (1024 ** 3)
    used = total - free
    return {
        "total": round(total, 1),
        "used": round(used, 1),
        "free": round(free, 1),
        "available": round(available, 1),
        "percent_used": round(used / total * 100, 1) if total > 0 else 0,
        "filesystem": target,
    }


def check_disk_space(min_gb: float | None = None, warn: bool = True) -> float:
    """Check disk space and abort if below threshold.

    Args:
        min_gb: Minimum free space in GB (defaults to DISK_MIN_FREE_GB from config).
        warn: Whether to log a warning if space is low but above threshold.

    Returns:
        Free space in GB.

    Raises:
        DiskSpaceError: If free space is below min_gb.
    """
    if min_gb is None:
        min_gb = DISK_MIN_FREE_GB
    space = get_disk_space_gb()
    free = space["available"]

    if free < min_gb:
        raise DiskSpaceError(
            f"Disk space critically low: {free:.1f}GB free (minimum: {min_gb}GB). "
            f"Total: {space['total']}GB, Used: {space['used']}GB ({space['percent_used']}%). "
            f"Aborting to prevent Docker corruption. Free up disk space and retry."
        )

    if warn and free < DISK_WARN_FREE_GB:
        logger.warning(
            f"Low disk space: {free:.1f}GB free (warning threshold: {DISK_WARN_FREE_GB}GB). "
            f"Consider cleaning up before running more instances."
        )

    return free


def get_docker_image_name(instance_id: str) -> tuple[str, str]:
    """Get Docker image names for a SWE-bench Verified instance.

    SWE-bench Verified image format:
        swebench/sweb.eval.x86_64.{instance_id with __ → _1776_}

    Example:
        django__django-12345 → swebench/sweb.eval.x86_64.django_1776_django-12345:latest

    Args:
        instance_id: Instance identifier string.

    Returns:
        Tuple of (hub_image, local_image) — same value for SWE-bench Verified.

    Raises:
        ValueError: If instance_id is empty or not a string.
    """
    if not instance_id or not isinstance(instance_id, str):
        raise ValueError(f"Invalid instance_id: {instance_id!r}")
    # Convert instance_id: django__django-12345 → django_1776_django-12345
    converted = instance_id.replace("__", "_1776_").lower()
    image_name = f"{IMAGE_NAMESPACE}/{IMAGE_PREFIX}.{IMAGE_ARCH}.{converted}:latest"
    return image_name, image_name


def _write_tar_to_container(container: Any, content: bytes, container_path: str, mode: str = "0644") -> None:
    """Helper: write bytes to a file in the container via tar archive.

    Creates the parent directory if it doesn't exist (put_archive requires it).

    Args:
        container: Docker container object.
        content: Raw bytes to write.
        container_path: Destination path inside the container.
        mode: File permission mode (default: "0644").
    """
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode='w') as tar:
        info = tarfile.TarInfo(name=os.path.basename(container_path))
        info.size = len(content)
        info.mode = int(mode, 8)
        tar.addfile(info, io.BytesIO(content))
    dest_dir = container_path.rsplit("/", 1)[0] if "/" in container_path else "/"
    # Ensure parent directory exists — put_archive fails with 404 if missing
    container.exec_run(f"mkdir -p {dest_dir}")
    container.put_archive(dest_dir, buf.getvalue())


# Module-level tracker for active DockerManager instances.
# Allows explicit cleanup at shutdown to prevent urllib3 race conditions.
_active_managers: list["DockerManager"] = []


class DockerManager:
    """Manages Docker containers for SWE-bench Verified instances.

    Handles image discovery, container lifecycle, file operations,
    command execution, and patch extraction.

    Supports context manager protocol and explicit close() to prevent
    urllib3 "I/O operation on closed file" errors during shutdown.
    """

    def __init__(self, docker_client: docker.DockerClient | None = None) -> None:
        """Initialize DockerManager.

        Args:
            docker_client: Optional Docker client (auto-detected if None).

        Raises:
            RuntimeError: If Docker daemon is unreachable.
        """
        try:
            self.client = docker_client or docker.from_env()
        except docker.errors.DockerException as e:
            raise RuntimeError(f"Cannot connect to Docker daemon: {e}") from e
        _active_managers.append(self)

    def close(self) -> None:
        """Close the underlying Docker client and HTTP connections.

        Safe to call multiple times (idempotent). After calling close(),
        the manager should not be used for further operations.
        """
        try:
            self.client.close()
        except Exception:
            pass  # Already closed or client is None

    def __del__(self) -> None:
        """Fallback cleanup if close() was not called explicitly."""
        self.close()

    def __enter__(self) -> "DockerManager":
        """Context manager entry."""
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Context manager exit — ensures client is closed."""
        self.close()

    # ─── Image operations ───────────────────────────────────────────────────

    def image_exists(self, image_name: str) -> bool:
        """Check if a Docker image exists locally.

        Args:
            image_name: Full image name with tag.

        Returns:
            True if image exists locally.
        """
        try:
            self.client.images.get(image_name)
            return True
        except docker.errors.ImageNotFound:
            return False
        except docker.errors.APIError as e:
            logger.warning(f"Docker API error checking image {image_name}: {e}")
            return False

    def find_image(self, instance_id: str, skip_disk_check: bool = False) -> str | None:
        """Find the Docker image for an instance.

        Tries: local → Docker Hub pull.
        Returns image name if found, None otherwise.

        Args:
            instance_id: Instance identifier.
            skip_disk_check: If True, skip disk space check (caller already checked).

        Returns:
            Image name string if found, None otherwise.
        """
        try:
            hub_image, local_image = get_docker_image_name(instance_id)
        except ValueError as e:
            logger.error(f"Invalid instance_id {instance_id}: {e}")
            return None

        # Check local first (fast)
        if self.image_exists(local_image):
            logger.info(f"Found local image: {local_image}")
            return local_image

        # Check if already pulled under hub name
        if self.image_exists(hub_image):
            logger.info(f"Found hub image: {hub_image}")
            return hub_image

        # Try to pull from Docker Hub
        try:
            # Check disk space before pulling (images can be 500MB-5GB+)
            if not skip_disk_check:
                check_disk_space(warn=True)
            logger.info(f"Pulling image {hub_image} from Docker Hub...")
            self._pull_with_retry(hub_image)
            return hub_image
        except docker.errors.APIError as e:
            logger.warning(f"Image {hub_image} not found on Docker Hub: {e}")
        except Exception as e:
            logger.warning(f"Failed to pull {hub_image}: {e}")

        return None

    def _pull_with_retry(self, image_name: str, max_retries: int = 3) -> None:
        """Pull image with retry logic for network issues.

        Args:
            image_name: Docker image name to pull.
            max_retries: Maximum number of retry attempts.

        Raises:
            docker.errors.APIError: If image doesn't exist on Hub.
            RuntimeError: If all retries fail.
        """
        for attempt in range(max_retries):
            try:
                self.client.images.pull(image_name, tag="latest")
                return
            except docker.errors.APIError as e:
                raise  # Image doesn't exist on Hub
            except Exception as e:
                if attempt < max_retries - 1:
                    logger.warning(f"Pull failed (attempt {attempt + 1}/{max_retries}): {e}, retrying...")
                    time.sleep(5 * (attempt + 1))
                else:
                    raise RuntimeError(f"Failed to pull {image_name} after {max_retries} attempts") from e

    # ─── Container lifecycle ────────────────────────────────────────────────

    def start_container(
        self, image_name: str, name: str | None = None, skip_disk_check: bool = False
    ) -> docker.models.containers.Container:
        """Start a fresh container from the given image.

        Uses host networking for LLM API access.

        Args:
            image_name: Docker image name.
            name: Container name (auto-generated if None).
            skip_disk_check: If True, skip disk space check (caller already checked).

        Returns:
            Started Docker container.

        Raises:
            RuntimeError: If image not found or container fails to start.
        """
        if not skip_disk_check:
            check_disk_space(warn=True)

        if name is None:
            name = f"swe-{uuid.uuid4().hex[:8]}"

        # Remove any stale container with the same name
        try:
            old = self.client.containers.get(name)
            old.remove(force=True)
        except docker.errors.NotFound:
            pass
        except docker.errors.APIError as e:
            logger.warning(f"Failed to remove stale container {name}: {e}")

        try:
            container = self.client.containers.run(
                image_name,
                command="sleep 3600",  # Keep container alive
                detach=True,
                network_mode=CONTAINER_NETWORK,
                name=name,
                working_dir=TESTBED_PATH,
                environment={
                    "LC_ALL": "C.UTF-8",
                    "LANG": "C.UTF-8",
                },
            )
            logger.info(f"Started container {container.id[:12]} ({name}) from {image_name}")
            return container
        except docker.errors.ImageNotFound:
            raise RuntimeError(f"Image {image_name} not found locally")
        except docker.errors.ContainerError as e:
            raise RuntimeError(f"Container {name} failed to start: {e}") from e
        except docker.errors.APIError as e:
            raise RuntimeError(f"Docker API error starting container {name}: {e}") from e

    def safe_cleanup(self, container: docker.models.containers.Container) -> None:
        """Stop and remove a container, handling all error cases.

        Args:
            container: Docker container to clean up.
        """
        try:
            container.reload()
            if container.status == "running":
                container.stop(timeout=15)
        except docker.errors.NotFound:
            pass
        except docker.errors.APIError as e:
            logger.debug(f"Container stop error (ignoring): {e}")
        try:
            container.remove(force=True)
        except (docker.errors.NotFound, docker.errors.APIError) as e:
            logger.debug(f"Container remove error (ignoring): {e}")

    def cleanup_orphaned_containers(self, prefix: str = "swe-") -> int:
        """Remove stale containers matching the prefix pattern.

        Called at startup to clean up after crashes or interrupted runs.
        Non-blocking: logs warnings but never raises exceptions.

        Args:
            prefix: Container name prefix to match (default: "swe-")

        Returns:
            Number of containers removed
        """
        try:
            containers = self.client.containers.list(all=True, filters={"status": "exited"})
            removed = 0
            for c in containers:
                name = c.name or ""
                if name.startswith(prefix):
                    try:
                        c.remove(force=True)
                        removed += 1
                    except Exception:
                        pass
            if removed:
                logger.info(f"Cleaned up {removed} orphaned container(s)")
            return removed
        except docker.errors.APIError as e:
            logger.warning(f"Failed to cleanup orphaned containers: {e}")
            return 0

    # ─── File operations ────────────────────────────────────────────────────

    def copy_file_to_container(self, container: Any, host_path: Path, container_path: str, mode: str = "0644") -> None:
        """Copy a file from host to container.

        Args:
            container: Docker container object.
            host_path: Path to file on the host.
            container_path: Destination path inside the container.
            mode: File permission mode (default: "0644").

        Raises:
            FileNotFoundError: If host_path does not exist.
            ValueError: If host_path is not a file.
            RuntimeError: If copy fails.
        """
        if not host_path.exists():
            raise FileNotFoundError(f"Host file not found: {host_path}")
        if not host_path.is_file():
            raise ValueError(f"Host path is not a file: {host_path}")
        try:
            content = host_path.read_bytes()
        except OSError as e:
            raise RuntimeError(f"Failed to read {host_path}: {e}") from e

        try:
            _write_tar_to_container(container, content, container_path, mode)
        except docker.errors.APIError as e:
            raise RuntimeError(f"Failed to copy {host_path} to container: {e}") from e

    def copy_text_to_container(self, container: Any, text: str, container_path: str) -> None:
        """Write text content to a file in the container.

        Args:
            container: Docker container object.
            text: Text content to write.
            container_path: Destination path inside the container.

        Raises:
            RuntimeError: If write fails.
        """
        content = text.encode('utf-8')
        try:
            _write_tar_to_container(container, content, container_path)
        except docker.errors.APIError as e:
            raise RuntimeError(f"Failed to write {container_path} to container: {e}") from e

    def copy_dir_to_container(self, container: Any, host_dir: Path, container_dir: str) -> None:
        """Copy a directory from host to container.

        Args:
            container: Docker container object.
            host_dir: Directory path on the host.
            container_dir: Destination directory inside the container.

        Raises:
            FileNotFoundError: If host_dir does not exist.
            NotADirectoryError: If host_dir is not a directory.
            RuntimeError: If copy fails.
        """
        if not host_dir.exists():
            raise FileNotFoundError(f"Host directory not found: {host_dir}")
        if not host_dir.is_dir():
            raise NotADirectoryError(f"Host path is not a directory: {host_dir}")
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode='w') as tar:
            tar.add(str(host_dir), arcname=os.path.basename(host_dir))
        try:
            container.put_archive(container_dir, buf.getvalue())
        except docker.errors.APIError as e:
            raise RuntimeError(f"Failed to copy {host_dir} to container: {e}") from e

    def extract_from_container(self, container: Any, container_path: str, host_path: Path) -> None:
        """Extract a file/directory from container to host.

        Args:
            container: Docker container object.
            container_path: Path inside the container.
            host_path: Destination path on the host.

        Raises:
            RuntimeError: If extraction fails.
        """
        host_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            result = container.get_archive(container_path)
            stream = result[0] if isinstance(result, tuple) else result
            data = b''.join(stream)
            with tarfile.open(fileobj=io.BytesIO(data)) as tar:
                tar.extractall(path=host_path.parent)
        except docker.errors.APIError as e:
            raise RuntimeError(f"Failed to extract {container_path} from container: {e}") from e
        except tarfile.TarError as e:
            raise RuntimeError(f"Failed to extract tar from container: {e}") from e

        # If container_path is a file, rename from extracted name
        extracted = host_path.parent / container_path.rsplit("/", 1)[-1]
        if extracted.exists() and extracted != host_path:
            try:
                extracted.rename(host_path)
            except OSError as e:
                raise RuntimeError(f"Failed to rename extracted file: {e}") from e

    # ─── Command execution ──────────────────────────────────────────────────

    def exec_command(self, container: Any, command: str, timeout: int = 300) -> tuple[int, str, str]:
        """Execute a command in the container.

        String commands are run through sh -c (supports &&, ;, |, etc).

        Args:
            container: Docker container object.
            command: Shell command string.
            timeout: Timeout in seconds (informational, Docker exec_run has no built-in timeout).

        Returns:
            Tuple of (exit_code, stdout, stderr).
        """
        try:
            # Docker exec_run with a string tries to exec the string as a binary.
            # Shell builtins (cd, &&, |, ;) won't work. Wrap in sh -c.
            result = container.exec_run(
                ["sh", "-c", command],
                demux=True,
            )
            exit_code = result.exit_code
            stdout = result.output[0].decode('utf-8', errors='replace') if result.output[0] else ""
            stderr = result.output[1].decode('utf-8', errors='replace') if result.output[1] else ""
            return exit_code, stdout, stderr
        except docker.errors.APIError as e:
            logger.error(f"Docker API error executing command: {e}")
            return -1, "", str(e)
        except Exception as e:
            logger.error(f"Unexpected error executing command: {e}")
            return -1, "", str(e)

    def run_tau(
        self,
        container: Any,
        prompts: list[str],
        artifact_dir: Path,
        llm_group: str | None = None,
        timeout: float = 1800,
        phase: str = "fix",
        stream: bool = False,
        python_path: str | None = None,
    ) -> tuple[int, str, float]:
        """Run Tau agent inside the container.

        Passes prompts as separate positional arguments (NOT shell-escaped).
        Saves stdout/stderr to artifact_dir/{phase}/stdout.log and stderr.log.
        Also extracts container logs to artifact_dir/{phase}/logs/.

        Args:
            container: Docker container object.
            prompts: List of prompt strings.
            artifact_dir: Directory for storing artifacts.
            llm_group: LLM group name (None = "cuda").
            timeout: Timeout in seconds (informational).
            phase: Phase name ("fix" or "analysis") — determines output directory.
            stream: If True, stream stdout/stderr to terminal in real-time.
            python_path: Path to Python interpreter (default: TESTBED_PYTHON from config).

        Returns:
            Tuple of (exit_code, stdout, duration_seconds).

        Raises:
            ValueError: If prompts is empty.
        """
        if not prompts:
            raise ValueError("prompts cannot be empty")
        llm_group = llm_group or DEFAULT_LLM_GROUP
        py = python_path if python_path is not None else TESTBED_PYTHON

        # Build command as a LIST — each prompt is a separate argument.
        # This avoids shell escaping issues with complex prompts.
        # TAU_CONTAINER_PATH = "/tau" (outside /testbed/)
        cmd = [py, f"{TAU_CONTAINER_PATH}/tau.py", "--llm", llm_group]
        for prompt in prompts:
            cmd.append(prompt)

        # Force unbuffered Python output for streaming
        env_vars = {"PYTHONUNBUFFERED": "1"}

        logger.info(f"Running Tau in container (timeout={timeout}s, stream={stream})...")
        start = time.time()

        # Create phase-specific artifact subdirectory
        phase_dir = artifact_dir / phase
        phase_dir.mkdir(parents=True, exist_ok=True)

        if stream:
            return self._run_tau_stream(
                container, cmd, env_vars, artifact_dir, phase_dir, phase, timeout, start
            )
        return self._run_tau_blocking(
            container, cmd, env_vars, artifact_dir, phase_dir, phase, timeout, start
        )

    def _run_tau_blocking(
        self,
        container: Any,
        cmd: list[str],
        env_vars: dict[str, str],
        artifact_dir: Path,
        phase_dir: Path,
        phase: str,
        timeout: float,
        start: float,
    ) -> tuple[int, str, float]:
        """Run Tau with hard timeout enforcement.

        Uses exec_start(socket=True) + close socket for non-blocking start,
        then polls exec_inspect for completion. Output is captured via
        wrapper script writing to /tmp files.
        """
        import shlex
        stdout_file = "/tmp/tau_stdout.log"
        stderr_file = "/tmp/tau_stderr.log"
        quoted_args = [shlex.quote(a) for a in cmd]
        script = f"#!/bin/sh\nexec >{stdout_file} 2>{stderr_file}\n{' '.join(quoted_args)}\n"
        _write_tar_to_container(container, script.encode('utf-8'), "/tmp/run_tau.sh", mode="0755")

        exec_id = None
        try:
            exec_res = self.client.api.exec_create(
                container.id,
                cmd=["/tmp/run_tau.sh"],
                workdir=TESTBED_PATH,
                stdout=True,
                stderr=True,
                environment=env_vars,
            )
            exec_id = exec_res['Id']

            # socket=True is NON-BLOCKING; socket=False blocks until process exits!
            sockio = self.client.api.exec_start(exec_id, socket=True)
            sockio.close()  # Close immediately — output goes to files via exec redirection

            # Poll until process exits or timeout
            deadline = start + timeout
            while time.time() < deadline:
                try:
                    inspect = self.client.api.exec_inspect(exec_id)
                    if not inspect.get('Running', False):
                        break
                except Exception:
                    # Docker API error — assume process exited
                    break
                time.sleep(0.5)
            else:
                logger.warning(f"Tau {phase} timed out after {timeout:.1f}s, killing")
                try:
                    self.exec_command(container, f"pkill -9 -f '{TAU_CONTAINER_PATH}' || true", timeout=5)
                except Exception:
                    pass
                time.sleep(1)

            # Get exit code (may fail if exec already cleaned up)
            exit_code = -1
            try:
                inspect = self.client.api.exec_inspect(exec_id)
                exit_code = inspect.get('ExitCode', -1)
            except Exception:
                pass

            # Extract output files (may fail if container is gone)
            stdout = ""
            stderr = ""
            try:
                stdout = self._extract_file_from_container(container, stdout_file)
            except Exception as e:
                logger.warning(f"Failed to extract stdout: {e}")
            try:
                stderr = self._extract_file_from_container(container, stderr_file)
            except Exception as e:
                logger.warning(f"Failed to extract stderr: {e}")

            stdout_log = phase_dir / "stdout.log"
            stderr_log = phase_dir / "stderr.log"
            stdout_log.write_text(stdout, encoding='utf-8')
            stderr_log.write_text(stderr, encoding='utf-8')
            logger.info(f"Saved {phase}/stdout.log: {len(stdout)} bytes, {phase}/stderr.log: {len(stderr)} bytes")

            try:
                self._extract_container_logs(container, artifact_dir, phase)
            except Exception as e:
                logger.warning(f"Failed to extract container logs: {e}")
            try:
                self._extract_issue_md(container, artifact_dir, phase)
            except Exception as e:
                logger.warning(f"Failed to extract ISSUE.md: {e}")

            duration = time.time() - start
            return exit_code, stdout, duration

        except Exception as e:
            duration = time.time() - start
            logger.error(f"Tau execution failed after {duration:.1f}s: {e}")
            stdout_log = phase_dir / "stdout.log"
            stderr_log = phase_dir / "stderr.log"
            try:
                stdout_log.write_text("", encoding='utf-8')
                stderr_log.write_text(str(e), encoding='utf-8')
            except Exception:
                pass
            return -1, str(e), duration

        duration = time.time() - start
        logger.info(f"Tau finished: exit={exit_code}, duration={duration:.1f}s")
        return exit_code, stdout + stderr, duration

    def _extract_file_from_container(self, container: Any, path: str) -> str:
        """Extract a single file from the container via tar archive."""
        try:
            result = container.get_archive(path)
            stream = result[0] if isinstance(result, tuple) else result
            data = b''.join(stream)
            with tarfile.open(fileobj=io.BytesIO(data)) as tar:
                member = tar.getmember(os.path.basename(path))
                fobj = tar.extractfile(member)
                if fobj:
                    return fobj.read().decode('utf-8', errors='replace')
        except Exception:
            pass
        return ""

    def _run_tau_stream(
        self,
        container: Any,
        cmd: list[str],
        env_vars: dict[str, str],
        artifact_dir: Path,
        phase_dir: Path,
        phase: str,
        timeout: float,
        start: float,
    ) -> tuple[int, str, float]:
        """Run Tau via socket-based exec for real-time streaming.

        Uses exec_create + exec_start(socket=True) to get a raw socket
        connected to the container's stdout/stderr. Output is teed to
        both the terminal and the log file.

        Uses select() for reliable non-blocking I/O with deadline enforcement.
        """
        import select as _select_mod

        try:
            # Create the exec instance via low-level API (needs container ID string)
            exec_res = self.client.api.exec_create(
                container.id,
                cmd=cmd,
                workdir=TESTBED_PATH,
                stdout=True,
                stderr=True,
                environment=env_vars,
            )
            exec_id = exec_res['Id']

            # Start and get raw socket (Docker returns SocketIO wrapping a real socket)
            sockio = self.client.api.exec_start(exec_id, socket=True)
            raw_sock = sockio._sock  # unwrap to get raw socket for non-blocking reads
            raw_sock.setblocking(False)

            stdout_log = phase_dir / "stdout.log"
            collected = []
            leftover = b""  # Handle partial frames across recv calls
            deadline = start + timeout

            try:
                while time.time() < deadline:
                    # Use select to wait for data with a deadline-aware timeout
                    remaining = deadline - time.time()
                    if remaining <= 0:
                        break
                    try:
                        ready, _, _ = _select_mod.select([raw_sock], [], [], min(remaining, 1.0))
                    except (OSError, ValueError):
                        break

                    if not ready:
                        # select timed out — check if process is still running
                        try:
                            inspect = self.client.api.exec_inspect(exec_id)
                            if inspect.get('Running', False):
                                continue  # keep waiting
                        except Exception:
                            pass
                        break  # process exited or inspect failed

                    # Data available — read it
                    try:
                        data = raw_sock.recv(65536)
                    except BlockingIOError:
                        continue
                    except OSError:
                        break

                    if not data:
                        # EOF — process exited
                        break

                    # Prepend any leftover partial frame from last recv
                    leftover += data
                    # Docker uses a framing protocol: 8-byte header + data
                    # Format: content_type(1) + reserved(3) + length(4) + payload
                    decoded = self._decode_docker_stream(leftover, collected)
                    leftover = decoded['remaining']
                    # Tee to terminal
                    sys.stdout.write(decoded['text'])
                    sys.stdout.flush()

                # Close socket
                sockio.close()
            except Exception:
                sockio.close()

            # Kill any remaining Tau processes (timeout or normal exit)
            self.exec_command(container, f"pkill -9 -f '{TAU_CONTAINER_PATH}' || true", timeout=5)

            # Get exit code
            inspect = self.client.api.exec_inspect(exec_id)
            exit_code = inspect.get('ExitCode', -1)

            # Write collected output to log file
            full_output = ''.join(collected)
            stdout_log.write_text(full_output, encoding='utf-8')
            logger.info(f"Saved {phase}/stdout.log: {len(full_output)} bytes (streamed)")

            # Extract container logs
            self._extract_container_logs(container, artifact_dir, phase)
            self._extract_issue_md(container, artifact_dir, phase)

        except Exception as e:
            duration = time.time() - start
            logger.error(f"Tau stream execution failed after {duration:.1f}s: {e}")
            return -1, str(e), duration

        duration = time.time() - start
        logger.info(f"Tau finished: exit={exit_code}, duration={duration:.1f}s")
        return exit_code, full_output, duration

    def _decode_docker_stream(
        self, data: bytes, collected: list[str]
    ) -> dict:
        """Decode Docker exec stream framing protocol.

        Docker exec stream uses: 1-byte content_type + 3-byte reserved + 4-byte length + payload.
        content_type=1 = stdout, content_type=2 = stderr.

        Returns dict with 'text' (decoded string) and 'remaining' (bytes not yet decodable).
        """
        result = []
        buf = data

        while len(buf) >= 8:
            if len(buf) < 8:
                break
            content_type = buf[0]  # 1=stdout, 2=stderr
            length = int.from_bytes(buf[4:8], 'big')
            if len(buf) < 8 + length:
                break
            payload = buf[8:8 + length]
            text = payload.decode('utf-8', errors='replace')
            result.append(text)
            collected.append(text)
            buf = buf[8 + length:]

        return {'text': ''.join(result), 'remaining': buf}

    def _extract_container_logs(self, container: Any, artifact_dir: Path, phase: str) -> None:
        """Extract Tau session logs from container to phase-specific directory.

        Only extracts for 'fix' phase. Analysis phase logs are skipped.
        Extracts flat into artifact_dir/{phase}/logs/ (no nested log/ subdir).

        Args:
            container: Docker container object.
            artifact_dir: Directory for storing artifacts.
            phase: Phase name ("fix" or "analysis").
        """
        # Skip analysis phase — we don't need analysis logs
        if phase != "fix":
            return

        # IMPORTANT: Tau runs as root in the container. Logs go to
        # /root/.local/tau/log/, NOT to /tau/.local/tau/log/.
        # tau/ is at /tau/ (outside /testbed/), separate from the repo.
        tau_log_path = "/root/.local/tau/log"
        phase_logs_dir = artifact_dir / phase / "logs"
        phase_logs_dir.mkdir(parents=True, exist_ok=True)

        try:
            result = container.get_archive(tau_log_path)
            stream = result[0] if isinstance(result, tuple) else result
            data = b''.join(stream)
            with tarfile.open(fileobj=io.BytesIO(data)) as tar:
                for member in tar.getmembers():
                    # Flatten: strip the top-level 'log/' directory component
                    # so files land directly in phase_logs_dir/
                    parts = member.name.split("/")
                    if len(parts) > 1:
                        member.name = "/".join(parts[1:])
                    elif member.isdir():
                        continue  # skip the top-level directory itself
                    if member.name:  # skip empty names
                        tar.extract(member, path=phase_logs_dir)
            logger.info(f"Extracted {phase} container logs to {phase_logs_dir}")
        except Exception as e:
            logger.warning(f"Could not extract {phase} container logs: {e}")

    def _extract_issue_md(self, container: Any, artifact_dir: Path, phase: str) -> None:
        """Extract ISSUE.md worklog from container to phase-specific logs directory.

        Only extracts for 'fix' phase. Saves to artifact_dir/{phase}/logs/ISSUE.md.

        Args:
            container: Docker container object.
            artifact_dir: Directory for storing artifacts.
            phase: Phase name ("fix" or "analysis").
        """
        if phase != "fix":
            return
        phase_logs_dir = artifact_dir / phase / "logs"
        phase_logs_dir.mkdir(parents=True, exist_ok=True)
        try:
            result = container.get_archive(f"{TESTBED_PATH}/ISSUE.md")
            stream = result[0] if isinstance(result, tuple) else result
            data = b''.join(stream)
            with tarfile.open(fileobj=io.BytesIO(data)) as tar:
                for member in tar.getmembers():
                    parts = member.name.split("/")
                    if len(parts) > 1:
                        member.name = "/".join(parts[1:])
                    elif member.isdir():
                        continue
                    if member.name:
                        tar.extract(member, path=phase_logs_dir)
            logger.info(f"Extracted ISSUE.md to {phase_logs_dir}")
        except Exception as e:
            logger.warning(f"Could not extract ISSUE.md: {e}")

    # ─── Patch extraction ───────────────────────────────────────────────────

    def extract_patch(self, container: Any, artifact_dir: Path) -> str:
        """Extract git diff from container.

        Strategy:
        1. Restore .gitignore to its original state (we modified it during setup)
        2. Remove all framework artifacts (ISSUE.txt, .BASE_COMMIT, etc.)
        3. Use git diff HEAD with path exclusions to capture ONLY source changes

        Creates patch.diff in artifact_dir/patches/.

        Args:
            container: Docker container object.
            artifact_dir: Directory for storing artifacts.

        Returns:
            Patch content as string (empty string if no changes).
        """
        patches_dir = artifact_dir / "patches"
        patches_dir.mkdir(parents=True, exist_ok=True)

        # Step 1: Restore .gitignore to original state
        # (we modified it during setup, git sees it as changed)
        self.exec_command(
            container,
            f"cd {TESTBED_PATH} && git checkout -- .gitignore 2>/dev/null || true",
            timeout=10,
        )

        # Step 2: Remove all framework artifacts from the working tree
        # (untracked files won't appear in git diff HEAD anyway, but be safe)
        # NOTE: tau/ is at /tau/ (outside testbed), so no need to rm it here
        self.exec_command(
            container,
            f"cd {TESTBED_PATH} && rm -rf .web/ build/ dist/ .eggs/ "
            f"&& rm -f .BASE_COMMIT ISSUE.txt ISSUE.md ANALYSIS.txt patch.diff "
            f"&& rm -f .coverage coverage.xml *.pkl *.log "
            f"&& find . -type d -name __pycache__ -exec rm -rf {{}} + 2>/dev/null || true "
            f"&& find . -name '*.egg-info' -type d -exec rm -rf {{}} + 2>/dev/null || true "
            f"&& find . -name '*.pyc' -delete 2>/dev/null || true",
            timeout=15,
        )

        # Step 3: Let git write the patch directly to a file — zero manipulation.
        # This avoids any stdout capture/strip issues that corrupt hunk counts.
        self.exec_command(
            container,
            f"cd {TESTBED_PATH} && git diff HEAD > model.patch",
            timeout=30,
        )

        # Step 4: Extract the patch file from the container via tar archive.
        patch_content = ""
        try:
            result = container.get_archive(f"{TESTBED_PATH}/model.patch")
            stream = result[0] if isinstance(result, tuple) else result
            data = b''.join(stream)
            with tarfile.open(fileobj=io.BytesIO(data)) as tar:
                member = tar.getmember(os.path.basename(f"{TESTBED_PATH}/model.patch"))
                patch_file_obj = tar.extractfile(member)
                if patch_file_obj:
                    patch_content = patch_file_obj.read().decode('utf-8', errors='replace')
        except Exception as e:
            logger.warning(f"Could not extract patch from container: {e}")

        patch_file = patches_dir / "patch.diff"
        if patch_content:
            patch_file.write_text(patch_content, encoding='utf-8')
            logger.info(f"Extracted patch: {len(patch_content)} bytes")
        else:
            patch_file.write_text("", encoding='utf-8')
            logger.warning("No patch extracted (empty diff)")

        return patch_content

    # ─── Log extraction ─────────────────────────────────────────────────────

    def extract_logs(self, container: Any, artifact_dir: Path) -> None:
        """Extract ALL Tau logs from container to artifact_dir/logs/.

        Extracts flat — no nested log/ subdirectory.

        Args:
            container: Docker container object.
            artifact_dir: Directory for storing artifacts.
        """
        logs_dir = artifact_dir / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)

        # IMPORTANT: Tau runs as root — logs are in /root/.local/tau/log/
        # (not /tau/.local/tau/log/ — tau.py writes to the user's home dir)
        # tau/ lives at /tau/ (outside /testbed/), separate from the repo.
        tau_log_path = "/root/.local/tau/log"
        try:
            result = container.get_archive(tau_log_path)
            stream = result[0] if isinstance(result, tuple) else result
            data = b''.join(stream)
            with tarfile.open(fileobj=io.BytesIO(data)) as tar:
                for member in tar.getmembers():
                    # Flatten: strip the top-level 'log/' directory component
                    parts = member.name.split("/")
                    if len(parts) > 1:
                        member.name = "/".join(parts[1:])
                    elif member.isdir():
                        continue  # skip the top-level directory itself
                    if member.name:  # skip empty names
                        tar.extract(member, path=logs_dir)
            logger.info(f"Extracted logs to {logs_dir}")
        except Exception as e:
            logger.warning(f"Failed to extract logs: {e}")

    # ─── Python detection ───────────────────────────────────────────────────

    def find_testbed_python(self, container: Any) -> str:
        """Find the testbed Python interpreter in the container.

        Checks known paths first, then falls back to `which python3`.
        Prefers Python >= 3.9 (Tau uses from __future__ import annotations for compatibility).

        Args:
            container: Docker container object.

        Returns:
            Path to Python interpreter (>= 3.9 if available).
        """
        MIN_PYTHON_MINOR = 10  # X | None requires Python 3.10+ (new Tau uses PEP 604 union syntax)

        # Try known paths — prefer versions >= 3.9
        for py_path in [
            "/opt/miniconda3/envs/testbed/bin/python3",
            "/opt/miniconda3/envs/testbed/bin/python",
            "/usr/bin/python3",
            "/usr/bin/python3.11",
            "/usr/bin/python3.10",
            "/usr/bin/python3.9",
            "/usr/local/bin/python3",
        ]:
            exit_code, _, _ = self.exec_command(container, f"test -f {py_path} && echo OK", timeout=5)
            if exit_code == 0:
                # Verify version >= 3.9 — use simple print so container Python evaluates
                ver_exit, ver_out, _ = self.exec_command(
                    container, f"{py_path} -c 'import sys; print(sys.version_info.major, sys.version_info.minor, sep=\".\")'", timeout=5
                )
                if ver_exit == 0:
                    major, minor = map(int, ver_out.strip().split("."))
                    if major > 3 or (major == 3 and minor >= MIN_PYTHON_MINOR):
                        logger.info(f"Found Python: {py_path} (v{ver_out.strip()})")
                        return py_path
                    else:
                        logger.warning(f"Skipping {py_path} (v{ver_out.strip()}) — need >= 3.{MIN_PYTHON_MINOR}")

        # Fallback: find any python3, then check version
        exit_code, stdout, _ = self.exec_command(
            container, "which python3 2>/dev/null || which python 2>/dev/null || echo /usr/bin/python3", timeout=10
        )
        py = stdout.strip() or "/usr/bin/python3"
        logger.warning(f"Fallback Python: {py} (version may be < 3.{MIN_PYTHON_MINOR})")
        return py


def cleanup_all() -> None:
    """Close all tracked DockerManager instances.

    Call this before interpreter exit to prevent urllib3 shutdown race
    conditions ("I/O operation on closed file" errors).
    """
    while _active_managers:
        manager = _active_managers.pop()
        manager.close()
