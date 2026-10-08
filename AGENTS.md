# Repository Guidance

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
