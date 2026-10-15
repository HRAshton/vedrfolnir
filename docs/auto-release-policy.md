# Automatic release eligibility

The weekly assessment is **read-only** and does not create version tags,
publish PyPI distributions or modify release environments. It compares
`master` to the selected prerelease tag and accepts only the reviewed
Linux/amd64 container dependency constraints file as a candidate for
fully unattended publication.

This policy deliberately rejects source, security controls, Dockerfile,
workflow, metadata and mixed changes. A qualifying change still requires
the same full tests, installed-wheel checks, container security review,
published-digest smoke, and provenance checks as a reviewed release.

## Conditions before publication can be automated

1. Merge a verified container lock and its CI drift gate.
2. Confirm that CI has built and qualified **that exact master SHA**.
3. Create a new immutable `vX.Y.ZrcN` candidate and version-bump commit
   through protected PR machinery; never rewrite an already published version.
4. Introduce a separate publishing identity and trusted PyPI environment
   for eligible maintenance, with no required human approval.
5. Keep the current reviewed release route for everything outside the allowlist.
6. Test injected failures, partial registry publication and retries before
   enabling any tag-creation or publishing token.

The planner is intentionally separate from the privileged
`.github/workflows/release.yml`, which remains unchanged in this draft.
