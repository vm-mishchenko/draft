You are writing a GitHub Pull Request title and description for the current branch. Do not run `gh`, do not push, do not edit any files.

Steps:
1. Read the PR-body template at: {{PR_BODY_TEMPLATE_PATH}}
2. Gather change context:
   - `git diff {{BASE_BRANCH}}..HEAD`
   - `git log {{BASE_BRANCH}}..HEAD --format="%s%n%n%b"`
3. Write a concise title (one line) and a body that follows the PR-body template structure.

Output contract. Your stdout is parsed by `pr-open`. Output exactly two fenced blocks, in this order, with the opening and closing markers on their own lines:

<<<PR-TITLE>>>
your one-line title here
<<</PR-TITLE>>>
<<<PR-BODY>>>
your multi-line body here,
following the template
<<</PR-BODY>>>

Do not run `gh pr create`, `git push`, or any command that edits the working tree. Do not print anything other than the two blocks above.
