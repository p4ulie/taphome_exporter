# taphome_exporter

Read-only Prometheus exporter for a TapHome Core. It only calls `GET discovery` and `GET getAllDevicesValues`; it never writes.

A background thread polls the Core every `TAPHOME_INTERVAL` seconds (default 30) and serves `:9878/metrics`
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
