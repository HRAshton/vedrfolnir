# Automatic release eligibility

The weekly assessment is **read-only** and does not create version tags,
publish PyPI distributions or modify release environments. Each run discovers
the highest versioned release tag reachable from `master` (numeric version
ordering, including RC numbers). It compares `master` to that baseline; the
workflow never hardcodes a particular RC tag. A manual `--from-tag` assertion
must match this latest reachable release and cannot override the safety check.

A missing reachable release, a newer tag on an unrelated history, or a latest
**final** release fails closed with an ineligible assessment. Once `rc2` has
been released on `master`, the next assessment uses `rc2` as its baseline
and may propose `rc3`. The planner never reuses an existing tag, even one
created on a different branch. An assessment may remain ineligible after a
historical release when unrelated changes have not yet received a manually
reviewed release; automatic retagging does not bypass that review.

The assessment also classifies changed paths as `no-distribution` (docs,
tests, tooling), `image-only` (Dockerfile, pinned dependencies, bundled inputs)
or `package-and-image` (Python source and package metadata). The result is
informational and must not authorize publication by itself.

Only changes to `requirements/container-constraints.txt` are candidates for
future unattended publication. Source, security controls, Dockerfile, workflow,
metadata and mixed changes require human review. A qualifying change still
requires the same full tests, installed-wheel checks, container security review,
published-digest smoke and provenance checks as a reviewed release.

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
`.github/workflows/release.yml`, which remains unchanged.
