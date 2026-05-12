# draft

CLI that takes a spec, runs it through an AI-powered pipeline, opens a draft pull request, and watches CI until it goes green.

```shell
draft create spec.md
```

## Table of Content

- [Install](#install)
- [Requirements](#requirements)
- [Commands](#commands)
- [How it works](#how-it-works)
- [Use Cases](#use-cases)
- [Config](#config)

## Install

- Clone and `cd` into the repo
- Run `make setup` and put the venv on your PATH (the command prints where it is)
- Nuclear option: `make clean && make setup`

## Requirements

- Python 3.12+
- `git` on `PATH`
- [`claude`](https://docs.anthropic.com/en/docs/claude-code) CLI, authenticated
- [`gh`](https://cli.github.com/) CLI, authenticated against the target repo

## Commands

- [draft list](#draft-list) — list recent runs
- [draft status](#draft-status) — show status of a single run
- [draft create](#draft-create) — start a new run from a spec or prompt
- [draft continue](#draft-continue) — resume a stopped or failed run
- [draft delete](#draft-delete) — remove a single run
- [draft prune](#draft-prune) — bulk-delete finished runs

### draft list

List the 15 most recent runs across all projects. Runs are ordered by `started_at` (from `state.json`), so custom run ids sort correctly by recency.

```
draft list
```

No options.

### draft status

Show the status of a single run: run-level state, step-by-step progress, branch, worktree path, and PR URL.

```
draft status <run-id>
```

**Arguments**

- `run-id` — run to inspect (required)

### draft create

Start a fresh run from a spec file or inline prompt.

```
draft create <spec-path>
draft create --prompt "TEXT"
```

**Arguments**

- `spec-path` — path to the spec file; omit when using `--prompt`
- `--prompt TEXT` — inline prompt text instead of a spec file
- `--run-id NAME` — custom run id instead of the auto-generated timestamp; allowed characters: `[a-z0-9._-]`, 1–64 chars, must not start or end with `-`, `_`, or `.`, must not contain `..` or match `YYMMDD-HHMMSS`
- `--from BRANCH` — base branch for the new worktree (default: `origin/main` or `origin/master`)
- `--branch [NAME]` — use an existing local branch; omit `NAME` to use current `HEAD`
- `--skip-pr` — stop after code generation; skip push and PR steps
- `--no-worktree` — run in the main repo instead of a linked worktree; requires `--branch`
- `--delete-worktree` — remove the worktree after the run succeeds
- `--set STEP.KEY=VALUE` — override a single step config field for this run; repeatable

`--branch` and `--from` are mutually exclusive. `--delete-worktree` and `--no-worktree` are mutually exclusive.

**Example with custom run id**

```
draft create spec.md --run-id auth-refactor
draft continue auth-refactor
draft status auth-refactor
draft delete auth-refactor
```

### draft continue

Resume a stopped or failed run.

```
draft continue [run-id]
```

**Arguments**

- `run-id` — run to resume; defaults to the most recent run (determined by `started_at`, not id sort)

### draft delete

Remove a single run's state directory and its linked git worktree.

```
draft delete <run-id>
draft delete <run-id> --delete-branch
```

**Arguments**

- `run-id` — run to remove (required)
- `--delete-branch` — also delete the local git branch for this run

### draft prune

Bulk-delete finished runs. By default operates on the current project.

```
draft prune
draft prune --yes
draft prune --dry-run
draft prune --all
draft prune --project NAME
draft prune --all-projects
draft prune --delete-branch
```

**Arguments**

- `--yes`, `-y` — skip the confirmation prompt
- `--dry-run` — print the selection and exit without deleting
- `--all` — include every non-active run regardless of finished status (not only successful ones)
- `--project NAME` — operate on the named project instead of the current one
- `--all-projects` — operate across every project under `~/.draft/runs/`; mutually exclusive with `--project`
- `--delete-branch` — also delete the local git branch for each pruned run

## How it works

`draft` turns a spec or prompt into a merge-ready pull request without you babysitting it. It's project-agnostic — no assumptions about language, framework, or build system. A run walks through a fixed sequence of steps. If any step fails, the run stops at that point and `draft continue` picks up where it left off.

Steps in order:

- `create-worktree` — sets up an isolated copy of your repo on a new branch. Your current checkout is never touched, so you can keep working on something else while a run is in progress.
- `implement-spec` — the agent reads your spec and edits code. After each attempt `draft` runs your verification commands (tests, linters, anything you configure under `hooks.verify`); failures are handed back to the agent for another attempt. When verification passes, a second agent run drafts a commit message and `draft` creates the commit.
- `push-commits` — publishes the new branch to your remote.
- `open-pr` — composes a PR title and body from the spec and opens a draft pull request on GitHub.
- `babysit-pr` — watches CI. Every failing check is fed back to the agent for a fix; the loop continues until every check is green or the retry budget is exhausted.
- `delete-worktree` — removes the isolated working copy when the run is done. Off by default; opt in per run with `--delete-worktree`.

Every step exposes lifecycle hooks where you can attach custom shell commands — bootstrap the environment, run your tests, send notifications — without forking `draft`. See [Config](#config) for the full reference.

`--skip-pr` stops the run after `implement-spec`. The commits stay on a local branch; nothing is pushed and no PR is opened.

## Use Cases

- [Spec or prompt to PR](#spec-or-prompt-to-pr) — generate code and open a draft PR
- [Spec or prompt to commit](#spec-or-prompt-to-commit) — generate code locally without pushing
- [Continue after failure](#continue-after-failure) — resume a stopped or failed run
- [Iterate on an existing branch](#iterate-on-an-existing-branch) — run against a branch that already has work
- [Clean up finished runs](#clean-up-finished-runs) — remove finished runs in bulk

### Spec or prompt to PR

Write a spec file describing the change, then run:

```
draft create path/to/spec.md
```

Or skip the file and pass an inline description:

```
draft create --prompt "add a health-check endpoint"
```

`draft` creates a worktree on a new branch, runs `claude` against the spec until the code is clean and tests pass, pushes the branch, opens a draft PR, then polls CI and asks `claude` to fix any failures until every check goes green.

### Spec or prompt to commit

Use `--skip-pr` to stop after code generation. No push or PR is created; the commits land on a local branch in a worktree.

```
draft create path/to/spec.md --skip-pr
```

Add `--delete-worktree` to clean up the worktree automatically once the commits are done:

```
draft create path/to/spec.md --skip-pr --delete-worktree
```

### Continue after failure

If a run stops partway through (network error, CI timeout, etc.), resume it:

```
draft continue
```

To resume a specific run rather than the most recent one:

```
draft continue 260506-143201
```

### Iterate on an existing branch

Point `draft` at a branch that already has some work on it. `draft` reuses or creates the canonical worktree for that branch and, if a draft PR is already open, skips straight to babysitting CI.

```
draft create path/to/spec.md --branch my-feature-branch
```

Use current `HEAD` without typing the branch name:

```
draft create path/to/spec.md --branch
```

### Clean up finished runs

Remove all successfully finished runs for the current project:

```
draft prune
```

Preview what would be deleted:

```
draft prune --dry-run
```

Remove runs for every project and also delete their branches:

```
draft prune --all-projects --delete-branch --yes
```

## Config

Project config: `.draft/config.yaml`
Global config: `~/.draft/config.yaml`

Project values override global; both merge on top of each step's defaults. `--set <step>.<key>=<value>` overrides a single field for one run.

```yaml
steps:
  create-worktree:
    hooks:
      post:
        - cmd: pwd
  implement-spec:
    max_retries: 10
    timeout: 1200
    hooks:
      pre:
        - cmd: make setup
      verify:
        - cmd: make test
  open-pr:
    title_prefix: "PROJ-12345: "
  babysit-pr:
    checks_delay: 30
```

### Step fields

Common fields (all steps):

- `timeout` — per-attempt timeout in seconds

Step-specific fields:

- `implement-spec.max_retries` — maximum implementation attempts before failing the step
- `implement-spec.suggest_extra_checks` — after static verify passes, draft asks an LLM for additional spec-scoped checks and runs them; commands execute with `shell=True` in the worktree and are gated only by timeout and budget; set to `false` to disable (default `true`)
- `implement-spec.max_checks` — maximum number of LLM-suggested checks to run per attempt (default 5, range 0–20)
- `implement-spec.per_check_timeout` — per-command timeout in seconds for suggested checks (default 120, range 1–180)
- `implement-spec.suggester_timeout` — timeout in seconds for the LLM call that generates suggested checks (default 120, range 1–600)
- `implement-spec.suggester_total_budget` — total time budget in seconds across all suggested checks per attempt (default 300, range 1–3600)
- `babysit-pr.max_retries` — maximum babysit iterations before giving up
- `open-pr.title_prefix` — string prepended to the PR title
- `open-pr.pr_body_template` — path to a file used as structural guidance for the PR body; supports `~` and is resolved relative to the project root; contents are inlined into the prompt (not passed as a path)
- `babysit-pr.checks_delay` — seconds to wait before the next CI poll
- `implement-spec.prompt_template` — path to a file that fully replaces the built-in implement-spec prompt; supports `~` and is resolved relative to the project root

Defaults per step:

- `create-worktree`: `timeout=60`
- `implement-spec`: `max_retries=10`, `timeout=1200`, `suggest_extra_checks=true`, `max_checks=5`, `per_check_timeout=120`, `suggester_timeout=120`, `suggester_total_budget=300`
- `push-commits`: `timeout=120`
- `open-pr`: `timeout=300`, `title_prefix=""`
- `babysit-pr`: `max_retries=100`, `timeout=1200`, `checks_delay=30`
- `delete-worktree`: `timeout=60`

### Hooks

A hook is a shell command attached to a step lifecycle event. Hooks live under `steps.<step-name>.hooks.<event>` and run sequentially; the first non-zero exit aborts the chain and fails the step.

```yaml
steps:
  implement-spec:
    hooks:
      pre:
        - cmd: make setup
          timeout: 60
      verify:
        - cmd: make test
      on_error:
        - cmd: notify-slack
```

Entry fields:

- `cmd` — shell command, required
- `timeout` — seconds before the hook is killed, default 30

Events available on every step:

- `pre` — before the step runs
- `post` — after the step finishes, success or failure
- `on_success` — after the step succeeds
- `on_error` — after the step raises a `StepError`

Step-specific events:

- `implement-spec.verify` — invoked after the agent edits the working tree, before draft commits; non-zero output is fed back into the next implement-spec attempt as test failures, and the failing changes stay in the working tree.

**LLM-suggested checks**

After the static verify hooks pass, draft makes an additional LLM call that inspects the changed-file list and suggests a short list of supplementary shell commands. These commands are deduplicated against the configured verify list and then executed with `shell=True` in the worktree, gated only by per-command timeout (`per_check_timeout`) and a total time budget (`suggester_total_budget`). Any failure is fed back to the implement agent as another retry. There is no opt-out for this phase.

### Custom implement-spec prompt

Set `implement-spec.prompt_template` to replace the built-in prompt with your own file.

**Template contract**

- `{{SPEC}}` is required — draft substitutes the spec content here.
- `{{VERIFY_COMMANDS}}` is optional — draft substitutes the list of configured `steps.implement-spec.hooks.verify` commands here as a fenced bash block; omit it to suppress the section (no warning). The rendered block is informational; the agent is encouraged but not required to run the commands before finishing.
- `{{VERIFY_ERRORS}}` is recommended — draft substitutes verify hook failures here on retries; omitting it means Claude will not see failure output and a warning is printed.
- Your template must not instruct the agent to commit; draft creates the commit. Including a "commit your work" line will cause the agent to commit, leaving the working tree clean, and the step will loop until max_retries.

**Path resolution**

The path is expanded with `~` support, then resolved relative to the project root (the directory containing `.draft/`). The resolved absolute path is snapshotted into `state.json` at `draft create` time; `draft continue` uses that snapshot. If the file is removed between create and continue, the step fails with an error naming the path.

**Precedence**

Default built-in → `~/.draft/config.yaml` → `.draft/config.yaml` → `--set implement-spec.prompt_template=<path>`

**Example**

```yaml
steps:
  implement-spec:
    prompt_template: prompts/my_implement.md
```

The default template lives at `src/draft/steps/implement_spec/implement_spec.md` — copy it as a starting point.

### Custom PR body template

Set `open-pr.pr_body_template` to give the agent a file with structural guidance for the PR body. Before invoking Claude, `draft` precomputes the body template content, `git diff <base>..HEAD`, and `git log <base>..HEAD --format=%s%n%n%b`, and inlines all three directly into the prompt — Claude does not read any files or run any git commands itself. Note that prompt size grows linearly with diff and log length on branches with many changes.

**Path resolution**

The path is expanded with `~` support, then resolved relative to the project root (the directory containing `.draft/`). The resolved absolute path is snapshotted into `state.json` at `draft create` time; `draft continue` uses that snapshot. If the file is removed between create and continue, the step fails with an error naming the path.

**Precedence**

Bundled default → `~/.draft/config.yaml` → `.draft/config.yaml` → `--set open-pr.pr_body_template=<path>`

**Example**

```yaml
steps:
  open-pr:
    pr_body_template: .draft/pr-template.md
```

The bundled default lives at `src/draft/steps/open_pr/pull-request-template.md` — copy it as a starting point.

**Migration note**

In previous versions, draft automatically looked for a PR body template at `<repo>/.draft/pull-request-template.md` and `~/.draft/pull-request-template.md`. This convention-path search has been removed. If you relied on either path, add an explicit `steps.open-pr.pr_body_template` entry pointing to the same file.

**Migration note: retry fields removed**

`retry_delay` has been removed from all steps. `max_retries` has been removed from `create-worktree`, `push-commits`, `open-pr`, and `delete-worktree` — those steps run exactly once. Setting either field on an unsupported step now fails preflight with a clear error message (exit 3 for YAML config, exit 2 for `--set`).
