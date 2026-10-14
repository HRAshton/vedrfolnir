# Synthetic replay controls

`jobs.csv` and its companion plan data are the existing staggered-admission
regression oracle used by scheduler-replay tests. Preserve their timestamps and
admission semantics: negative eligibility is useful evidence, not a reason to
delete the fixture. `queue_control.py` generates a separate seeded finite-capacity
positive control. Do not fabricate admission timestamps or present either as a
real scheduler trace. See [timeline replay](../../docs/timeline-replay.md).
