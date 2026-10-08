# automerge-repair

Python/Dagster foundation for bounded recovery from Renovate automerge failures
in infrastructure and homelab. Foundation jobs:
`runtime_smoke_job` proves Dagster execution without application secrets;
`foundation_health_job` validates the configured repository policy and emits
non-secret structured logs and run metadata.

Story #481 adds `incident_ingest_job`, the explicit incident-state and failure
provenance entrypoint. It verifies normalized evidence, persists the incident and
concurrency claims transactionally, and emits structured Dagster audit metadata.
See [the incident contract](docs/incident-contract.md) for the trusted collector
boundary, release lineage, storage configuration, state transitions, and retry
semantics. It performs no recovery actions.

Created from the tracked files of SpencerRWood/template-python-dagster main
at 2b7d901. The package is renamed to `automerge_repair`; example assets and
schedules are replaced with foundation checks. Application logic stays here;
infrastructure owns the code-location container, secrets, network and workspace.

## Local development

Use Python 3.14 and uv:

```sh
uv sync --frozen --group dev
uv run pre-commit install
AUTOMERGE_REPAIR_POLICY_FILE=config/policy.toml uv run dagster api grpc -m automerge_repair.dagster.definitions -h 127.0.0.1 -p 4000
wood repo validate --json
```

The committed policy allows infrastructure and homelab in dev, with repair
disabled and rollback set to notification-only. Missing policy, unknown fields,
repositories outside R1, malformed types, and an unsupported runtime environment
fail closed. Policy contains no credentials. The 300-second verification window
is an explicit initial policy value and performs no recovery action in S1.

The Dagster 1.13.16 / dagster-postgres 0.29.16 / SQLAlchemy 2.0.52 runtime family
matches the current infrastructure main contract. The lockfile also explicitly
selects psycopg2-binary. Review these versions together.

## Build and development deployment

```sh
docker build -t automerge-repair .
docker run --rm -e DAGSTER_GRPC_PORT=4000 -p 4000:4000 automerge-repair
```

Centralized validation and integrated semantic/GHCR release use the existing
SpencerRWood/workflows `validate.yml@v3` and `release-container.yml@v3` contracts.
The container release opts into the PostgreSQL-backed runtime gate through
`.github/release.toml`; image validation precedes GitHub Release publication.
The deployed image must be a real `vX.Y.Z@sha256:...` reference.

Infrastructure S1 changes live on `feature/op-479-automerge-repair-runtime`.
The `automerge_repair` Ansible role runs the code location on the private
Postgres and proxy networks. The workspace registers it only when
`services.automerge_repair` is enabled. The initial manifest keeps it disabled;
there is no released image yet.

After review, deliver the service to the configured GitHub remote and record
the verified release image in infrastructure dev.
Provision the scoped Infisical dev path `/automerge-repair` with the shared
`DAGSTER_POSTGRES_PASSWORD` before enabling the component. Infrastructure's
resolver owns the protected runtime file; the application never receives a
machine identity credential. The role registers that scoped path in the existing
runtime refresh metadata when enabled. No production service is selected.

The normal reviewed infrastructure release/deploy path applies the role and
checks gRPC readiness. Run `foundation_health_job` through the shared Dagster
instance to prove the mounted policy and PostgreSQL-backed run/event storage.
Do not claim deployment from a passing local test or a published image alone.

## R1 boundaries

Story #481 owns authoritative incident state and Renovate failure provenance.
Later stories integrate the aligned deterministic rollback contract,
wood-events-service Telegram interactions, codex-runtime
capacity and reset handling, approval gates and isolated repair execution.
Capacity deferral consumes no repair attempt. PR merge stays a human gate and
redeployment requires explicit approval. This foundation invokes no recovery,
Codex, Telegram or OpenProject APIs. Read-only GitHub provenance enrichment is
available through an injected authenticated reader; monitoring is not enabled.

Planning: OpenProject Story 479 / AR-R1-S1, Project 8, Initiative 477, Epic 478,
Version 23 (R1).

## Shared Codex capacity implementation contract

Story 470 / AD-R1-01 supplies the `codex-runtime` library. Later capacity
integration must pin its delivered release/revision and consume
`codex_runtime.check_availability(AppServerProvider())`; provider response parsing
and five-hour/weekly reset normalization belong exclusively to that library.
Use the same authenticated Codex runtime/account for capacity reads and execution.

Proceed only when the normalized `available` field is true. Otherwise persist
incident deferral. When `provider_resets_at` is known, compute `next_retry_at`
as that latest blocking provider reset plus five minutes. When it is unknown,
remain deferred under consumer failure policy; never infer recovery or invent
a reset. Recheck capacity before execution after the boundary. Capacity
deferral consumes no repair attempt and does not bypass policy or approval gates.

The authoritative contract and offline consumer examples live in
[codex-runtime](https://github.com/SpencerRWood/codex-runtime/blob/main/docs/consumer-contract.md).
This foundation adds no quota checker or capacity execution; runtime integration,
durable state, retry scheduling and attempt accounting remain later Story work.
