class DraftError(Exception):
    exit_code = 1


class UserInputError(DraftError):
    exit_code = 2


class PreflightError(DraftError):
    exit_code = 3


class StepFailedError(DraftError):
    exit_code = 1
