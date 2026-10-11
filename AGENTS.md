# Repository Guidance

## Branching strategy

- This repository uses **release-branch integration**. Create Story branches from the verified active `release/vX.Y.Z` and target Story PRs there; `main` changes only after the release readiness gate.
- Follow the [canonical branching and delivery policy](https://github.com/SpencerRWood/codex-config/blob/main/docs/branching-policy.md), available through the installed codex-config source path. Verify the strategy, release association, fetched base and PR target before implementation and record them in Story evidence. Story completion does not authorize publication or deployment; retain existing review, CI, security and rollback requirements.

- Application source and Dagster definitions live here; infrastructure owns deployment.
- Use Wood Tools for Story, repository validation, and delivery operations.
- Runtime secrets arrive through infrastructure's protected Infisical environment.
- Repository policy is non-secret TOML; unknown or missing policy fails closed.
- R1 covers Renovate automerge failures only.
- Preserve runtime_smoke_job as a read-only, secret-free infrastructure check.
- Do not implement rollback exceptions, Codex quota parsing, or Telegram transport here.
- Incident state and provenance are owned here by Story #481. Capacity integration,
  approval transport, recovery actions, and repair execution remain later Stories.
- Persist transitions and audit evidence atomically; external artifacts are projections.
