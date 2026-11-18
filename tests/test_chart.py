import json
import re
import subprocess

import yaml

CHART = "helm/taphome-exporter"
REQUIRED = ["--set", "taphome.url=http://192.0.2.1/api/TapHomeApi/v1", "--set", "taphome.tokenSecret.name=taphome-token"]
TOGGLES = [
    "--set", "serviceMonitor.enabled=true",
    "--set", "prometheusRule.enabled=true",
    "--set", "dashboard.enabled=true",
]
# Fixed names from exporter.py plus the value types the dashboards use (the rest follow the snake_case rule).
KNOWN = {
    "taphome_up", "taphome_last_success_timestamp_seconds", "taphome_read_duration_seconds",
    "taphome_read_errors_total", "taphome_devices",
    "taphome_temperature_celsius", "taphome_humidity_ratio", "taphome_setpoint_celsius",
    "taphome_switch_state", "taphome_reed_contact", "taphome_device_status", "taphome_operation_mode",
    "taphome_analog_output_value", "taphome_analog_output_desired_value",
}


def template(*args):
    return subprocess.run(["helm", "template", "t", CHART, *args], capture_output=True, text=True)


def render_all(*args):
    out = template(*args)
    assert out.returncode == 0, out.stderr
    return [d for d in yaml.safe_load_all(out.stdout) if d]


def render(*args):
    return {d["kind"]: d for d in render_all(*args)}


def test_requires_url_and_secret():
    assert template("--set", "taphome.tokenSecret.name=x").returncode != 0
    assert template("--set", "taphome.url=x").returncode != 0


def test_defaults_render_only_workload():
    docs = render(*REQUIRED)
    assert set(docs) == {"Deployment", "Service"}
    pod = docs["Deployment"]["spec"]["template"]["spec"]
    # a Service named taphome-exporter injects TAPHOME_EXPORTER_PORT=tcp://..., which the exporter reads as its port
    assert pod["enableServiceLinks"] is False
    assert pod["securityContext"] == {
        "runAsNonRoot": True,
        "runAsUser": 65534,
        "seccompProfile": {"type": "RuntimeDefault"},
    }
    c = pod["containers"][0]
    chart = yaml.safe_load(open(f"{CHART}/Chart.yaml"))
    # one version for chart, app and image tag; the release bumps it, so the test does not pin it
    assert chart["version"] == chart["appVersion"]
    assert c["image"] == f"10.34.1.1:30095/taphome-exporter:{chart['appVersion']}"
    env = {e["name"]: e for e in c["env"]}
    assert set(env) == {"TAPHOME_URL", "TAPHOME_TOKEN", "TAPHOME_INTERVAL"}
    assert env["TAPHOME_TOKEN"]["valueFrom"]["secretKeyRef"] == {"name": "taphome-token", "key": "api-token"}
    assert c["ports"][0]["containerPort"] == 9878
    assert c["securityContext"] == {
        "allowPrivilegeEscalation": False,
        "readOnlyRootFilesystem": True,
        "capabilities": {"drop": ["ALL"]},
    }
    assert c["readinessProbe"]["tcpSocket"]["port"] == "metrics"
    assert c["livenessProbe"]["httpGet"] == {"path": "/metrics", "port": "metrics"}
    assert docs["Service"]["spec"]["ports"][0]["name"] == "metrics"
    assert docs["Service"]["spec"]["ports"][0]["port"] == 9878


def rules_of(docs):
    return {r["alert"]: r for g in docs["PrometheusRule"]["spec"]["groups"] for r in g["rules"]}


def test_all_toggles():
    all_docs = render_all(*REQUIRED, *TOGGLES)
    docs = {d["kind"]: d for d in all_docs if d["kind"] != "ConfigMap"}
    assert set(docs) == {"Deployment", "Service", "ServiceMonitor", "PrometheusRule"}

    ep = docs["ServiceMonitor"]["spec"]["endpoints"][0]
    assert (ep["port"], ep["path"], ep["interval"]) == ("metrics", "/metrics", "30s")

    rules = rules_of(docs)
    assert {n: (r["expr"], r["for"]) for n, r in rules.items()} == {
        "TapHomeUnreachable": ("taphome_up == 0", "15m"),
        "TapHomeStale": ("time() - taphome_last_success_timestamp_seconds > 600 and taphome_up == 1", "5m"),
        "TapHomeContactOpen": ('taphome_reed_contact{usage!="MotionSensor"} == 1', "30m"),
        "TapHomeRoomTemperature": (
            'taphome_temperature_celsius{usage="AirTemperature"} < 17'
            ' or taphome_temperature_celsius{usage="AirTemperature"} > 28', "30m"),
        "TapHomeHumidityHigh": ('taphome_humidity_ratio{usage="AirTemperature"} > 0.7', "1h"),
    }
    assert all(r["labels"]["severity"] == "warning" for r in rules.values())

    cms = {next(iter(c["data"])): c for c in all_docs if c["kind"] == "ConfigMap"}
    assert set(cms) == {"taphome-climate.json", "taphome-house.json"}
    for name, cm in cms.items():
        assert cm["metadata"]["labels"]["grafana_dashboard"] == "1"
        assert cm["metadata"]["annotations"]["grafana_folder"] == "Home"
        assert json.loads(cm["data"][name])["uid"] == name.removesuffix(".json")


def test_alert_values_are_tunable():
    docs = render(*REQUIRED, "--set", "prometheusRule.enabled=true", "--set", "alerts.contactOpenFor=1h",
                  "--set", "alerts.roomTempMin=15", "--set", "alerts.roomTempMax=30", "--set", "alerts.humidityMax=0.8")
    rules = rules_of(docs)
    assert rules["TapHomeContactOpen"]["for"] == "1h"
    assert "< 15" in rules["TapHomeRoomTemperature"]["expr"] and "> 30" in rules["TapHomeRoomTemperature"]["expr"]
    assert rules["TapHomeHumidityHigh"]["expr"].endswith("> 0.8")


def test_dashboard_with_empty_labels_and_annotations():
    # Helm merges a {} over the defaults, so only null really empties the maps; both must still parse.
    docs = render(*REQUIRED, "--set", "dashboard.enabled=true",
                  "--set-json", "dashboard.labels={}", "--set-json", "dashboard.annotations={}")
    assert docs["ConfigMap"]["metadata"]["labels"]["grafana_dashboard"] == "1"
    docs = render(*REQUIRED, "--set", "dashboard.enabled=true",
                  "--set-json", "dashboard.labels=null", "--set-json", "dashboard.annotations=null")
    meta = docs["ConfigMap"]["metadata"]
    assert "grafana_dashboard" not in meta["labels"]
    assert "annotations" not in meta


def test_metrics_exist():
    docs = render(*REQUIRED, "--set", "prometheusRule.enabled=true")
    used = set(re.findall(r"taphome_[a-z_]+", json.dumps(docs["PrometheusRule"])))
    assert used and used <= KNOWN, used - KNOWN
    for uid in ("taphome-climate", "taphome-house"):
        with open(f"{CHART}/dashboards/{uid}.json") as f:
            dash = json.load(f)
        assert dash["uid"] == uid and dash["editable"] is False
        panels = [p for p in dash["panels"] if p["type"] != "row"]
        assert len(panels) >= 6
        for p in panels:
            assert p["datasource"] == {"type": "prometheus", "uid": "prometheus"}
            for t in p["targets"]:
                assert t["datasource"]["uid"] == "prometheus"
                used = set(re.findall(r"taphome_[a-z_]+", t["expr"]))
                assert used and used <= KNOWN, used - KNOWN
                # empty legend = exporter-health panel without labels
                legend = t["legendFormat"]
                assert not legend or "{{name}}" in legend or "{{zone}}" in legend, legend
