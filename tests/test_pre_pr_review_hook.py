"""The pre-PR review hook (.claude/hooks/pre_pr_review.py).

It is dev tooling, not part of the package, but it gates every PR, so a
false negative (an unreviewed PR gets through) or a false positive (an
unrelated command is blocked) matters.
"""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parents[1] / ".claude" / "hooks" / "pre_pr_review.py"

_spec = importlib.util.spec_from_file_location("pre_pr_review", HOOK)
hook = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hook)


@pytest.mark.parametrize(
    "command",
    [
        "gh pr create --title x",
        'gh pr create --title "feat: x" --body-file body.md',
        '"$GH" pr create --title x',
        "${GH} pr create -t x",
        'GH="/c/Program Files/GitHub CLI/gh.exe"; "$GH" pr create -t x',
        "git push -u origin feat/x && gh pr create -t x",
        '& "C:\\Program Files\\GitHub CLI\\gh.exe" pr create -t x',
        "/usr/bin/gh pr create -t x",
        "cd repo\ngh pr create -t x",
        "out=$(gh pr create -t x)",
        "gh pr new -t x",
        "gh -R owner/repo pr create -t x",
        "gh --repo=owner/repo pr create -t x",
        "gh pr \\\n  create -t x",
        "gh pr `\n  create -t x",
    ],
)
def test_detects_pr_creation(command):
    assert hook.opens_pr(command)


@pytest.mark.parametrize(
    "command",
    [
        "ls",
        "gh pr view 8",
        "gh pr checks 8 --watch",
        "gh pr edit 8 --body-file body.md",
        'git commit -m "chore: gate gh pr create on a review"',
        "grep -n 'pr create' .claude/hooks/pre_pr_review.py",
        'echo "remember to run gh pr create later"',
        "git log --grep 'pr create'",
    ],
)
def test_ignores_other_commands_and_mentions(command):
    assert not hook.opens_pr(command)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    _git(tmp_path, "init", "-q")
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    _git(tmp_path, "add", "a.txt")
    _git(tmp_path, "commit", "-q", "-m", "first")
    return tmp_path


def _run(cwd: Path, *args: str, stdin: str = "") -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(HOOK), *args],
        cwd=cwd,
        input=stdin,
        capture_output=True,
        text=True,
    )


def _payload(command: str) -> str:
    return json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})


def test_blocks_unreviewed_head_and_allows_after_mark(repo):
    blocked = _run(repo, "check", stdin=_payload("gh pr create -t x"))
    assert blocked.returncode == 2
    assert "pre-PR review" in blocked.stderr

    assert _run(repo, "mark", "standard: 0, codex: 0").returncode == 0
    head = _git(repo, "rev-parse", "HEAD")
    marker = repo / ".git" / "pre-pr-review" / head
    assert marker.read_text(encoding="utf-8").strip() == "standard: 0, codex: 0"
    assert _run(repo, "check", stdin=_payload("gh pr create -t x")).returncode == 0


def test_new_commit_needs_a_new_review(repo):
    _run(repo, "mark")
    (repo / "b.txt").write_text("b", encoding="utf-8")
    _git(repo, "add", "b.txt")
    _git(repo, "commit", "-q", "-m", "second")
    assert _run(repo, "check", stdin=_payload("gh pr create -t x")).returncode == 2


def test_other_commands_pass_without_a_review(repo):
    for command in ("ls", 'git commit -m "gate gh pr create"', "gh pr view 1"):
        assert _run(repo, "check", stdin=_payload(command)).returncode == 0


def test_help_is_not_gated(repo):
    for command in ("gh pr create --help", "gh pr create -h"):
        assert _run(repo, "check", stdin=_payload(command)).returncode == 0


@pytest.mark.parametrize(
    "command",
    [
        'git commit -qm "fix" && gh pr create -t x',
        "git switch other && gh pr create -t x",
        "cd ../other && gh pr create -t x",
        'git -C ../other commit -qm "fix" && gh pr create -t x',
        'git commit -qm "fix"\ngh pr create -t x',
    ],
)
def test_moving_head_in_the_same_command_is_blocked_even_when_reviewed(repo, command):
    _run(repo, "mark")
    result = _run(repo, "check", stdin=_payload(command))
    assert result.returncode == 2
    assert "command of its own" in result.stderr


def test_head_flag_must_name_the_checked_out_branch(repo):
    _run(repo, "mark")
    branch = _git(repo, "branch", "--show-current")
    ok = _run(repo, "check", stdin=_payload(f"gh pr create --head {branch} -t x"))
    assert ok.returncode == 0
    other = _run(repo, "check", stdin=_payload("gh pr create --head other -t x"))
    assert other.returncode == 2
    assert "--head other" in other.stderr


def test_other_repository_is_outside_the_gate(repo):
    _git(repo, "remote", "add", "origin", "https://github.com/me/this-repo.git")
    other = _payload("gh pr create -R me/another-repo --head feat -t x")
    assert _run(repo, "check", stdin=other).returncode == 0
    same = _payload("gh pr create --repo me/this-repo -t x")
    assert _run(repo, "check", stdin=same).returncode == 2
    flag_first = _payload("gh -R me/this-repo pr create -t x")
    assert _run(repo, "check", stdin=flag_first).returncode == 2
    other_first = _payload("gh -R me/another-repo pr create --head feat -t x")
    assert _run(repo, "check", stdin=other_first).returncode == 0


def test_outside_a_git_repository_nothing_is_gated(tmp_path):
    assert _run(tmp_path, "check", stdin=_payload("gh pr create")).returncode == 0


def test_malformed_input_is_not_blocked(repo):
    assert _run(repo, "check", stdin="not json").returncode == 0
    assert _run(repo, "check", stdin=json.dumps({"tool_input": None})).returncode == 0
