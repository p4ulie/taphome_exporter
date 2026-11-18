import json
import logging
import urllib.error
from pathlib import Path

import pytest
from prometheus_client import CollectorRegistry, generate_latest

from taphome_exporter import exporter
from taphome_exporter.exporter import State, TapHomeCollector, metric_name, poll_interval, poll_once

FIX = Path(__file__).parent / "fixtures"
DISCOVERY = json.loads((FIX / "discovery.json").read_text())
VALUES = json.loads((FIX / "values.json").read_text())
TOKEN = "s3cret-token"


def fake_fetch(url, token, endpoint, timeout=10):
    return {"discovery": DISCOVERY, "getAllDevicesValues": VALUES}[endpoint]


def render(state):
    registry = CollectorRegistry()
    registry.register(TapHomeCollector(state))
    return generate_latest(registry).decode()


def good_state():
    state = State()
    poll_once(state, "http://core/api", TOKEN, fetch=fake_fetch)
    return state


def test_before_first_poll():
    out = render(State())
    assert "taphome_up 0.0" in out
    assert "taphome_read_errors_total 0.0" in out
    assert "taphome_temperature_celsius" not in out
    assert "taphome_devices" not in out


def test_good_poll():
    out = render(good_state())
    assert "taphome_up 1.0" in out
    line = next(l for l in out.splitlines() if l.startswith("taphome_temperature_celsius{") and 'device_id="1"' in l)
    for label in ('name="Sensor 1"', 'zone="Room A"', 'type="SensorType1"', 'usage="AirTemperature"'):
        assert label in line
    assert line.endswith(" 18.1")
    assert "taphome_humidity_ratio{" in out and " 0.51" in out
    assert "taphome_setpoint_celsius{" in out and " 21.5" in out
    assert "taphome_switch_state{" in out
    assert "taphome_devices 3.0" in out
    assert "taphome_last_success_timestamp_seconds " in out
    assert "taphome_read_duration_seconds " in out


def test_nan_and_null_skipped():
    out = render(good_state())
    assert "taphome_device_status" not in out
    assert "taphome_operation_mode" not in out


def test_device_missing_from_discovery_has_empty_labels():
    out = render(good_state())
    line = next(l for l in out.splitlines() if l.startswith("taphome_temperature_celsius{") and 'device_id="9"' in l)
    for label in ('name=""', 'zone=""', 'type=""', 'usage=""'):
        assert label in line


def test_new_value_type_exported():
    assert metric_name("BlindsLevel") == "taphome_blinds_level"
    assert metric_name("RealTemperature") == "taphome_temperature_celsius"
    assert "taphome_blinds_level{" in render(good_state())


def test_failed_poll(caplog):
    def boom(url, token, endpoint, timeout=10):
        raise urllib.error.HTTPError(f"{url}/{endpoint}", 401, "Unauthorized", {}, None)

    state = good_state()
    with caplog.at_level(logging.WARNING):
        poll_once(state, "http://core/api", TOKEN, fetch=boom)
    out = render(state)
    assert "taphome_up 0.0" in out
    assert "taphome_temperature_celsius" not in out
    assert "taphome_devices" not in out
    assert "taphome_read_errors_total 1.0" in out
    assert "HTTPError" in caplog.text and TOKEN not in caplog.text


def test_second_endpoint_failure_fails_poll():
    def half(url, token, endpoint, timeout=10):
        if endpoint == "getAllDevicesValues":
            raise TimeoutError("slow")
        return DISCOVERY

    state = State()
    poll_once(state, "u", TOKEN, fetch=half)
    assert "taphome_up 0.0" in render(state)


def test_missing_env_exits(monkeypatch):
    monkeypatch.delenv("TAPHOME_URL", raising=False)
    monkeypatch.delenv("TAPHOME_TOKEN", raising=False)
    with pytest.raises(SystemExit):
        exporter.main()


def fetch_with(discovery=DISCOVERY, values=VALUES):
    return lambda url, token, endpoint, timeout=10: {"discovery": discovery, "getAllDevicesValues": values}[endpoint]


def values_with(value_type):
    return {"devices": [{"deviceId": 1, "values": [{"valueTypeName": value_type, "value": 1}]}]}


@pytest.mark.parametrize("discovery,values", [
    ([], VALUES),
    (DISCOVERY, []),
    ({"devices": [{}]}, VALUES),  # device without deviceId
    (DISCOVERY, {"devices": [{}]}),
    (DISCOVERY, {"devices": [{"deviceId": 1, "values": [{"value": 1}]}]}),  # no valueTypeName
])
def test_malformed_reply_is_a_failed_poll(caplog, discovery, values):
    state = State()
    with caplog.at_level(logging.WARNING):
        poll_once(state, "u", TOKEN, fetch=fetch_with(discovery, values))
    out = render(state)  # the scrape still answers
    assert "taphome_up 0.0" in out
    assert "taphome_read_errors_total 1.0" in out
    assert "poll failed" in caplog.text


@pytest.mark.parametrize("bad", ["Devices", "ReadErrors", "Bad Name", "Wh/h"])
def test_unusable_value_type_is_skipped(caplog, bad):
    values = {"devices": [{"deviceId": 1, "values": [
        {"valueTypeName": bad, "value": 1}, {"valueTypeName": "BlindsLevel", "value": 2}]}]}
    state = State()
    with caplog.at_level(logging.WARNING):
        poll_once(state, "u", TOKEN, fetch=fetch_with(values=values))
    out = render(state)
    assert "taphome_up 1.0" in out
    assert "taphome_blinds_level{" in out
    assert "taphome_devices 3.0" in out  # the fixed metric is not clobbered
    assert bad in caplog.text


def test_interval_clamped_to_one_second(monkeypatch):
    monkeypatch.setenv("TAPHOME_INTERVAL", "0")
    assert poll_interval() == 1
    monkeypatch.setenv("TAPHOME_INTERVAL", "45")
    assert poll_interval() == 45
    monkeypatch.delenv("TAPHOME_INTERVAL")
    assert poll_interval() == 30
