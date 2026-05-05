Generate a GitHub pull request description based on the spec and git diff below.

Output ONLY the markdown body — no preamble, no code fences, no explanation. Two sections:

## Summary

A concise description of what was changed and why.

## Test plan

Relevant checkboxes (`- [ ]`) for testing these specific changes.

---

Spec:
{{SPEC}}

---

Diff:
{{DIFF}}
