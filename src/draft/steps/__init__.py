from draft.steps.create_worktree import CreateWorktreeStep
from draft.steps.implement_spec import ImplementSpecStep
from draft.steps.push_commits import PushCommitsStep
from draft.steps.open_pr import OpenPrStep
from draft.steps.babysit_pr import BabysitPrStep
from draft.steps.delete_worktree import DeleteWorktreeStep

STEPS = [
    CreateWorktreeStep(),
    ImplementSpecStep(),
    PushCommitsStep(),
    OpenPrStep(),
    BabysitPrStep(),
    DeleteWorktreeStep(),
]
