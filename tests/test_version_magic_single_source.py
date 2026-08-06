# SPDX-FileCopyrightText: 2026 Daniel J. Mazure
# SPDX-License-Identifier: MIT
"""REA-T2.3 — the VERSION feature-tier magic must not drift across surfaces.

WHY THIS EXISTS. ``C_REA_VERSION`` was bumped 0x52454107 → 0x52454109 when the
v0.8 trust tier and PRETRIG_VALID landed (RTL-P2.1106 / RTL-T1.16). Three
cocotb tests, one entity header and one requirement kept the v0.7 literal. The
suite went red and stayed red for weeks, because nothing ran it — the enforcement
gap REA-T2.3 was filed for. Running the suite is the primary fix; this guard is
the cheap structural backstop, and it runs with no simulator, no cocotb and no
SDK, so CI can execute it in a second.

WHAT IT DOES NOT DO. It does not tell a cocotb test what to expect — that would
make the behavioural tests tautological (AGENTS.md / ROUTERTL-002: hard-coded
expected values only, never derived from the DUT). The cocotb tests still
hard-code the magic and independently prove the DUT returns it. This guard
separately says "your independently hard-coded constants disagree with each
other, so one of you is stale" — a consistency check between peers, not an
oracle.

Non-VERSION uses of the 0x524541xx space (the self-test LFSR seed) and
deliberately HISTORICAL mentions (on-silicon magics quoted in closed-ticket
provenance, which must never be rewritten) are exempted BY NAME below, with a
reason each. A new unexplained literal is a failure, which is the point: the
exemption list is the record of every place this constant is allowed to differ.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
PKG_VHD = REPO / "rtl" / "rr_rea_pkg.vhd"

# 0x524541xx in either VHDL (x"524541xx") or Python/C (0x524541xx) spelling.
_MAGIC = re.compile(r'(?:0[xX]|[xX]")(524541[0-9A-Fa-f]{2})')

_SEARCH_SUFFIXES = {".vhd", ".vhdl", ".py", ".yml", ".yaml", ".md"}

# SCOPE: the surfaces that either STATE the contract or TEST it. Process docs
# (AGENTS.md, README, CI workflows) legitimately NARRATE the bump — "0x52454107
# -> 0x52454109" is the story of the defect, not a stale expectation — and
# scanning them would train people to silence the guard rather than read it.
# Everything below is normative: the RTL, the requirements, the frozen SW
# interface contract, the FDD, and the tests that assert against them.
_SCAN_ROOTS = ("rtl", "sim", "tests", "docs")
_SCAN_FILES = ("requirements.yml", "SPEC.md", "ip.yml", "rea_regbank.yml")

_SKIP_DIRS = {".git", "work", "artifacts", "libs", "generated", "__pycache__",
              "verif", "sim/cocotb/logs", "sim/cocotb/waves", ".routertl_cache",
              ".github"}

# Escape hatch for narration INSIDE a normative file (e.g. a requirement whose
# rationale cites the superseded value). Explicit, greppable, and it keeps the
# reason next to the text instead of in a table at the top of this file.
_INLINE_OK = "rea-magic:historical"

# (path suffix, literal) → why this occurrence is allowed to differ.
_EXEMPT: dict[tuple[str, str], str] = {
    ("rtl/rr_rea_pkg.vhd", "52454108"):
        "C_SELFTEST_SEED_DEFAULT — an LFSR seed, not a version (REQ-851).",
    ("rtl/rr_rea_fill_fsm.vhd", "52454108"):
        "REQ-851 doc comment for the substituted default seed.",
    ("requirements.yml", "52454108"):
        "REQ-851 prose for the substituted default seed.",
    ("sim/cocotb/tests/test_rea_fill_sweep_conflict_t1_2.py", "52454108"):
        "DEFAULT_SEED mirror of C_SELFTEST_SEED_DEFAULT.",
    ("sim/cocotb/tests/test_rea_selftest_fill_p2_3.py", "52454108"):
        "DEFAULT_SEED mirror of C_SELFTEST_SEED_DEFAULT.",
    ("rtl/rr_rea_pkg.vhd", "52454105"):
        "HISTORICAL on-silicon v0.5 magic cited for provenance "
        "(RTL-P3.1198 / T2.119) — deliberately never rewritten.",
    ("sim/cocotb/tests/test_rea_field_bit0_parity.py", "52454105"):
        "HISTORICAL: the field bench's original v0.5 read, quoted in the "
        "module docstring as the defect's provenance.",
    ("SPEC.md", "52454108"):
        "SELFTEST_SEED default in the register table — a seed, not a version.",
    ("docs/FDD_REA_TRUST_TIER_V08.md", "52454108"):
        "SELFTEST_SEED default in the FDD register table — a seed.",
}

# This guard's own prose necessarily NAMES the stale value it was written to
# catch, so it is excluded from its own scan. That is safe by construction: the
# current tier is PARSED from rr_rea_pkg.vhd (never typed here), so this file
# holds no expectation that could itself go stale — every 0x524541xx in it is
# narrative. Keep it that way.
_SELF = "tests/test_version_magic_single_source.py"


def _current_version() -> str:
    text = PKG_VHD.read_text(encoding="utf-8", errors="replace")
    match = re.search(
        r'constant\s+C_REA_VERSION\s*:.*?:=\s*x"([0-9A-Fa-f]{8})"',
        text, re.IGNORECASE,
    )
    assert match, (
        f"C_REA_VERSION not found in {PKG_VHD} — the single source of truth "
        "for the feature tier moved or was renamed; fix this guard with it."
    )
    return match.group(1).lower()


def _candidate_files() -> list[Path]:
    out = []
    for path in REPO.rglob("*"):
        if path.suffix.lower() not in _SEARCH_SUFFIXES or not path.is_file():
            continue
        rel = path.relative_to(REPO).as_posix()
        if any(rel == d or rel.startswith(f"{d}/") for d in _SKIP_DIRS):
            continue
        in_scope = rel in _SCAN_FILES or any(
            rel.startswith(f"{root}/") for root in _SCAN_ROOTS
        )
        if not in_scope:
            continue
        out.append(path)
    return out


def test_c_rea_version_is_parseable_and_odd():
    """The tier byte is ODD by permanent contract (REA-REQ-806)."""
    version = _current_version()
    assert int(version, 16) & 1, (
        f"C_REA_VERSION = 0x{version} has an EVEN tier byte. The one-read "
        "odd-VERSION acceptance probe (RTL-P1.96) keys on bit0=1 — an even "
        "tier makes a wide-readback miscompile undetectable."
    )


def test_no_surface_carries_a_stale_version_magic():
    """Every 0x524541xx literal is the current tier, or exempt by name."""
    current = _current_version()
    stale: list[str] = []

    for path in _candidate_files():
        rel = path.relative_to(REPO).as_posix()
        text = path.read_text(encoding="utf-8", errors="replace")
        for lineno, line in enumerate(text.splitlines(), start=1):
            for found in _MAGIC.findall(line):
                found = found.lower()
                if found == current:
                    continue
                if (rel, found) in _EXEMPT:
                    continue
                if _INLINE_OK in line:
                    continue
                if rel == _SELF:
                    continue
                stale.append(
                    f"  {rel}:{lineno}  0x{found}  (current is 0x{current})\n"
                    f"      {line.strip()[:110]}"
                )

    assert not stale, (
        "Stale REA VERSION magic — C_REA_VERSION is 0x"
        + current
        + " but these surfaces disagree:\n"
        + "\n".join(stale)
        + "\n\nEither update them, or add an entry to _EXEMPT in this file "
          "stating WHY that occurrence is allowed to differ (a historical "
          "on-silicon magic, or a non-version use of the 0x524541xx space)."
    )


@pytest.mark.parametrize("rel_path", [
    "sim/cocotb/tests/test_rea_regbank.py",
    "sim/cocotb/tests/test_rea_field_bit0_parity.py",
    "sim/cocotb/tests/test_rea_build_id_p3_1203.py",
])
def test_the_three_tests_that_actually_went_stale_still_carry_the_magic(rel_path):
    """Anti-vacuity: the guard above passes trivially if the literals vanish.

    These three files are the ones REA-T2.3 caught holding 0x52454107. If a
    future edit deletes the hard-coded expectation instead of updating it (or
    derives it from the RTL), the consistency check would go green while the
    behavioural assertion it protects has quietly stopped existing.
    """
    text = (REPO / rel_path).read_text(encoding="utf-8", errors="replace")
    assert _MAGIC.search(text), (
        f"{rel_path} no longer hard-codes a 0x524541xx VERSION expectation. "
        "If that was deliberate, say so here; if the value is now derived "
        "from the DUT's own source, that is a tautological test (ROUTERTL-002)."
    )
