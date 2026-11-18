# taphome_exporter

Read-only Prometheus exporter for a TapHome Core. It only calls `GET discovery` and `GET getAllDevicesValues`; it never writes.

A background thread polls the Core every `TAPHOME_INTERVAL` seconds (default 30, minimum 1) and serves `:9878/metrics`
(`TAPHOME_EXPORTER_PORT`). A scrape only formats the last result and never touches the network.

Config: `cp .env.example .env` and set `TAPHOME_URL` (e.g. `http://<core>/api/TapHomeApi/v1`) and `TAPHOME_TOKEN`.
Each token has its own device list and `deviceId` numbering.

| Metric | Meaning |
|---|---|
| `taphome_up` | 1 if the last poll succeeded, else 0 |
| `taphome_last_success_timestamp_seconds` | Unix time of the last successful poll |
| `taphome_read_duration_seconds` | Duration of the last poll |
| `taphome_read_errors_total` | Failed polls |
| `taphome_devices` | Devices in discovery |
| `taphome_temperature_celsius`, `taphome_humidity_ratio` (0-1), `taphome_setpoint_celsius` | From `RealTemperature`, `Humidity`, `TemperatureSetPoint` |
| `taphome_<snake_case_type>` | Every other value type, no unit guessed (`SwitchState` gives `taphome_switch_state`) |

Value gauges carry `device_id`, `name`, `zone`, `type`, `usage` from discovery (empty when missing).
`"NaN"`, string and null values are skipped. While polls fail no value gauges are exported, so a stale value never looks fresh.

```sh
mise run test
mise run exporter                  # local
mise run image:build               # taphome-exporter:dev
docker run -p 9878:9878 --env-file .env taphome-exporter:dev
```

## Helm chart

```sh
helm install taphome helm/taphome-exporter --set taphome.url=http://192.0.2.1/api/TapHomeApi/v1 --set taphome.tokenSecret.name=taphome-token
```

| Value | Default | Meaning |
|---|---|---|
| `image.repository` | `10.34.1.1:30095/taphome-exporter` | Image to run |
| `image.tag` | `""` | Empty means the chart's `appVersion` |
| `taphome.url` | none | Required |
| `taphome.tokenSecret.name`, `.key` | none, `api-token` | Required existing Secret holding the token; the chart creates none |
| `taphome.interval` | `30` | Poll interval in seconds |
| `resources` | 10m CPU, 32Mi request, 96Mi limit | Pod resources |
| `serviceMonitor.enabled`, `.interval` | `false`, `30s` | Prometheus Operator scrape config |
| `prometheusRule.enabled` | `false` | Alerts: unreachable, stale, contact open, room temperature, humidity |
| `alerts.contactOpenFor`, `.roomTempMin`, `.roomTempMax`, `.humidityMax` | `30m`, `17`, `28`, `0.7` | Alert thresholds (humidity is a fraction) |
| `dashboard.enabled`, `.labels`, `.annotations` | `false` | One Grafana dashboard ConfigMap each for climate, lights and security |

The chart installs no CRDs. The ServiceMonitor and PrometheusRule need prometheus-operator, the dashboards need the Grafana sidecar.

## Release

1. Bump `version` and `appVersion` in `helm/taphome-exporter/Chart.yaml` (same value), commit, tag `v<version>`, push.
2. `mise run image:push` builds and pushes `${REGISTRY:-10.34.1.1:30095}/taphome-exporter:<version>`, with the version read from `Chart.yaml`.
3. Point the Argo CD Application at the tag.
