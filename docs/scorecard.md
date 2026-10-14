# OpenSSF Scorecard monitoring

[← Documentation](README.md) · [Security setup](security-setup.md)

The dedicated Ubuntu job runs weekly, on default-branch pushes and explicit
dispatch. It uses only Scorecard's approved checkout/Scorecard/SARIF/artifact
steps, SHA-pinned Actions, no custom shell, no containers, no top-level env
or write rights. OIDC and SARIF write permissions are confined to that job.
No admin PAT is required with repository rulesets.

After this stack merges, dispatch a run and verify its SARIF in GitHub Security
and authenticated public results at `https://api.scorecard.dev/projects/github.com/HRAshton/vedrfolnir`.
Add a README badge only after the published API result is visible. Publication
has not been verified by merely adding the workflow; that is this PR's exit gate.

Review categories rather than chasing a number: active branch protection,
reviews, CI tests, SAST, dependency updates, pinned build inputs, dangerous
permissions, releases and private vulnerability reporting. Record concrete
false positives and tool visibility limits with evidence. Recheck after the RC
and meaningful infrastructure changes. A 10/10 score is not a release gate,
and a good score does not qualify ETA accuracy or eliminate vulnerabilities.
