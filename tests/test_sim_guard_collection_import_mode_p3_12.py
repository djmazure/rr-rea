# SPDX-FileCopyrightText: 2026 Daniel J. Mazure
# SPDX-License-Identifier: MIT
"""REA-P3.12 — the pre-push sim guards collect under a PARENT repo's pytest config.

When rr-rea is checked out as a submodule, it has no pytest config of its own,
so pytest walks up to the parent's. tich-super's pytest.ini sets
`--import-mode=importlib`, which does NOT put a test's directory on sys.path.
A bare sibling import (`from test_rea_qual_elab_guard_p2_7 import ...`) then
fails at collection. That happened on 2026-09-26: the v1.9.0/v1.10.0 tag push
was refused from the submodule while the same commit passed from a standalone
worktree.

This test collects the same guard set the gate discovers (every
sim/cocotb/tests/test_*.py without @cocotb.test), under an explicit config
carrying importlib mode.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GUARD_DIR = ROOT / "sim" / "cocotb" / "tests"


def _gate_guards() -> list[Path]:
    """The pre-push hook's discovery: `grep -L '@cocotb.test' test_*.py`."""
    return sorted(p for p in GUARD_DIR.glob("test_*.py")
                  if "@cocotb.test" not in p.read_text(encoding="utf-8"))


def test_gate_discovers_guards():
    assert _gate_guards(), "no pytest-style sim guards found — discovery broke"


def test_sim_guards_collect_under_importlib_import_mode(tmp_path):
    cfg = tmp_path / "pytest.ini"
    cfg.write_text("[pytest]\naddopts = --import-mode=importlib\n", encoding="utf-8")
    res = subprocess.run(
        [sys.executable, "-m", "pytest", "-c", str(cfg), "--rootdir", str(tmp_path),
         "--collect-only", "-q", "-p", "no:cacheprovider",
         *map(str, _gate_guards())],
        cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert res.returncode == 0, (
        "a sim guard fails collection under --import-mode=importlib, as it "
        "would inside a parent repo's pytest config (REA-P3.12). A sibling "
        "import needs the file's own dir on sys.path first:\n"
        + res.stdout[-3000:] + res.stderr[-2000:])
