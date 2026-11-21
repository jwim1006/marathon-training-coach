# Local rehearsal results — 29 October 2026

Tested with Docker Desktop 4.97.0, Docker Engine 29.9.2 and Compose v5.6.1,
using Linux/amd64 containers on Windows/WSL.

Passed:

- Compose configuration validation with secrets suppressed.
- OpenClaw 2026.4.9 image build with official Python 3.12 and the deployed
  coaching skill's pinned direct requirements. Python 3.11 cannot install
  garminconnect 0.3.7; the Dockerfile includes the required Python 3.12 runtime.
- Python dependency imports and `pip check`.
- Restored gateway startup and HTTP 200 from `/healthz`.
- Weekly report and marathon-readiness report generated as JSON from restored
  cached activities, without a devbox, network fetches or token refreshes.
- Gateway health and both cached reports after container restart.
- Seven offline regression tests for staging and rehearsal safeguards.

Observed idle gateway memory was approximately 525 MiB with channels, schedules
and heartbeats disabled. This is not a measurement of active coaching or the
portfolio bot, and is not a total VPS sizing guarantee.

The rehearsal was stopped after testing. Zeabur services and source backups
were not modified; Telegram and the portfolio bot were not started locally.

Remaining before cutover:

- Authenticate Docker to GitHub Container Registry for the portfolio image:
  anonymous access returned HTTP 401. Then resolve its digest and inspect its
  local-state requirements before running it.
- Verify model API access, dashboard device pairing and provider connectivity.
- Test Strava/Garmin from the destination VPS. Review token rotation before
  authenticating a second copy alongside the live coach.
- Verify scheduled delivery, VPS reboot recovery and a fresh backup restore.
- Take final consistent backups and rotate affected secrets before activation.

Observed base-image digests (no credentials):

```text
ghcr.io/openclaw/openclaw:2026.4.9@sha256:d65cc3d5fd0c8b1f752c2f70377843230112250c10e99c3b61769234c217c5db
python:3.12-slim-bookworm@sha256:e697c81897302a4c4333bade000d98038fbb21eb064f8ee1fd42d02ebcd9d337
```
