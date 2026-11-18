# TapHome exporter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A read-only Prometheus exporter for the TapHome Core, with image, Helm chart, alerts and two Grafana dashboards, deployed by homenet_2.0.

**Architecture:** Copy of `~/Work/Personal/flexit_nordic_s3` (read its `flexit_nordic/exporter.py`, `helm/flexit-exporter/`, `tests/`, `Dockerfile`, `mise.toml`): a background thread polls, a custom collector formats the last state.

**Tech Stack:** Python 3.12, prometheus_client, urllib (stdlib), pytest, Helm 3, Grafana JSON.

**Spec:** `docs/superpowers/specs/2026-11-18-taphome-exporter-design.md`

## Global Constraints

- Read-only: only `GET discovery` and `GET getAllDevicesValues`. Never log or print the token.
- Env: `TAPHOME_URL`, `TAPHOME_TOKEN` (required), `TAPHOME_INTERVAL` (30), `TAPHOME_EXPORTER_PORT` (9878).
- Metric names and labels exactly as the spec's Exporter section; alert names, exprs and `for` exactly as its Alerts section.
- No real room, device or person names anywhere in the repo (public). Fixtures are synthetic but shaped like the spec's API section (include a `"NaN"` value, a device with no `usage`/`zone`, humidity as a fraction).
- Chart: no CRDs; version == appVersion == image tag, starting `0.1.0`; image `10.34.1.1:30095/taphome-exporter`.
- Comments explain why, dry; mark deliberate shortcuts `ponytail:` as the Flexit repo does.

## Review Focus

1. The Core returns 401 or times out → `taphome_up 0`, no value gauges, error counter +1, scrape still answers.
2. A value of `"NaN"`, a string or null → skipped, never a scrape error.
3. A device in values but missing from discovery → exported with empty name/zone/type/usage labels.
4. A new value type appears (e.g. `BlindsLevel`) → exported as `taphome_blinds_level` without code change.
5. `TAPHOME_EXPORTER_PORT` overridden by Kubernetes service links → `enableServiceLinks: false` in the chart.

---

### Task 1: Exporter, image and repo scaffolding

**Files:** create `taphome_exporter/{__init__,api,exporter}.py`, `tests/test_exporter.py`, `tests/fixtures/{discovery,values}.json`, `requirements.txt` (`prometheus-client`), `requirements-dev.txt` (`pytest`), `mise.toml` (tasks `test`, `exporter`, `image:build`, `image:push` reading `helm/taphome-exporter/Chart.yaml`), `Dockerfile`, `.dockerignore`, `.gitignore`, `.env.example`, `README.md`, `LICENSE` (MIT, Pavol Antalík, 2026).

**Interfaces:**
- `api.fetch(url: str, token: str, endpoint: str, timeout: float = 10) -> dict` (raises on HTTP error / timeout).
- `exporter.State` (fields `discovery`, `values`, `ok`, `last_success`, `duration`, `errors`), `exporter.poll_once(state, url, token, fetch=api.fetch) -> None`, `exporter.TapHomeCollector(state)`, `exporter.metric_name(value_type: str) -> str`, `exporter.main()`.

- [ ] Write failing tests (fake `fetch`, fixtures): good poll exports `taphome_temperature_celsius{device_id="1",name=…,zone=…,type=…,usage="AirTemperature"}`, `taphome_humidity_ratio`, `taphome_setpoint_celsius`, `taphome_switch_state`, `taphome_devices`; `"NaN"` skipped; device missing from discovery gets empty labels; `metric_name("BlindsLevel") == "taphome_blinds_level"`; failed poll (raise `urllib.error.HTTPError` 401) → `taphome_up 0`, no value gauges, `taphome_read_errors_total 1`, log line without the token; before first poll → `taphome_up 0` only (+ errors 0); missing env → `SystemExit`.
- [ ] Run `mise run test` → fails. Implement. Run → passes.
- [ ] `docker build` succeeds (`mise run image:build`), if Docker is available; otherwise note it.
- [ ] Commit `feat: TapHome exporter`.

### Task 2: Helm chart and dashboards

**Files:** `helm/taphome-exporter/{Chart.yaml,values.yaml,.helmignore,templates/{_helpers.tpl,deployment.yaml,service.yaml,servicemonitor.yaml,prometheusrule.yaml,dashboards.yaml},dashboards/{taphome-climate,taphome-house}.json}`, `tests/test_chart.py`. README section on the chart and release flow.

**Interfaces:** values `taphome.url` (required), `taphome.tokenSecret.name` (required), `taphome.tokenSecret.key` (`api-token`), `taphome.interval` (30), `alerts.contactOpenFor` (`30m`), `alerts.roomTempMin` (17), `alerts.roomTempMax` (28), `alerts.humidityMax` (0.7), toggles as Flexit, `dashboard.labels`/`annotations` as Flexit. One ConfigMap per file in `dashboards/`.

- [ ] Failing chart tests modelled on Flexit's `tests/test_chart.py`: render fails without url or secret name; defaults render only Deployment + Service with Flexit's security contexts, `enableServiceLinks: False`, env `TAPHOME_TOKEN` from `secretKeyRef`, port 9878; all toggles → five alerts with the spec's exprs/`for` (default values), two dashboard ConfigMaps with uids `taphome-climate`, `taphome-house`; every PromQL metric name in both dashboards and the rules is one the exporter produces (`taphome_` names from Task 1 or the snake_case rule).
- [ ] Implement chart and dashboards (panels per the spec's Dashboards section). `helm lint` clean. Tests pass.
- [ ] Commit `feat: Helm chart and Grafana dashboards`.

### Task 3: homenet_2.0 deployment (controller runs after the GitHub repo exists, tag v0.1.0 pushed, image pushed)

Separate repo, own worktree, rules in its CLAUDE.md. Files per the spec's homenet section; follow how `kubernetes/apps/flexit-exporter/` and an app with an ExternalSecret (e.g. `kubernetes/apps/home-assistant/`) are wired. `mise run check` must pass.
