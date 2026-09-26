# SPDX-FileCopyrightText: 2026 Daniel J. Mazure
# SPDX-License-Identifier: MIT
"""REA-P3.9 — the per-family support matrix stays complete and consistent.

SPEC.md holds the authoritative matrix and README.md a summary of it, both
between `rea-support-matrix` markers. This pins the ticket's done-when:
- every family is listed;
- each row has one of the four statuses;
- a row that claims silicon cites its evidence ticket and the rr-rea version
  witnessed;
- Cyclone V and plain PolarFire stay untested until a witness says otherwise;
- README can never tell a reader something SPEC does not.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FAMILIES = [
    "Xilinx 7-series", "Xilinx UltraScale+", "Intel Agilex 5", "Intel Arria 10",
    "Intel Cyclone V", "Microchip PolarFire", "Microchip PolarFire SoC",
]
STATUSES = {"parity", "works-with-gaps", "sim-only", "untested"}
TICKET = re.compile(r"^[A-Z0-9]+-[PT][0-9]\.[0-9]+$")
VERSION = re.compile(r"^[0-9]+\.[0-9]+(\.[0-9]+)?$")
SUMMARY_COLUMNS = ["Family", "Status", "Evidence", "rr-rea witnessed", "Open gaps"]


def _matrix(path: Path) -> list[dict[str, str]]:
    text = path.read_text(encoding="utf-8")
    body = re.search(r"<!-- rea-support-matrix:begin -->\n(.*?)<!-- rea-support-matrix:end -->",
                     text, re.S)
    assert body, f"{path.name}: rea-support-matrix markers missing"
    lines = [ln for ln in body.group(1).splitlines() if ln.startswith("|")]
    header = [c.strip() for c in lines[0].strip("|").split("|")]
    rows = []
    for ln in lines[2:]:
        cells = [c.strip() for c in ln.strip("|").split("|")]
        assert len(cells) == len(header), f"{path.name}: malformed row {ln!r}"
        rows.append(dict(zip(header, cells)))
    return rows


def _ids(cell: str) -> list[str]:
    return [] if cell == "—" else [c.strip() for c in cell.split(",")]


@pytest.fixture(params=["SPEC.md", "README.md"])
def rows(request):
    return _matrix(ROOT / request.param)


def test_every_family_is_listed_once_in_order(rows):
    assert [r["Family"] for r in rows] == FAMILIES


def test_status_is_one_of_the_four(rows):
    for r in rows:
        assert r["Status"] in STATUSES, r


def test_a_silicon_claim_cites_evidence_and_version(rows):
    for r in rows:
        evidence, version = _ids(r["Evidence"]), r["rr-rea witnessed"]
        if r["Status"] in {"parity", "works-with-gaps"}:
            assert evidence and all(TICKET.match(t) for t in evidence), r
            assert VERSION.match(version), r
        else:
            # sim-only / untested: no silicon witness exists to cite
            assert not evidence and version == "—", r
        assert all(TICKET.match(t) for t in _ids(r["Open gaps"])), r


def test_cyclone_v_and_plain_polarfire_are_untested(rows):
    by = {r["Family"]: r["Status"] for r in rows}
    assert by["Intel Cyclone V"] == "untested"
    assert by["Microchip PolarFire"] == "untested"


def test_readme_summary_matches_spec():
    spec = _matrix(ROOT / "SPEC.md")
    readme = _matrix(ROOT / "README.md")
    assert [{k: r[k] for k in SUMMARY_COLUMNS} for r in spec] == readme
