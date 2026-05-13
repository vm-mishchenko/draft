import shutil
import subprocess

import pytest

DRAFT = shutil.which("draft")
SUBCOMMANDS = ["list", "status", "create", "babysit", "continue", "delete", "prune"]

pytestmark = pytest.mark.skipif(
    DRAFT is None, reason="draft binary not on PATH; run `make setup`"
)


def _run(*args):
    return subprocess.run([DRAFT, *args], capture_output=True, text=True, timeout=10)


def test_root_help_exits_zero():
    r = _run("--help")
    assert r.returncode == 0
    assert r.stdout.strip()
    assert "draft" in r.stdout.lower()


@pytest.mark.parametrize("sub", SUBCOMMANDS)
def test_subcommand_help_exits_zero(sub):
    r = _run(sub, "--help")
    assert r.returncode == 0
    assert r.stdout.strip()
