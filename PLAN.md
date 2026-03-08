# Marathon Training Coach - Enhancement Plan

## Phase 1: Infrastructure Changes

### 1.1 Add .env Support
- Create `.env` file with all credentials (STRAVA_CLIENT_ID, STRAVA_CLIENT_SECRET, webhook URLs, Telegram bot token, etc.)
- Add `python-dotenv` to load `.env` in all scripts (auth.py, coach_check.py, weekly_report.py)
- Add `.env` to `.gitignore` so secrets are never committed
- Create `.env.example` with placeholder values for reference
- When deployed, the bot reads from real environment variables (no .env needed)

### 1.2 Add Telegram Notification Channel
- Add TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID to .env
- Implement `send_telegram_alert()` in coach_check.py
- Implement `send_telegram_report()` in weekly_report.py
- Support NOTIFICATION_CHANNEL=telegram alongside discord/slack
- Validate Telegram bot token format and chat ID
- Use Telegram Bot API (sendMessage with MarkdownV2 formatting)
- Update SKILL.md to document Telegram setup

## Phase 2: Live Testing with Strava

### 2.1 Authenticate Strava Account
- Run auth.py to get OAuth tokens via browser flow
- Verify token storage and refresh mechanism
- Test fetching real activities from the API

### 2.2 Validate Analysis with Real Data
- Run coach_check.py against real Strava data
- Verify ACWR calculation accuracy
- Verify intensity analysis (HR-based 80/20 check)
- Verify recovery gap detection
- Verify streak detection
- Test alert delivery to Telegram

### 2.3 Run Weekly Report
- Run weekly_report.py against real data
- Verify 4-week trend calculation
- Verify intensity distribution
- Test report delivery to Telegram

## Phase 3: Marathon-Specific Features

### 3.1 marathon_config.py — Manage race goals
- Stores a **list** of marathons (multiple upcoming races)
- CLI commands: `set`, `get`, `list`, `remove`
- Each race: name, date, target time, distance (default 42.195km), notes
- Stored in ~/.config/marathon-training-coach/marathons.json
- Agent calls this when runner tells it about a race

### 3.2 marathon_status.py — Assess readiness for next race
- Reads marathon config + Strava data
- Outputs structured JSON assessment:
  - Weeks to race, current training phase (base/build/peak/taper)
  - Long run analysis (longest recent, % of weekly volume)
  - Weekly volume vs phase target
  - Race pace readiness (estimated finish from recent paces)
  - Taper detection
  - Key recommendations for this week
- Agent calls this to give personalized coaching advice

### 3.3 Integration
- coach_check.py can warn about race-specific issues
- weekly_report.py can include race countdown + phase info

## Status

| Task | Status |
|------|--------|
| 1.1 .env support | Done |
| 1.2 Telegram notifications | Done |
| 2.1 Strava auth | Done |
| 2.2 Live testing coach_check | Done |
| 2.3 Live testing weekly_report | Done |
| 3.1 marathon_config.py | Done |
| 3.2 marathon_status.py | Done |
| 3.3 Integration with existing scripts | Done |

## Phase 4: Enrich with Endurance Coach Ideas

### 4.1 Agent Guidance (Reference Docs + SKILL.md)

#### 4.1F SKILL.md Restructure
- Add progressive discovery pattern (lean SKILL.md + reference file index)
- Add reference file table with "when to read" guidance
- Add plan personalization workflow
- Keep SKILL.md under ~3k tokens

#### 4.1A Athlete Context Persistence
- Create `references/athlete-context-template.md` with profile structure
- Add decision tree to SKILL.md: check for athlete_context.md, read or build
- Foundation vs Form guidance for progression rates

#### 4.1B Post-Workout Interview Protocol
- Add 5 baseline questions and interview workflow to SKILL.md
- Add subjective monitoring section to `references/training-principles.md` (RPE, wellness scales)
- Workout notes stored in `~/.config/marathon-training-coach/workout_notes.json`

#### 4.1C Race Day Execution Guide
- Create `references/race-day-execution.md` with pacing, nutrition, hydration, caffeine
- Add SKILL.md trigger: "within 7 days of race, read race-day-execution.md"

#### 4.1D Periodization Reference
- Create `references/periodization.md` with loading patterns, recovery weeks, adaptation timelines

#### 4.1E Training Plan Library
- Move `references/marathon-plan.md` to `references/plans/sub3-16week.md`
- Create `references/plans/README.md` (index)
- Create `references/plans/beginner-16week.md`
- Create `references/plans/sub330-16week.md`
- Create `references/plans/sub4-16week.md`

### 4.2 New Analysis Scripts

#### 4.2A TSS / CTL / ATL / TSB
- Add `calculate_hr_tss()` and `calculate_ctl_atl_tsb()` to `scripts/utils.py`
- Add TSS/CTL/ATL/TSB to `scripts/weekly_report.py` JSON output
- Add TSB fatigue alert (TSB < -20) to `scripts/coach_check.py`

#### 4.2B Strengths/Limiters Analysis
- Add `analyze_strengths_limiters()` to `scripts/marathon_status.py`
- Scoring: endurance, speed, volume consistency, recovery discipline
- Output: `"strengths_limiters": {"strengths": [...], "limiters": [...]}`

### Phase 4 Status

| Task | Status |
|------|--------|
| 4.1F SKILL.md restructure | Done |
| 4.1A Athlete context persistence | Done |
| 4.1B Post-workout interview protocol | Done |
| 4.1C Race day execution guide | Done |
| 4.1D Periodization reference | Done |
| 4.1E Training plan library | Done |
| 4.2A TSS/CTL/ATL/TSB | Done |
| 4.2B Strengths/limiters analysis | Done |
