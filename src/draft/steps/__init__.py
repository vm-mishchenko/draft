from draft.steps.worktree_create import WorktreeCreateStep
from draft.steps.code_spec import CodeSpecStep
from draft.steps.push import PushStep
from draft.steps.pr_open import PrOpenStep
from draft.steps.pr_view import PrViewStep
from draft.steps.pr_babysit import PrBabysitStep

STEPS = [
    WorktreeCreateStep(),
    CodeSpecStep(),
    PushStep(),
    PrOpenStep(),
    PrViewStep(),
    PrBabysitStep(),
]
