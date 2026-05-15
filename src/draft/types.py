from enum import StrEnum


class BranchSource(StrEnum):
    NEW = "new"
    EXISTING = "existing"


class PrMode(StrEnum):
    OPEN = "open"
    REUSE = "reuse"
    SKIP = "skip"


class WorktreeMode(StrEnum):
    WORKTREE = "worktree"
    NO_WORKTREE = "no-worktree"
    REUSE_EXISTING = "reuse-existing"
