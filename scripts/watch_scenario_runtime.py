#!/usr/bin/env python3
"""
GreenRAN scenario watcher.

Monitora o estado vivo do cenário sem mexer no rApp:
- lê métricas em /tmp/xapp_metrics/extended_metrics.json
- lê snapshot do App2 em /tmp/app2_monitoramento/monitoring_snapshot.json
- lê a última decisão no banco do rApp
- registra timeline em JSONL
- emite alertas por transição
- opcionalmente envia alertas para um webhook externo

Observação:
- envio para WhatsApp depende de um gateway/API externa já configurada.
- este script expõe um webhook simples para você plugar em Twilio, Z-API,
  Evolution API, CallMeBot ou outro integrador do seu ambiente.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from greenran_paths import (  # noqa: E402
    EXTENDED_METRICS_JSON_PATH,
    RAPP_DB_PATH,
    STATE_DIR,
)


METRICS_PATH = EXTENDED_METRICS_JSON_PATH
APP2_SNAPSHOT_PATH = STATE_DIR / "app2_monitoramento" / "monitoring_snapshot.json"
DEFAULT_OUTPUT = STATE_DIR / "greenran_scenario_watch.jsonl"
TWILIO_MESSAGES_URL = "https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json"

CAMERA_WARNING_MBPS = 30.0
CAMERA_CRITICAL_MBPS = 25.0
APP2_WARNING_RATIO = 0.95
APP2_CRITICAL_RATIO = 0.90
APP2_STALE_SECONDS = 15.0


@dataclass
class WatchState:
    decision: str = ""
    energy_state: str = ""
    reason: str = ""
    app2_ratio: float = 1.0
    camera_min_mbps: float = 999.0
    metrics_timestamp: str = ""


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def _load_latest_decision() -> dict[str, Any]:
    if not Path(RAPP_DB_PATH).exists():
        return {}

    conn = sqlite3.connect(str(RAPP_DB_PATH))
    try:
        row = conn.execute(
            """
            SELECT timestamp, datetime, decision, energy_state, reason,
                   ml_decision, ml_confidence, ml_predicted_cvar_ms
            FROM decisions_history
            ORDER BY timestamp DESC
            LIMIT 1
            """
        ).fetchone()
    finally:
        conn.close()

    if not row:
        return {}

    return {
        "timestamp": row[0],
        "datetime": row[1],
        "decision": row[2],
        "energy_state": row[3],
        "reason": row[4],
        "ml_decision": row[5],
        "ml_confidence": row[6],
        "ml_predicted_cvar_ms": row[7],
    }


def _extract_camera_mbps(metrics: dict[str, Any]) -> dict[str, float]:
    ue_metrics = metrics.get("ue_metrics", {})
    cameras = {}
    for ue_id, info in ue_metrics.items():
        if info.get("device_type") != "camera":
            continue
        mbps = info.get("rx_throughput_kbps", info.get("throughput_kbps", 0.0)) / 1000.0
        cameras[str(ue_id)] = round(float(mbps), 3)
    return cameras


def _build_snapshot() -> dict[str, Any]:
    metrics = _load_json(METRICS_PATH)
    app2 = _load_json(APP2_SNAPSHOT_PATH)
    latest = _load_latest_decision()

    gm = metrics.get("global_metrics", {})
    cameras = _extract_camera_mbps(metrics)
    camera_values = list(cameras.values())

    app2_total = int(app2.get("total_sensors", 0) or 0)
    app2_connected = int(app2.get("connected_sensors", 0) or 0)
    app2_ratio = (app2_connected / app2_total) if app2_total > 0 else 1.0
    raw_app2_ts = app2.get("timestamp", 0.0)
    app2_ts = 0.0
    if isinstance(raw_app2_ts, (int, float)):
        app2_ts = float(raw_app2_ts)
    elif isinstance(raw_app2_ts, str) and raw_app2_ts:
        try:
            app2_ts = datetime.fromisoformat(raw_app2_ts).timestamp()
        except ValueError:
            app2_ts = 0.0
    app2_age_s = max(0.0, time.time() - app2_ts) if app2_ts else None

    return {
        "observed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "metrics_timestamp": metrics.get("timestamp_iso"),
        "sim_time_range": metrics.get("sim_time_range"),
        "cvar_ms": round(float(gm.get("cvar_per_ue_us", 0.0)) / 1000.0, 3),
        "p95_ms": round(float(gm.get("latency_p95_us", 0.0)) / 1000.0, 3),
        "pdcp_delta_mbps": round(float(gm.get("pdcp_delta_throughput_kbps", 0.0)) / 1000.0, 2),
        "camera_mbps": cameras,
        "camera_min_mbps": min(camera_values) if camera_values else None,
        "app2_connected": app2_connected,
        "app2_total": app2_total,
        "app2_ratio": round(app2_ratio, 4),
        "app2_packet_loss_pct": app2.get("packet_loss_percent"),
        "app2_delivery_success_pct": app2.get("delivery_success_percent"),
        "app2_age_s": None if app2_age_s is None else round(app2_age_s, 2),
        "app2_alerts": app2.get("alerts", []),
        "latest_decision": latest,
    }


def _post_webhook(url: str, payload: dict[str, Any]) -> None:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            resp.read()
    except urllib.error.URLError as exc:
        print(f"[watcher] webhook falhou: {exc}")


def _post_twilio_whatsapp(
    account_sid: str,
    auth_token: str,
    from_number: str,
    to_number: str,
    message: str,
) -> None:
    url = TWILIO_MESSAGES_URL.format(account_sid=account_sid)
    form = urllib.parse.urlencode(
        {
            "From": from_number,
            "To": to_number,
            "Body": message,
        }
    ).encode("utf-8")

    token = base64.b64encode(f"{account_sid}:{auth_token}".encode("utf-8")).decode("ascii")
    req = urllib.request.Request(
        url,
        data=form,
        headers={
            "Authorization": f"Basic {token}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            resp.read()
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="ignore")
        print(f"[watcher] twilio HTTP {exc.code}: {body}")
    except urllib.error.URLError as exc:
        print(f"[watcher] twilio falhou: {exc}")


def _format_alert_message(alert: dict[str, Any], snapshot: dict[str, Any]) -> str:
    latest = snapshot.get("latest_decision", {})
    lines = [
        "GreenRAN Alert",
        f"Level: {alert.get('level', 'info').upper()}",
        f"Type: {alert.get('type', 'unknown')}",
        f"Msg: {alert.get('message', '')}",
        f"Decision: {latest.get('decision') or 'N/A'}",
        f"Energy: {latest.get('energy_state') or 'N/A'}",
        f"CVaR: {snapshot.get('cvar_ms')} ms",
        f"P95: {snapshot.get('p95_ms')} ms",
        f"Cam min: {snapshot.get('camera_min_mbps')}",
        f"App2: {snapshot.get('app2_connected')}/{snapshot.get('app2_total')}",
    ]
    reason = alert.get("reason") or latest.get("reason")
    if reason:
        lines.append(f"Reason: {reason}")
    return "\n".join(lines)


def _derive_alerts(snapshot: dict[str, Any], previous: WatchState | None) -> list[dict[str, Any]]:
    alerts: list[dict[str, Any]] = []
    latest = snapshot.get("latest_decision", {})
    decision = latest.get("decision", "") or ""
    energy_state = latest.get("energy_state", "") or ""
    reason = latest.get("reason", "") or ""
    app2_ratio = float(snapshot.get("app2_ratio", 1.0) or 1.0)
    camera_min = float(snapshot.get("camera_min_mbps", 999.0) or 999.0)
    metrics_ts = snapshot.get("metrics_timestamp", "") or ""
    app2_age_s = snapshot.get("app2_age_s")

    if previous is None:
        alerts.append({
            "type": "watch_started",
            "level": "info",
            "message": f"Watcher iniciado com decision={decision or 'N/A'} energy={energy_state or 'N/A'}",
        })
        return alerts

    if decision != previous.decision:
        alerts.append({
            "type": "decision_changed",
            "level": "warning" if decision != "ALLOWED" else "info",
            "message": f"Decisão mudou: {previous.decision or 'N/A'} -> {decision or 'N/A'}",
            "reason": reason,
        })

    if energy_state != previous.energy_state:
        alerts.append({
            "type": "energy_changed",
            "level": "info",
            "message": f"Energy mudou: {previous.energy_state or 'N/A'} -> {energy_state or 'N/A'}",
            "reason": reason,
        })

    if camera_min < CAMERA_CRITICAL_MBPS <= previous.camera_min_mbps:
        alerts.append({
            "type": "camera_critical",
            "level": "critical",
            "message": f"Câmera abaixo de {CAMERA_CRITICAL_MBPS:.1f} Mbps: {camera_min:.2f} Mbps",
        })
    elif camera_min < CAMERA_WARNING_MBPS <= previous.camera_min_mbps:
        alerts.append({
            "type": "camera_warning",
            "level": "warning",
            "message": f"Câmera abaixo de {CAMERA_WARNING_MBPS:.1f} Mbps: {camera_min:.2f} Mbps",
        })
    elif previous.camera_min_mbps < CAMERA_WARNING_MBPS and camera_min >= CAMERA_WARNING_MBPS:
        alerts.append({
            "type": "camera_recovered",
            "level": "info",
            "message": f"Câmeras recuperadas: mínimo {camera_min:.2f} Mbps",
        })

    if app2_ratio < APP2_CRITICAL_RATIO <= previous.app2_ratio:
        alerts.append({
            "type": "app2_critical",
            "level": "critical",
            "message": f"App2 abaixo de {APP2_CRITICAL_RATIO:.0%}: {app2_ratio:.1%}",
        })
    elif app2_ratio < APP2_WARNING_RATIO <= previous.app2_ratio:
        alerts.append({
            "type": "app2_warning",
            "level": "warning",
            "message": f"App2 abaixo de {APP2_WARNING_RATIO:.0%}: {app2_ratio:.1%}",
        })
    elif previous.app2_ratio < APP2_WARNING_RATIO and app2_ratio >= APP2_WARNING_RATIO:
        alerts.append({
            "type": "app2_recovered",
            "level": "info",
            "message": f"App2 recuperado: {app2_ratio:.1%} conectados",
        })

    if app2_age_s is not None and app2_age_s > APP2_STALE_SECONDS:
        alerts.append({
            "type": "app2_stale",
            "level": "warning",
            "message": f"Snapshot App2 antigo: {app2_age_s:.1f}s",
        })

    if metrics_ts != previous.metrics_timestamp:
        pass

    return alerts


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=True) + "\n")


def _state_from_snapshot(snapshot: dict[str, Any]) -> WatchState:
    latest = snapshot.get("latest_decision", {})
    return WatchState(
        decision=latest.get("decision", "") or "",
        energy_state=latest.get("energy_state", "") or "",
        reason=latest.get("reason", "") or "",
        app2_ratio=float(snapshot.get("app2_ratio", 1.0) or 1.0),
        camera_min_mbps=float(snapshot.get("camera_min_mbps", 999.0) or 999.0),
        metrics_timestamp=snapshot.get("metrics_timestamp", "") or "",
    )


def _print_summary(snapshot: dict[str, Any]) -> None:
    latest = snapshot.get("latest_decision", {})
    print(
        "[watcher]",
        snapshot.get("observed_at"),
        f"decision={latest.get('decision') or 'N/A'}",
        f"energy={latest.get('energy_state') or 'N/A'}",
        f"ml={latest.get('ml_decision') or 'N/A'}",
        f"cvar={snapshot.get('cvar_ms')}ms",
        f"p95={snapshot.get('p95_ms')}ms",
        f"cam_min={snapshot.get('camera_min_mbps')}",
        f"app2={snapshot.get('app2_connected')}/{snapshot.get('app2_total')}",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Monitor externo do cenário GreenRAN")
    parser.add_argument("--interval", type=float, default=5.0, help="intervalo em segundos")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="arquivo JSONL de saída")
    parser.add_argument("--webhook-url", help="webhook opcional para alertas")
    parser.add_argument("--twilio-account-sid", default=os.getenv("GREENRAN_TWILIO_ACCOUNT_SID"))
    parser.add_argument("--twilio-auth-token", default=os.getenv("GREENRAN_TWILIO_AUTH_TOKEN"))
    parser.add_argument("--twilio-from", default=os.getenv("GREENRAN_TWILIO_FROM"))
    parser.add_argument("--twilio-to", default=os.getenv("GREENRAN_TWILIO_TO"))
    parser.add_argument("--once", action="store_true", help="executa um ciclo e sai")
    args = parser.parse_args()

    output_path = Path(args.output)
    previous: WatchState | None = None

    while True:
        snapshot = _build_snapshot()
        _print_summary(snapshot)
        _append_jsonl(output_path, {"kind": "snapshot", "payload": snapshot})

        alerts = _derive_alerts(snapshot, previous)
        for alert in alerts:
            payload = {
                "kind": "alert",
                "payload": alert,
                "snapshot": snapshot,
            }
            print(f"[watcher] {alert['level'].upper()}: {alert['message']}")
            _append_jsonl(output_path, payload)
            if args.webhook_url:
                _post_webhook(args.webhook_url, payload)
            if args.twilio_account_sid and args.twilio_auth_token and args.twilio_from and args.twilio_to:
                _post_twilio_whatsapp(
                    account_sid=args.twilio_account_sid,
                    auth_token=args.twilio_auth_token,
                    from_number=args.twilio_from,
                    to_number=args.twilio_to,
                    message=_format_alert_message(alert, snapshot),
                )

        previous = _state_from_snapshot(snapshot)

        if args.once:
            return 0
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
