---
name: pre-pr-review
description: Run the two pre-PR reviews for this repo (standard-reviewer subagent + Codex correctness review), fix blocking findings, and mark HEAD as reviewed so `gh pr create` is allowed. Use before opening every pull request.
---

# Pre-PR review

Every PR in this repo goes through two reviewers before it is opened. The PreToolUse hook in `.claude/settings.json` blocks `gh pr create` until this skill has marked the current commit as reviewed.

## 1. Prepare

- Commit all work first. The marker is tied to the HEAD commit, and reviewers read the branch diff.
- Run `git diff --stat main...HEAD`. Decide whether this is a **spec/docs PR** (only Markdown under `planning/`, `docs/` or the repo root) or a **code PR**.
- Run `make format`, `make test` and `make lint` for a code PR. Fix failures before reviewing.

## 2. Run both reviewers in parallel

**Standard reviewer (Claude).** Launch the `standard-reviewer` subagent with the Agent tool. Give it the base (`main`), the PR's purpose and title, and, for milestone work, the spec file. A new agent definition only becomes an Agent type after a session restart. If `standard-reviewer` isn't listed, launch a `general-purpose` agent told to read `.claude/agents/standard-reviewer.md` and follow it exactly.

**Correctness reviewer (Codex).** Run it with the Bash tool and `run_in_background: true`, because a review can take several minutes. Use one command: shell variables don't survive between Bash calls. That command finds the newest installed plugin script and fails clearly if there is none:

```bash
CODEX=$(ls -d "$HOME"/.claude/plugins/cache/openai-codex/codex/*/scripts/codex-companion.mjs 2>/dev/null | sort -V | tail -1); [ -n "$CODEX" ] || { echo "Codex plugin not found" >&2; exit 1; }; node "$CODEX" review --base main --scope branch --wait
```

- **Code PR:** the command above.
- **Spec/docs PR:** replace the last part with `node "$CODEX" adversarial-review --base main --scope branch --wait "<focus: the decisions to challenge, internal contradictions, rules that can't be implemented as written>"`.

Codex reads `AGENTS.md`, which holds its review rules. If the plugin isn't found, or Codex reports it isn't installed or logged in, tell the user and ask whether to continue with the standard reviewer only. Never mark a commit as reviewed by both when only one review ran.

## 3. Triage, once

- Merge the two lists and drop duplicates. Check every finding against the code yourself; reviewers can be wrong.
- **Fix** everything blocking, P0 or P1 that holds up: bugs, invariant or contract violations, behaviour changes, security issues, wrong figures. Also fix cheap should-fix items.
- **List** the rest for the user, with the reason each one is left: already covered (say where), cosmetic, or out of scope (and whether it should become a follow-up PR).
- After fixing, rerun the tests and commit. Don't start another full review round for the fixes. The CI Claude review on the PR covers them.

## 4. Mark and open

```bash
python .claude/hooks/pre_pr_review.py mark "standard: <n> findings, codex: <n> findings, fixed: <n>"
```

Then open the PR with `gh pr create` as a command of its own: the hook blocks it when it's chained with a commit, a branch switch or a `cd`. Add a short **Pre-PR review** section to its body: what each reviewer found, what was fixed, and what was left and why.

## After the PR is open

Read the CI Claude review (every comment, including the latest). Keep fixing while a round raises a real issue. Stop when a round has only nits, repeats or points already covered, and report those to the user instead of pushing again. After about three rounds, check in with the user. A fix push after the PR is open doesn't need a new pre-PR review.
