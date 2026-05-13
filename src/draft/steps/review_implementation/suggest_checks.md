You are auditing a code change to decide if any extra lightweight checks should run before the change is committed. Output a strict JSON array.

## Constraints

- Commands must run from the repo root, in the current working tree, with no setup.
- Each command must complete within {{PER_CHECK_TIMEOUT}} seconds.
- No e2e, browser, integration, or network-dependent tests.
- No git mutation, no commits, no network calls, no external services.
- Do not repeat commands that are already in the static check list below.
- If no extra checks are warranted, return [].

## Static checks already running

{{STATIC_CHECKS}}

## Spec

{{SPEC}}

## Changed files (git diff --name-status HEAD)

```
{{CHANGED_FILES}}
```

Use your Read tool to inspect any of these files before deciding.

## Output

Return only a JSON array. Each item: `{"cmd": "...", "timeout": 60}`. The `timeout` field is optional; omit it to use the default. No prose, no markdown fences, no rationale field.
