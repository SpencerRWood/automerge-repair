# Incident ingestion and authority (Story #481)

`incident_ingest_job` is an explicit Dagster entrypoint for normalized deployment
or promotion failure evidence. It performs correlation and persistence only.
There is no sensor, rollback, notification transport, approval processing, Codex
invocation, work-item creation, deployment, or health monitoring in this Story.
`runtime_smoke_job` remains read-only and requires no application credentials.

## Trusted evidence boundary

The job accepts `ops.ingest_incident.config.candidate_json`, serialized using
`Candidate.model_dump_json()`. `Candidate` is the version-1 input contract in
`automerge_repair.incidents`. Unknown fields and coercible/malformed values are
rejected before any incident write. Supply normalized facts and GitHub authority
references only; never send tokens, resolved environment files, or raw logs.
Run configuration is visible to Dagster operators, so collectors must enforce
this boundary before submitting a run, even for rejected input.

An authenticated collector must establish the failed deployment/promotion target,
not merely observe that a release workflow failed. Required failure facts are:

- Exact owner/repository, source merge SHA, deployment or promotion identity,
  target environment, immutable release/deployment target, resolved target SHA.
- Deployment/promotion conclusion and the authoritative evidence URL.
- Workflow run ID and attempt; available health evidence URLs are supporting data.

Current infrastructure and homelab `deploy.yml` workflows call the centralized
`deploy-ansible.yml@v1` workflow, use immutable release tags, and write runner-local
deployment metadata. Their target names are `infrastructure-dev` and `homelab`.
A failed workflow conclusion alone does not prove that its deployment failed:
validation, skipped/superseded runs, release failure, and successful rollback must
be distinguished by the producer. Runner metadata and logs are authorities for
deployment outcomes; the GitHub workflow run SHA is not a substitute for resolving
the deployed release tag. Producer integration/monitoring is outside this Story.

If the tag points to a generated release commit rather than the Renovate merge,
include `ReleaseProvenance` linking source SHA, target SHA, target identity,
repository, release authority, and source-run authority. The collector must verify
that lineage from the release workflow/tag evidence. A nearest-commit search,
latest release lookup, or PR-title heuristic is insufficient. Missing/conflicting
lineage fails closed. This job verifies the normalized relationships; it does not
authenticate arbitrary webhook bodies or independently fetch release evidence.

`github.collect_candidate` can enrich the failure with read-only GitHub facts
using an authenticated `GitHubReader`. Its transport owns bounded timeouts/retries,
HTTP status checking, and credentials injected by the approved secret manager.
The collector reads PR associations for the exact source commit, then the PR
detail. It requires exactly one association. Zero, multiple, truncated, malformed,
or unavailable associations are incomplete and lead to human attention.

Both current repository Renovate policies set `platformAutomerge: true` and
use platform squash automerge. Correlation requires a merged PR targeting
`main`, matching the exact source SHA and repository, with both author and merger
`renovate[bot]` of type `Bot`. The collector also reads the bounded GraphQL
automerge timeline. Its last squash enablement must be by Renovate and followed
by one matching merge event, with no intervening disablement or method change.
Truncated history, reversed timestamps, a different actor or commit, and absent
enablement fail closed. GraphQL bot logins omit REST's `[bot]` suffix; the adapter
normalizes that representation only when the independently supplied type is Bot.
The [GitHub event schema](https://docs.github.com/en/graphql/reference/pulls#autosquashenabledevent)
defines the enablement and merge records. A human merge, bot-authored PR alone,
configuration flag, or nonmatching PR cannot prove automatic merge. GitHub PR,
merge, diff, release and failure authority references remain in the incident.

Unknown repositories or targets, health-only events, release-only events,
nonfailures and incomplete provenance are recorded as
`needs_human_intervention`; they never become remediation-eligible. Missing
policy, malformed input, or unavailable persistence fails the Dagster run without
accepting state. Human attention is a durable outcome here; notification transport
belongs to Story #482.

## Persistence and transitions

Set `AUTOMERGE_REPAIR_INCIDENT_DATABASE_URL` explicitly in the worker process.
Deployment should inject a PostgreSQL URL through its protected Infisical runtime
environment. Do not put a credential-bearing URL in policy, run configuration,
committed files, or logs. All workers must use the same database. File-backed
SQLite is supported for offline development/tests; in-memory storage is rejected.
There is no fallback to Dagster run tags, messages, or local process memory.
The tables `automerge_repair_incidents_v1` and `automerge_repair_claims_v1` are
created non-destructively. Future schema changes need an explicit migration.

The SHA-256 incident identity contains owner/repository, source merge SHA,
target environment and deployment identity. Trigger kind, run attempts, delivery
IDs, and supporting health observations do not alter identity. Producers must
reuse a deployment identity across observations/retries of the same target;
distinct deployments use distinct IDs.

One transaction saves the canonical candidate, evidence observations, state,
approval placeholders, policy fingerprint, audit events, and concurrency claims.
The original candidate is retained. Exact repeated evidence is a no-op. New
observations append evidence; optimistic version checks prevent lost updates.
Unique claim keys serialize incidents by repository, merge SHA, and deployment.
The repository claim conservatively permits one active incident per repository,
even across environments. Database uniqueness/version races retry at most three
times; unresolved contention or storage errors fail closed for a later Dagster
retry with the same input.

Transitions are limited to:

```text
detected -> correlated
detected -> needs_human_intervention
correlated -> needs_human_intervention (conflicting evidence/policy)
```

`correlated` means provenance is established; it grants no approval or execution
authority. The repository's `repair_enabled` value is recorded separately and
the committed policy keeps it false. A correlated incident holds its claims
durably across process restarts. Human-attention incidents release their claims
and remain terminal for automated ingestion: later favorable evidence does not
authorize them. New conflicting evidence/policy invalidates a correlated
incident. Later Stories must explicitly define manual continuation and safe claim
release following completed recovery; this Story provides no reset/delete path.

Repair and redeploy approval placeholders start at `not_requested`. Telegram,
GitHub, OpenProject and Codex artifact slots are linked projections, initially
empty. They cannot supply approval or change authoritative workflow state.

## Dagster audit

Each detection, placeholder creation, state transition, and evidence update stores
an audit sequence, stable event ID, UTC timestamp, Dagster actor/run attribution,
old/new state, reason, evidence hash and policy hash. The job projects every saved
event as a structured Dagster asset observation, plus bounded output metadata and
JSON logs. Persistence commits before observations are emitted. A retry replays
the same event IDs, allowing recovery if the worker stops between database commit
and Dagster emission. Consumers deduplicate by `event_id`; the database remains
authoritative and Dagster event records are projections of its audit.

Offline tests cover both repository targets, eligibility, platform-automerge
history, exact lineage,
malformed/truncated/unavailable GitHub reads, deterministic identity, duplicate
workers, new supporting evidence, restart persistence, policy conflicts, terminal
state protection, atomic rollback on database error, and Dagster audit replay.
Run the complete declared checks with `wood repo validate --json`. PostgreSQL
deployment credentials, runtime rollout, and operational monitoring remain outside
this implementation; passing SQLite tests do not attest to a deployed PostgreSQL
instance or runtime health. Supplemental persistence tests can use
`AUTOMERGE_REPAIR_TEST_POSTGRES_URL` with an explicitly supplied disposable
PostgreSQL database. Each test creates an isolated schema; removal of the
disposable database/container owns cleanup. No existing application database is
selected or modified by that test fixture.
