"""Runtime smoke tests for Docker stage2 browser executable discovery.

Replaces the old text-assertion tests that grepped stage2-hook.sh for
string patterns. These tests build the real image and verify the
chromium binary is actually discovered at boot — i.e.
``AGENT_BROWSER_EXECUTABLE_PATH`` is set, points to a real executable,
and is a browser binary (not a shared library picked up by a broad
``find | grep``).
"""
from __future__ import annotations

import subprocess
import time

from tests.docker.conftest import docker_exec_sh


def _wait_for_stage2(container: str, deadline_s: float = 30.0) -> None:
    """Wait for stage2-hook to complete by polling for its completion log."""
    end = time.monotonic() + deadline_s
    while time.monotonic() < end:
        r = docker_exec_sh(
            container,
            "grep -q 'stage2.*Setup complete' /proc/1/fd/2 2>/dev/null "
            "|| grep -rq 'stage2.*Setup complete' /run/s6 2>/dev/null "
            "|| true",
            timeout=5,
        )
        # stage2 logs to stderr which s6 captures; check the container's
        # s6 log surface instead. The simplest reliable signal is that
        # the env var exists.
        r = docker_exec_sh(
            container,
            "test -f /run/s6/container_environment/AGENT_BROWSER_EXECUTABLE_PATH",
            timeout=5,
        )
        if r.returncode == 0:
            return
        time.sleep(0.5)


def test_stage2_discovers_chromium_binary(
    built_image: str, container_name: str,
) -> None:
    """The stage2 hook must discover the Playwright chromium binary and
    export AGENT_BROWSER_EXECUTABLE_PATH so the browser tool can find it.

    Regression: the old ``find | grep -Ei 'chrome|chromium'`` picked up
    shared libraries (libGLESv2.so etc.) that inherit the executable bit
    from Playwright's tarball. The fix uses filename matching; this test
    verifies the discovered binary is a real browser, not a .so.
    """
    subprocess.run(
        ["docker", "run", "-d", "--name", container_name,
         built_image, "sleep", "infinity"],
        check=True, capture_output=True, timeout=60,
    )
    # Give s6 + stage2-hook time to run.
    time.sleep(5)

    # AGENT_BROWSER_EXECUTABLE_PATH must be set via s6 container_environment.
    r = docker_exec_sh(
        container_name,
        "cat /run/s6/container_environment/AGENT_BROWSER_EXECUTABLE_PATH",
        timeout=10,
    )
    assert r.returncode == 0, (
        f"AGENT_BROWSER_EXECUTABLE_PATH not set by stage2 hook: {r.stderr}"
    )
    browser_path = r.stdout.strip()
    assert browser_path, "AGENT_BROWSER_EXECUTABLE_PATH is empty"

    # Must be a real file and executable.
    r = docker_exec_sh(
        container_name,
        f'test -x "{browser_path}"',
        timeout=5,
    )
    assert r.returncode == 0, (
        f"discovered browser path is not executable: {browser_path}"
    )

    # Must be a browser binary by basename — NOT a shared library.
    # This is the runtime equivalent of the old "filename-matched" test.
    accepted_names = (
        "chrome", "chromium", "chrome-headless-shell",
        "headless_shell", "chromium-browser",
    )
    r = docker_exec_sh(
        container_name,
        f'basename "{browser_path}"',
        timeout=5,
    )
    basename = r.stdout.strip()
    assert basename in accepted_names, (
        f"discovered binary basename {basename!r} is not a recognized "
        f"browser name (accepted: {accepted_names}) — the discovery may "
        f"have picked up a shared library (.so) instead of the real browser"
    )


def test_stage2_browser_path_accessible_to_hermes_user(
    built_image: str, container_name: str,
) -> None:
    """The discovered browser binary must be accessible to the
    unprivileged hermes user (UID 10000), since that's who runs
    agent-browser subprocesses."""
    subprocess.run(
        ["docker", "run", "-d", "--name", container_name,
         built_image, "sleep", "infinity"],
        check=True, capture_output=True, timeout=60,
    )
    time.sleep(5)

    r = docker_exec_sh(
        container_name,
        'path="$(cat /run/s6/container_environment/AGENT_BROWSER_EXECUTABLE_PATH)" '
        '&& test -r "$path" && test -x "$path"',
        timeout=10,
    )
    assert r.returncode == 0, (
        f"browser binary not readable+executable by hermes user: {r.stderr}"
    )
