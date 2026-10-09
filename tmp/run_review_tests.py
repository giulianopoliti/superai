"""Local validation runner: inherit workspace ACL for disposable test folders."""
import os
import sys
import tempfile
from pathlib import Path

import pytest

base = Path("tmp/validation-20260927").resolve()
base.mkdir(exist_ok=True)
original_mkdir = os.mkdir


def mkdir(path, mode=0o777, *, dir_fd=None):
    if Path(path).resolve().is_relative_to(base):
        mode = 0o777
    return original_mkdir(path, mode, dir_fd=dir_fd)


os.mkdir = mkdir
tempfile.tempdir = str(base)
raise SystemExit(pytest.main([
    *sys.argv[1:], "-q", "--tb=short", "--basetemp=" + str(base / "pytest"),
]))
