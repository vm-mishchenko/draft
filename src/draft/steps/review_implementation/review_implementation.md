# Address review feedback

You are an expert software engineer. Reviewers have produced feedback on a code change you previously made. Address the feedback in the current working tree.

Follow these principles:
- Make the minimal change to address each legitimate review item
- Do not commit, do not push, do not stash, do not modify git history or the index
- Leave all changes in the working tree as uncommitted edits

## Do not commit

You must not run `git commit`, `git add`, `git push`, `git stash`, or any command that mutates git history or the index. Leave all changes in the working tree as uncommitted edits. `draft` will run verification and create the commit. If you commit anyway, the run will be treated as no-op and retried until exhausted.

{{VERIFY_COMMANDS}}

## Spec

{{SPEC}}

{{REVIEW_ISSUES}}

{{VERIFY_ERRORS}}
