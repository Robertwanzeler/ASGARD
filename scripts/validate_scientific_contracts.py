#!/usr/bin/env python3
"""Validate versioned campaign contracts without executing a campaign."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def validate_manifest(path: Path) -> list[str]:
    problems: list[str] = []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"{path}: invalid JSON: {exc}"]
    if not isinstance(payload, dict):
        return [f"{path}: root must be an object"]
    schema = str(payload.get("schema", ""))
    if schema == "greenran.autonomous_vehicle_feasibility.v2":
        if payload.get("manifest_version") != 2:
            problems.append(f"{path}: missing manifest_version=2")
        provenance = payload.get("provenance")
        if not isinstance(provenance, dict):
            problems.append(f"{path}: missing provenance")
        contract = (provenance or {}).get("metric_contract", {})
        if contract.get("pdcp_source") != "native_pdcp_trace_unique_sim_epochs":
            problems.append(f"{path}: non-native PDCP source")
        if contract.get("collector_mode") != "pdcp_real":
            problems.append(f"{path}: collector_mode is not pdcp_real")
        if contract.get("proxy_allowed") is not False:
            problems.append(f"{path}: proxy rows are allowed")
        if payload.get("status") == "passed" and payload.get("selected_interval_us") is None:
            problems.append(f"{path}: passed campaign has no selected interval")
        if payload.get("scientific_decision") not in {
            "approved", "baseline_infeasible", "blocked", "rejected"
        }:
            problems.append(f"{path}: missing explicit scientific_decision")
    elif schema == "greenran.autonomous_vehicle_feasibility.v3":
        if payload.get("metric_contract") != "per_pdu_cohort_v1":
            problems.append(f"{path}: v3 campaign lacks per-PDU contract")
        if payload.get("scientific_decision") not in {
            "approved", "baseline_infeasible", "blocked", "rejected"
        }:
            problems.append(f"{path}: v3 campaign lacks explicit scientific_decision")
        if str(payload.get("profile", "")) == "tasam_training_balanced_v6_v2x_gbr_deadline_mc_fallback":
            if payload.get("link_metric_contract") != "vehicle_link_state_v2":
                problems.append(f"{path}: v6 campaign lacks link state contract")
            if payload.get("connectivity_mode") != "lte_anchored_mc":
                problems.append(f"{path}: v6 campaign lacks LTE anchored connectivity")
    elif schema == "greenran.campaign_manifest.v1":
        if payload.get("kind") == "vehicle_feasibility" and not payload.get("provenance"):
            problems.append(f"{path}: vehicle campaign lacks provenance")
        if payload.get("kind") == "vehicle_feasibility" and payload.get("scientific_decision") not in {
            "approved", "baseline_infeasible", "blocked", "rejected"
        }:
            problems.append(f"{path}: vehicle campaign lacks explicit scientific_decision")
    elif schema == "greenran.asgard.paired_campaign.v2":
        if payload.get("scientific_decision") not in {
            "approved", "baseline_infeasible", "blocked", "rejected"
        }:
            problems.append(f"{path}: ASGARD campaign lacks explicit scientific_decision")
        checkpoint = payload.get("checkpoint") if isinstance(payload.get("checkpoint"), dict) else {}
        if payload.get("scientific_decision") == "approved":
            if payload.get("approved") is not True:
                problems.append(f"{path}: approved ASGARD campaign has approved=false")
            if checkpoint.get("readiness") != "ready":
                problems.append(f"{path}: approved ASGARD campaign lacks checkpoint readiness")
            criteria = payload.get("criteria") if isinstance(payload.get("criteria"), dict) else {}
            if not all(criteria.get(key) is True for key in (
                "complete_15_pairs", "all_pairs_valid", "all_strict_slas",
                "all_e2_acks", "no_rollbacks_or_starvation",
                "paired_energy_ci95_strictly_below_zero",
            )):
                problems.append(f"{path}: approved ASGARD campaign has an incomplete acceptance gate")
    return problems


def _is_historical_diagnostic(path: Path) -> bool:
    """Keep immutable legacy diagnostics visible without making CI fail on them."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(payload, dict):
        return False
    schema = str(payload.get("schema", ""))
    if schema in {
        "greenran.campaign_manifest.v1",
        "greenran.autonomous_vehicle_feasibility.v2",
    }:
        return True
    return (
        schema == "greenran.autonomous_vehicle_feasibility.v3"
        and payload.get("status") in {"smoke_failed", "cancelled", "metric_invalid"}
        and payload.get("promotion_eligible") is not True
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="*", type=Path)
    parser.add_argument(
        "--strict",
        action="store_true",
        help="treat legacy/non-promotable diagnostic findings as errors",
    )
    args = parser.parse_args()
    paths = args.paths or sorted(
        path
        for root_name in ("runs",)
        for path in Path(root_name).rglob("*.json")
        if path.name in {
            "campaign_manifest.json",
            "campaign_report.json",
            "selected_vehicle_profile.json",
        }
    )
    problems: list[str] = []
    historical_warnings: list[str] = []
    for path in paths:
        findings = validate_manifest(path)
        if findings and not args.strict and _is_historical_diagnostic(path):
            historical_warnings.extend(findings)
        else:
            problems.extend(findings)
    if problems:
        print("\n".join(problems))
        return 1
    if historical_warnings:
        print("historical diagnostic warnings (not promotion-eligible):")
        print("\n".join(historical_warnings))
    print(f"validated {len(paths)} JSON contract file(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
