---
description: Multi-agent code review - correctness, security and tests reviewers in parallel, then a verifier
argument-hint: "[staged | <base-branch>]"
---

Run a multi-agent review of the current changes. Argument: `$ARGUMENTS`

**1. Work out the changes**
- If the argument is `staged`: the change is `git diff --cached`.
- If the argument names a branch: the change is `git diff <branch>`. Use this for a phase branch built on another phase, e.g. `/review phase-c-api`.
- Otherwise: `git diff main` (committed and uncommitted changes against main).

Run `git diff --stat` with the same arguments, and `git status --short`. If there are no changes, say so and stop. Otherwise note the diff command and the list of changed files; don't paste the whole diff into the prompts, because the reviewers read it themselves.

**2. Run the three specialists in parallel.** In one message, launch three Agent calls with `subagent_type` set to `correctness-reviewer`, `security-reviewer` and `tests-reviewer`. Give each the same brief: the repo path, the exact diff command, the changed files, and one or two sentences on what the change does (from the commit messages or `git log`). Wait for all three.

**3. Verify.** Launch one Agent call with `subagent_type: review-verifier`. Give it the repo path, the diff command, the changed files, and all three reports in full, each labeled with its reviewer.

**4. Report.** Print the verifier's final report as it is. After it, add one line with the specialists' raw finding counts, e.g. "Specialists reported 9 findings; 4 kept after verification." Don't fix anything unless the user asks.

The review is read-only: no edits, no commits, no `RUN_DB_TESTS=1`, no paid API calls, no `gh`.
