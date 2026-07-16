#!/usr/bin/env python3
"""Assemble the unified CI review comment for a pull request.

Several CI review jobs (package-lock diff, CI-sensitive file review, MCP
catalog review) used to post independent PR comments — one each, each with
its own marker. This script merges them into a **single** comment body that
the ``comment-results`` job in ``ci.yml`` upserts via the
``<!-- hermes-ci-review-bot -->`` marker.

Inputs arrive as CLI args:

* ``--lockfile-changed`` — ``true`` if ``lockfile-diff.yml`` reported changes.
* ``--lockfile-diff`` — path to the lockfile diff markdown (downloaded from
  the ``lockfile-diff`` artifact). Only meaningful when ``--lockfile-changed``
  is ``true``.
* ``--ci-reviewed`` — ``true`` / ``false`` / empty (job was skipped).
* ``--mcp-reviewed`` — ``true`` / ``false`` / empty (job was skipped).

Each section is only included when the corresponding job actually ran, so a
PR that doesn't touch lockfiles or the MCP catalog gets a shorter comment.
Exits 0 always — comment posting is best-effort (fork PRs are read-only).

Usage::

    python scripts/ci/assemble_review_comment.py \
        --lockfile-changed "$LOCKFILE_CHANGED" \
        --lockfile-diff /tmp/lockfile-diff.md \
        --ci-reviewed "$CI_REVIEWED" \
        --mcp-reviewed "$MCP_REVIEWED" \
        --output /tmp/comment-body.md
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Hidden marker the ``comment-results`` job uses to find-and-edit its
# previous comment instead of stacking new ones on each run.
MARKER = "<!-- hermes-ci-review-bot -->"


def _bool(val: str | None) -> bool | None:
    """Coerce a workflow_call output string to a tri-state bool.

    Returns ``True`` / ``False`` for ``"true"`` / ``"false"``, and ``None``
    when the value is empty (the job was skipped, so there's no status to
    report).
    """
    if not val:
        return None
    return val.strip().lower() == "true"


def section_lockfile(changed: bool | None, diff_path: Path) -> str:
    """Build the package-lock.json diff section.

    ``changed=None`` means the lockfile-diff job was skipped (no
    ``package-lock.json`` changes) — return an empty string so the section is
    omitted entirely.
    """
    if changed is None:
        return ""
    if not changed:
        return (
            "### 📦 package-lock.json\n\n"
            "✅ No lockfile changes — locked versions match the target branch.\n"
        )
    content = diff_path.read_text(encoding='utf-8').strip() if diff_path.exists() else ""
    if not content:
        return (
            "### 📦 package-lock.json\n\n"
            "Lockfile changes detected but the diff content was unavailable.\n"
        )
    # Ensure the diff content starts with the section header we want.
    return f"### 📦 package-lock.json\n\n{content}\n"


def section_ci_review(reviewed: bool | None) -> str:
    """Build the CI-sensitive file review section."""
    if reviewed is None:
        # Job was skipped — no CI-sensitive files changed.
        return ""
    if reviewed:
        return (
            "### 🔒 CI-sensitive file review\n\n"
            "✅ The `ci-reviewed` label is present on this PR.\n"
        )
    return (
        "### ⚠️ CI-sensitive file review\n\n"
        "This PR changes CI-sensitive files (eslint config, workflow YAMLs, "
        "or composite actions). These files influence what code the js-autofix "
        "job executes and pushes to main.\n\n"
        "A maintainer should verify:\n"
        "- no new eslint rules with custom `fix` functions that write outside linted paths,\n"
        "- no workflow changes that widen permissions or remove guards,\n"
        "- no composite action changes that alter what gets executed.\n\n"
        "After review, add the `ci-reviewed` label and re-run this check.\n"
    )


def section_mcp_review(reviewed: bool | None) -> str:
    """Build the MCP catalog security review section."""
    if reviewed is None:
        # Job was skipped — no MCP catalog changes.
        return ""
    if reviewed:
        return (
            "### 🔧 MCP catalog security review\n\n"
            "✅ The `mcp-catalog-reviewed` label is present on this PR.\n"
        )
    return (
        "### ⚠️ MCP catalog security review\n\n"
        "This PR changes the bundled MCP catalog or MCP catalog installer code. "
        "MCP entries can define local commands that users later install into "
        "`mcp_servers`, so this needs explicit maintainer review before merge.\n\n"
        "A maintainer should verify:\n"
        "- any new/changed `optional-mcps/**/manifest.yaml` command and args are expected,\n"
        "- stdio transports do not use shell+egress/exfiltration payloads,\n"
        "- git install refs are pinned and bootstrap commands are minimal,\n"
        "- requested env vars/secrets match the upstream MCP's documented needs.\n\n"
        "After review, add the `mcp-catalog-reviewed` label and re-run this check.\n"
    )


def assemble(
    lockfile_changed: bool | None,
    lockfile_diff: Path,
    ci_reviewed: bool | None,
    mcp_reviewed: bool | None,
) -> str:
    """Assemble the full comment body from individual job outputs."""
    sections: list[str] = []

    lf = section_lockfile(lockfile_changed, lockfile_diff)
    if lf:
        sections.append(lf)

    cr = section_ci_review(ci_reviewed)
    if cr:
        sections.append(cr)

    mr = section_mcp_review(mcp_reviewed)
    if mr:
        sections.append(mr)

    if not sections:
        # All jobs were skipped or reported no issues — still post a comment
        # so the pending → done transition is visible.
        body = (
            f"{MARKER}\n"
            "## ✅ CI review\n\n"
            "All review checks passed — no issues to report.\n"
        )
    else:
        body = f"{MARKER}\n## ૮ >ﻌ< ა CI review\n\n" + "---\n".join(sections)

    return body


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--lockfile-changed",
        default="false",
        help="Whether lockfile-diff reported changes ('true', 'false', or empty when skipped).",
    )
    parser.add_argument(
        "--lockfile-diff",
        type=Path,
        default=Path("/dev/null"),
        help="Path to the lockfile diff markdown file.",
    )
    parser.add_argument(
        "--ci-reviewed",
        default="",
        help="ci-reviewed label status: 'true', 'false', or empty (skipped).",
    )
    parser.add_argument(
        "--mcp-reviewed",
        default="",
        help="mcp-catalog-reviewed label status: 'true', 'false', or empty (skipped).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Output file for the assembled comment body.",
    )
    args = parser.parse_args()

    body = assemble(
        lockfile_changed=_bool(args.lockfile_changed),
        lockfile_diff=args.lockfile_diff,
        ci_reviewed=_bool(args.ci_reviewed),
        mcp_reviewed=_bool(args.mcp_reviewed),
    )

    args.output.write_text(body)
    print(f"Wrote {len(body)} chars to {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
