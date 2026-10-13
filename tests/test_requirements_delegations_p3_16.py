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


def test_contract_report_renders_delegated_obligations():
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

    con = Console(record=True, width=200)
    _render_req_drilldown(con, report.req("REA-REQ-910"))
    text_910 = con.export_text()
    assert "Delegated to REA-P3.4: two live masters on one register bus stay forbidden until the dual-door arbiter" in text_910

    con = Console(record=True, width=200)
    _render_req_drilldown(con, report.req("REA-REQ-916"))
    text_916 = con.export_text()
    assert "Delegated to REA-P3.4: register-map semantic addition deferred with the dual-door arbiter" in text_916
