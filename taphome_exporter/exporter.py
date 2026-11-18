"""Prometheus exporter: a background thread reads the Core, scrapes only format the last result."""

import logging
import os
import re
import sys
import threading
import time
from dataclasses import dataclass

from prometheus_client import start_http_server
from prometheus_client.core import REGISTRY, CounterMetricFamily, GaugeMetricFamily

from . import api

LABELS = ("device_id", "name", "zone", "type", "usage")
# Only the types whose unit is known; the rest get no unit guessed.
KNOWN = {
    "RealTemperature": "taphome_temperature_celsius",
    "Humidity": "taphome_humidity_ratio",
    "TemperatureSetPoint": "taphome_setpoint_celsius",
}
log = logging.getLogger("taphome_exporter.exporter")


@dataclass
class State:
    discovery: dict | None = None  # last successful poll; kept out of /metrics while polls fail
    values: dict | None = None
    ok: bool = False
    last_success: float | None = None
    duration: float | None = None
    errors: int = 0


def metric_name(value_type: str) -> str:
    # A new value type on the Core becomes a new metric without a code change.
    return KNOWN.get(value_type) or "taphome_" + re.sub(r"(?<!^)(?=[A-Z])", "_", value_type).lower()


def poll_once(state: State, url: str, token: str, fetch=api.fetch) -> None:
    start = time.monotonic()
    try:
        discovery = fetch(url, token, "discovery")
        values = fetch(url, token, "getAllDevicesValues")
    except Exception as e:  # 401, timeout, unreachable, bad JSON: record it, keep polling
        state.duration = time.monotonic() - start
        state.errors += 1
        state.ok = False
        log.warning("poll failed: %s: %s", type(e).__name__, e)  # message carries the URL, never the token
        return
    state.duration = time.monotonic() - start
    state.discovery, state.values = discovery, values
    state.last_success = time.time()
    state.ok = True


class TapHomeCollector:
    def __init__(self, state: State):
        self.state = state

    def collect(self):
        s = self.state  # ponytail: no lock; a scrape racing a poll can mix two fields, harmless for gauges
        # One snapshot of the fields the poller may flip mid-scrape, so up and the value gauges never disagree.
        ok, discovery, values = s.ok, s.discovery, s.values
        yield GaugeMetricFamily("taphome_up", "1 if the last poll of the Core succeeded", value=float(ok))
        if s.last_success is not None:
            yield GaugeMetricFamily("taphome_last_success_timestamp_seconds", "Unix time of the last successful poll", value=s.last_success)
        if s.duration is not None:
            yield GaugeMetricFamily("taphome_read_duration_seconds", "Duration of the last poll", value=s.duration)
        yield CounterMetricFamily("taphome_read_errors", "Failed polls", value=s.errors)
        if not ok or discovery is None or values is None:
            return
        devices = {d["deviceId"]: d for d in discovery.get("devices", [])}
        yield GaugeMetricFamily("taphome_devices", "Devices in discovery", value=len(devices))
        families: dict[str, GaugeMetricFamily] = {}
        for dev in values.get("devices", []):
            meta = devices.get(dev["deviceId"], {})  # missing from discovery: empty labels
            labels = [str(dev["deviceId"])] + [str(meta.get(k) or "") for k in ("name", "zone", "type", "usage")]
            for v in dev.get("values", []):
                value = v.get("value")
                # "NaN" strings, other strings and null are skipped; bools become 0/1
                if not isinstance(value, (int, float)):
                    continue
                name = metric_name(v["valueTypeName"])
                fam = families.setdefault(name, GaugeMetricFamily(name, v["valueTypeName"], labels=LABELS))
                fam.add_metric(labels, float(value))
        yield from families.values()


def main() -> None:
    url, token = os.environ.get("TAPHOME_URL"), os.environ.get("TAPHOME_TOKEN")
    if not url or not token:
        sys.exit("TAPHOME_URL and TAPHOME_TOKEN are required")  # fail fast on missing config, once
    interval = float(os.environ.get("TAPHOME_INTERVAL", "30"))
    port = int(os.environ.get("TAPHOME_EXPORTER_PORT", "9878"))
    state = State()
    REGISTRY.register(TapHomeCollector(state))
    start_http_server(port)
    print(f"taphome exporter on :{port}/metrics → {url}, every {interval:g}s", flush=True)

    def loop():
        while True:
            poll_once(state, url, token)
            time.sleep(interval)

    threading.Thread(target=loop, daemon=True).start()
    threading.Event().wait()  # daemon thread dies with the main thread


if __name__ == "__main__":
    main()
