# Database Operations (Managed PostgreSQL)

Keel provides managed PostgreSQL as an addon. This page covers provisioning, connection management, migrations, backups, and restores.

## Provisioning

Add the addon in `keel.yaml`:

```yaml
addons:
  - type: postgres
    name: main
    version: "16"
    plan: standard-2
    cross_region_replica: true   # prod only
```

Supported PostgreSQL major versions are **15**, **16**, and **17**. Version 14 reached end of support on 2026-06-30.

### Plans

| Plan | vCPU | Memory | Storage (max) | Max connections | Allowed envs |
|------|------|--------|---------------|-----------------|--------------|
| `dev-1` | 1 | 2 GiB | 20 GiB | 50 | dev |
| `standard-2` | 2 | 8 GiB | 500 GiB | 200 | staging, prod |
| `standard-4` | 4 | 16 GiB | 1 TiB | 400 | staging, prod |
| `highmem-8` | 8 | 64 GiB | 4 TiB | 800 | prod |

Storage auto-grows in 10% increments when usage exceeds 85%. Plan changes cause a failover of roughly **30 seconds** of write unavailability.

In `prod`, every database runs with a synchronous standby in a second availability zone.

## Connecting

Keel injects `DATABASE_URL` (or `DATABASE_URL_<NAME>` when a service has multiple databases). Credentials rotate every 30 days; applications must read the URL from the environment at startup and must not cache it elsewhere.

All connections go through a PgBouncer pooler in **transaction mode**. Consequences:

- Session-level features (`SET` without `LOCAL`, advisory session locks, `LISTEN/NOTIFY`, prepared statements across transactions) are not supported through the pooler.
- For those cases, use `DATABASE_DIRECT_URL`, which bypasses the pooler and counts against the plan's max connections.

Open an interactive session:

```bash
keel psql --env staging --db main
```

`keel psql` in `prod` opens a **read-only** session and requires the `db-reader` role. Write access in `prod` requires a break-glass request (see [Security and Access Control](10-security-access-control.md)).

## Migrations

Migrations run as a `release` process before traffic shifts to a new release:

```yaml
processes:
  release:
    command: alembic upgrade head
```

If the release process exits non-zero, the rollout is aborted and the previous release keeps serving traffic. Release processes time out after **15 minutes**.

### Expand/contract pattern

Because rollbacks do not revert schema changes, and canaries run old and new code side by side, every migration must be backward compatible with the previous release. Use expand/contract:

1. **Expand:** add new columns/tables as nullable or with defaults. Deploy code that writes to both old and new.
2. **Migrate:** backfill data in batches (no more than 10,000 rows per transaction).
3. **Switch:** deploy code that reads from the new structure.
4. **Contract:** in a later release, drop the old columns.

Never rename or drop a column in the same release that stops using it.

### Locking safety

Keel sets `lock_timeout = 5s` and `statement_timeout = 10min` for release processes. Create indexes with `CREATE INDEX CONCURRENTLY`. Adding a `NOT NULL` constraint on a large table should use `NOT VALID` followed by `VALIDATE CONSTRAINT` in a separate step.

## Backups

| Environment | Point-in-time recovery window | Daily snapshots retained |
|-------------|-------------------------------|--------------------------|
| `dev`       | none | 3 days |
| `staging`   | 3 days | 7 days |
| `prod`      | 14 days | 35 days |

Production snapshots are copied to `eu-west-1` (or `us-east-2` for EU-primary databases) and encrypted with a separate key. Monthly snapshots of `prod` are retained for 1 year for compliance.

## Restores

Restores always create a **new** database; they never overwrite the existing one.

```bash
# Point-in-time restore
keel db restore --env prod --db main --to-time "2026-11-10T14:32:00Z" --name main-restore

# Restore from a snapshot
keel db snapshots --env prod --db main
keel db restore --env prod --db main --snapshot snap_8812 --name main-restore
```

A restore of a 100 GiB database typically completes in **20–40 minutes**. After verifying the restored data, either copy specific rows back or swap the service to the new database with `keel db attach`. Restore drills are required quarterly for tier-1 services.

## Monitoring

Key metrics on the default database dashboard: `pg_connections_active`, replication lag, transaction ID age, cache hit ratio, and slow queries (over 1 second, captured by `pg_stat_statements`). An alert fires automatically when connections exceed 80% of the plan maximum for 10 minutes.
