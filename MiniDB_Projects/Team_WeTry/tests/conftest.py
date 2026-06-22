"""Make the `minidb` package importable when running pytest from the project
root, and provide a temp-database fixture."""

import os
import sys
import tempfile

import pytest

SRC = os.path.join(os.path.dirname(__file__), "..", "src")
sys.path.insert(0, os.path.abspath(SRC))

from minidb.engine import Engine  # noqa: E402


@pytest.fixture
def tmpdb(tmp_path):
    def _make(mode="mvcc"):
        return Engine(str(tmp_path / mode), mode=mode)
    return _make
