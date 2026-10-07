"""Validate the SCTP port plan shared by RIC, xApp and ns-3 E2 agents."""

from __future__ import annotations

import argparse
import json
from typing import Iterable


E2_PORT_PLAN_SCHEMA = "greenran.e2_port_plan.v1"
DEFAULT_E2_CELL_IDS = (1, 2, 3, 4)


class E2PortPlanError(ValueError):
    """Raised when an E2 endpoint plan can collide or cannot bind."""


def build_e2_port_plan(
    e2_term_port: int,
    e2_xapp_port: int,
    e2_local_port: int,
    *,
    cell_ids: Iterable[int] = DEFAULT_E2_CELL_IDS,
) -> dict[str, object]:
    """Return a validated plan, including every local E2 bind port.

    ns-3 derives each local E2 endpoint as ``e2_local_port + cell_id``.
    The LTE anchor (cell 1) is included because it is enabled by the
    baseline-max profile even though the TA-SAM gate requires DUs 2--4.
    """
    try:
        term = int(e2_term_port)
        xapp = int(e2_xapp_port)
        local = int(e2_local_port)
        cells = tuple(int(cell) for cell in cell_ids)
    except (TypeError, ValueError) as exc:
        raise E2PortPlanError("E2 port plan must contain integer ports and cell IDs") from exc

    if not cells or len(set(cells)) != len(cells) or any(cell <= 0 for cell in cells):
        raise E2PortPlanError("E2 cell IDs must be unique positive integers")
    if term < 1 or term > 65535 or xapp < 1 or xapp > 65535:
        raise E2PortPlanError("E2 termination and xApp ports must be in 1..65535")
    bind_ports = {str(cell): local + cell for cell in cells}
    if local < 1 or any(port < 1 or port > 65535 for port in bind_ports.values()):
        raise E2PortPlanError("E2 local base plus cell ID must produce ports in 1..65535")

    owners: dict[int, list[str]] = {}
    owners.setdefault(term, []).append("e2_term")
    owners.setdefault(xapp, []).append("e2_xapp")
    for cell, port in bind_ports.items():
        owners.setdefault(port, []).append(f"e2_local_cell_{cell}")
    collisions = {port: labels for port, labels in owners.items() if len(labels) > 1}
    if collisions:
        rendered = ", ".join(
            f"{port} ({'/'.join(labels)})" for port, labels in sorted(collisions.items())
        )
        raise E2PortPlanError(f"E2 port collision: {rendered}")

    return {
        "schema": E2_PORT_PLAN_SCHEMA,
        "valid": True,
        "e2_term_port": term,
        "e2_xapp_port": xapp,
        "e2_local_port_base": local,
        "cell_ids": list(cells),
        "e2_local_bind_ports": bind_ports,
    }


def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--e2-term-port", required=True, type=int)
    parser.add_argument("--e2-xapp-port", required=True, type=int)
    parser.add_argument("--e2-local-port", required=True, type=int)
    args = parser.parse_args()
    try:
        plan = build_e2_port_plan(args.e2_term_port, args.e2_xapp_port, args.e2_local_port)
    except E2PortPlanError as exc:
        parser.error(str(exc))
    print(json.dumps(plan, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
