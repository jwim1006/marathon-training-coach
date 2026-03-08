---
name: marathon-training-coach
description: |
  AI running coach that prevents injuries by monitoring your Strava training load daily.
  Detects dangerous mileage spikes, intensity imbalances, and recovery gaps using evidence-based
  sports science (80/20 rule, acute:chronic workload ratio), then outputs structured JSON
  for the AI agent to interpret and deliver smart coaching advice.

  Marathon-specific: manages multiple upcoming races, determines your training phase
  (base/build/peak/taper) on a 16-week plan, assesses race readiness with pace estimates,
  long run analysis, and taper detection.

  Use when:
  - "Am I overtraining?" — Analyze weekly mileage and intensity for injury risk
  - "Check my training load" — Run a daily analysis of your Strava activities
  - "How's my marathon prep going?" — Assess readiness for your next race
  - "Add a race" — Register an upcoming marathon with target time and training start date
  - "What phase am I in?" — Determine base/build/peak/taper based on weeks to race
  - "Is my running mileage safe?" — Calculate acute:chronic workload ratio (ACWR)
  - "Generate a weekly report" — 4-week trends, ACWR, intensity distribution
  - Monitoring heart rate to ensure easy days are actually easy (80/20 compliance)
  - Tracking recovery gaps and consistency streaks
  - Estimating marathon finish time from recent Z3/Z4 paces

  Scripts output structured JSON for the AI agent to interpret. The agent reads the JSON
  and provides personalized, conversational coaching advice to the runner.

  Security: No hardcoded secrets, input validation, log redaction, secure token storage
  (XDG, 0600 permissions), rate limiting, 30s request timeouts, activity caching.
homepage: https://developers.strava.com/docs/reference/
metadata: {"clawdbot":{"emoji":"🏃","tags":["fitness","strava","running","injury-prevention","training","alerts","discord","slack","telegram","health","marathon","overtraining","recovery","80-20-rule","heart-rate","coaching","endurance","race-readiness","taper","ACWR"],"requires":{"env":["STRAVA_CLIENT_ID","STRAVA_CLIENT_SECRET"]}}}
---

# Marathon Training Coach

Evidence-based AI training partner that catches injury risk before you feel it — and coaches you through marathon prep.

## Why This Matters

Most running injuries follow the same pattern: too much, too soon. Nielsen et al. (2014) found that runners who increase weekly distance by more than 30% have significantly higher injury rates. By the time you feel pain, the damage is weeks old.

This coach watches your Strava data daily and alerts you **before** problems become injuries — so you stay consistent instead of sidelined.

Built on the **80/20 polarized training model** (Seiler, 2010; Stoggl & Sperlich, 2014) — the same approach used by elite endurance coaches to build durable athletes who train smarter, not just harder.

## What You Get

### Daily Monitoring (`coach_check.py`)
- **ACWR Monitoring** — Tracks your acute:chronic workload ratio (Gabbett, 2016). ACWR > 1.5 = high injury risk
- **Acute Load Alerts** — Weekly mileage up 30%+? You'll know before your knees do
- **80/20 Intensity Checks** — VT1-anchored HR zones detect too many hard days
- **Recovery Nudges** — Extended gaps that might affect your training adaptations
- **Consistency Streaks** — Milestone celebrations at 7, 14, 30, 60, 100 days
- **Marathon Phase Alignment** — Warns if your training doesn't match your current phase (e.g., not tapering when you should be)

### Weekly Reports (`weekly_report.py`)
- 4-week volume trends with week-over-week comparisons
- ACWR with risk zone classification (undertraining / sweet spot / caution / high risk)
- Intensity distribution (easy/hard split) vs 80/20 target with zone breakdown
- Marathon countdown with phase and plan week

### Marathon Race Management (`marathon_config.py`)
- Store multiple upcoming races with target times, distances, and training start dates
- CLI commands: `set`, `get`, `list`, `remove`
- Target pace calculation from finish time
- Training week tracking (weeks into plan, weeks remaining)

### Race Readiness Assessment (`marathon_status.py`)
- **Training Phase** — Auto-detects base/build/peak/taper on a 16-week plan
- **Long Run Analysis** — Tracks all long runs (15km+), top 5 by distance, recent frequency
- **Weekly Volume Trends** — 4-week breakdown with averages
- **Race Pace Estimation** — Extrapolates marathon finish from Z3 pace and Z4 threshold pace (Daniels formula)
- **Taper Detection** — Identifies declining volume patterns
- **Phase-Specific Recommendations** — Actionable advice for the current training week

## Interpreting Script Output

**CRITICAL: Always read the JSON values directly. Never recalculate metrics yourself — use the exact numbers from the script output.**

### TSB in Context of Training Phase

TSB (Training Stress Balance) must be interpreted relative to the training phase. Negative TSB is not always bad:

| Phase | Expected TSB | What It Means |
|-------|-------------|---------------|
| **Base** | -5 to -15 | Moderate fatigue from building volume |
| **Build** | -15 to -30 | Higher fatigue as intensity increases — normal and productive |
| **Peak** | -20 to -40 | Highest fatigue of the cycle — this is expected and necessary |
| **Taper** | Rising toward +5 to +15 | Fatigue should be dropping as volume decreases |
| **Race day** | +5 to +15 | Fresh but fit — the goal of tapering |

**Do NOT tell an athlete in peak phase to cut volume 40-50% just because TSB is negative.** During peak, negative TSB means training is working. Only recommend backing off if:
- TSB is extreme (below -40 in peak, below -20 in base)
- The athlete reports feeling terrible (sleep issues, persistent soreness, no motivation)
- ACWR is also in the high-risk zone (> 1.5)
- It has been more than 3 weeks since the last recovery week

The alerts from `coach_check.py` are already phase-aware — present them as-is without adding your own interpretation.

### Race Pace Estimates

The script provides `estimated_finish_time` — use this value directly. Do not recalculate from pace values.

## Athlete Setup

At the start of every session, check if the athlete is configured:

### Step 1: Athlete Config (Required)
```bash
python3 scripts/athlete_config.py get
```
- If empty, **prompt the user** for max HR and VT1 HR — these are required for all analysis:
  ```bash
  python3 scripts/athlete_config.py set --max-hr 201 --vt1-hr 175
  ```
- Optionally collect more: `--years-running`, `--peak-weekly-km`, `--long-run-day`, `--rest-days`, `--hours-per-week`, `--race-prs`, `--injury-history`
- All zone calculations, TSS, and 80/20 checks depend on these HR values being correct.

### Step 2: Athlete Context (Coaching Profile)
Check: does `~/.config/marathon-training-coach/athlete_context.md` exist?
- **YES** → Read it, use as primary coaching context. Don't re-run scripts unnecessarily.
- **NO** → Build it conversationally:
  1. Run `coach_check.py`, `weekly_report.py`, `marathon_status.py --json` to gather data
  2. Ask about foundation, goals, schedule, coaching preferences
  3. Write the file using the template in `references/athlete-context-template.md`
  4. Update at milestones — don't regenerate from scratch

This file replaces re-running all scripts every session. One 2-3k token file provides full coaching context.

## Post-Workout Check-In

When the athlete tells you about a workout (e.g., "just did my long run", "tempo felt hard today"):

1. Ask up to 5 follow-up questions:
   - How did the workout feel overall? (1-10 scale)
   - What were the key challenges or highlights?
   - Did you stick to the planned structure?
   - How were energy, hydration, and mental focus?
   - What would you change next time?
2. Save the note using the script:
   ```bash
   python3 scripts/workout_notes.py add \
     --date 2026-03-08 \
     --type "long run" \
     --feel 7 \
     --summary "25km, felt strong through 20km, legs heavy last 5km" \
     --notes "Energy good, pushed too hard on hills"
   ```
3. Before giving training advice, check for patterns:
   ```bash
   python3 scripts/workout_notes.py patterns
   ```
   This returns pattern analysis after 5+ check-ins (e.g., trending fatigue, consistently hard tempos).
4. Keep check-ins to 5-7 turns (hard cap: 10)

The athlete initiates this — don't prompt unprompted. But if they mention a workout in passing, offer to do a quick check-in.

See `references/training-principles.md` for RPE scale and wellness monitoring.

## Quick Start

### 1. Connect Strava

```bash
# Set your Strava API credentials (required)
export STRAVA_CLIENT_ID=your_id
export STRAVA_CLIENT_SECRET=your_secret

# Or use a .env file (see .env.example)

# Authenticate (opens browser for OAuth)
python3 scripts/auth.py
```

Tokens are stored in `~/.config/marathon-training-coach/strava_tokens.json` with 0600 permissions.

### 2. Add a Race

```bash
# Register your marathon
python3 scripts/marathon_config.py set \
  --race-name "Taipei Marathon" \
  --race-date 2026-10-18 \
  --target-time 3:30:00 \
  --start-date 2026-06-28

# Add another race
python3 scripts/marathon_config.py set \
  --race-name "Fuji Marathon" \
  --race-date 2026-12-06 \
  --target-time 3:25:00

# List all races
python3 scripts/marathon_config.py list

# Check next upcoming race
python3 scripts/marathon_config.py get --next
```

Race configs stored in `~/.config/marathon-training-coach/marathons.json`.

### 3. Run

```bash
# Daily training check (outputs JSON with alerts)
python3 scripts/coach_check.py

# Weekly summary report (outputs JSON with trends)
python3 scripts/weekly_report.py

# Marathon readiness assessment
python3 scripts/marathon_status.py

# Machine-readable JSON output
python3 scripts/marathon_status.py --json

# Assess a specific race
python3 scripts/marathon_status.py --race-name "Fuji Marathon"
```

All scripts output structured JSON for the AI agent to interpret and deliver coaching advice.

### 4. Optional: Schedule with Cron

```json
{
  "name": "Training Coach - Daily Check",
  "schedule": {"kind": "every", "everyMs": 86400000},
  "command": "python3 scripts/coach_check.py"
}
```

## Configuration

### Athlete Config (Required)

HR thresholds and athlete data are stored in `~/.config/marathon-training-coach/athlete_config.json` via `athlete_config.py`. MAX_HR and VT1_HR are **required** — all zone calculations, TSS, and 80/20 checks depend on them.

```bash
# Required: set HR thresholds
python3 scripts/athlete_config.py set --max-hr 201 --vt1-hr 175

# Optional: add background info
python3 scripts/athlete_config.py set --years-running 3 --peak-weekly-km 85
python3 scripts/athlete_config.py set --long-run-day saturday --rest-days monday,friday

# View current config
python3 scripts/athlete_config.py get
```

### Environment Variables

Strava credentials and training thresholds are set via `.env` file:

```bash
# Strava (required)
STRAVA_CLIENT_ID=your_id
STRAVA_CLIENT_SECRET=your_secret

# Training thresholds (optional - sensible defaults)
MAX_WEEKLY_MILEAGE_JUMP=30      # 5-100%, default: 30
MAX_HARD_DAY_PERCENTAGE=25      # 5-100%, default: 25
PLANNED_REST_DAYS=2             # 0-7, default: 2

# Debug logging
VERBOSE=false
```

### HR Zones (VT1-Anchored 5-Zone Model)

| Zone | Range | Description |
|------|-------|-------------|
| Z1 | < 65% max HR | Recovery |
| Z2 | 65% max HR to VT1 | Aerobic |
| Z3 | VT1 to VT1 + 38% remaining | Tempo / Marathon Pace |
| Z4 | VT1 + 38% to VT1 + 77% remaining | Threshold |
| Z5 | > VT1 + 77% remaining | VO2max / Max |

## Security Features

### Credential Handling
- **No hardcoded secrets** — All credentials via environment variables or `.env`
- **Secure token storage** — Tokens saved with 0600 permissions
- **XDG compliance** — Config stored in `~/.config/marathon-training-coach/`
- **Token auto-refresh** — Expired tokens refreshed automatically via Strava OAuth

### Input Validation
- **Date format validation** — ISO8601 / YYYY-MM-DD checking
- **Numeric range validation** — All thresholds bounded (min/max)
- **Activity validation** — API responses validated before processing
- **Time format validation** — H:MM:SS format for target times

### Data Protection
- **Log redaction** — Sensitive data (tokens, webhooks) masked in logs
- **Secure temp files** — Proper permissions on state and cache files
- **Activity caching** — Local cache with deduplication reduces API calls
- **Rate limiting** — Max 1 alert per hour per type

### Network Security
- **HTTPS only** — All API calls use TLS
- **Timeout handling** — 30-second timeouts on all requests
- **Retry logic** — 3 attempts with exponential backoff
- **Certificate validation** — Standard SSL verification

## Example Output

### coach_check.py (Daily)

```json
{
  "weekly_km": 42.3,
  "acwr": 1.15,
  "alerts": [
    {
      "type": "load_spike",
      "severity": "medium",
      "message": "Weekly mileage up 35% (31.3 -> 42.3 km). ACWR: 1.15.",
      "recommendation": "Consider an easy week or cut next week's mileage by 20%."
    },
    {
      "type": "marathon_alignment",
      "severity": "medium",
      "message": "Taipei Marathon is 85 days away (Peak phase). No long run (>= 15km) in last 2 weeks.",
      "recommendation": "Schedule a long run this weekend with the last portion at marathon pace (Z3)."
    }
  ],
  "checks_run": ["load", "intensity", "recovery", "streak", "marathon"]
}
```

### marathon_status.py (Race Readiness)

```json
{
  "race": {
    "name": "Taipei Marathon",
    "date": "2026-10-18",
    "target_time": "3:30:00",
    "target_pace": "4:58/km",
    "days_to_race": 85,
    "weeks_to_race": 12.1
  },
  "training_phase": {
    "phase": "build",
    "label": "Build",
    "plan_week": 5,
    "description": "Threshold work + race simulations, long run progression"
  },
  "long_run_analysis": {
    "total_long_runs": 6,
    "recent_long_runs": 2,
    "longest": {"distance_km": 25.3, "duration_min": 145, "pace_min_km": "5:44"}
  },
  "race_pace_readiness": {
    "marathon_pace_estimate": {
      "pace_min_km": "5:02",
      "estimated_finish_time": "3:33:15",
      "based_on_runs": 4
    }
  },
  "taper_detection": {"is_tapering": false},
  "recommendations": [
    "Add threshold (Z4) sessions and race simulations.",
    "Pace is close: Z3 pace projects 3:33:15 vs 3:30:00 target."
  ]
}
```

## Training Philosophy (Evidence-Based)

1. **Polarized Training** — 80% easy, 20% hard (Seiler & Kjerland, 2006; Stoggl & Sperlich, 2014)
2. **ACWR Sweet Spot** — Keep acute:chronic workload ratio between 0.8-1.3 (Gabbett, 2016)
3. **Progressive Overload** — Gradual increases; >30% weekly spikes raise injury risk (Nielsen et al., 2014)
4. **Consistency > Intensity** — Frequency drives mitochondrial and capillary adaptation (Holloszy & Coyle, 1984)
5. **Strength Training** — Reduces sports injuries by 68% and overuse injuries by ~50% (Lauersen et al., 2014)

See `references/training-principles.md` for the full guide with 30+ scientific references.

## Reference Files

Read these as needed — not all at once. Progressive discovery keeps token usage low.

| File | When to Read |
|------|-------------|
| `references/athlete-context-template.md` | Building a new athlete profile |
| `references/training-principles.md` | Explaining training science to athlete |
| `references/periodization.md` | Designing training blocks, adjusting load |
| `references/plans/README.md` | Athlete asks for a training plan |
| `references/race-day-execution.md` | Within 7 days of race |

## Plan Personalization Workflow

When an athlete asks for a training plan:

1. Read `athlete_context.md` (know the runner)
2. Read `references/plans/README.md` (pick closest plan)
3. Read the selected plan file
4. Adjust for athlete's zones, schedule, fitness, and foundation
5. Discuss and validate with athlete before prescribing

## Files

- `scripts/auth.py` — Strava OAuth setup (tokens stored in XDG config dir)
- `scripts/utils.py` — Shared utilities: HR zones, Strava API, activity caching, TSS/CTL/ATL/TSB, config loading
- `scripts/athlete_config.py` — CLI to manage athlete HR thresholds and profile (set/get/remove)
- `scripts/coach_check.py` — Daily training analysis: load spikes, 80/20, recovery, streaks, marathon alignment, TSB fatigue
- `scripts/weekly_report.py` — Weekly summary: 4-week trends, ACWR, intensity distribution, TSS, CTL/ATL/TSB
- `scripts/marathon_config.py` — CLI to manage upcoming races (set/get/list/remove)
- `scripts/marathon_status.py` — Race readiness assessment: phase, long runs, pace estimates, taper, strengths/limiters
- `scripts/workout_notes.py` — Persist and analyze post-workout check-in notes (add/list/patterns)
- `references/training-principles.md` — Evidence-based injury prevention guide
- `references/athlete-context-template.md` — Template for building persistent athlete profiles
- `references/periodization.md` — Loading patterns, recovery weeks, adaptation timelines
- `references/race-day-execution.md` — Pacing, nutrition, hydration, caffeine for race day
- `references/plans/README.md` — Index of available training plans
- `references/plans/sub3-16week.md` — Sub-3 marathon plan (16 weeks)
- `references/plans/sub330-16week.md` — Sub-3:30 marathon plan (16 weeks)
- `references/plans/sub4-16week.md` — Sub-4 marathon plan (16 weeks)
- `references/plans/beginner-16week.md` — Beginner marathon plan (16 weeks)

## Rate Limits

- Strava allows 100 req/15 min, 1000/day
- Activity caching minimizes API calls (only fetches new activities since last cache)
- Daily checks use ~1-2 API calls per run
