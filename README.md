# draft

CLI that takes a spec file (or inline prompt), runs it through an AI-powered pipeline, opens a draft pull request, and watches CI until it goes green.

## Install

- Clone and `cd` into the repo
- Run `make setup` and put the venv on your PATH (the command prints where it is)
- Nuclear option: `make clean && make setup`

## Commands

### draft list

List the 15 most recent runs across all projects.

```
draft list
```

No options.

### draft create

Start a fresh run from a spec file or inline prompt.

```
draft create <spec-path>
draft create --prompt "TEXT"
```

**Arguments**

- `spec-path` — path to the spec file; omit when using `--prompt`
- `--prompt TEXT` — inline prompt text instead of a spec file
- `--from BRANCH` — base branch for the new worktree (default: `origin/main` or `origin/master`)
- `--branch [NAME]` — use an existing local branch; omit `NAME` to use current `HEAD`
- `--skip-pr` — stop after code generation; skip push and PR steps
- `--no-worktree` — run in the main repo instead of a linked worktree; requires `--branch`
- `--delete-worktree` — remove the worktree after the run succeeds
- `--set STEP.KEY=VALUE` — override a single step config field for this run; repeatable

`--branch` and `--from` are mutually exclusive. `--delete-worktree` and `--no-worktree` are mutually exclusive.

### draft continue

Resume a stopped or failed run.

```
draft continue [run-id]
```

**Arguments**

- `run-id` — run to resume; defaults to the most recent run

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

## Use Cases

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

## Pipeline

A run is an ordered chain of steps. Each step streams its log to `~/.draft/runs/<project>/<run-id>/<step>.log` and persists progress in `state.json`, so a failing run can be resumed with `draft continue`. Around every step `draft` fires lifecycle events that user-defined hooks can subscribe to.

Steps run in this order:

- `create-worktree` — create a linked git worktree on a fresh branch off the base branch
- `implement-spec` — invoke `claude` with the spec, retry until it produces a clean tree with at least one commit, then run the `verify` hooks; if verification fails the errors are fed back into the next attempt
- `push-commits` — `git push -u origin HEAD`
- `open-pr` — ask `claude` for a title and body, then `gh pr create --draft`
- `view-pr` — resolve the PR URL via `gh pr view` (used when resuming a run that already pushed)
- `babysit-pr` — poll CI; when checks fail, hand them to `claude` to fix until everything is green
- `delete-worktree` — remove the linked worktree with `git worktree remove --force`

`--skip-pr` stops after `implement-spec` and skips `push-commits`, `open-pr`, `view-pr`, `babysit-pr`.

`delete-worktree` is included only when `--delete-worktree` is set and `worktree_mode` is `worktree` or `reuse-existing`; it is skipped otherwise. If the worktree directory is already absent when the step runs, it succeeds without error (idempotent). This makes resume safe: re-running after a partial cleanup does not fail. Hooks at `steps.delete-worktree.hooks.<event>` are opt-in and fire only when the step is active; a skipped step fires no hooks.

## Config

Project config: `.draft/config.yaml`
Global config: `~/.draft/config.yaml`

Project values override global; both merge on top of each step's defaults. `--set <step>.<key>=<value>` overrides a single field for one run.

```yaml
steps:
  create-worktree:
    hooks:
      post:
        - cmd: connect-global-agent
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

- `max_retries` — attempts before failing the step
- `timeout` — per-attempt timeout in seconds
- `retry_delay` — seconds to wait between retries

Step-specific fields:

- `open-pr.title_prefix` — string prepended to the PR title
- `babysit-pr.checks_delay` — seconds to wait before the first CI poll

Defaults per step:

- `create-worktree`: `max_retries=1`, `timeout=60`
- `implement-spec`: `max_retries=10`, `timeout=1200`
- `push-commits`: `max_retries=1`, `timeout=120`
- `open-pr`: `max_retries=1`, `timeout=300`, `title_prefix=""`
- `view-pr`: `max_retries=3`, `timeout=30`, `retry_delay=5`
- `babysit-pr`: `max_retries=100`, `timeout=1200`, `retry_delay=60`, `checks_delay=30`
- `delete-worktree`: `max_retries=1`, `timeout=60`

## Hooks

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

- `implement-spec.verify` — invoked once `claude` produces a clean commit; non-zero output is fed back into the next `implement-spec` attempt as test failures
