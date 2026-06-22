"""Runtime smoke tests for Docker top-level state-file ownership repair.

Replaces the old text-assertion tests that extracted the chown for-loop
from stage2-hook.sh and ran it in a sandbox. These tests build the real
image and verify the actual runtime behavior:

  1. Root-owned top-level state files (auth.json, state.db, gateway.lock,
     gateway_state.json) are chowned to hermes on boot
  2. Non-allowlisted host-owned files are NOT touched (targeted, not
     blanket find -user root sweep)
"""
from __future__ import annotations

import subprocess
import time

from tests.docker.conftest import docker_exec, docker_exec_sh


# The files the stage2 hook should repair (mirrors the allowlist in
# stage2-hook.sh). We test a representative subset.
ALLOWLISTED_FILES = ("auth.json", "state.db", "gateway.lock", "gateway_state.json")


def test_root_owned_state_files_repaired_on_boot(
    built_image: str, container_name: str,
) -> None:
    """Root-owned top-level state files must be chowned to hermes on boot."""
    subprocess.run(
        ["docker", "run", "-d", "--name", container_name,
         built_image, "sleep", "infinity"],
        check=True, capture_output=True, timeout=60,
    )
    time.sleep(5)

    # Create root-owned state files to simulate docker exec (root) writes
    for f in ALLOWLISTED_FILES:
        docker_exec(
            container_name, "touch", f"/opt/data/{f}",
            user="root", timeout=5,
        )

    # Verify they're root-owned
    r = docker_exec_sh(
        container_name,
        " ".join(f'stat -c %U /opt/data/{f}' for f in ALLOWLISTED_FILES),
        timeout=5,
    )
    for line in r.stdout.split():
        assert line == "root", f"expected root-owned, got: {line}"

    # Restart - stage2 should repair ownership
    subprocess.run(
        ["docker", "restart", container_name],
        check=True, capture_output=True, timeout=60,
    )
    time.sleep(5)

    # Verify files are now hermes-owned
    r = docker_exec_sh(
        container_name,
        " ".join(f'stat -c %U /opt/data/{f}' for f in ALLOWLISTED_FILES),
        timeout=5,
    )
    for line in r.stdout.split():
        assert line == "hermes", (
            f"expected hermes-owned after restart, got: {line}"
        )


def test_non_allowlisted_host_file_not_touched(
    built_image: str, container_name: str,
) -> None:
    """A non-allowlisted host-owned file must NOT be chowned, even if
    root-owned. Regression guard for #19788 / #19795: a bind-mounted
    $HERMES_HOME may contain host-owned files Hermes does not manage."""
    subprocess.run(
        ["docker", "run", "-d", "--name", container_name,
         built_image, "sleep", "infinity"],
        check=True, capture_output=True, timeout=60,
    )
    time.sleep(5)

    # Create a non-allowlisted file as root
    docker_exec(
        container_name, "touch", "/opt/data/host_secret.json",
        user="root", timeout=5,
    )
    # Make it root-owned explicitly (it already is, but be sure)
    docker_exec(
        container_name, "chown", "root:root", "/opt/data/host_secret.json",
        user="root", timeout=5,
    )

    # Restart
    subprocess.run(
        ["docker", "restart", container_name],
        check=True, capture_output=True, timeout=60,
    )
    time.sleep(5)

    # The file must STILL be root-owned (not touched by stage2)
    r = docker_exec_sh(
        container_name,
        "stat -c %U /opt/data/host_secret.json",
        timeout=5,
    )
    assert r.stdout.strip() == "root", (
        f"non-allowlisted host file was chowned by stage2 (should be "
        f"preserved): {r.stdout.strip()}"
    )
