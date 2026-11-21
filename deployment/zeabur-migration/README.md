# Local preparation for leaving Zeabur

This package stages the existing OpenClaw coach and portfolio bot for a Linux VPS.
Docker is **not needed** to prepare or inspect the files. Python 3.11+ is needed
for the offline preparation script. Docker Engine and Compose 2.30+ are needed
only when building/running the deployment. Docker Desktop on Windows is optional.

## What preparation does

- Reads the two volume backups, OpenClaw environment export and two project YAMLs.
- Restores files to a fresh private directory without executing archived scripts.
- Preserves the deployed skill and its pinned requirements, not an assumed repo version.
- Adds Python 3.12 from the official Python image; pinned Garmin 0.3.7 requires
  Python 3.12 even though the OpenClaw base image has system Python 3.11.
- Keeps the devbox files for recovery, but does not deploy a devbox service.
- Disables Telegram, all other configured channels, cron and heartbeats in the
  rehearsal configuration. Portfolio is excluded by default using a Compose profile.
- Saves original config, jobs and resolved environment separately under `secrets/`.
- Removes Zeabur's public-domain setting and unused self-referencing AI Hub key.
- Replaces Zeabur's insecure dashboard flags with normal device authentication.
- Uses a fresh rehearsal gateway token and publishes the dashboard on host localhost only.
- Omits archive links rather than following them on Windows. The observed links
  are npm cache launchers and obsolete Zeabur backup/restore helpers; reinstall
  an npm tool if one of those cached launchers is needed later.

The exported YAML reader accepts only this exporter's single-line scalar layout.
Other layouts are rejected; the tool is not a general YAML parser.

## Prepare on this computer

Run `prepare.py` with these arguments (quote full paths containing spaces):

```text
python prepare.py --openclaw-backup <coach.tar> --devbox-backup <devbox.tar> --openclaw-env <openclaw.env.txt> --openclaw-project <openclaw-project.yaml> --portfolio-project <portfolio-monitor-project.yaml> --output <fresh-private-directory>
```

The prepared bundle for this repository belongs under `.migration-private/`,
which is ignored by Git. Never commit source exports, backups or the generated
bundle. The bundle contains passwords, provider tokens and conversation history.
File modes restrict access on Linux; Windows access follows the folder's NTFS ACL.
Preparation refuses to overwrite an existing bundle. Do not delete older backups.

Offline recheck:

```text
python prepare.py --validate <bundle-directory>
```

## Rehearse on a new VPS

First install Docker Engine and the Compose plugin from Docker's official Ubuntu
repository. Confirm Compose is at least 2.30 (needed for literal env file values).
Use SSH keys, restrict SSH ingress, and keep port 18789 closed in the provider
firewall. Do not expose Docker's socket to either bot.

Transfer the **generated bundle** directly over SSH/SCP, preserving its directory
layout, to a private directory owned by your SSH user. No upload to a public repo
or file-sharing service is needed. Inside that directory on Linux:

```sh
sh prepare-host.sh
docker compose version
docker compose --env-file runtime.env -f compose.json config --quiet
docker compose --env-file runtime.env -f compose.json build openclaw
docker compose --env-file runtime.env -f compose.json up -d openclaw
docker compose --env-file runtime.env -f compose.json ps
docker compose --env-file runtime.env -f compose.json exec openclaw openclaw doctor
docker compose --env-file runtime.env -f compose.json exec openclaw python -c 'import garminconnect, curl_cffi, dotenv; print("Python dependencies ready")'
docker compose --env-file runtime.env -f compose.json exec -T openclaw python - < offline_smoke.py
```

`doctor` can change the staged configuration; recheck that channels and schedules
remain disabled after using it. Avoid dumping `docker compose config` without
`--quiet`: resolved output can include secrets.

The offline smoke check mocks provider authentication and network fetches, then
generates reports from restored caches. It does not test live provider access.
Do not refresh Strava/Garmin credentials from two copies without reviewing the
provider's token behavior; token rotation can affect the still-running old coach.

From Windows, create a tunnel (replace the placeholder with the SSH user@server):

```sh
ssh -N -L 18789:127.0.0.1:18789 USER@SERVER
```

Visit `http://localhost:18789`. Retrieve the rehearsal token privately from
`secrets/openclaw.env`; complete dashboard device pairing if requested. Keep that
terminal open while using the dashboard. The dashboard is not publicly exposed.

## Required runtime checks before cutover

1. Pull/build both images; check registry access. Resolve portfolio's `latest`
   to a digest and record the deployed OpenClaw digest before production.
2. Verify memory, sessions, athlete profile and saved races.
3. Test Strava and Garmin **separately**, including token refresh from the VPS.
   Provider reads can update token/cache files in the rehearsal copy.
4. Run `weekly_report.py` and `marathon_status.py --json` from the restored skill.
   Confirm Python execution works without the devbox.
5. Restart the container and reboot the VPS; confirm state survives both.
6. Inspect the portfolio image/source for local-file writes. Its Zeabur export
   declares no volume, which does not prove it has no local state.
7. Stop the rehearsal gateway and make a fresh application backup. Restore it
   into another directory and verify it; archive readability alone is insufficient.

## Production cutover is a separate step

Do not enable the `cutover` profile during rehearsal: it starts the real portfolio
bot. No automatic production activation is included in this preparation package.

Before activation, stop the old bots and take fresh consistent volume backups.
Prepare a new bundle from those backups to avoid losing intervening conversations
or rolling back refreshed tokens. Rotate affected provider/bot secrets privately
as appropriate for Zeabur's incident; put new secrets only on the new deployment.
Restore the intended channels and jobs from the saved originals while retaining
the localhost-only dashboard, normal device authentication and new gateway token.
Then start one bot at a time and test Telegram plus a scheduled notification.
Check cron timezones and job schedules before enabling reminders.

Keep the stopped Zeabur services until verification is complete. Rollback needs
the latest data and credentials; simply starting an old snapshot can lose newer
messages or token updates. Schedule encrypted independent application backups,
test restoration, and document updates before retiring Zeabur.

## Limits of local preparation

Without Docker, the image build, Compose runtime validation, container startup,
memory requirements, device pairing and provider connectivity remain untested.
The private bundle is a prepared rehearsal, not a verified production deployment.

References: [OpenClaw migration](https://docs.openclaw.ai/install/migrating),
[Docker Ubuntu installation](https://docs.docker.com/engine/install/ubuntu/),
[Compose env files](https://docs.docker.com/reference/compose-file/services/#env_file).
