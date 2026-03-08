# Athlete Context Template

Use this template to build `~/.config/marathon-training-coach/athlete_context.md` during the first coaching session. The agent reads this file at the start of every session instead of re-running all scripts.

## How to Build

1. Run scripts to gather data: `coach_check.py`, `weekly_report.py`, `marathon_status.py --json`
2. Ask the athlete about foundation, goals, schedule, and preferences
3. Write the file using the structure below
4. Update at milestones — don't regenerate from scratch

---

## Template Structure

```markdown
# Athlete Context — [Name]
*Last updated: [date]*

## Foundation
- **Years running:** [e.g., 3 years]
- **Peak weekly km:** [e.g., 65 km]
- **Race PRs:** [e.g., HM 1:42, 10K 45:30]
- **Injury history:** [e.g., IT band 2024, shin splints 2023]
- **Dormant fitness:** [e.g., ran 60km/week until 3 months ago, now 25km/week]

## Current Form
- **Recent weekly volume:** [from weekly_report.py]
- **ACWR:** [from coach_check.py]
- **80/20 compliance:** [from coach_check.py]
- **Longest recent run:** [from marathon_status.py]
- **Current CTL/ATL/TSB:** [from weekly_report.py]

## Goals
- **A race:** [name, date, target time]
- **B race:** [optional]
- **C race:** [optional]
- **Ultimate goal:** [e.g., sub-3 marathon within 2 years]

## Schedule
- **Preferred long run day:** [e.g., Saturday]
- **Workout days:** [e.g., Tuesday, Thursday]
- **Rest days:** [e.g., Monday, Friday]
- **Available hours/week:** [e.g., 8-10 hours]
- **Constraints:** [e.g., early mornings only, no track access]

## Coaching Preferences
- **Communication style:** [e.g., direct, data-driven]
- **Risk tolerance:** [e.g., conservative — prioritizes staying healthy]
- **Feedback preference:** [e.g., wants honest assessment, not sugarcoating]

## Known Patterns
*Populated after 5+ workout check-ins*
- [e.g., tends to run easy days too fast]
- [e.g., skips strength training when busy]
- [e.g., underestimates fatigue until it's too late]
```

---

## Progression Rate Guidance

Use foundation level to set safe weekly volume increases:

| Foundation Level | Progression Rate | Rationale |
|-----------------|-----------------|-----------|
| True beginner (< 1 year) | 5-10%/week | Everything is new; high injury risk |
| Some experience (1-3 years) | 10%/week | Standard progression |
| Strong foundation, returning after break | 10-15%/week | Body remembers; rebuilding is faster |
| Recently fit, no break | Maintain or small build | Already adapted; focus on specificity |

**Dormant fitness returns in 4-8 weeks** — an experienced runner returning from a break can rebuild faster than a beginner building for the first time. However, injury-related breaks require more caution than life-circumstance breaks.

---

## When to Update

- **Milestone interviews:** After 5, 10+ workout check-ins
- **Life changes:** New schedule, injury, job change
- **Phase shifts:** Moving from base to build, build to peak, etc.
- **Goal changes:** New race, revised target time
- **Edit existing sections** — don't regenerate the whole file
