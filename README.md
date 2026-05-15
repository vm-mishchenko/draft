# draft

CLI that implements a spec, opens a PR, and watches CI until green.

```shell
draft create spec.md
```

## Table of Contents

- [Features](#features)
- [Requirements](#requirements)
- [Install](#install)
- [Commands](#commands)
- [Config](#config)

## Features

You define the spec and checks to pass, and the CLI drives the workflow.

- Project-agnostic - no assumptions about language, framework, or build system
- Hooks - run custom commands at every pipeline stage
- Configured checks - you define which checks the generated code must pass
- Discovered tests - LLM picks which tests to run based on spec and edited files
- Code review - performed by a separate agent
- Resumable runs - stop and continue at any point
- Cost tracking - time and cost spent on each spec

Steps in order:

- `create-worktree` — isolated copy of the repo on a new branch
- `implement-spec` — agent edits code and retries until your checks go green
- `review-implementation` — runs your configured reviewers; each reviews the implementation and may produce a follow-up commit
- `push-commits` — pushes the branch to the remote
- `open-pr` — opens a draft PR on GitHub
- `babysit-pr` — watches CI on the PR; failing checks are fed back to the agent until green
- `delete-worktree` — removes the worktree on success; off by default

## Requirements

- Python 3.12+
- `git` on `PATH`
- [`claude`](https://docs.anthropic.com/en/docs/claude-code) CLI, authenticated
- [`gh`](https://cli.github.com/) CLI, authenticated against the target repo

## Install

- Clone and `cd` into the repo
- Run `make setup` and put the venv on your PATH (the command prints where it is)
- Nuclear option: `make clean && make setup`


## Commands

- [draft init](#draft-init) — create a default `.draft/config.yaml` in the current repo
- [draft list](#draft-list) — list recent runs
- [draft status](#draft-status) — show status of a single run
- [draft create](#draft-create) — start a new run from a spec or prompt
- [draft babysit](#draft-babysit) — watch CI on an existing PR and fix failures
- [draft fix-pr](#draft-fix-pr) — fix the current CI failures on a PR locally without pushing
- [draft continue](#draft-continue) — resume a stopped or failed run
- [draft delete](#draft-delete) — remove a single run
- [draft prune](#draft-prune) — bulk-delete finished runs

### draft init

Create `<repo>/.draft/config.yaml` populated with default `timeout` and `max_retries` for every step. Must be run inside a git repository. Fails if `.draft/config.yaml` already exists.

```shell
draft init
```

### draft list

List the 15 most recent runs across all projects.

```shell
draft list
```

### draft status

Show the status of a single run.

```shell
draft status <run-id>
```

**Arguments**

- `run-id` — run to inspect (required)

### draft create

Start a fresh run from a spec file or inline prompt.

```shell
draft create <spec-path>
```

**Arguments**

- `spec-path` — path to the spec file; omit when using `--prompt`
- `--prompt TEXT` — inline prompt text instead of a spec file
- `--run-id NAME` — custom run id instead of the auto-generated timestamp
- `--from BRANCH` — base branch for the new worktree (default: `origin/main` or `origin/master`)
- `--branch [NAME]` — use an existing local branch; omit `NAME` to use current `HEAD`
- `--skip-pr` — stop the run after code generation; skip push and PR steps
- `--no-worktree` — run in the main repo instead of a linked worktree; requires `--branch`
- `--delete-worktree` — remove the worktree after the run succeeds
- `--set STEP.KEY=VALUE` — override a single step config field for this run; repeatable

`--branch` and `--from` are mutually exclusive. `--delete-worktree` and `--no-worktree` are mutually exclusive.

### draft babysit

Watch CI on an existing PR and feed failing checks back to the agent until every check is green or the retry budget is exhausted.

```shell
draft babysit <pr>
```

**Arguments**

- `pr` — PR URL or number (required)
- `--spec PATH` — path to a spec file used as context for the fixer; defaults to the PR body
- `--no-worktree` — run in the main repo instead of a linked worktree
- `--delete-worktree` — remove the worktree after the run succeeds
- `--run-id NAME` — custom run id instead of the auto-generated timestamp
- `--set STEP.KEY=VALUE` — override a single step config field for this run; repeatable

`--delete-worktree` and `--no-worktree` are mutually exclusive. The PR must be open and not from a fork; the branch must already exist locally and match the PR head. If CI is already green, the command exits without running the pipeline.

### draft fix-pr

Apply a single round of fixes to failing PR checks locally. The agent edits the working tree and creates a commit, but `draft` does not push and does not poll checks afterwards. Use this when you want to inspect or hand-edit the fix before publishing it.

```shell
draft fix-pr <pr>
```

**Arguments**

- `pr` — PR URL or number (required)
- `--spec PATH` — path to a spec file used as context for the fixer; defaults to the PR body
- `--no-worktree` — run in the main repo instead of a linked worktree
- `--delete-worktree` — remove the worktree after the run succeeds
- `--run-id NAME` — custom run id instead of the auto-generated timestamp
- `--set STEP.KEY=VALUE` — override a single step config field for this run; repeatable
- `--watch` — wait for the first failing check to appear instead of exiting early when CI is pending or has no failures

`--delete-worktree` and `--no-worktree` are mutually exclusive. The PR must be open and not from a fork; the branch must already exist locally and match the PR head. Without `--watch`, the command exits early if CI is green, has no configured checks, or is still pending; with `--watch`, it polls until a failure appears, the PR head moves on the remote, or watch timeout elapses.

### draft continue

Resume a stopped or failed run.

```shell
draft continue <run-id>
```

**Arguments**

- `run-id` — run to resume; defaults to the most recent run

### draft delete

Remove a single run's state directory and its linked git worktree.

```shell
draft delete <run-id>
```

**Arguments**

- `run-id` — run to remove (required)
- `--delete-branch` — also delete the local git branch for this run

### draft prune

Bulk-delete finished runs. By default operates on the current project.

```shell
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

## Config

- [Model](#model)
- Commands configuration
  - [create-worktree](#create-worktree)
  - [implement-spec](#implement-spec)
  - [review-implementation](#review-implementation)
  - [push-commits](#push-commits)
  - [open-pr](#open-pr)
  - [babysit-pr](#babysit-pr)
  - [delete-worktree](#delete-worktree)
- [Hooks](#hooks)

Config files:

- Project: `.draft/config.yaml`
- Global: `~/.draft/config.yaml`

Project values override global; both merge on top of each step's defaults. `--set <step>.<key>=<value>` overrides a single field for one run.

General configuration structure:
```yaml
steps:
  create-worktree:
    # step configuration
  ...
```

Configuration for each pipeline step.

### Model

```yaml
model: opus

steps:
  implement-spec:
    ...
```

- The value is passed verbatim to `claude --model`, so any value the installed `claude` CLI accepts is valid (aliases like `opus`, `sonnet`, `haiku`; or full IDs like `claude-sonnet-4-5-20250929`).
- When the key is absent, `draft` does not pass `--model` and the `claude` CLI's own default applies.
- The setting is global to the run: every step uses it, and per-step overrides are not supported.
- It merges across global / project configs the same way every other config key does (project wins).

### create-worktree

```yaml
steps:
  create-worktree:
    timeout: 60
```

- `timeout` — per-attempt timeout in seconds

### implement-spec

```yaml
steps:
  implement-spec:
    max_retries: 10
    timeout: 1200
    suggest_extra_checks: true
    max_checks: 5
    per_check_timeout: 120
    suggester_timeout: 120
    suggester_total_budget: 300
    prompt_template: prompts/my_implement.md
```

- `max_retries` — max implementation attempts before failing the step
- `timeout` — per-attempt timeout in seconds
- `suggest_extra_checks` — let an LLM propose extra spec-scoped checks after verify hooks pass
- `max_checks` — cap on suggested checks per attempt
- `per_check_timeout` — per-suggested-check timeout in seconds
- `suggester_timeout` — per-call timeout for the suggester LLM
- `suggester_total_budget` — total time budget for suggester calls per attempt
- `prompt_template` — path to a file that fully replaces the built-in prompt; supports `~`; resolved relative to project root; no default

Step-specific hook event:

- `verify` — runs after each agent attempt, before the commit. Non-zero output is fed back into the next attempt as test failures, and the failing changes stay in the working tree.

```yaml
steps:
  implement-spec:
    hooks:
      verify:
        - cmd: make test
        - cmd: make lint
```

For common hook events (`pre`, `post`, `on_success`, `on_error`) see [Hooks](#hooks).

### review-implementation

```yaml
steps:
  review-implementation:
    suggest_extra_checks: true
    reviewers:
      - name: code-quality
        cmd: scripts/review-code-quality.sh
        timeout: 300
        max_retries: 10
      - name: docs
        cmd: scripts/review-docs.sh
      - name: observability
        cmd: scripts/review-observability.sh
```

- `reviewers` — ordered list of reviewers; each runs sequentially against the worktree as it stands after the previous reviewer's commit
- `reviewers[].name` — unique identifier used in log file names and state; must match `[A-Za-z0-9_-]+`
- `reviewers[].cmd` — command that invokes the reviewer; prints free-text feedback to stdout; empty stdout means approve (no address loop, no commit)
- `reviewers[].timeout` — per-invocation timeout in seconds (default 300)
- `reviewers[].max_retries` — max address-and-fix attempts for that reviewer before failing the step (default 10)
- `suggest_extra_checks` — when true, each reviewer's address loop asks an LLM to propose extra checks before committing

Reviewers run sequentially in declared order. An approve (empty stdout) produces no commit. A reject (non-empty stdout) feeds the feedback to the address loop, which produces exactly one commit if it succeeds. Any reviewer failure aborts the step.

Per-reviewer log files are named `review-implementation.<name>.<suffix>.log` (e.g. `review-implementation.code-quality.review.log`).

**Upgrading from earlier versions**

If your config uses the removed top-level `cmd:` key:

```yaml
# old
steps:
  review-implementation:
    cmd: scripts/review.sh
```

Rewrite it as a single-element `reviewers:` list:

```yaml
# new
steps:
  review-implementation:
    reviewers:
      - name: review
        cmd: scripts/review.sh
```

### push-commits

```yaml
steps:
  push-commits:
    timeout: 120
```

- `timeout` — per-attempt timeout in seconds

### open-pr

```yaml
steps:
  open-pr:
    timeout: 300
    title_prefix: ""
    pr_body_template: .draft/pr-template.md
```

- `timeout` — per-attempt timeout in seconds
- `title_prefix` — string prepended to the PR title
- `pr_body_template` — path to structural guidance for the PR body; supports `~`; resolved relative to project root; contents are inlined into the prompt; no default

### babysit-pr

```yaml
steps:
  babysit-pr:
    max_retries: 100
    timeout: 1200
    checks_delay: 60
```

- `max_retries` — max babysit iterations before giving up
- `timeout` — per-attempt timeout in seconds
- `checks_delay` — seconds to wait before the next CI poll

Step-specific hook event:

- `verify` — runs locally after the agent fixes failing CI checks, before the fix is pushed. Non-zero output is fed back into the next attempt as test failures.

```yaml
steps:
  babysit-pr:
    hooks:
      verify:
        - cmd: make test
        - cmd: make lint
```

For common hook events (`pre`, `post`, `on_success`, `on_error`) see [Hooks](#hooks).

### delete-worktree

```yaml
steps:
  delete-worktree:
    timeout: 60
```

- `timeout` — per-attempt timeout in seconds

### Hooks

A hook is a shell command attached to a step lifecycle event. Hooks run sequentially; the first non-zero exit aborts the chain.

Some steps also expose step-specific events on top of the common ones below. See the corresponding step configuration section.

```yaml
steps:
  implement-spec:
    hooks:
      pre:
        - cmd: make setup
          timeout: 60
        - cmd: pip install -r requirements-dev.txt
        - cmd: ./scripts/seed-db.sh
```

Entry fields:

- `cmd` — shell command, required
- `timeout` — seconds before the hook is killed, default 30

Events available on every step:

- `pre` — before the step runs
- `post` — after the step finishes, success or failure
- `on_success` — after the step succeeds
- `on_error` — after the step fails
