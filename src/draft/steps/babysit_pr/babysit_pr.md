# PR Babysit

You are an expert software engineer. A PR has failing CI checks.

PR URL: {{PR_URL}}

Your task:
1. Look at the CI check failures using `gh pr checks` or by reading the repository
2. Diagnose the root cause of each failure
3. Fix the code to make the checks pass

Focus only on fixing the failing checks. Do not make unrelated changes.

## Do not commit

You must not run `git commit`, `git add`, `git push`, `git stash`, or any command that mutates git history or the index. Leave all changes in the working tree as uncommitted edits. `draft` will run verification and create the commit. If you commit anyway, the run will be treated as no-op and retried until exhausted.

{{VERIFY_COMMANDS}}

## Spec

{{SPEC}}


{{VERIFY_ERRORS}}
