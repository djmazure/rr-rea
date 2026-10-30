# SPDX-FileCopyrightText: 2026 Daniel J. Mazure
# SPDX-License-Identifier: MIT
"""REA-P3.17 — the register-map artefacts rr-rea owns stay generated from the map.

`rea_regbank.yml` declares two generated artefacts. `vhdl_template`
(rtl/rr_rea_pkg.vhd, between the REGBANK_ADDRESSES markers) lives in this
repository. `python_host` (sdk/cli/rea/registers.py) lives in the RouteRTL SDK,
which is the REA host client's home; this repository ships no host Python, so
that path can never exist here. A plain `rr regbank diff` therefore reported it
"missing" for good once the map validated (REA-T2.7), and a permanent red is
not a gate. The check here is scoped to the artefact this repository owns:
`rr regbank diff --only vhdl_template`. The SDK's copy is kept in sync on the
SDK side, where the file is.

* the committed package matches a fresh generation from the map;
* mutant: an address constant edited by hand in a copy is reported as drift;
* mutant: a register added to the map in a copy, package not regenerated, is
  reported as drift.
Each mutant is judged by the same harness as the real check, so a harness that
went blind fails them.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
REGBANK = REPO / "rea_regbank.yml"
PKG = REPO / "rtl" / "rr_rea_pkg.vhd"


def _diff(root: Path) -> subprocess.CompletedProcess:
    rr = shutil.which("rr")
    assert rr, "rr regbank diff failed: rr is not on PATH, the regbank gate cannot run"
    return subprocess.run(
        [rr, "regbank", "diff", "rea_regbank.yml", "--only", "vhdl_template"],
        cwd=root, capture_output=True, text=True, timeout=300)


def _drift_lines(stdout: str) -> str:
    """The per-artefact finding lines, without the always-printed scope line."""
    return "\n".join(line for line in stdout.splitlines()
                     if "drift" in line or "missing" in line)


def _scratch(tmp_path: Path, regbank: str, pkg: str) -> Path:
    (tmp_path / "rtl").mkdir()
    (tmp_path / "rea_regbank.yml").write_text(regbank, encoding="utf-8")
    (tmp_path / "rtl" / "rr_rea_pkg.vhd").write_text(pkg, encoding="utf-8")
    return tmp_path


def test_the_committed_package_matches_a_fresh_generation_from_the_map():
    proc = _diff(REPO)
    assert proc.returncode == 0, (
        f"rr regbank diff --only vhdl_template reports drift (rc={proc.returncode}):\n"
        f"{proc.stdout}\n{proc.stderr}")
    assert "in sync" in proc.stdout


def test_control_an_unmodified_copy_is_in_sync_from_a_scratch_directory(tmp_path):
    root = _scratch(tmp_path, REGBANK.read_text(encoding="utf-8"),
                    PKG.read_text(encoding="utf-8"))
    proc = _diff(root)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_mutant_a_hand_edited_address_constant_is_reported_as_drift(tmp_path):
    pkg = PKG.read_text(encoding="utf-8")
    old = 'C_REGBANK_ADDR_CTRL               : unsigned(15 downto 0) := x"0004";'
    assert old in pkg, "the CTRL constant is no longer in the form this mutant edits"
    root = _scratch(tmp_path, REGBANK.read_text(encoding="utf-8"),
                    pkg.replace(old, old.replace('x"0004"', 'x"0005"')))
    proc = _diff(root)
    assert proc.returncode != 0, "a hand-edited address constant passed the diff"
    assert "out of sync" in proc.stdout and "vhdl_template" in _drift_lines(proc.stdout)


def test_mutant_a_register_added_to_the_map_without_regenerating_is_reported(tmp_path):
    regbank = REGBANK.read_text(encoding="utf-8")
    anchor = "registers:\n"
    assert regbank.count(anchor) == 1
    mutated = regbank.replace(
        anchor, anchor + "  - {name: MUTANT_REG, address: 0x03F0, access: RW, "
                         "description: Added by the mutant}\n")
    root = _scratch(tmp_path, mutated, PKG.read_text(encoding="utf-8"))
    proc = _diff(root)
    assert proc.returncode != 0, "a register missing from the package passed the diff"
    assert "out of sync" in proc.stdout and "vhdl_template" in _drift_lines(proc.stdout)
