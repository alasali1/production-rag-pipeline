# Security and Access Control

Keel enforces least privilege for both humans and workloads. This page covers roles, service tokens, break-glass access, network policy, and image security.

## Roles

Roles are granted per project and per environment.

| Role | Capabilities |
|------|--------------|
| `viewer` | Read services, releases, logs, metrics, and dashboards. |
| `developer` | `viewer` + deploy to `dev`, manage `dev` config and secrets, `keel exec` in `dev`/`staging`. |
| `deployer` | `developer` + deploy and roll back in `staging` and `prod`, approve rollouts. |
| `secrets-admin` | Set and unset secrets in `staging` and `prod`. |
| `db-reader` | Read-only `keel psql` in `prod`. |
| `owner` | All of the above, plus manage membership, tokens, and service deletion. |

Grant a role:

```bash
keel access grant --project payments --env prod --user jdoe --role deployer
```

Role grants in `prod` expire after **180 days** and must be renewed. Access is reviewed quarterly by each project owner; unreviewed grants are revoked automatically 14 days after the review deadline.

## Service Tokens

Use service tokens for CI/CD and automation; never use a personal session token in a pipeline.

```bash
keel tokens create --project payments --name github-actions \
  --roles deployer --envs staging,prod --ttl 90d
```

- Maximum TTL is **365 days**; default is 90 days.
- The token value is shown once at creation and cannot be retrieved later.
- Tokens can be restricted to source IP ranges with `--allow-cidr`.
- List and revoke with `keel tokens list` and `keel tokens revoke <token-id>`.

For GitHub Actions, prefer **OIDC federation** over long-lived tokens: `keel tokens federate --github-repo halyard/invoice-api --branch main` lets workflows exchange their OIDC token for a 15-minute Keel token.

## Break-Glass Access

Write access to production databases and `keel exec` into `prod` containers are not normally available. In an emergency:

```bash
keel breakglass request --env prod --reason "INC-2291: corrupted invoice rows" --duration 2h
```

- Requires approval from a second engineer holding `owner` on the project.
- Maximum duration is **4 hours**.
- All commands executed during the session are recorded and attached to the audit log.
- Every break-glass use is reviewed by the Security team within 2 business days.

## Network Policy

By default, services can:

- Receive traffic from the Keel edge (if `public: true`) and from services in the **same project**.
- Make outbound HTTPS calls to the internet through an egress gateway with fixed IPs (published at `go/keel-egress-ips`).

Calls to services in **other projects** must be explicitly allowed by the receiving service:

```yaml
network:
  allow_from:
    - project: checkout
      service: cart-api
```

All in-cluster traffic uses mTLS; certificates rotate every 24 hours.

## Image Security

- All images are scanned on push. Images with **critical** CVEs that have a fix available are blocked from `prod` (`KEEL-9002`).
- High-severity CVEs generate a ticket with a **30-day** remediation deadline.
- Production images must be built by Keel or signed with cosign (`KEEL-9003`).
- Keel base images are rebuilt weekly with the latest OS patches. Redeploying picks up the new base automatically.
- Containers run as a non-root user with a read-only root filesystem. Writable scratch space is available at `/tmp` (limited to 1 GiB).

## Audit Log

Every control plane mutation—deploys, config changes, secret changes (names only), role grants, token creation, break-glass sessions—is written to an immutable audit log retained for **7 years**. Project owners can query it:

```bash
keel audit --project payments --since 7d --actor jdoe
```

## Reporting Security Issues

Report suspected vulnerabilities or credential exposure to `#security-incidents` or `security@halyard.io`. Leaked credentials should be treated as a **SEV1** incident (see [Incident Response](07-incident-response.md)): rotate first, then investigate.
