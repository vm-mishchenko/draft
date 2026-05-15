#!/usr/bin/env bash
# Invokes auggie to review the working-tree diff against a spec.
# Usage: review.sh <model>
# This script is model-agnostic; the caller picks the model.
# Recommended: pass an OpenAI model (e.g. gpt-4.1) to avoid Auggie's Anthropic default.
# Verdict contract: stdout empty → approval; stdout non-empty → rejection; rc != 0 → infra failure.
# Required env vars: DRAFT_REPO_DIR, DRAFT_BRANCH, DRAFT_BASE_BRANCH, DRAFT_SPEC_FILE.
# Authentication: run 'auggie login' once or export AUGMENT_SESSION_AUTH before invoking.
# Full contract: features/260423-draft-cli/scopes/260514-scope-review-model-arg.md

set -euo pipefail

# Maximum number of review items to report
MAX_REVIEW_ITEMS=3

die() { echo "review.sh: $*" >&2; exit 1; }

MODEL="${1:-}"
[[ -n "$MODEL" ]] || die "usage: review.sh <model>"

for v in DRAFT_REPO_DIR DRAFT_BRANCH DRAFT_BASE_BRANCH DRAFT_SPEC_FILE; do
  [[ -n "${!v:-}" ]] || die "$v is unset or empty"
done
[[ -r "$DRAFT_SPEC_FILE" ]] || die "DRAFT_SPEC_FILE not readable: $DRAFT_SPEC_FILE"

command -v auggie >/dev/null || die "auggie not on PATH"

if ! auth_out="$(auggie token print 2>&1)"; then
  echo "review.sh: auggie pre-flight auth check failed; run 'auggie login' or export AUGMENT_SESSION_AUTH" >&2
  echo "--- auggie token print output ---" >&2
  echo "$auth_out" >&2
  exit 1
fi
[[ "$auth_out" == *"SESSION="* ]] || die "auggie token print did not return a SESSION; run 'auggie login'"

if ! diff_text="$(git diff "$DRAFT_BASE_BRANCH"...HEAD 2>&1)"; then
  echo "review.sh: git diff $DRAFT_BASE_BRANCH...HEAD failed:" >&2
  echo "$diff_text" >&2
  exit 1
fi
[[ -n "$diff_text" ]] || die "empty diff (HEAD == merge-base of $DRAFT_BASE_BRANCH and HEAD); pipeline invariant violated"

spec_text="$(<"$DRAFT_SPEC_FILE")"
[[ -n "$spec_text" ]] || die "DRAFT_SPEC_FILE is empty: $DRAFT_SPEC_FILE"

prompt_file="$(mktemp)"
trap 'rm -f "$prompt_file"' EXIT

cat > "$prompt_file" <<DRAFT_REVIEW_PROMPT_END
You are a code reviewer for a draft-managed change about to be committed.

Branch: ${DRAFT_BRANCH}
Base branch: ${DRAFT_BASE_BRANCH}

## Spec

${spec_text}

## Diff

${diff_text}

## Review rules

- The spec is the source of truth.
- Flag errors or bugs related to the changed files (anything wrong inside the diff).
- Flag mismatches between the diff and the spec (the spec asks for X, the diff does Y).
- Do NOT flag nice-to-have features. If the spec doesn't ask for it, don't suggest adding it.
- Do NOT flag refactoring opportunities. If the existing code is not buggy, leave it alone, even if it could be cleaner.
- Do NOT flag stylistic preferences without a correctness impact.
- Report at most ${MAX_REVIEW_ITEMS} items. If more real issues exist, pick the most severe (severity = potential to cause incorrect runtime behaviour or spec violation) and drop the rest.

## Output

There are exactly two valid outputs. Choose one. Never both. Never something else.

(a) If no issues meet the criteria above, print EXACTLY this line and nothing else:

NO_ISSUES

(b) Otherwise, the FIRST line of your output MUST be exactly:

FOUND_ISSUES

followed by a blank line, then up to ${MAX_REVIEW_ITEMS} items in the format below. No preamble before FOUND_ISSUES, no NO_ISSUES sentinel mixed in. Items separated by a blank line.

## <name (1-4 words)>

**Summary**: <one to three sentences>

**Details**:
<free-form, as long as needed: file/line references, code excerpts, spec excerpts, failure scenarios>

**Proposed fix**:
<free-form, as long as needed: concrete change recommendation>

Example of a single well-formed item:

## Missing null check

**Summary**: \`parse_config\` dereferences the result of \`os.environ.get("DRAFT_SPEC_FILE")\` without checking for None, which crashes the step when the env var is unset.

**Details**:
At \`src/draft/steps/review_implementation/__init__.py:42\`, the diff introduces:

    spec_path = os.environ.get("DRAFT_SPEC_FILE")
    with open(spec_path) as f:
        ...

The spec says \`DRAFT_SPEC_FILE\` is always set, but \`open(None)\` raises \`TypeError\`, which surfaces as an opaque crash.

**Proposed fix**:
Guard the lookup:

    spec_path = os.environ.get("DRAFT_SPEC_FILE")
    if not spec_path:
        raise StepError("review-implementation", "DRAFT_SPEC_FILE is required")
    with open(spec_path) as f:
        ...
DRAFT_REVIEW_PROMPT_END

if ! auggie_stdout="$(auggie --print --quiet --ask \
    --workspace-root "$DRAFT_REPO_DIR" \
    --allow-indexing \
    --max-turns 10 \
    --model "$MODEL" \
    --instruction-file "$prompt_file")"; then
  rc=$?
  echo "review.sh: auggie review call failed with rc=$rc" >&2
  exit "$rc"
fi

# Rejection = FOUND_ISSUES sentinel present anywhere in stdout.
# Anything else (NO_ISSUES, empty output, stray metadata, etc.) is approval.
if [[ "$auggie_stdout" == *"FOUND_ISSUES"* ]]; then
  printf '%s' "$auggie_stdout"
fi
exit 0
