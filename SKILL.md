---
name: marathon-training-coach
description: |
  AI running coach dedicated to one goal: a sub-3 marathon. Monitors Strava daily,
  tracks 80/20 intensity balance, long-run progression, training phase, and race-pace
  readiness, then outputs structured JSON for the AI agent to deliver conversational
  coaching advice.

  Use when:
  - "How's my sub-3 prep going?" — Assess readiness for the target race
  - "Check my training" — Run a daily analysis of your Strava activities
  - "Add a race" — Register the goal marathon with target time and training start date
  - "What phase am I in?" — Determine base/build/peak/taper based on weeks to race
  - "Generate a weekly report" — 4-week volume and intensity trends
  - Monitoring heart rate to ensure easy days are actually easy (80/20 compliance)
  - Tracking recovery gaps and consistency streaks
  - Estimating marathon finish time from recent Z3/Z4 paces

  Scripts output structured JSON for the AI agent to interpret and deliver
  personalized, conversational coaching advice to the runner.

  Security: No hardcoded secrets, input validation, log redaction, secure token storage
  (XDG, 0600 permissions), rate limiting, 30s request timeouts, activity caching.
homepage: https://developers.strava.com/docs/reference/
metadata: {"clawdbot":{"emoji":"🏃","tags":["fitness","strava","running","marathon","sub3","training","alerts","80-20-rule","heart-rate","coaching","endurance","race-readiness","taper"],"requires":{"env":["STRAVA_CLIENT_ID","STRAVA_CLIENT_SECRET"]}}}
---

# Marathon Training Coach

Evidence-based AI training partner dedicated to one goal: a **sub-3 marathon**.

Built on the **80/20 polarized training model** (Seiler, 2010; Stoggl & Sperlich, 2014) — the same approach elite endurance coaches use to build athletes who train smarter, not just harder.

## What You Get

### Daily Monitoring (`coach_check.py`)
- **80/20 Intensity Checks** — VT1-anchored HR zones detect too many hard days
- **Recovery Nudges** — Extended gaps that might affect your training adaptations
- **Consistency Streaks** — Milestone celebrations at 7, 14, 30, 60, 100 days
- **Marathon Phase Alignment** — Warns if your training doesn't match your current phase (e.g., not tapering when you should be)

### Weekly Reports (`weekly_report.py`)
- 4-week volume trends with week-over-week comparisons
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

### Race Pace Estimates

The script provides `estimated_finish_time` — use this value directly. Do not recalculate from pace values. The target is **sub-3:00:00** (4:15/km) — flag any projection slower than that and suggest phase-appropriate adjustments.

## Sub-3 Readiness Gate

Before endorsing sub-3 as the target, confirm the athlete meets these markers (from `athlete_config.py` and Strava data):

| Check | Minimum | Red flag |
|-------|---------|----------|
| Current weekly volume | 60+ km/week for 4+ weeks | Under 50 km/week → suggest sub-3:15 or extended base |
| Recent HM time | ≤ 1:25 | Slower than 1:30 → sub-3 is aspirational this cycle |
| Recent 10K time | ≤ 38:00 | Slower than 40:00 → fitness gap too wide for 16 weeks |
| Long run foundation | 25km+ in last 6 weeks | Longest under 20km → extend base |
| Injury-free weeks | 6+ | Any injury in last 4 weeks → delay or gentler plan |

If any red flag fires, surface it explicitly and propose sub-3:15 or an extended base block.

## Sub-3 Phase Playbook

Tailor weekly coaching to the current phase (auto-detected from `marathon_status.py`):

| Phase | Weeks | Focus | Key workouts | Long run | What to flag |
|-------|-------|-------|--------------|----------|--------------|
| **Base** | 1-4 | Aerobic volume, neuromuscular priming | Easy Z2, 2x strides, 1 light VO2, hill sprints | Builds to 2hrs Z2; first MP touch wk 4 | Too much Z3+ too early; missing strides |
| **Build** | 5-8 | Threshold + MP intro | 1 tempo Z3, 1 threshold Z4 (4x10min Z4), 1 race-sim | 2.25-2.5hrs with 30min Z3 finish | 80/20 below 75% easy; no MP in long runs |
| **Peak** | 9-12 | Race-specific, peak volume | 2x25min Z3 or 3x20min Z3, race-sim long runs | 2.5hrs with 40-50min MP; 3+ MP-finish long runs this block | Finish projection >3:05; missing MP long runs |
| **Taper** | 13-16 | Shed fatigue, keep sharpness | Short tempos + strides + MP pickups | Drop: -20%, -40%, -60%, race week minimal | Volume not dropping; too much Z4/Z5 late |

Sub-3 paces: **MP 4:15/km, Threshold ~4:00/km, VO2 ~3:50/km**. Every workout prescription should reference these paces explicitly.

Full plan and readiness details: `references/plans/sub3-16week.md`.

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
- All zone calculations and 80/20 checks depend on these HR values being correct.

### Step 2: Athlete Context (Coaching Profile)
Check: does `~/.config/marathon-training-coach/athlete_context.md` exist?
- **YES** → Read it, use as primary coaching context. Don't re-run scripts unnecessarily.
- **NO** → Build it conversationally:
  1. Run `coach_check.py`, `weekly_report.py`, `marathon_status.py --json` to gather data
  2. Ask about foundation, goals, schedule, coaching preferences
  3. Write the file using the template in `references/athlete-context-template.md`
  4. Update at milestones — don't regenerate from scratch

This file replaces re-running all scripts every session. One 2-3k token file provides full coaching context.

## Workout Lifecycle (Pre → Post → Pattern)

This skill runs a three-stage loop around every key workout. The agent is expected to drive this proactively — the athlete should not have to prompt each step.

### Stage 1: Pre-Workout Planning

When the athlete describes an upcoming key workout ("tomorrow I'm doing 6x800m", "long run Saturday with 40min MP"):

1. Confirm the session fits the current training phase (cross-check with `marathon_status.py --json` output).
2. Specify target paces and HR zones explicitly using sub-3 references (MP 4:15/km, Threshold 4:00/km, VO2 3:50/km).
3. Log a placeholder so the feedback loop closes later:
   ```bash
   python3 scripts/workout_notes.py add \
     --date 2026-04-20 \
     --type "intervals" \
     --summary "PLANNED: 6x800m @ 3:12 w/ 400m jog recovery" \
     --notes "Target HR Z4-Z5 on reps. Rep goal pace 3:12."
   ```
4. Remember the plan in context so Stage 2 can compare intent vs execution.

### Stage 2: Post-Workout Analysis (Objective + Subjective)

When the athlete says they finished ("just did my long run", "tempo is done"), **do both automatically**:

**A) Pull the objective data yourself** (don't wait to be asked):
```bash
# Most recent run
python3 scripts/workout_analysis.py

# Or target the matching type
python3 scripts/workout_analysis.py --type intervals
python3 scripts/workout_analysis.py --type long_run

# Or a specific activity
python3 scripts/workout_analysis.py --activity-id 18027968819
```

The JSON returns:
- **Structured numbers** (`detected_structure`, `reps`, `hr_drift_bpm`, `pace_cv_pct`, `mp_segment_detected`) — these are objective and always accurate. Use them directly.
- **`assessment` bullets** — generic rule-based fallbacks (e.g., "drift +5 bpm → mild"). They do NOT know the planned workout, phase, or how you felt. **Treat them as a sanity check, not the coaching output.** The real interpretation is your job as the agent.

Your interpretation should combine:
1. The raw numbers from the JSON
2. The planned workout (what the athlete told you in Stage 1)
3. The current phase playbook (Base/Build/Peak/Taper expectations)
4. The athlete's subjective feedback (Stage 2B)
5. Accumulated patterns from `workout_notes.py patterns`

Example: HR drift +8 bpm on a peak-phase 6x1km at threshold is *expected and fine*. The same drift on a base-phase easy progression is *a red flag*. The assessment bullet can't tell the difference — you can.

**B) Collect subjective feedback** (keep this short — 2-3 questions, max 5 turns):
- Overall feel (1-10)
- Anything that surprised you (good or bad)
- Fueling / sleep / stress if relevant

**C) Merge both into a rich note and save it**:
```bash
python3 scripts/workout_notes.py add \
  --date 2026-04-20 \
  --type "intervals" \
  --feel 7 \
  --summary "PLANNED 6x800m @ 3:12 | ACTUAL 6 reps avg 3:14, HR drift +7bpm, pace CV 1.8%" \
  --notes "Felt strong first 4 reps, last 2 legs heavy. HR drift suggests rep 1 slightly hot. Sleep 6.5hrs night before."
```

The summary line should **always** combine planned vs actual + the objective numbers from `workout_analysis.py`. The notes field adds subjective context.

**D) Deliver comprehensive feedback** combining both streams:
- What the data shows (pace, HR, drift, structure adherence)
- What the athlete reported (feel, context)
- What it means for the next session (e.g., "your rep 1 was hot and rep 6 paid for it — next time start closer to target")
- Whether this workout moves sub-3 readiness forward (reference the Sub-3 Phase Playbook)

### Stage 3: Cycle-Wide Pattern Review

Before giving training advice or adjusting the plan, check accumulated patterns:
```bash
python3 scripts/workout_notes.py patterns
```

This reveals signals like: "quality sessions trending down in feel", "long runs hitting MP consistently", "fueling flagged on 3 of last 4 long runs". Feed those into the marathon execution plan:

- If patterns are positive → keep progressing or consider bumping target paces.
- If patterns show recurring fatigue / low feel scores → propose a cutback week or extra easy day.
- If MP-finish long runs are missing from peak phase → explicitly schedule one.

### Why this matters for sub-3

Subjective-only notes miss drift and pacing errors the athlete can't feel in the moment. Objective-only data misses fueling, sleep, and mental context. Combining both — stored across the cycle — is what lets the agent generate a plan adjustment that's actually informed, rather than generic advice.

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
# Register your sub-3 goal race
python3 scripts/marathon_config.py set \
  --race-name "Taipei Marathon" \
  --race-date 2026-10-18 \
  --target-time 2:59:00 \
  --start-date 2026-06-28

# List races
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

HR thresholds and athlete data are stored in `~/.config/marathon-training-coach/athlete_config.json` via `athlete_config.py`. MAX_HR and VT1_HR are **required** — all zone calculations and 80/20 checks depend on them.

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
  "alerts": [
    {
      "type": "marathon_alignment",
      "severity": "medium",
      "message": "Taipei Marathon is 85 days away (Peak phase). No long run (>= 15km) in last 2 weeks.",
      "recommendation": "Schedule a long run this weekend with the last portion at marathon pace (Z3)."
    }
  ],
  "checks_run": ["intensity", "recovery", "streak", "marathon", "fatigue"]
}
```

### workout_analysis.py (Single Workout Deep-Dive)

Intervals session:
```json
{
  "activity_id": 18027968819,
  "date": "2026-04-08",
  "distance_km": 10.01,
  "duration_min": 49.2,
  "avg_hr": 164,
  "detected_structure": "intervals",
  "analysis": {
    "warmup": {"distance_m": 6592.1, "duration_s": 2055, "pace": "5:11", "avg_hr": 162},
    "reps": [
      {"index": 1, "distance_m": 1002.9, "pace": "4:08", "avg_hr": 184, "max_hr": 189, "zone": "Z4"},
      {"index": 2, "distance_m": 998.0,  "pace": "4:09", "avg_hr": 183, "max_hr": 190, "zone": "Z3"},
      {"index": 3, "distance_m": 1001.7, "pace": "4:08", "avg_hr": 189, "max_hr": 192, "zone": "Z4"}
    ],
    "hr_drift_bpm": 5,
    "pace_cv_pct": 0.0
  },
  "assessment": [
    "3 work reps at 4:08 starting pace.",
    "HR drift +5 bpm - mild, acceptable for threshold work.",
    "Pace consistency excellent (+/-0.0%)."
  ]
}
```

Long run with MP finish:
```json
{
  "detected_structure": "long_run",
  "analysis": {
    "full_run": {"distance_m": 16047.2, "pace": "4:20", "avg_hr": 181},
    "last_40min": {"distance_m": 11047.2, "pace": "4:16", "avg_hr": 183},
    "mp_segment_detected": true
  },
  "assessment": [
    "Last 40min: 4:16 @ 183 bpm.",
    "MP segment confirmed - this counts as an MP-finish long run."
  ]
}
```

### marathon_status.py (Race Readiness)

```json
{
  "race": {
    "name": "Taipei Marathon",
    "date": "2026-10-18",
    "target_time": "2:59:00",
    "target_pace": "4:15/km",
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
      "pace_min_km": "4:20",
      "estimated_finish_time": "3:02:45",
      "based_on_runs": 4
    }
  },
  "taper_detection": {"is_tapering": false},
  "recommendations": [
    "Add threshold (Z4) sessions and race simulations.",
    "Pace gap: Z3 projects 3:02:45 vs 2:59:00 target — sharpen threshold."
  ]
}
```

## Training Philosophy (Evidence-Based)

1. **Polarized Training** — 80% easy, 20% hard (Seiler & Kjerland, 2006; Stoggl & Sperlich, 2014)
2. **Consistency > Intensity** — Frequency drives mitochondrial and capillary adaptation (Holloszy & Coyle, 1984)
3. **Strength Training** — Reduces sports injuries by 68% and overuse injuries by ~50% (Lauersen et al., 2014)

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
- `scripts/utils.py` — Shared utilities: HR zones, Strava API, activity caching, config loading
- `scripts/athlete_config.py` — CLI to manage athlete HR thresholds and profile (set/get/remove)
- `scripts/coach_check.py` — Daily training analysis: 80/20, recovery, streaks, marathon alignment
- `scripts/weekly_report.py` — Weekly summary: 4-week trends, intensity distribution
- `scripts/marathon_config.py` — CLI to manage upcoming races (set/get/list/remove)
- `scripts/marathon_status.py` — Race readiness assessment: phase, long runs, pace estimates, taper, strengths/limiters
- `scripts/workout_notes.py` — Persist and analyze post-workout check-in notes (add/list/patterns)
- `scripts/workout_analysis.py` — Deep-dive single workout via Strava laps: auto-detects intervals / long run / tempo / easy, per-rep HR + pace, drift, MP-segment detection
- `references/training-principles.md` — Evidence-based injury prevention guide
- `references/athlete-context-template.md` — Template for building persistent athlete profiles
- `references/periodization.md` — Loading patterns, recovery weeks, adaptation timelines
- `references/race-day-execution.md` — Pacing, nutrition, hydration, caffeine for race day
- `references/plans/README.md` — Index of available training plans
- `references/plans/sub3-16week.md` — Sub-3 marathon plan (16 weeks)

## Rate Limits

- Strava allows 100 req/15 min, 1000/day
- Activity caching minimizes API calls (only fetches new activities since last cache)
- Daily checks use ~1-2 API calls per run
