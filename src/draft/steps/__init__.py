from draft.steps.babysit_pr import BabysitPrStep
from draft.steps.create_worktree import CreateWorktreeStep
from draft.steps.delete_worktree import DeleteWorktreeStep
from draft.steps.implement_spec import ImplementSpecStep
from draft.steps.open_pr import OpenPrStep
from draft.steps.push_commits import PushCommitsStep
from draft.steps.review_implementation import ReviewImplementationStep

# Deprecated: use draft.pipelines.PIPELINES["create"].steps instead.
STEPS = [
    CreateWorktreeStep(),
    ImplementSpecStep(),
    ReviewImplementationStep(),
    PushCommitsStep(),
    OpenPrStep(),
    BabysitPrStep(),
    DeleteWorktreeStep(),
]
