"""Gate `gh pr create` on a completed pre-PR review of the current commit.

Two modes:
  check  PreToolUse hook. Reads the tool call as JSON on stdin. If the
         command opens a PR and the current HEAD has no review marker,
         it exits 2, which blocks the call and shows the reason to Claude.
  mark   Records that HEAD was reviewed. The pre-pr-review skill runs it
         after both reviews are done and their blocking findings are fixed.

Markers live in .git/pre-pr-review/<sha>, so they are never committed. A new
commit needs a new review before a PR can be opened; later pushes to an
open PR are not gated.
"""

import json
import re
import subprocess
import sys
from pathlib import Path

# Matches the subcommand, not the executable: gh is often called through a
# variable or its full path ("$GH" pr create, & "...\gh.exe" pr create).
PR_CREATE = re.compile(r"\bpr\s+create\b")


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], capture_output=True, text=True, check=True
    ).stdout.strip()


def _marker() -> Path:
    return Path(_git("rev-parse", "--git-path", "pre-pr-review")) / _git(
        "rev-parse", "HEAD"
    )


def check() -> int:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        return 0
    command = (payload.get("tool_input") or {}).get("command") or ""
    if not PR_CREATE.search(command):
        return 0
    try:
        marker = _marker()
    except subprocess.CalledProcessError:
        return 0  # not in a git repository: nothing to gate
    if marker.is_file():
        return 0
    print(
        f"Blocked: HEAD {marker.name[:7]} has not been through the pre-PR review. "
        "Run the pre-pr-review skill (standard reviewer + Codex correctness "
        "review), fix the blocking findings, then open the PR.",
        file=sys.stderr,
    )
    return 2


def mark(summary: str) -> int:
    marker = _marker()
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(summary + "\n", encoding="utf-8")
    print(f"Marked {marker.name[:7]} as reviewed.")
    return 0


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "check"
    if mode == "mark":
        sys.exit(mark(" ".join(sys.argv[2:]) or "reviewed"))
    sys.exit(check())
