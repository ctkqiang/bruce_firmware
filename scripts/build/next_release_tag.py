#!/usr/bin/env python3
"""Compute the next SemVer release tag (vX.Y.Z) for Nezha Cyber CI auto-tagging.

Rules:
- Inspects existing `v<major>.<minor>.<patch>` git tags reachable in the repo.
- If HEAD already carries a `v*` tag, sets `should_release=false` so CI stays idempotent.
- Otherwise inspects commit subjects since the latest SemVer tag:
  - `feat` / `feat(...)` / breaking change -> minor bump (`vX.(Y+1).0`)
  - other commits (`fix`, `perf`, `ui`, `chore`, etc.) -> patch bump (`vX.Y.(Z+1)`)
- Falls back to `v1.0.0` (or `v1.1.0` if `v1.0.0` already exists) cleanly.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

SEMVER_TAG_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")


def run_git(args: list[str], cwd: Path) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return ""
    return proc.stdout.strip()


def parse_semver(tag: str) -> tuple[int, int, int] | None:
    m = SEMVER_TAG_RE.match(tag.strip())
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)))


def get_latest_semver_tag(cwd: Path) -> tuple[str, tuple[int, int, int]] | None:
    raw = run_git(["tag", "-l", "v[0-9]*.[0-9]*.[0-9]*"], cwd)
    best: tuple[tuple[int, int, int], str] | None = None
    for line in raw.splitlines():
        tag = line.strip()
        ver = parse_semver(tag)
        if ver is None:
            continue
        if best is None or ver > best[0]:
            best = (ver, tag)
    if best is None:
        return None
    return (best[1], best[0])


def get_head_vtags(cwd: Path) -> list[str]:
    raw = run_git(["tag", "--points-at", "HEAD", "v*"], cwd)
    return [line.strip() for line in raw.splitlines() if line.strip()]


def get_commits_since(prev_tag: str, cwd: Path) -> list[str]:
    rev_range = f"{prev_tag}..HEAD" if prev_tag else "HEAD"
    raw = run_git(["log", rev_range, "--no-merges", "--pretty=format:%s"], cwd)
    if not raw:
        raw = run_git(["log", rev_range, "--pretty=format:%s"], cwd)
    return [line.strip() for line in raw.splitlines() if line.strip()]


def compute_next_version(
    prev_ver: tuple[int, int, int] | None,
    subjects: list[str],
    bump: str = "auto",
) -> tuple[int, int, int]:
    if prev_ver is None:
        return (1, 0, 0)

    major, minor, patch = prev_ver
    if bump == "major":
        return (major + 1, 0, 0)
    if bump == "minor":
        return (major, minor + 1, 0)
    if bump == "patch":
        return (major, minor, patch + 1)

    has_feat = any(
        re.match(r"^(feat)(\([^)]*\))?!?:", s, re.IGNORECASE)
        or re.match(r"^[a-z]+(\([^)]*\))?!:", s, re.IGNORECASE)
        for s in subjects
    )
    if has_feat:
        return (major, minor + 1, 0)
    return (major, minor, patch + 1)


def main() -> int:
    parser = argparse.ArgumentParser(description="Compute next SemVer release tag for Nezha Cyber")
    parser.add_argument("--repo-dir", type=Path, default=Path("."))
    parser.add_argument("--bump", choices=["auto", "major", "minor", "patch"], default="auto")
    parser.add_argument("--prerelease-suffix", default="", help="Optional suffix e.g. beta.1")
    parser.add_argument("--force", action="store_true", help="Compute tag even if HEAD is already tagged")
    parser.add_argument("--github-output", type=Path, default=None)
    args = parser.parse_args()

    repo = args.repo_dir.resolve()
    head_tags = get_head_vtags(repo)
    latest = get_latest_semver_tag(repo)
    prev_tag = latest[0] if latest else ""
    prev_ver = latest[1] if latest else None

    if head_tags and not args.force:
        result = {
            "should_release": False,
            "already_tagged": True,
            "existing_tag": head_tags[0],
            "prev_tag": prev_tag,
            "next_tag": head_tags[0],
            "commit_count": 0,
        }
    else:
        subjects = get_commits_since(prev_tag, repo)
        next_ver = compute_next_version(prev_ver, subjects, args.bump)
        next_tag = f"v{next_ver[0]}.{next_ver[1]}.{next_ver[2]}"
        if args.prerelease_suffix:
            suffix = args.prerelease_suffix.lstrip("-")
            next_tag = f"{next_tag}-{suffix}"

        # Avoid colliding with an existing tag of the same name
        existing_all = set(run_git(["tag", "-l"], repo).splitlines())
        while next_tag in existing_all:
            next_ver = (next_ver[0], next_ver[1], next_ver[2] + 1)
            next_tag = f"v{next_ver[0]}.{next_ver[1]}.{next_ver[2]}"
            if args.prerelease_suffix:
                next_tag = f"{next_tag}-{args.prerelease_suffix.lstrip('-')}"

        result = {
            "should_release": True,
            "already_tagged": False,
            "existing_tag": "",
            "prev_tag": prev_tag,
            "next_tag": next_tag,
            "commit_count": len(subjects),
        }

    if args.github_output:
        with args.github_output.open("a", encoding="utf-8") as f:
            f.write(f"should_release={'true' if result['should_release'] else 'false'}\n")
            f.write(f"already_tagged={'true' if result['already_tagged'] else 'false'}\n")
            f.write(f"prev_tag={result['prev_tag']}\n")
            f.write(f"next_tag={result['next_tag']}\n")
            f.write(f"commit_count={result['commit_count']}\n")

    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
