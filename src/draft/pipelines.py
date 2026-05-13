from collections.abc import Callable
from dataclasses import dataclass

from draft.steps.babysit_pr import BabysitPrStep
from draft.steps.create_worktree import CreateWorktreeStep
from draft.steps.delete_worktree import DeleteWorktreeStep
from draft.steps.implement_spec import ImplementSpecStep
from draft.steps.open_pr import OpenPrStep
from draft.steps.push_commits import PushCommitsStep


class CorruptStateError(Exception):
    pass


@dataclass(frozen=True)
class Pipeline:
    name: str
    steps: tuple
    expected_steps: Callable[[dict], tuple]


_create_worktree = CreateWorktreeStep()
_implement_spec = ImplementSpecStep()
_push_commits = PushCommitsStep()
_open_pr = OpenPrStep()
_babysit_pr = BabysitPrStep()
_delete_worktree = DeleteWorktreeStep()


def _expected_steps_create(data: dict) -> tuple[str, ...]:
    steps: list[str] = []
    worktree_mode = data.get("worktree_mode", "worktree")
    pr_mode = data.get("pr_mode")
    skip_pr = bool(data.get("skip_pr", False))
    delete_worktree = bool(data.get("delete_worktree", False))
    if worktree_mode not in ("no-worktree", "reuse-existing"):
        steps.append("create-worktree")
    steps.append("implement-spec")
    if not skip_pr:
        steps.append("push-commits")
        if pr_mode != "reuse":
            steps.append("open-pr")
        steps.append("babysit-pr")
    if delete_worktree and worktree_mode in ("worktree", "reuse-existing"):
        steps.append("delete-worktree")
    return tuple(steps)


def _expected_steps_babysit(data: dict) -> tuple[str, ...]:
    steps: list[str] = []
    worktree_mode = data.get("worktree_mode", "worktree")
    delete_worktree = bool(data.get("delete_worktree", False))
    if worktree_mode not in ("no-worktree", "reuse-existing"):
        steps.append("create-worktree")
    steps.append("babysit-pr")
    if delete_worktree and worktree_mode in ("worktree", "reuse-existing"):
        steps.append("delete-worktree")
    return tuple(steps)


PIPELINES: dict[str, "Pipeline"] = {
    "create": Pipeline(
        name="create",
        steps=(
            _create_worktree,
            _implement_spec,
            _push_commits,
            _open_pr,
            _babysit_pr,
            _delete_worktree,
        ),
        expected_steps=_expected_steps_create,
    ),
    "babysit": Pipeline(
        name="babysit",
        steps=(
            _create_worktree,
            _babysit_pr,
            _delete_worktree,
        ),
        expected_steps=_expected_steps_babysit,
    ),
}


def get_pipeline(name: str) -> Pipeline:
    if not name:
        raise CorruptStateError("missing or empty pipeline name")
    try:
        return PIPELINES[name]
    except KeyError as err:
        raise CorruptStateError(f"unknown pipeline: {name!r}") from err
