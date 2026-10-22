# Repository security setup

[← Documentation](README.md) · [Security policy](../SECURITY.md)

Tracked configuration is not active enforcement. After the CI stack merges:

1. Verify green `master` runs and exact required status-check names. In GitHub
   Settings → Rules → Rulesets, inspect every active ruleset targeting `master`
   and any classic branch protections. Rulesets are layered; satisfying one
   does not exempt a PR from the others.
   
   The active `master-all` ruleset requires a PR with zero approving reviews,
   passing required checks, an up-to-date branch, resolved review threads,
   and squash merging. The separate `master-except-renovate` ruleset normally
   requires an approving review, but permits the Renovate GitHub App to bypass
   *that ruleset*. It does not bypass `master-all`.
   See [GitHub's ruleset layering documentation](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/about-rulesets#about-rule-layering).
   
   Confirm that `PR title and commit policy`, Python validation, tooling,
   dependency review, Python audit, and container qualification are required
   checks, and verify their real behavior on a PR. Tracked workflow and
   ruleset templates are not substitutes for active repository settings.
2. Import `.github/rulesets/immutable-release-tags.json`: approved creation of
   `v*` tags stays possible, while update/deletion is blocked. Test enforcement
   on a disposable tag before enabling publication. Review who can create tags.
3. Set default Actions workflow permissions to read-only; enable dependency
   graph, Code Scanning and private vulnerability reporting. Verify the private
   report form in `SECURITY.md`. CodeQL default setup is already enabled: an
   advanced-workflow upload was rejected by GitHub for that conflict. Keep
   default setup as the single SAST configuration; verify its Security results
   and actual check name before adding it as a required check.
4. Install/enable the hosted Renovate GitHub App for this repository and review
   its onboarding dashboard. `renovate.json` covers Python, SHA-pinned Actions
   and Docker. Auto-merge defaults to off; only eligible development-tool patch
   updates and non-major GitHub Actions updates may auto-merge. All require
   GitHub's mandatory checks; major updates require Dependency Dashboard approval
   and manual merge. Runtime libraries and Docker base updates remain manual.
   `tests/test_renovate_policy.py` guards this explicit allowlist. Do not
   enable a second dependency-update bot.
5. Verify dependency review and Python audit runs on a real dependency PR.
   Review advisories and license changes; record narrow expiring exceptions.

These settings, Renovate installation and a verified private disclosure route
remain owner setup gates. This PR does not claim they are enabled merely because
JSON/workflow files exist. Container vulnerability scanning accompanies the
image qualification gate. Treat CodeQL findings as engineering issues, not a score.
