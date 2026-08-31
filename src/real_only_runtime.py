"""Guards for official TA-SAM runs that must use real PDCP metrics only."""

import os
import time


def real_only_status(metrics, now_ms=None, max_age_s=None):
    """Return ``(valid, reason)`` for a live extended-metrics payload.

    This is deliberately strict: a missing, stale, or proxy-backed payload is
    not usable for a TA-SAM transition or shadow comparison.
    """
    if not isinstance(metrics, dict) or not metrics:
        return False, "metrics_missing"

    # Extended metrics keep quality counters under global_metrics, while
    # timestamp/sim_time remain at the document root.
    quality = metrics.get("global_metrics", metrics) or {}

    if str(quality.get("collector_mode", "") or "") != "pdcp_real":
        return False, "collector_mode_not_pdcp_real"

    if int(quality.get("proxy_latency_sample_count", 0) or 0) > 0:
        return False, "proxy_latency_present"

    for field in ("pdcp_stale", "rlc_stale", "mac_stale"):
        if bool(quality.get(field, False)):
            return False, f"{field}_true"

    if int(quality.get("real_latency_sample_count", 0) or 0) <= 0:
        return False, "real_latency_samples_missing"

    if max_age_s is None:
        max_age_s = float(os.getenv("GREENRAN_REAL_ONLY_MAX_TRACE_AGE_S", "30"))
    timestamp = metrics.get("timestamp")
    if timestamp is not None:
        try:
            age_s = max(0.0, (float(now_ms or int(time.time() * 1000)) - float(timestamp)) / 1000.0)
            if age_s > max_age_s:
                return False, f"metrics_stale_age_{age_s:.1f}s"
        except (TypeError, ValueError):
            return False, "metrics_timestamp_invalid"

    return True, "ok"
