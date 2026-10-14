# SPDX-FileCopyrightText: 2026 Daniel J. Mazure
# SPDX-License-Identifier: MIT
"""REA-P3.16 — Migrate rr-rea prose delegations to REQ delegated_to (RTL-P2.1691).

RTL-P2.1691 introduced the optional ``delegated_to: [{ref, obligation}]`` field
on functional requirements so that deferred obligations can be tracked in a
structured format by tools such as the TIC-P2.246 close gate.

This module tests that:
  * REA-REQ-910 contains a structured delegation to REA-P3.4 with its obligation,
    while preserving its original prose;
  * REA-REQ-916 contains a structured delegation to REA-P3.4 with its obligation,
    while preserving its original prose;
  * ``rr contract report`` (both data model and rendered views) reports both
    delegations in its output surfaces.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from rich.console import Console

from sdk.cli.commands.contract import _render_report, _render_req_drilldown
from sdk.cli.requirements import load_requirements
from sdk.engine.contract_report import build_contract_report, report_to_dict

REPO = Path(__file__).resolve().parents[1]
REQS_PATH = REPO / "requirements.yml"


def _get_req(req_id: str):
    doc = load_requirements(REQS_PATH)
    for r in doc.functional_contract:
        if r.id == req_id:
            return r
    pytest.fail(f"Requirement {req_id} not found in {REQS_PATH}")


def test_rea_req_910_delegated_to_p3_4():
    req = _get_req("REA-REQ-910")
    pairs = [(d.ref, d.obligation) for d in getattr(req, "delegated_to", ())]
    assert ("REA-P3.4", "two live masters on one register bus stay forbidden until the dual-door arbiter") in pairs
    assert "two live masters on one register bus remain forbidden" in req.desc
    assert "until the dual-door arbiter (REA-P3.4)" in req.desc


def test_rea_req_916_delegated_to_p3_4():
    req = _get_req("REA-REQ-916")
    pairs = [(d.ref, d.obligation) for d in getattr(req, "delegated_to", ())]
    assert ("REA-P3.4", "register-map semantic addition deferred with the dual-door arbiter") in pairs
    assert "The dump engine and the register-map DATA_BASE decode share" in req.desc
    assert "deferred with the dual-door arbiter (REA-P3.4)" in req.desc


def _normalize_panel_text(rendered: str) -> str:
    """Strip Rich panel box borders and collapse line-wrap whitespace (REA-P1.2).

    Why REA-P3.16 passed pre-push at its own land (d-eb85b204) and failed at
    REA-P2.23's land review (d-43acabc3): environment drift across agent
    harnesses interacting with ``rich.console.Console.size`` (not routertl
    version drift). In Rich, ``Console.size`` only short-circuits on
    ``self._width is not None and self._height is not None``; when ``height``
    is omitted, ``if self.is_dumb_terminal: return ConsoleDimensions(80, 25)``
    runs before checking ``COLUMNS`` or ``self._width``. ``is_dumb_terminal``
    is ``self.is_terminal and TERM.lower() in ("dumb", "unknown")``, and
    ``is_terminal`` treats any non-empty ``FORCE_COLOR`` (including
    ``FORCE_COLOR="0"``) as ``True``.

    At REA-P3.16's own author/land runs (agy + claude-sonnet-5-5),
    ``FORCE_COLOR`` was unset and pytest's captured stdout had
    ``isatty() == False``, so ``is_dumb_terminal`` was ``False`` and
    ``Console(record=True, width=200).size`` was ``(200, 25)``. Under
    ``cursor-agent`` (d-43acabc3, which exports ``TERM=dumb`` and
    ``FORCE_COLOR=0`` into tool subprocesses) or ``pytest -s`` on a dumb PTY,
    ``is_dumb_terminal`` is ``True`` and ``Console(record=True, width=200)``
    clamps to 80 columns regardless of ``width=200`` or ``COLUMNS``, wrapping
    the 102-char REA-REQ-910 and 89-char REA-REQ-916 delegation lines across
    two ``│ ... │`` panel rows.
    """
    lines = [line.strip().strip("│").strip() for line in rendered.splitlines()]
    return " ".join(part for part in lines if part)


def test_contract_report_renders_delegated_obligations(monkeypatch):
    report = build_contract_report(REPO)
    d = report_to_dict(report)
    reqs = {r["id"]: r for r in d["requirements"]}

    assert reqs["REA-REQ-910"]["delegated_to"] == [
        {"ref": "REA-P3.4", "obligation": "two live masters on one register bus stay forbidden until the dual-door arbiter"}
    ]
    assert reqs["REA-REQ-916"]["delegated_to"] == [
        {"ref": "REA-P3.4", "obligation": "register-map semantic addition deferred with the dual-door arbiter"}
    ]

    con = Console(record=True, width=200)
    _render_report(con, report, full=False)
    text = con.export_text()
    assert "Delegated obligations" in text
    assert "REA-REQ-910" in text
    assert "REA-REQ-916" in text
    assert "REA-P3.4" in text

    # Pin the cursor-agent dumb-terminal env (TERM=dumb + FORCE_COLOR=0) where
    # Rich's Console(record=True, width=200) clamps to 80 columns and wraps the
    # drilldown panel across two lines (REA-P1.2).
    monkeypatch.delenv("TTY_COMPATIBLE", raising=False)
    monkeypatch.setenv("TERM", "dumb")
    monkeypatch.setenv("FORCE_COLOR", "0")

    con = Console(record=True, width=200)
    assert con.size.width == 80
    _render_req_drilldown(con, report.req("REA-REQ-910"))
    text_910 = _normalize_panel_text(con.export_text())
    assert "Delegated to REA-P3.4: two live masters on one register bus stay forbidden until the dual-door arbiter" in text_910

    con = Console(record=True, width=200)
    assert con.size.width == 80
    _render_req_drilldown(con, report.req("REA-REQ-916"))
    text_916 = _normalize_panel_text(con.export_text())
    assert "Delegated to REA-P3.4: register-map semantic addition deferred with the dual-door arbiter" in text_916
