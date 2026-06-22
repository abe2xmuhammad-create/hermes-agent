"""Runtime smoke test for Docker image license-file presence.

Replaces the old text-assertion test that grepped .dockerignore for
the LICENSE filename. This test builds the real image and verifies the
LICENSE file is actually present inside the container, which is the
behavioral outcome the old test was trying to guard (PEP 639
license-files metadata must resolve inside the Docker image).
"""
from __future__ import annotations

import subprocess


def test_docker_image_contains_license_file(built_image: str) -> None:
    """The LICENSE file must be present inside the built Docker image.

    PEP 639 license-files metadata references LICENSE, and the Docker
    build context must not exclude it. The old test checked .dockerignore
    text; this test verifies the actual runtime outcome: the file exists
    at /opt/hermes/LICENSE inside the image.
    """
    r = subprocess.run(
        ["docker", "run", "--rm", "--entrypoint", "test",
         built_image, "-f", "/opt/hermes/LICENSE"],
        capture_output=True, text=True, timeout=60,
    )
    assert r.returncode == 0, (
        f"LICENSE file not found at /opt/hermes/LICENSE inside the Docker "
        f"image: {r.stderr[-500:]}"
    )
