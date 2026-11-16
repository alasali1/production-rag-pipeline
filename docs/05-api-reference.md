# Keel REST API Reference

The Keel API is the programmatic interface to everything the CLI does. The current version is **v3**. Version v2 was retired on 2026-03-31 and now returns `410 Gone`.

Base URL:

```
https://api.keel.halyard.io/v3
```

## Authentication

All requests require a bearer token:

```
Authorization: Bearer <token>
```

Two token types are accepted:

| Type | Obtained via | Lifetime | Use case |
|------|-------------|----------|----------|
| Session token | `keel login` | 12 hours | Interactive human use |
| Service token | `keel tokens create` | Up to 365 days (default 90) | CI/CD and automation |

Service tokens are scoped to one project and a set of roles. See [Security and Access Control](10-security-access-control.md).

## Rate Limits

| Token type | Sustained limit | Burst |
|------------|-----------------|-------|
| Session token | 600 requests/minute | 100 |
| Service token | 1,200 requests/minute | 200 |

Every response includes:

```
X-RateLimit-Limit: 600
X-RateLimit-Remaining: 587
X-RateLimit-Reset: 1762861200
```

Exceeding the limit returns `429 Too Many Requests` with a `Retry-After` header in seconds. Clients should use exponential backoff with jitter starting at 1 second.

## Pagination

List endpoints use cursor pagination. Pass `limit` (default 50, max 200) and the `next_cursor` from the previous response as `cursor`. When `next_cursor` is `null`, there are no more results.

## Errors

Errors use a consistent envelope:

```json
{
  "error": {
    "code": "KEEL-2201",
    "message": "Release r142 has not run in staging for the required 30 minutes.",
    "request_id": "req_01HZX4K8M2"
  }
}
```

Always include `request_id` when contacting `#keel-help`. Full code list: [Troubleshooting and Error Codes](09-troubleshooting-error-codes.md).

## Endpoints

### Services

| Method | Path | Description |
|--------|------|-------------|
| GET    | `/projects/{project}/services` | List services in a project. |
| GET    | `/projects/{project}/services/{service}` | Get a service and its manifest. |
| DELETE | `/projects/{project}/services/{service}` | Delete a service. Requires `owner` role. Fails if any environment has running replicas. |

### Releases

| Method | Path | Description |
|--------|------|-------------|
| GET    | `/projects/{project}/services/{service}/envs/{env}/releases` | List releases (newest first). |
| POST   | `/projects/{project}/services/{service}/envs/{env}/releases` | Create a release. Body: `{"image_digest": "sha256:...", "description": "..."}` |
| GET    | `/.../releases/{release_id}` | Get release details including resolved config keys. |

### Rollouts

| Method | Path | Description |
|--------|------|-------------|
| POST | `/projects/{project}/services/{service}/envs/{env}/rollouts` | Start a rollout. Body: `{"release_id": "r142"}` |
| GET  | `/rollouts/{rollout_id}` | Get rollout status: `pending`, `in_progress`, `paused`, `succeeded`, `rolled_back`, `aborted`. |
| POST | `/rollouts/{rollout_id}/pause` | Pause at current stage. |
| POST | `/rollouts/{rollout_id}/resume` | Resume. |
| POST | `/rollouts/{rollout_id}/abort` | Abort and roll back. |

### Secrets

| Method | Path | Description |
|--------|------|-------------|
| GET    | `/projects/{project}/envs/{env}/secrets` | List secret names and metadata (never values). |
| PUT    | `/projects/{project}/envs/{env}/secrets/{name}` | Create or replace. Body: `{"value": "..."}` |
| DELETE | `/projects/{project}/envs/{env}/secrets/{name}` | Delete. |

## Example: Deploy from CI

```bash
curl -sS -X POST \
  -H "Authorization: Bearer $KEEL_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"image_digest":"sha256:9f2c...","description":"CI build 8812"}' \
  https://api.keel.halyard.io/v3/projects/payments/services/invoice-api/envs/staging/releases
```

The response contains the new `release_id`, which can then be passed to the rollouts endpoint.

## Idempotency

`POST` endpoints accept an `Idempotency-Key` header. Keys are retained for **24 hours**; replaying a request with the same key returns the original response instead of creating a duplicate release or rollout.

## Webhooks

Projects can register webhooks for `rollout.succeeded`, `rollout.rolled_back`, `release.created`, and `alert.fired`. Payloads are signed with HMAC-SHA256 in the `X-Keel-Signature` header. Failed deliveries are retried up to 8 times over roughly 4 hours with exponential backoff.
