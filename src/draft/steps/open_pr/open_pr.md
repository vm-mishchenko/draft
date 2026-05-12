You are writing a GitHub Pull Request title and description. Do not run any shell commands. Do not read any files. Use only the context provided below.

**Spec**

{{SPEC}}

**PR body template**

{{PR_BODY_TEMPLATE}}

**Git diff**

```
{{GIT_DIFF}}
```

**Git log**

```
{{GIT_LOG}}
```

Write a concise title (one line) and a body that follows the PR body template structure above.

Output contract. Your stdout is parsed by `open-pr`. Output exactly two fenced blocks, in this order, with the opening and closing markers on their own lines:

<<<PR-TITLE>>>
your one-line title here
<<</PR-TITLE>>>
<<<PR-BODY>>>
your multi-line body here,
following the template
<<</PR-BODY>>>

Do not run `gh pr create`, `git push`, or any command that edits the working tree. Do not print anything other than the two blocks above.
