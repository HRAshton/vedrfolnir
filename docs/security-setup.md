# Repository security setup

[← Documentation](README.md) · [Security policy](../SECURITY.md)

Tracked configuration is not active enforcement. After the CI stack merges:

1. Verify green `master` runs and the exact stable job names before importing
   `.github/rulesets/master.json` in GitHub Settings → Rules → Rulesets.
   It requires PRs and successful checks, blocks deletion/force pushes, and
   permits zero approvals for a solo maintainer while requiring resolved threads.
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
   and Docker; routine updates are grouped, majors need dashboard approval,
   and automerge is disabled. Do not enable a second dependency-update bot.
5. Verify dependency review and Python audit runs on a real dependency PR.
   Review advisories and license changes; record narrow expiring exceptions.

These settings, Renovate installation and a verified private disclosure route
remain owner setup gates. This PR does not claim they are enabled merely because
JSON/workflow files exist. Container vulnerability scanning accompanies the
image qualification gate. Treat CodeQL findings as engineering issues, not a score.
