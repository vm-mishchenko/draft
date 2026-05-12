# Suggest verification checks

You are reviewing code changes and need to suggest additional lightweight verification commands.

## Spec

{{SPEC}}

## Changed files

{{CHANGED_FILES}}

## Static checks (already configured — do not suggest these)

{{STATIC_CHECKS}}

## Instructions

Use the Read tool to inspect changed files if needed to understand what checks are appropriate.

Based on the spec and changed files, suggest additional verification commands that would catch mistakes not covered by the static checks above.

**Constraints:**
- Do not suggest any command already listed in the static checks above
- Commands run with `shell=True` in the worktree directory
- Each command must complete within {{PER_CHECK_TIMEOUT}} seconds
- Only suggest commands that are likely already available in this repository
- Prefer targeted checks (specific files or modules) over broad ones
- Do not suggest commands that modify files, write to disk, or have side effects beyond exit code
- Do not suggest interactive commands or commands requiring user input
- Do not suggest commands that require network access
- Suggest at most 5 commands; fewer is fine if nothing additional is warranted

## Output format

Output a JSON array only, with no other text before or after it. Each entry must have a `cmd` field (non-empty string). You may include a `timeout` field (positive integer, seconds). Do not include a `rationale` field or any other field.

If no additional checks are warranted, output an empty array.

Example:

[{"cmd": "python -m pytest tests/test_foo.py -x", "timeout": 60}]
