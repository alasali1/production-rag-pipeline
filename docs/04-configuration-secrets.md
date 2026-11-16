# Configuration and Secrets

Keel separates non-sensitive configuration from secrets. Both are injected into containers as environment variables at release time.

## Configuration Precedence

When the same variable is defined in multiple places, Keel resolves it in this order (highest wins):

1. Secrets set with `keel secrets set`
2. Environment-specific config set with `keel config set --env <env>`
3. The `env:` block in `keel.yaml`
4. Platform-injected defaults

Platform-injected variables include `PORT`, `KEEL_ENV`, `KEEL_SERVICE`, `KEEL_PROJECT`, `KEEL_RELEASE`, and `KEEL_REGION`. These names are reserved; attempting to override them returns `KEEL-3102`.

## Plain Configuration

Put defaults that are safe to commit in `keel.yaml`:

```yaml
env:
  LOG_LEVEL: info
  FEATURE_NEW_CHECKOUT: "false"
```

Override per environment:

```bash
keel config set --env staging LOG_LEVEL=debug
keel config list --env staging
keel config unset --env staging LOG_LEVEL
```

Config changes do **not** take effect until the next release. Run `keel restart --env <env>` to create a new release with the same image and updated config.

## Secrets

Secrets are stored in `keel-vault`, encrypted with a per-project key, and are never written to disk on nodes.

```bash
keel secrets set --env prod STRIPE_API_KEY          # prompts for value; not echoed
keel secrets set --env prod DB_PASSWORD --from-file ./pw.txt
keel secrets list --env prod                          # names and last-modified only
keel secrets unset --env prod STRIPE_API_KEY
```

Rules:

- Secret values cannot be read back through the CLI or API. If you lose a value, rotate it.
- Maximum secret size is **64 KiB**.
- Secret names must match `^[A-Z][A-Z0-9_]{1,63}$`.
- Default quota is 200 secrets per project in `prod`.
- Only members with the `secrets-admin` role can set or unset secrets in `prod`.

### Rotation

Keel tracks secret age. Secrets older than **90 days** generate a warning in `keel status`; secrets older than **180 days** raise a ticket for the owning team. To rotate without downtime:

1. Add the new credential at the provider while the old one remains valid.
2. `keel secrets set --env prod NAME` with the new value.
3. `keel restart --env prod` (this goes through the normal canary rollout).
4. Revoke the old credential at the provider once the rollout reaches 100%.

### Addon credentials

Credentials for managed addons (PostgreSQL, Redis) are generated and rotated automatically every 30 days and injected as `DATABASE_URL` and `REDIS_URL`. Do not set these manually.

## Local Development

`keel env pull --env dev > .env` writes `dev` configuration (but **not** secrets) to a local `.env` file. Secrets for `dev` can be pulled with `--include-secrets`; this flag is disabled for `staging` and `prod`.

Make sure `.env` is listed in `.gitignore`. Keel's pre-receive hook rejects pushes containing strings that match known credential formats (AWS keys, Stripe keys, private keys) and reports `KEEL-9001`.

## Feature Flags

Keel does not provide a feature flag service. Use the company-wide flag service (`flags.halyard.io`) for runtime toggles. Environment variables are suitable only for flags that change no more than once per release.
