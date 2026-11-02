# SPDX-FileCopyrightText: 2026 Daniel J. Mazure
# SPDX-License-Identifier: MIT
"""REA-T2.7 — rea_regbank.yml stays a register map `rr regbank validate` accepts.

WHY THIS EXISTS. The SEQ_BASE line carried an unquoted description with commas
inside a flow mapping, so YAML split it into bogus register keys. The regbank
was invalid from the commit that added that line, and nothing in the gate
asked the validator. The first sign was the FDD check: a fresh render of the
register section degraded to "unreadable" and the generated block drifted.
An invalid regbank is now a failure of its own, named as such.

* the real register map validates;
* control: an unmodified copy validates from a scratch directory, so the
  mutant below is judged by the same harness;
* mutant: the same file with the SEQ_BASE description unquoted again is
  rejected, naming SEQ_BASE. If this stops failing, the validator or the
  harness went blind and the first test above proves nothing.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
REGBANK = REPO / "rea_regbank.yml"
QUOTED = 'description: "Sequencer window base;'


def _validate(path: Path) -> subprocess.CompletedProcess:
    rr = shutil.which("rr")
    assert rr, "rr regbank validate failed: rr is not on PATH, the regbank gate cannot run"
    return subprocess.run([rr, "regbank", "validate", str(path)], cwd=path.parent,
                          capture_output=True, text=True, timeout=300)


def _scratch_copy(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "rea_regbank.yml"
    path.write_text(text, encoding="utf-8")
    return path


def test_the_register_map_validates():
    proc = _validate(REGBANK)
    assert proc.returncode == 0, (
        f"rr regbank validate rejects rea_regbank.yml (rc={proc.returncode}):\n"
        f"{proc.stdout}\n{proc.stderr}")
    assert "Schema is valid" in proc.stdout


def test_control_an_unmodified_copy_validates_from_a_scratch_directory(tmp_path):
    proc = _validate(_scratch_copy(tmp_path, REGBANK.read_text(encoding="utf-8")))
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_mutant_an_unquoted_seq_base_description_is_rejected(tmp_path):
    text = REGBANK.read_text(encoding="utf-8")
    assert QUOTED in text, "the SEQ_BASE description is no longer the quoted form this mutant undoes"
    line_start = text.index(QUOTED)
    line_end = text.index("}", line_start)
    mutated = (text[:line_start]
               + text[line_start:line_end].replace('"', "") + text[line_end:])
    proc = _validate(_scratch_copy(tmp_path, mutated))
    assert proc.returncode != 0, "an unquoted flow-mapping description passed validation"
    assert "SEQ_BASE: unknown register key" in proc.stdout + proc.stderr
