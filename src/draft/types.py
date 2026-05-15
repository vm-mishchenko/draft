from enum import StrEnum


class BranchSource(StrEnum):
    NEW = "new"  # working branch will be created from the base
    EXISTING = "existing"  # working branch already exists locally and is reused


class PrMode(StrEnum):
    OPEN = "open"  # open a new PR for this run
    REUSE = "reuse"  # reuse an existing open PR for the working branch
    SKIP = "skip"  # do not push commits or interact with PRs (--skip-pr)


class WorktreeMode(StrEnum):
    WORKTREE = "worktree"  # create a new worktree at the canonical path
    NO_WORKTREE = "no-worktree"  # no worktree; check out the branch in the main repo
    REUSE_EXISTING = (
        "reuse-existing"  # reuse an existing worktree at the canonical path
    )
