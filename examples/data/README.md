# Historical ingestion fixture

This small fixture exercises normalized source ingestion and historical fallback.
It intentionally has little live activity. Keep it separate from the onboarding
CSV lifecycle example in [quickstart/csv](../quickstart/csv/README.md), which has
completed/running/queued jobs and a declared pool. Neither establishes real ETA
accuracy. Use a separate SQLite database/volume when switching fixture sources.
