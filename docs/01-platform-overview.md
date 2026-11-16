# Keel Platform Overview

Keel is the internal developer platform used by Halyard Systems engineering teams to build, deploy, and operate backend services. It provides a single CLI (`keel`), a REST API, a managed container runtime, managed PostgreSQL, secrets management, and a built-in observability stack. Every production service at Halyard has run on Keel since the platform reached general availability in version 2.0.

This page describes the overall architecture. For hands-on setup, see [Getting Started](02-getting-started.md).

## Goals

Keel was designed around four goals:

1. **Paved road by default.** A new service should reach production in under one day using only the defaults.
2. **Safe deploys.** Every production deploy is progressive (canary) and automatically rolled back on SLO regression.
3. **Least privilege.** Services and humans receive only the permissions they need, scoped per environment.
4. **Observable everything.** Logs, metrics, and traces are collected without code changes for any service that uses the Keel base images.

## Core Concepts

| Concept       | Description |
|---------------|-------------|
| **Project**   | A logical grouping of services owned by one team. Billing, access control, and quotas are applied at the project level. |
| **Service**   | A deployable unit defined by a `keel.yaml` manifest. A service has one container image and one or more processes (`web`, `worker`, `cron`). |
| **Environment** | One of `dev`, `staging`, or `prod`. Each environment is isolated at the network and IAM level. |
| **Release**   | An immutable snapshot of an image digest plus resolved configuration. Releases are numbered sequentially per service (e.g. `r142`). |
| **Rollout**   | The process of moving traffic from one release to another. Production rollouts use canary stages. |
| **Addon**     | A managed resource attached to a service, such as `postgres`, `redis`, or `queue`. |

## Architecture

Keel is composed of a control plane and a data plane.

### Control plane

The control plane runs in a dedicated account and consists of:

- **keel-api** – the REST API that the CLI and web console call. All mutations go through this API and are written to an append-only audit log.
- **keel-scheduler** – reconciles desired state (releases, replica counts, autoscaling policies) with the data plane.
- **keel-rollout** – drives canary rollouts and evaluates rollback criteria.
- **keel-vault** – a wrapper around HashiCorp Vault that stores service secrets, encrypted with per-project keys.

### Data plane

The data plane consists of Kubernetes clusters in two regions:

- **us-east-2** (primary) – hosts all three environments.
- **eu-west-1** (secondary) – hosts `staging` and `prod` only, used for EU data residency and as the disaster recovery region.

Each service runs in its own Kubernetes namespace named `<project>-<service>-<env>`. Teams do not have direct `kubectl` access in `prod`; all changes go through Keel. Read-only `kubectl` access is available in `dev` and `staging`.

### Networking

Public traffic enters through the Keel edge (an Envoy-based gateway) which terminates TLS, enforces rate limits, and routes by hostname. Default hostnames follow the pattern `<service>.<project>.<env>.keel.halyard.io`. Service-to-service traffic inside a cluster uses mutual TLS issued by the platform; certificates rotate every 24 hours.

## Supported Runtimes

Keel provides maintained base images. Using a base image is strongly recommended because it enables automatic instrumentation.

| Base image              | Language version | Support ends |
|-------------------------|------------------|--------------|
| `keel/python:3.12`      | Python 3.12      | 2028-10      |
| `keel/python:3.11`      | Python 3.11      | 2027-10      |
| `keel/node:22`          | Node.js 22 LTS   | 2027-04      |
| `keel/go:1.25`          | Go 1.25          | 2027-08      |
| `keel/go:1.23`          | Go 1.23          | 2026-08 (ended; migrate to `keel/go:1.25`) |
| `keel/jvm:21`           | Java 21          | 2029-09      |

Custom images are allowed but must expose a health endpoint and emit logs as JSON to stdout to be compatible with the observability stack.

## Quotas

Default per-project quotas in `prod`:

- 64 vCPU and 256 GiB memory total
- 20 services
- 5 PostgreSQL addons
- 200 secrets

Quota increases are requested with `keel quota request` and are reviewed by the Platform team within two business days.

## Ownership and Support

The Platform team owns Keel. Support is available in the `#keel-help` Slack channel during business hours (09:00–18:00 US Eastern). Production incidents caused by the platform are handled by the Platform on-call rotation; see [Incident Response](07-incident-response.md).
