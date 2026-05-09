from draft.steps.worktree_create import WorktreeCreateStep
from draft.steps.code_spec import CodeSpecStep
from draft.steps.push import PushStep
from draft.steps.pr_open import PrOpenStep
from draft.steps.pr_babysit import PrBabysitStep
from draft.steps.delete_worktree import DeleteWorktreeStep

STEPS = [
    WorktreeCreateStep(),
    CodeSpecStep(),
    PushStep(),
    PrOpenStep(),
    PrBabysitStep(),
    DeleteWorktreeStep(),
]
