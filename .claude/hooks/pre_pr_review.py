"""Gate `gh pr create` on a completed pre-PR review of the current commit.

Two modes:
  check  PreToolUse hook. Reads the tool call as JSON on stdin. If the
         command opens a PR for this repository and the current HEAD has no
         review marker, it exits 2, which blocks the call and shows the
         reason to Claude.
  mark   Records that HEAD was reviewed. The pre-pr-review skill runs it
         after both reviews are done and their blocking findings are fixed.

Markers live in .git/pre-pr-review/<sha>, so they are never committed. A new
commit needs a new review before a PR can be opened; later pushes to an
open PR are not gated.

The hook checks HEAD as it is before the command runs, so a command that
also moves HEAD or changes directory (`git commit ... && gh pr create`) is
blocked with a request to run `gh pr create` on its own. PRs for another
repository (`--repo other/repo`) are outside this gate.

Detection is a best-effort guard rail for Claude's own commands, not a
security boundary: indirect forms such as `bash -c '...'`, `env gh ...` or
`gh api .../pulls` are not recognised.
"""

import json
import re
import shlex
import subprocess
import sys
from pathlib import Path

# Command position: the start of a line, or after ; & | ( { ` or $(.
_CMD_START = r"(?:^|[;&|({`\n]|\$\()\s*"

# `<exe> pr create|new` with <exe> in command position, optionally behind
# PowerShell's & call operator. A mention inside an argument (a commit
# message, a grep pattern) is not in command position, so it is not matched.
PR_CREATE = re.compile(
    _CMD_START + r"(?:&\s*)?"
    r"(?P<exe>\"[^\"\n]*\"|'[^'\n]*'|\$\{\w+\}|[^\s;&|(){}`\"']+)"
    # global flags before the subcommand, e.g. `gh -R owner/repo pr create`
    r"(?P<pre>(?:\s+-{1,2}[\w-]+(?:[=\s]+[^\s;&|\-][^\s;&|]*)?)*)"
    r"\s+pr\s+(?:create|new)\b(?P<args>[^;&|\n]*)",
    re.MULTILINE,
)
# gh by name or path (gh, gh.exe, .../GitHub CLI/gh.exe), or a variable
# holding it ($GH, ${GH}, $GH_BIN).
GH_EXE = re.compile(r"(?:^|[\\/])gh(?:\.exe)?$|^\$\{?\w*GH\w*\}?$", re.IGNORECASE)
# Line continuations: bash `\` and PowerShell backtick before a newline.
CONTINUATION = re.compile(r"[\\`]\r?\n")
# Commands that change HEAD or the directory before gh would run.
MOVES_HEAD = re.compile(
    # `git [-C dir] [-c key=value] <subcommand>`
    _CMD_START + r"(?:git(?:\s+-[Cc]\s+\S+)*\s+"
    r"(?:commit|switch|checkout|reset|rebase|merge|cherry-pick|am|pull|stash)\b"
    r"|cd\b|pushd\b|Set-Location\b)",
    re.MULTILINE | re.IGNORECASE,
)


def _flags(args: str) -> dict[str, str]:
    """`help`, `repo` and `head` from gh's arguments, read from shell tokens so
    text inside a quoted --title or --body never counts as a flag. Arguments
    that can't be tokenized give {}, which keeps the PR gated."""
    try:
        tokens = shlex.split(args)
    except ValueError:
        return {}
    flags: dict[str, str] = {}
    for i, token in enumerate(tokens):
        if token in ("-h", "--help"):
            flags["help"] = ""
        for name, short in (("repo", "-R"), ("head", "-H")):
            if token in (short, f"--{name}") and i + 1 < len(tokens):
                flags[name] = tokens[i + 1]
            elif token.startswith(f"--{name}="):
                flags[name] = token.split("=", 1)[1]
    return flags


def pr_invocations(command: str) -> list[str]:
    """The flags and arguments of every `gh pr create`/`gh pr new` in `command`."""
    command = CONTINUATION.sub(" ", command)
    return [
        m.group("pre") + " " + m.group("args")
        for m in PR_CREATE.finditer(command)
        if GH_EXE.search(m.group("exe").strip("\"'"))
    ]


def opens_pr(command: str) -> bool:
    """True if `command` runs `gh pr create` (directly, by path or via a variable)."""
    return bool(pr_invocations(command))


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], capture_output=True, text=True, check=True
    ).stdout.strip()


def _marker() -> Path:
    return Path(_git("rev-parse", "--git-path", "pre-pr-review")) / _git(
        "rev-parse", "HEAD"
    )


def _this_repo(slug: str) -> bool:
    """Whether `--repo` names this repository (compared with origin's URL)."""
    try:
        origin = _git("remote", "get-url", "origin").lower().removesuffix(".git")
    except subprocess.CalledProcessError:
        return True
    slug = slug.lower().removesuffix(".git").split("github.com/")[-1]
    return origin.endswith("/" + slug) or origin.endswith(":" + slug)


def _block(reason: str) -> int:
    print(f"Blocked: {reason}", file=sys.stderr)
    return 2


def check() -> int:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        return 0
    command = (payload.get("tool_input") or {}).get("command") or ""
    gated = []
    for args in pr_invocations(command):
        flags = _flags(args)
        if "help" in flags:
            continue
        if "repo" in flags and not _this_repo(flags["repo"]):
            continue
        gated.append(flags)
    if not gated:
        return 0
    try:
        marker = _marker()
        branch = _git("branch", "--show-current")
    except subprocess.CalledProcessError:
        return 0  # not in a git repository (or no commits yet): nothing to gate
    if MOVES_HEAD.search(CONTINUATION.sub(" ", command)):
        return _block(
            "this command also commits, switches branches or changes directory, "
            "so the review check would look at the wrong commit. Run `gh pr "
            "create` as a command of its own, after the last commit."
        )
    for flags in gated:
        head = flags.get("head")
        if head and head.split(":")[-1] != branch:
            return _block(
                f"--head {head} is not the checked-out branch "
                f"({branch or 'detached HEAD'}). Switch to it, run the pre-PR "
                "review there, then open the PR."
            )
    if marker.is_file():
        return 0
    return _block(
        f"HEAD {marker.name[:7]} has not been through the pre-PR review. Run "
        "the pre-pr-review skill (standard reviewer + Codex correctness "
        "review), fix the blocking findings, then open the PR."
    )


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
