# Getting Started with Keel

This guide walks through installing the `keel` CLI, authenticating, and deploying your first service to the `dev` environment.

## Prerequisites

- A Halyard SSO account that belongs to at least one Keel project
- Docker 24 or newer
- macOS, Linux, or Windows (via WSL2)

## Install the CLI

```bash
# macOS / Linux
curl -fsSL https://get.keel.halyard.io/install.sh | sh

# Verify
keel version
```

The CLI auto-updates once per day. To pin a version (for CI), set `KEEL_CLI_VERSION=4.7.2` and pass `--no-update`. The minimum supported CLI version is **4.5.0**; older versions are rejected by the API with error `KEEL-1003`.

## Authenticate

```bash
keel login
```

This opens a browser window for SSO. On success the CLI stores a session token in `~/.keel/credentials`. Session tokens are valid for **12 hours**. For CI pipelines, use a service token instead (see [Security and Access Control](10-security-access-control.md)).

Check who you are logged in as:

```bash
keel whoami
```

## Create a Service

From the root of your repository:

```bash
keel init --project payments --service invoice-api --runtime python:3.12
```

This creates a `keel.yaml` manifest:

```yaml
service: invoice-api
project: payments
runtime: keel/python:3.12

processes:
  web:
    command: uvicorn app.main:app --host 0.0.0.0 --port $PORT
    port: 8080
    replicas: 2
    healthcheck:
      path: /healthz
      interval: 10s
      timeout: 2s

resources:
  cpu: 500m
  memory: 512Mi
```

### Manifest fields

| Field | Required | Notes |
|-------|----------|-------|
| `service` | yes | Lowercase, 3–40 characters, letters, digits, and hyphens. |
| `project` | yes | Must already exist. |
| `runtime` | yes | A Keel base image or a full image reference. |
| `processes` | yes | At least one process. `web` processes receive the `PORT` environment variable. |
| `resources` | no | Defaults to `cpu: 250m`, `memory: 256Mi`. Maximum per replica is `cpu: 4`, `memory: 8Gi`. |
| `addons` | no | See [Database Operations](08-database-operations.md). |

## Deploy to dev

```bash
keel deploy --env dev
```

The CLI builds the image, pushes it to the Keel registry, creates a new release, and rolls it out. Deploys to `dev` skip canary stages and complete in about 90 seconds.

When the deploy finishes, your service is reachable at:

```
https://invoice-api.payments.dev.keel.halyard.io
```

## Useful Commands

| Command | Purpose |
|---------|---------|
| `keel status --env dev` | Show current release, replicas, and health. |
| `keel logs --env dev -f` | Stream logs. |
| `keel releases --env dev` | List recent releases. |
| `keel rollback --env dev` | Roll back to the previous release. |
| `keel open --env dev` | Open the service in the web console. |
| `keel exec --env dev -- bash` | Open a shell in a running replica (`dev` and `staging` only). |

## Next Steps

- Configure environment variables and secrets: [Configuration and Secrets](04-configuration-secrets.md)
- Promote to staging and production: [Deployments and Environments](03-deployments-environments.md)
- Add dashboards and alerts: [Observability](06-observability.md)
