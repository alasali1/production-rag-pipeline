# Troubleshooting and Error Codes

Keel error codes have the form `KEEL-NXXX`, where the first digit indicates the category.

| Range | Category |
|-------|----------|
| 1xxx | Authentication and CLI |
| 2xxx | Deployments and rollouts |
| 3xxx | Configuration and secrets |
| 4xxx | Quotas and resources |
| 5xxx | Platform internal errors |
| 6xxx | Observability |
| 9xxx | Security policy |

## Error Code Reference

### 1xxx – Authentication and CLI

| Code | Message | Resolution |
|------|---------|------------|
| KEEL-1001 | Token expired | Run `keel login` again. Session tokens last 12 hours. |
| KEEL-1002 | Token lacks required role | Ask a project owner to grant the role, e.g. `deployer`. |
| KEEL-1003 | CLI version unsupported | Upgrade to 4.5.0 or newer: `keel update`. |
| KEEL-1004 | Service token revoked | Create a new token with `keel tokens create`. |

### 2xxx – Deployments

| Code | Message | Resolution |
|------|---------|------------|
| KEEL-2101 | Image build failed | Check build output with `keel builds logs <build-id>`. |
| KEEL-2102 | Image not found in registry | Ensure the digest was pushed to `registry.keel.halyard.io`. |
| KEEL-2201 | Release not validated in staging | The same digest must run healthily in `staging` for 30 minutes first. |
| KEEL-2202 | Missing prod approval | A reviewer from the owning team must approve with `keel approve <rollout-id>`. |
| KEEL-2301 | Healthcheck failing | The new release did not pass its healthcheck within 120 seconds. See below. |
| KEEL-2302 | Automated rollback triggered | Canary breached error-rate, latency, or restart criteria. Run `keel rollout describe`. |
| KEEL-2304 | Deploy freeze active | Wait for the freeze to end or request a `--freeze-exception`. |
| KEEL-2305 | Release process failed | The migration/`release` process exited non-zero. Check `keel logs --process release`. |

### 3xxx – Configuration

| Code | Message | Resolution |
|------|---------|------------|
| KEEL-3101 | Invalid secret name | Names must match `^[A-Z][A-Z0-9_]{1,63}$`. |
| KEEL-3102 | Reserved variable name | Do not override `PORT` or `KEEL_*` variables. |
| KEEL-3103 | Secret too large | Maximum is 64 KiB. Store larger blobs in object storage. |

### 4xxx – Quotas and Resources

| Code | Message | Resolution |
|------|---------|------------|
| KEEL-4001 | Project CPU quota exceeded | Reduce replicas/resources or `keel quota request`. |
| KEEL-4002 | Project memory quota exceeded | As above. |
| KEEL-4010 | Replica resource limit exceeded | Per-replica maximum is 4 vCPU / 8 GiB. |
| KEEL-4020 | Addon plan not allowed in env | e.g. `dev-1` cannot be used in `prod`. |

### 5xxx – Platform Errors

Any `KEEL-5xxx` indicates a problem inside Keel. Retry once after 30 seconds; if it persists, post the `request_id` in `#keel-help` or, during an incident, page Platform on-call.

### 6xxx – Observability

| Code | Message | Resolution |
|------|---------|------------|
| KEEL-6004 | Metric series limit exceeded | Remove high-cardinality labels; limit is 50,000 active series per project. |
| KEEL-6101 | Alert missing runbook | Add a `runbook` URL to the alert definition. |

### 9xxx – Security Policy

| Code | Message | Resolution |
|------|---------|------------|
| KEEL-9001 | Credential detected in push | Remove the credential from git history and **rotate it immediately**. |
| KEEL-9002 | Image has critical vulnerabilities | Rebuild on the latest base image; see the scan report in the console. |
| KEEL-9003 | Unsigned image | Production images must be built by Keel or signed with cosign. |

## Common Problems

### My deploy fails with KEEL-2301 (healthcheck failing)

1. Confirm the app listens on `$PORT`, not a hard-coded port.
2. Confirm the healthcheck path returns HTTP 200 within the configured `timeout` (default 2 seconds).
3. Check startup time. If the app needs more than 120 seconds to become healthy, set `healthcheck.startup_grace: 300s`.
4. Run `keel logs --env <env> --release <id>` to see crash output.

### Container is OOMKilled

`keel status` shows `OOMKilled` restarts. Increase `resources.memory` or investigate leaks with the memory panel on the default dashboard. JVM services should set `-XX:MaxRAMPercentage=75`.

### "too many connections" from PostgreSQL

Make sure the app connects through `DATABASE_URL` (pooled), not `DATABASE_DIRECT_URL`. Reduce per-replica pool size so that `replicas × pool_size` stays below the plan's max connections.

### Requests return 429 from the edge

The Keel edge applies a default limit of 1,000 requests/second per service per region. Raise it with `edge.rate_limit` in `keel.yaml` (maximum 10,000 without Platform review).

### Logs are missing

Confirm the app writes to stdout, not a file. In `prod`, `debug` lines are dropped unless `KEEL_LOG_DEBUG=true`. Lines over 32 KiB are truncated.
