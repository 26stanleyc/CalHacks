# CRS decision (M0)

**on hold**

Checked 2026-10-24 08:45 UTC by `scripts/probe_integrations.py`.

Reason: CRS_API_KEY is empty at M0.

Per spec 11, with no CRS sandbox access we keep the simulated identity profiles
(clean / thin-file / flagged) and do not submit to the CRS track. `Principal.crs_profile_id`
stays `None`. The step-up stays a fixed persona pass rate rather than a live
re-verification. Nothing else in the build depends on this.

To revisit: set `CRS_API_KEY` and `CRS_BASE_URL` in `.env` and run the probe again.
