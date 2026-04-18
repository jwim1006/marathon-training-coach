# Marathon Training Coach

An AI-powered running coach that connects to your Strava, monitors your training load daily, and helps you nail your marathon. Built for [OpenClaw](https://openclaw.com) — scripts output structured JSON that the AI agent interprets to give you personalized coaching advice.

## What It Does

### Keeps Your Training Honest

The coach watches your Strava data and catches drift early:

- **80/20 intensity check** — flags when too many runs are above VT1 (easy days should be easy)
- **Recovery gaps** — nudges you when extended breaks risk detraining
- **Phase alignment** — warns when your training doesn't match your current marathon phase

### Tracks Your Marathon Prep

- **Training phase detection** — knows if you're in base, build, peak, or taper based on weeks to race
- **Race pace estimation** — predicts your marathon finish time from Z3 and Z4 paces (Daniels formula)
- **Long run tracking** — monitors progression toward 30-35km peak distance
- **Taper detection** — confirms you're actually reducing volume when you should be
- **Strengths & limiters** — identifies what's working (endurance, speed, consistency, recovery discipline) and what needs attention

### Provides a Training Plan

Includes 4 ready-made 16-week plans (sub-3, sub-3:30, sub-4, beginner). The AI agent picks the closest plan to your target, then personalizes it based on your fitness, schedule, and goals.

### Coaches You Through Race Day

Within 7 days of your race, the agent pulls up a race execution guide covering pacing strategy, nutrition timing, hydration, caffeine protocol, and weather adjustments.

## Getting Started

### What the Agent Needs From You

When you first start chatting with the coach, it will walk you through setup. Here's what you'll need to provide:

#### 1. Strava Connection

The coach needs access to your Strava data. It will guide you through Strava OAuth — just follow the browser prompt. Your training history powers every analysis.

#### 2. Heart Rate Thresholds (Required)

The coach will ask you for two numbers:

| Value | What It Is | How to Find It |
|-------|-----------|---------------|
| **Max HR** | Your highest heart rate ever recorded | Look at your hardest race or all-out effort on Strava |
| **VT1 HR** | First ventilatory threshold — the HR where conversation gets hard | A lactate test is best; as a rough guide, ~75-80% of max HR |

These are **required** — every zone calculation and 80/20 check depends on them. Bad values = bad advice. The coach stores these in your athlete config and uses them across all sessions.

#### 3. Your Upcoming Race(s)

Tell the coach about your marathon(s):

- **Race name** — e.g., "Milano Marathon"
- **Race date** — e.g., 2026-04-12
- **Target time** — e.g., 2:59:59
- **Training start date** (optional) — when your 16-week block began

You can add multiple races. The coach auto-detects your training phase (base/build/peak/taper) based on weeks to race.

#### 4. Athlete Profile (First Session)

The coach builds a persistent profile about you. Some data is structured (stored in config, used by scripts):

- **Years running**, **peak weekly km**, **race PRs**
- **Preferred long run day**, **rest days**, **hours/week available**
- **Injury history**

Other data is conversational (stored as a coaching profile):

- **Current form** — pulled from Strava automatically
- **Goals** — A/B/C race priorities, time targets
- **Coaching preferences** — communication style, risk tolerance

Everything is saved locally (`~/.config/marathon-training-coach/`) and reused every session — you only do full setup once. The coach updates it at milestones.

#### 5. Workout Feedback (Ongoing)

After a run, tell the coach about it — "just did my long run" or "tempo session today, felt rough." The coach will ask follow-up questions:

1. How did the workout feel overall? (1-10)
2. Key challenges or highlights?
3. Did you stick to the planned structure?
4. Energy, hydration, mental focus?
5. What would you change next time?

This works best as a habit: check in after your long runs and quality sessions. The coach stores your notes, and after 5+ check-ins, starts spotting your patterns (e.g., "you tend to push too hard on easy days" or "your energy dips on Thursday tempos — maybe a fueling issue").

#### All Your Data in One Place

Everything the coach knows about you lives in `~/.config/marathon-training-coach/`:

| File | What It Stores |
|------|---------------|
| `athlete_config.json` | HR thresholds, running background, schedule preferences |
| `athlete_context.md` | Coaching profile (goals, preferences, observed patterns) |
| `marathons.json` | Registered races and target times |
| `workout_notes.json` | Post-workout check-in history |
| `strava_tokens.json` | Strava OAuth tokens |
| `activities_cache.json` | Cached Strava activities |

To reset everything and start fresh, delete this folder. The coach recreates it on next run.

## Metrics Reference

### HR Zones (VT1-Anchored 5-Zone Model)

All zone calculations are anchored to your VT1, not generic percentages:

| Zone | Range | What It Means |
|------|-------|---------------|
| Z1 | < 65% max HR | Recovery — very easy, barely breathing hard |
| Z2 | 65% max HR to VT1 | Aerobic — conversational pace, where 80% of your runs should be |
| Z3 | VT1 to VT1 + 38% remaining | Tempo / marathon pace — "comfortably hard" |
| Z4 | VT1 + 38% to VT1 + 77% remaining | Threshold — can only speak a few words |
| Z5 | > VT1 + 77% remaining | VO2max — near max effort, intervals only |

### 80/20 Intensity Distribution

The coach checks that ~80% of your runs are in Z1-Z2 (below VT1) and ~20% are in Z3+. Research shows this polarized distribution produces better endurance gains than training at moderate intensity all the time. If your hard percentage creeps above 25%, you'll get a warning.

### Strengths & Limiters

The marathon status report scores you on four dimensions:

| Dimension | What's Measured | Strength Signal | Limiter Signal |
|-----------|----------------|----------------|---------------|
| **Endurance** | Longest run distance, 90+ min run frequency | Longest run 30km+, weekly long runs | Longest run < 20km, few long runs |
| **Speed** | Z3/Z4 quality session frequency | 6+ quality sessions in 4 weeks | 0-2 quality sessions in 4 weeks |
| **Volume consistency** | Week-to-week km variance | Low variance (CV < 15%) | High variance (CV > 35%) |
| **Recovery discipline** | Easy vs hard run ratio | 75%+ runs easy | < 60% runs easy |

## Alerts

### Daily Check Alerts (`coach_check.py`)

| Alert | Severity | Trigger | What To Do |
|-------|----------|---------|-----------|
| **Intensity imbalance** | medium | Hard runs > 25% of total | More easy days, slow down recovery runs |
| **Recovery gap** | low | 5+ days since last activity | Easy jog or walk to maintain adaptations |
| **Marathon alignment** | medium | Training doesn't match phase (e.g., not tapering in taper phase, no long runs in peak phase) | Follow phase-specific guidance |
| **Streak milestone** | positive | 7/14/30/60/100 day streak | Celebration! Consistency is king |

### How Alerts Work

- Each alert type fires **at most once per hour** (rate-limited)
- Alerts include a **message** (what's happening) and a **recommendation** (what to do)
- The AI agent reads the JSON and delivers the advice conversationally

## Automated Daily Monitoring

Ask the agent to set up a daily cron job so you get proactive alerts without having to ask. The coach will run a daily training check and notify you only when something needs attention — intensity imbalances, recovery gaps, or phase misalignment.

You can also ask it to run weekly reports on a schedule (e.g., every Monday morning) for a regular training summary.

## How It Helps You Nail the Marathon

### Months Out: Build Safely
- Monitors weekly volume progression
- Keeps your 80/20 distribution honest — most runners run their easy days too fast

### Weeks Out: Peak Smart
- Confirms you're hitting long run targets for your plan week
- Estimates your marathon finish time from recent workouts
- Identifies strengths to lean on and limiters to address
- Warns if training doesn't match your current phase

### Race Week: Execute
- Race day execution guide: pacing, nutrition, hydration, caffeine
- Carb loading protocol (8-10g/kg/day for 2-3 days)
- Gel timing (every 45-60min starting at 45min)
- Weather adjustments (pace, hydration, gear)
- Pre-race checklist so nothing gets forgotten

### Post-Workout: Learn
- 5-question check-in after key workouts
- Builds a history of workout notes
- After 5+ check-ins, spots patterns (e.g., "you always run easy days too fast")

## Training Plans

Four 16-week plans included, each with week-by-week schedules, pace zones, long run progressions, and taper guidelines:

| Plan | Target | Runs/Week | Peak Volume |
|------|--------|-----------|-------------|
| Sub-3 | 2:45-3:00 | 6 | 80-90km |
| Sub-3:30 | 3:15-3:30 | 5-6 | 50-75km |
| Sub-4 | 3:45-4:00 | 5 | 40-65km |
| Beginner | 4:00-4:30+ | 4-5 | 30-55km |

The AI agent picks the closest plan to your target, then adjusts for your schedule, current fitness, and HR zones.

## Example Output

### Daily Check

```json
{
  "weekly_km": 61.3,
  "alerts": [
    {
      "type": "marathon_alignment",
      "severity": "medium",
      "message": "Milano Marathon is 35 days away (Peak phase). No long run (>= 15km) in last 2 weeks.",
      "recommendation": "Schedule a long run this weekend with the last portion at marathon pace (Z3)."
    }
  ],
  "checks_run": ["intensity", "recovery", "streak", "marathon"]
}
```

### Weekly Report

```json
{
  "week_km": 61.3,
  "four_week_avg_km": 67.0,
  "intensity": { "easy_pct": 84.4, "hard_pct": 15.6 },
  "marathon": {
    "race_name": "Milano Marathon",
    "days_to_race": 35,
    "phase": "peak",
    "plan_week": 12
  }
}
```

### Marathon Readiness

```json
{
  "race": { "name": "Milano Marathon", "target_time": "2:59:59", "days_to_race": 35 },
  "training_phase": { "phase": "peak", "plan_week": 12 },
  "race_pace_readiness": {
    "marathon_pace_estimate": { "pace_min_km": "4:43", "estimated_finish_time": "3:19:04" },
    "threshold_extrapolation": { "estimated_finish_time": "3:02:05" }
  },
  "strengths_limiters": {
    "strengths": ["Recovery discipline: 81% easy runs"],
    "limiters": []
  }
}
```

## Science Behind It

Built on peer-reviewed sports science — not bro science:

- **80/20 Polarized Training** — Seiler & Kjerland (2006), Stoggl & Sperlich (2014)
- **Recovery Science** — Mujika & Padilla (2000): detraining timelines and taper optimization

The coach references 30+ peer-reviewed studies. Ask it "why?" about any recommendation and it'll cite the research.
