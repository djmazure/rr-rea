# SPDX-FileCopyrightText: 2026 Daniel J. Mazure
# SPDX-License-Identifier: MIT
"""REA-P2.25 — FDD.md stays present, complete, current and true to the tree.

WHY THIS EXISTS. rr-rea shipped without an FDD, and `rr pkg publish` printed
the advisory missing-fdd finding for it. The FDD is an aggregate view of the
declarative files (requirements.yml, ip.yml, rea_regbank.yml) plus authored
prose, so it rots in two ways the publish advisory does not catch:

  * the machine-owned block goes stale when a declarative file changes and
    nobody runs `rr fdd generate` (currency), or a prose section is left as
    the scaffold placeholder (completeness). `rr fdd check --strict --full`
    sees both, and it is run here through the CLI, the surface a release uses.
  * the authored prose cites files and generic defaults that the tree no longer
    has. Nothing in rr checks the prose, so these two guards do: every cited
    repository path exists, every test file in the tree is named in the
    verification summary, and the generics table equals the rr_rea_top entity.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
FDD = REPO / "FDD.md"
TOP = REPO / "rtl" / "rr_rea_top.vhd"
COCOTB_DIR = REPO / "sim" / "cocotb" / "tests"

# Backticked spans that name a repository file: a known top-level directory
# with a path, or a top-level file. Bare `test_*.py` names are the cocotb table
# and resolve under sim/cocotb/tests/.
_DIRS = ("rtl/", "tests/", "sim/", "constraints/", "verif/", "docs/",
         "targets/", "examples/", ".githooks/", ".github/")
_TOP_FILES = {"SPEC.md", "README.md", "AGENTS.md", "ip.yml", "project.yml",
              "requirements.yml", "rea_regbank.yml", "FDD.md"}


def _fdd_text(prefix: str) -> str:
    assert FDD.is_file(), f"{prefix}: FDD.md absent at {FDD}"
    return FDD.read_text(encoding="utf-8")


def _authored(text: str) -> str:
    """The prose outside the rr:fdd:generated markers."""
    begin = text.index("<!-- rr:fdd:generated:begin -->")
    end = text.index("<!-- rr:fdd:generated:end -->")
    return text[:begin] + text[end:]


def test_fdd_check_strict_full_passes():
    rr = shutil.which("rr")
    assert rr, "rr fdd check failed: rr is not on PATH, the FDD gate cannot run"
    proc = subprocess.run([rr, "fdd", "check", "--strict", "--full"],
                          cwd=REPO, capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, (
        f"rr fdd check failed (rc={proc.returncode}):\n"
        f"{proc.stdout}\n{proc.stderr}")


def test_fdd_cited_paths_exist_and_tests_are_named():
    prose = _authored(_fdd_text("FDD path check"))
    spans = set(re.findall(r"`([^`\s]+)`", prose))

    missing = []
    for span in sorted(spans):
        if span.startswith(_DIRS) or span in _TOP_FILES:
            if not (REPO / span).exists():
                missing.append(span)
        elif re.fullmatch(r"test_\w+\.py", span):
            if not (COCOTB_DIR / span).is_file():
                missing.append(f"sim/cocotb/tests/{span}")
    assert not missing, f"FDD path check: FDD.md cites paths that do not exist: {missing}"

    unnamed = sorted(
        [p.name for p in COCOTB_DIR.glob("test_*.py") if p.name not in spans]
        + [f"tests/{p.name}" for p in (REPO / "tests").glob("test_*.py")
           if f"tests/{p.name}" not in spans])
    assert not unnamed, (
        f"FDD path check: test files missing from the FDD verification summary: {unnamed}")


def _rtl_generics() -> dict[str, tuple[str, str]]:
    src = TOP.read_text(encoding="utf-8")
    entity = re.search(r"entity rr_rea_top is\s+generic \((.*?)\n    \);", src, re.S)
    assert entity, "FDD generics table: cannot find the rr_rea_top generic block"
    out = {}
    for line in entity.group(1).splitlines():
        code = line.split("--", 1)[0]
        m = re.match(r"\s*(G_\w+)\s*:\s*(\w+)\s*:=\s*(.+?);?\s*$", code)
        if m:
            out[m.group(1)] = (m.group(2).lower(), m.group(3).strip().lower())
    return out


def _fdd_generics(prose: str) -> dict[str, tuple[str, str]]:
    section = prose.split("## Configuration generics", 1)
    assert len(section) == 2, "FDD generics table: no '## Configuration generics' section"
    body = section[1].split("\n## ", 1)[0]
    out = {}
    for m in re.finditer(r"^\| `(G_\w+)` \| (\w+) \| ([^|]+?) \|", body, re.M):
        out[m.group(1)] = (m.group(2).lower(), m.group(3).strip().lower())
    return out


def test_fdd_generics_table_matches_rtl():
    prose = _authored(_fdd_text("FDD generics table"))
    rtl = _rtl_generics()
    fdd = _fdd_generics(prose)
    assert rtl, "FDD generics table: parsed no generics from rtl/rr_rea_top.vhd"
    assert fdd == rtl, (
        "FDD generics table differs from the rr_rea_top entity:\n"
        f"  only in RTL or different: {sorted(set(rtl.items()) - set(fdd.items()))}\n"
        f"  only in FDD or different: {sorted(set(fdd.items()) - set(rtl.items()))}")
