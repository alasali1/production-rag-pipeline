# Observability: Logs, Metrics, Traces, and Alerts

Keel collects telemetry automatically for services built on Keel base images. This page covers what is collected, how long it is kept, and how to define SLOs and alerts.

## Logs

Write logs to **stdout as JSON**, one object per line. Keel parses these fields specially:

| Field | Purpose |
|-------|---------|
| `level` | One of `debug`, `info`, `warn`, `error`, `fatal`. |
| `msg` | Human-readable message. |
| `trace_id` | Links the log line to a distributed trace. |
| `ts` | RFC 3339 timestamp. If absent, ingestion time is used. |

Non-JSON lines are accepted and stored with `level: info` and the raw text in `msg`.

Retention:

| Environment | Hot (searchable) | Archive |
|-------------|------------------|---------|
| `dev`       | 3 days  | none |
| `staging`   | 7 days  | none |
| `prod`      | 30 days | 1 year (restorable on request) |

Lines larger than **32 KiB** are truncated. `debug` logs are dropped in `prod` unless `KEEL_LOG_DEBUG=true` is set, which automatically expires after 4 hours.

Search logs from the CLI:

```bash
keel logs --env prod --since 1h --level error --grep "timeout"
```

## Metrics

Keel exposes Prometheus-compatible metrics. Base images automatically export:

- `http_requests_total{status,route,method}`
- `http_request_duration_seconds` (histogram)
- `process_cpu_seconds_total`, `process_resident_memory_bytes`
- Addon metrics such as `pg_connections_active`

Custom metrics are scraped from `:9090/metrics` every 15 seconds. Metrics are retained at full resolution for 15 days and downsampled to 5-minute resolution for 13 months.

Each project is limited to **50,000 active time series**. Avoid high-cardinality labels such as user IDs or request IDs; series beyond the limit are dropped and `KEEL-6004` is reported in `keel status`.

## Traces

Base images include OpenTelemetry auto-instrumentation for HTTP servers, HTTP clients, PostgreSQL, and Redis. Traces are sampled at **10%** in `prod` and **100%** in `dev` and `staging`. Requests that return a 5xx are always sampled (tail-based). Trace retention is 7 days.

## Dashboards

Every service gets a default dashboard in the web console with request rate, error rate, latency percentiles (p50, p95, p99), saturation, and recent releases overlaid as annotations. Custom dashboards can be defined as code in `.keel/dashboards/*.yaml`.

## SLOs

Define SLOs in `keel.yaml`:

```yaml
slos:
  - name: availability
    objective: 99.9
    window: 30d
    indicator:
      type: ratio
      good: http_requests_total{status!~"5.."}
      total: http_requests_total
  - name: latency
    objective: 99.0
    window: 30d
    indicator:
      type: threshold
      metric: http_request_duration_seconds
      threshold: 0.3
```

A 99.9% availability objective over 30 days allows an **error budget of about 43 minutes** of full downtime. Tier-1 services (those on the customer checkout or payment path) must define an availability SLO of at least 99.9%.

## Alerting

Keel generates multi-window burn-rate alerts from SLOs automatically:

| Alert | Burn rate | Windows | Action |
|-------|-----------|---------|--------|
| Fast burn | 14.4× | 1 hour and 5 minutes | Page on-call |
| Slow burn | 6× | 6 hours and 30 minutes | Page on-call |
| Budget warning | 1× | 3 days | Ticket only |

Custom alerts can be added under `alerts:` in `keel.yaml`. Each alert must specify `severity` (`page` or `ticket`) and a `runbook` URL. Alerts without a runbook are rejected with `KEEL-6101`.

Pages are routed through PagerDuty to the escalation policy configured with `keel oncall set`. If the primary does not acknowledge within **5 minutes**, the page escalates to the secondary; after **15 minutes**, to the team's engineering manager.
