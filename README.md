# draft

CLI that takes a spec file (or inline prompt), runs it through an AI-powered pipeline, opens a draft pull request, and watches CI until it goes green.

## Install

- Clone and `cd` into the repo
- Run `make setup` and put the venv on your PATH (the command prints where it is)
- Nuclear option: `make clean && make setup`

## Usage

```
draft list
draft create <spec-path>
draft create --prompt "TEXT"
draft create <spec-path> --skip-pr
draft create <spec-path> --from <branch>
draft create <spec-path> --set <step>.<key>=<value>
draft continue [run-id]
draft delete <run-id> [--delete-branch]
draft prune
draft prune --yes [--delete-branch]
draft prune --all [--project NAME | --all-projects]
draft prune --dry-run
```

## Pipeline

A run is an ordered chain of steps. Each step streams its log to `~/.draft/runs/<project>/<run-id>/<step>.log` and persists progress in `state.json`, so a failing run can be resumed with `draft continue`. Around every step `draft` fires lifecycle events that user-defined hooks can subscribe to.

Steps run in this order:

- `worktree-create`: create a linked git worktree on a fresh branch off the base branch
- `code-spec`: invoke `claude` with the spec, retry until it produces a clean tree with at least one commit, then run the `verify` hooks; if verification fails the errors are fed back into the next attempt
- `push`: `git push -u origin HEAD`
- `pr-open`: ask `claude` for a title and body, then `gh pr create --draft`
- `pr-view`: resolve the PR URL via `gh pr view` (used when resuming a run that already pushed)
- `pr-babysit`: poll CI; when checks fail, hand them to `claude` to fix until everything is green

`--skip-pr` stops after `code-spec` and skips `push`, `pr-open`, `pr-view`, `pr-babysit`.

## Config

Project config: `.draft/config.yaml`
Global config: `~/.draft/config.yaml`

Project values override global; both merge on top of each step's defaults. `--set <step>.<key>=<value>` overrides a single field for one run.

```yaml
steps:
  worktree-create:
    hooks:
      post:
        - cmd: connect-global-agent
  code-spec:
    max_retries: 10
    timeout: 1200
    hooks:
      pre:
        - cmd: make setup
      verify:
        - cmd: make test
  pr-open:
    title_prefix: "PROJ-12345: "
  pr-babysit:
    checks_delay: 30
```

### Step fields

Common fields (all steps):

- `max_retries`: attempts before failing the step
- `timeout`: per-attempt timeout in seconds
- `retry_delay`: seconds to wait between retries

Step-specific fields:

- `pr-open.title_prefix`: string prepended to the PR title
- `pr-babysit.checks_delay`: seconds to wait before the first CI poll

Defaults per step:

- `worktree-create`: `max_retries=1`, `timeout=60`
- `code-spec`: `max_retries=10`, `timeout=1200`
- `push`: `max_retries=1`, `timeout=120`
- `pr-open`: `max_retries=1`, `timeout=300`, `title_prefix=""`
- `pr-view`: `max_retries=3`, `timeout=30`, `retry_delay=5`
- `pr-babysit`: `max_retries=100`, `timeout=1200`, `retry_delay=60`, `checks_delay=30`

## Hooks

A hook is a shell command attached to a step lifecycle event. Hooks live under `steps.<step-name>.hooks.<event>` and run sequentially; the first non-zero exit aborts the chain and fails the step.

```yaml
steps:
  code-spec:
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

- `cmd`: shell command, required
- `timeout`: seconds before the hook is killed, default 30

Events available on every step:

- `pre`: before the step runs
- `post`: after the step finishes, success or failure
- `on_success`: after the step succeeds
- `on_error`: after the step raises a `StepError`

Step-specific events:

- `code-spec.verify`: invoked once `claude` produces a clean commit; non-zero output is fed back into the next `code-spec` attempt as test failures
