#!/usr/bin/env python3
"""
Env variables:
  GREENRAN_TWILIO_ACCOUNT_SID
  GREENRAN_TWILIO_AUTH_TOKEN
  GREENRAN_TWILIO_FROM
  GREENRAN_TWILIO_TO
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request


def main() -> int:
    parser = argparse.ArgumentParser(description="Send a Twilio WhatsApp test message")
    parser.add_argument(
        "--message",
        default="GreenRAN test: watcher WhatsApp integration is working.",
        help="message body",
    )
    args = parser.parse_args()

    account_sid = os.getenv("GREENRAN_TWILIO_ACCOUNT_SID")
    auth_token = os.getenv("GREENRAN_TWILIO_AUTH_TOKEN")
    from_number = os.getenv("GREENRAN_TWILIO_FROM")
    to_number = os.getenv("GREENRAN_TWILIO_TO")

    missing = [
        name for name, value in [
            ("GREENRAN_TWILIO_ACCOUNT_SID", account_sid),
            ("GREENRAN_TWILIO_AUTH_TOKEN", auth_token),
            ("GREENRAN_TWILIO_FROM", from_number),
            ("GREENRAN_TWILIO_TO", to_number),
        ] if not value
    ]
    if missing:
        print(json.dumps({"ok": False, "error": f"missing env vars: {', '.join(missing)}"}))
        return 2

    url = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json"
    body = urllib.parse.urlencode(
        {
            "From": from_number,
            "To": to_number,
            "Body": args.message,
        }
    ).encode("utf-8")
    token = base64.b64encode(f"{account_sid}:{auth_token}".encode("utf-8")).decode("ascii")
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Authorization": f"Basic {token}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        print(json.dumps({
            "ok": True,
            "sid": payload.get("sid"),
            "status": payload.get("status"),
            "to": payload.get("to"),
            "from": payload.get("from"),
        }, indent=2))
        return 0
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="ignore")
        try:
            payload = json.loads(raw)
        except Exception:
            payload = {"raw": raw}
        print(json.dumps({
            "ok": False,
            "http_status": exc.code,
            "error": payload,
        }, indent=2))
        return 1
    except urllib.error.URLError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
