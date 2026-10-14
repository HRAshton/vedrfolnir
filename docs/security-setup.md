# Repository security setup

[← Documentation](README.md) · [Security policy](../SECURITY.md)

Tracked configuration is not active enforcement. After the CI stack merges:

1. Verify green `master` runs and the exact stable job names. In GitHub Settings
   → Rules → Rulesets, inspect all active rulesets targeting `master`, including
   inherited rules, and check Settings → Branches for classic branch protections.
   The existing `master` ruleset identified during review requires one approval
   and approval of the most recent reviewable push, with no bypass permissions.
   [GitHub combines overlapping rules using the stricter requirements](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/about-rulesets#about-rule-layering):
   importing `.github/rulesets/master.json` does not override that ruleset.

   For the template's solo-maintainer policy, preferably update the existing
   ruleset in place: set required approvals to zero and disable approval of the
   most recent reviewable push. Apply the template's required checks while
   retaining required PRs, resolved threads, deletion/force-push restrictions,
   and no bypass permissions. Alternatively, import and activate a replacement,
   verify it covers those protections, then disable or remove the superseded
   ruleset; keep protection active throughout the transition. Reconcile any
   other overlapping protections with their owner. Keeping the stricter approval
   policy is also valid, but merges still need an eligible independent reviewer.

   Require the separate `PR title and commit policy` Actions check in the
   **active** `Reviewed master` ruleset, in addition to the existing Python,
   tooling and dependency checks. The tracked template includes this check,
   but does not update GitHub settings. Confirm the check is enforced after a
   title edit; only this short workflow should run, while full CI runs for
   new commits and other source events.

   Verify the effective rules on a real PR targeting `master`: the intended
   approval policy, every required check, and resolved threads must apply. Do
   not infer enforcement from the template or one ruleset's settings alone.
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
