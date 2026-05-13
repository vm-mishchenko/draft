# Fix PR

You are an expert software engineer. A PR has failing CI checks. Fix them in the local working tree; `draft` will run verification and create a commit, but the changes will NOT be pushed.

PR URL: {{PR_URL}}

{{CHECK_FAILURES}}

Your task:
1. The failing checks are listed above. Treat that list as authoritative. You may run `gh pr checks` or `gh run view` for deeper logs
2. Diagnose the root cause of each failure
3. Fix the code to make the checks pass

Focus only on fixing the failing checks. Do not make unrelated changes.

## Do not commit

You must not run `git commit`, `git add`, `git push`, `git stash`, or any command that mutates git history or the index. Leave all changes in the working tree as uncommitted edits. `draft` will run verification and create the commit. If you commit anyway, the run will be treated as no-op and retried until exhausted.

{{VERIFY_COMMANDS}}

## Spec

{{SPEC}}


{{VERIFY_ERRORS}}
