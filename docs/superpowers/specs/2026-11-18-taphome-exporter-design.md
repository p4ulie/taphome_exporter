# TapHome exporter — design

Date: 2026-11-18. Repo: `p4ulie/taphome_exporter` (public). Consumer: `p4ulie/homenet_2.0` (RKE2 lab, ArgoCD).
Approved by the owner 2026-11-18. Card: Homenet_2.0 031. Template: `p4ulie/flexit_nordic_s3`
(`docs/superpowers/specs/2026-11-17-monitoring-app-design.md`), whose shape this copies.

## Intent

Record what the TapHome Core sees (room temperatures, humidity, thermostat setpoints, heating and cooling valves,
security contacts, lights) in Prometheus, with alerts and Grafana dashboards, independent of Home Assistant.

## Goals / non-goals / anti-goals

- Goals: a Prometheus exporter for the Core's local API, a container image, a Helm chart with ServiceMonitor,
  PrometheusRule and dashboard ConfigMaps behind flags, homenet_2.0 deploying it from a git tag.
- Non-goals: **no writes** (never the set-value endpoints); no CRDs in the chart; no CI image build (built and pushed
  from the workstation to `10.34.1.1:30095`, as Flexit); no change to Home Assistant.
- Anti-goals: a scrape that blocks on the Core; stale values that look fresh; a code change needed when the owner
  exposes another device; real room or person names in the public repo (test fixtures are synthetic).

## Approaches

Decided by the owner (approved design): copy the Flexit app — background poll, scrape serves the last result.
Considered and rejected on the card (2026-11-17): reading Home Assistant's Prometheus export (depends on Home
Assistant, and its climates export no temperature series).

## TapHome API (measured 2026-11-18)

- Base `http://<core>/api/TapHomeApi/v1`, header `Authorization: TapHome <token>`. Wrong token: HTTP 401.
- `GET discovery` → `{"devices": [{"deviceId", "type", "usage"?, "name", "description", "zone"?, "category"?,
  "supportedValues": [{"valueTypeId", "valueTypeName", "readOnly"}]}], "timestamp"}`.
- `GET getAllDevicesValues` → `{"devices": [{"deviceId", "values": [{"value", "valueTypeId", "valueTypeName"}]}],
  "timestamp"}`. A value can be the string `"NaN"`. Humidity is a 0–1 fraction. `usage`, `zone` can be missing/null.
- Each token has its own device list and `deviceId` numbering.

## Exporter

- Package `taphome_exporter/`: `api.py` (two GETs with `urllib`, timeout 10 s, no new HTTP dependency),
  `exporter.py` (State, `poll_once`, collector, `main`). Run: `python -m taphome_exporter.exporter`.
- Env: `TAPHOME_URL` (base URL, required), `TAPHOME_TOKEN` (required), `TAPHOME_INTERVAL` (default 30),
  `TAPHOME_EXPORTER_PORT` (default 9878). Missing required env: exit with a one-line message.
- A background thread fetches discovery and all values every interval; the collector formats the last state only.
- Metrics:
  - `taphome_up` (1/0), `taphome_last_success_timestamp_seconds`, `taphome_read_duration_seconds`,
    `taphome_read_errors_total`, `taphome_devices` (count in discovery).
  - One gauge per value type, labels `device_id`, `name`, `zone`, `type`, `usage` (from discovery; `""` when
    missing). Names: `RealTemperature` → `taphome_temperature_celsius`, `Humidity` → `taphome_humidity_ratio`,
    `TemperatureSetPoint` → `taphome_setpoint_celsius`; every other type → `taphome_` + snake_case of the
    name (`SwitchState` → `taphome_switch_state`, `ReedContact` → `taphome_reed_contact`, `DeviceStatus` →
    `taphome_device_status`, a future `BlindsLevel` → `taphome_blinds_level`) with no unit guessed.
  - Non-numeric values (`"NaN"`, strings, null) are skipped. While polls fail no value gauges are exported.
- One log line per failed poll (exception type and message, never the token).

## Image, chart, release

As Flexit: `python:3.12-slim`, UID 65534, read-only root FS; `mise` tasks `test`, `exporter`, `image:build`,
`image:push` (version from `Chart.yaml`). Chart `helm/taphome-exporter`: `taphome.url` required; token from an
existing Secret (`taphome.tokenSecret.name` required, `.key` default `api-token`), passed as env `TAPHOME_TOKEN`;
`enableServiceLinks: false`; Service port 9878 `metrics`; toggles `serviceMonitor`, `prometheusRule`, `dashboard`
(labels `grafana_dashboard: "1"`, annotation `grafana_folder: Home`). Chart version = appVersion = image tag.

## Alerts (group `taphome`, all `severity: warning`)

- `TapHomeUnreachable`: `taphome_up == 0` for 15m.
- `TapHomeStale`: `time() - taphome_last_success_timestamp_seconds > 600 and taphome_up == 1` for 5m (a down Core is Unreachable).
- `TapHomeContactOpen`: `taphome_reed_contact{usage!="MotionSensor"} == 1` for 30m (default; motion sensors are
  reed-type devices but pulse, so they are excluded).
- `TapHomeRoomTemperature`: `taphome_temperature_celsius{usage="AirTemperature"}` outside 17–28 for 30m
  (defaults; tune per room after a week). Thermostats are excluded by the usage filter (they repeat the sensors).
- `TapHomeHumidityHigh`: `taphome_humidity_ratio{usage="AirTemperature"} > 0.7` for 1h.
  Thresholds are chart values (`alerts.*`), defaults as above.

## Dashboards (in the chart, `helm/taphome-exporter/dashboards/`, one ConfigMap each)

- `taphome-climate.json`, uid `taphome-climate`, "TapHome climate": now-row (temperature and humidity per
  room), temperatures over time, humidity over time, thermostat setpoint vs actual, heating and cooling valves as
  state timelines.
- `taphome-lights.json`, uid `taphome-lights`, "TapHome lights": lights on/off timeline, dimmer level set.
- `taphome-security.json`, uid `taphome-security`, "TapHome security": security contacts (state timeline),
  devices with `device_status != 0`, exporter health (up, last poll age, poll duration, errors).
- Titles carry no "Home / " prefix: the Grafana folder (annotation `grafana_folder: Home`) already says it
  (owner, 2026-11-18; lights and security were split into two dashboards the same day).
- Datasource uid `prometheus`, not editable; panels use `{{name}}` / `{{zone}}` legends so new devices appear.

## homenet_2.0 side (after the repo is on GitHub and tagged)

`kubernetes/apps/taphome-exporter/` Application (wave 60, namespace `taphome`, restricted Pod Security, pinned tag),
values (`taphome.url: http://10.3.252.3/api/TapHomeApi/v1` from `inventory/site.yaml`, all toggles on),
an ExternalSecret from OpenBao `kv/taphome-exporter` property `api-token`; register it as the Flexit exporter was
(`scripts/test_apps.py` WAVES, runbook section in `docs/runbooks/home-assistant.md` or its own).

## Left to the builder

Panel geometry and colours; module internals as long as a scrape never blocks; README wording.
