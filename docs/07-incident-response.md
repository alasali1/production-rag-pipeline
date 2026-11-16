# Incident Response

This runbook describes how Halyard engineering responds to production incidents on services running on Keel.

## Severity Levels

| Severity | Definition | Examples | Response target | Comms cadence |
|----------|------------|----------|-----------------|---------------|
| **SEV1** | Critical customer-facing outage or data loss/exposure | Checkout down; database corruption; credentials leaked | Acknowledge in 5 min; Incident Commander assigned in 15 min | Every 30 min |
| **SEV2** | Major degradation of a key feature, or SEV1 risk | 20% of API requests failing; one region down with failover working | Acknowledge in 15 min | Every 60 min |
| **SEV3** | Minor degradation with a workaround | Slow report generation; one non-critical background job failing | Next business day | Daily |
| **SEV4** | Cosmetic or internal-only issue | Dashboard misconfigured | Normal backlog | None |

When in doubt, declare the higher severity. Downgrading later is cheap; under-reacting is not.

## Declaring an Incident

Anyone can declare an incident:

```
/incident declare sev2 "Invoice API returning 503 in us-east-2"
```

This Slack command creates a dedicated channel (`#inc-YYYYMMDD-<slug>`), pages the on-call for the affected service, and opens an incident record.

## Roles

- **Incident Commander (IC):** owns coordination and decisions. Does not debug. Required for SEV1 and SEV2.
- **Operations Lead:** performs and directs technical mitigation.
- **Communications Lead:** posts updates to `#status` and, for SEV1, coordinates with Customer Support to update the public status page.
- **Scribe:** records a timeline in the incident channel.

For SEV3 and SEV4 one person may hold all roles.

## Response Process

1. **Acknowledge** the page and join the incident channel.
2. **Assess impact:** which customers, regions, and features are affected? Check the service dashboard and `keel status --env prod`.
3. **Mitigate first, diagnose later.** Preferred mitigations, in order:
   1. Roll back the most recent release: `keel rollback --env prod`. Rollbacks are permitted during deploy freezes.
   2. Disable the offending feature flag at `flags.halyard.io`.
   3. Scale out: `keel scale --env prod web=10`.
   4. Fail over to `eu-west-1` (SEV1 only, requires Platform on-call): `keel region failover --project <p> --to eu-west-1`.
4. **Communicate** at the cadence for the severity level.
5. **Resolve** once customer impact has ended and metrics have been stable for 30 minutes.

## Platform Escalation

If the problem appears to be in Keel itself (control plane errors, multiple unrelated services failing, `KEEL-5xxx` errors), page the Platform on-call:

```
/incident page platform
```

Platform on-call commits to acknowledging within **10 minutes**, 24/7.

## Regional Failover

Failover from `us-east-2` to `eu-west-1` shifts traffic at the edge within about **3 minutes**. PostgreSQL addons with cross-region replicas promote the replica; the recovery point objective (RPO) is **5 minutes** and the recovery time objective (RTO) is **30 minutes**. Services without a cross-region replica cannot fail over and remain unavailable until the primary region recovers.

## Postmortems

A blameless postmortem is **required for every SEV1 and SEV2** and must be published within **5 business days** of resolution. Use the template at `go/postmortem-template`. It must include:

- Summary and customer impact (duration, number of affected customers, revenue impact if known)
- Timeline in UTC
- Root cause and contributing factors
- What went well / what went poorly
- Action items, each with an owner and due date, tracked in Jira with the label `postmortem`

Postmortems are reviewed in the weekly Reliability Review meeting on Wednesdays.
