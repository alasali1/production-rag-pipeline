# Deployments and Environments

Keel has three environments—`dev`, `staging`, and `prod`—and enforces a promotion path between them. This page explains how releases move through environments and how production rollouts protect users.

## Environment Rules

| Environment | Who can deploy | Approval required | Canary | Data |
|-------------|----------------|-------------------|--------|------|
| `dev`       | Any project member | No | No | Synthetic only |
| `staging`   | Project members with `deployer` role | No | Optional | Anonymized copy of prod, refreshed weekly on Sunday |
| `prod`      | Project members with `deployer` role | Yes, one reviewer from the owning team | Yes (mandatory) | Real customer data |

A release can only be deployed to `prod` if the **same image digest** has been running healthily in `staging` for at least **30 minutes**. Attempting to skip staging returns error `KEEL-2201`.

## Promotion

Instead of rebuilding, promote an existing release:

```bash
keel promote --from staging --to prod --release r142
```

Promotion reuses the image digest and re-resolves configuration for the target environment. Environment-specific config (for example, database URLs) is never copied between environments.

## Production Rollouts

Every production rollout proceeds through canary stages:

| Stage | Traffic to new release | Bake time |
|-------|------------------------|-----------|
| 1     | 5%                     | 10 minutes |
| 2     | 25%                    | 10 minutes |
| 3     | 50%                    | 10 minutes |
| 4     | 100%                   | — |

A standard rollout therefore takes about 30 minutes. During each bake period `keel-rollout` compares the canary against the baseline release using these criteria:

- **Error rate:** canary 5xx rate must not exceed baseline by more than 1 percentage point, or exceed 2% absolute.
- **Latency:** canary p99 latency must not exceed baseline p99 by more than 25%.
- **Crash loops:** no replica may restart more than 3 times.

If any criterion fails, the rollout is **automatically rolled back** and the deployer is notified in Slack. The failure reason is visible with `keel rollout describe <rollout-id>`.

### Manual controls

```bash
keel rollout pause   <rollout-id>   # Hold at the current stage
keel rollout resume  <rollout-id>
keel rollout abort   <rollout-id>   # Roll back immediately
keel rollout promote <rollout-id>   # Skip remaining bake time (requires on-call approval)
```

Skipping bake time is reserved for emergency fixes during an active incident and is recorded in the audit log.

## Deploy Freezes

Production deploys are blocked during freeze windows:

- **Weekly freeze:** Fridays 16:00 to Mondays 09:00 US Eastern.
- **Holiday freeze:** announced by the Platform team; typically the last two weeks of December.

During a freeze, deploys fail with `KEEL-2304`. An exception can be requested with `keel deploy --env prod --freeze-exception "<reason>"`, which requires approval from an engineering manager and the current Platform on-call.

## Rollbacks

```bash
keel rollback --env prod            # Roll back to the previous release
keel rollback --env prod --to r139  # Roll back to a specific release
```

Rollbacks skip canary stages and complete in about 2 minutes. Rollbacks are allowed during freeze windows. Keel retains the last **50 releases** per service per environment; older releases cannot be rolled back to.

Rollbacks do not revert database migrations. See [Database Operations](08-database-operations.md) for the expand/contract migration pattern that keeps rollbacks safe.

## Autoscaling

Horizontal autoscaling is configured in `keel.yaml`:

```yaml
processes:
  web:
    autoscaling:
      min: 3
      max: 20
      target_cpu: 65
```

In `prod`, `min` must be at least 2 for `web` processes so that a single node failure does not cause downtime. Scale-up decisions are evaluated every 15 seconds; scale-down waits for 5 minutes of sustained low usage.

## Multi-Region Deploys

Services that set `regions: [us-east-2, eu-west-1]` are deployed to both regions. Rollouts proceed in `us-east-2` first; `eu-west-1` begins only after `us-east-2` reaches 100% successfully.
