#!/usr/bin/env python3
"""
Training Coach - Daily check for sub-3 marathon training insights.
Outputs structured JSON for the AI agent (OpenClaw) to interpret.

Checks:
- 80/20 intensity compliance (Seiler 2010)
- Recovery gaps
- Consistency streaks
- Marathon phase alignment
"""

import os
import sys
import json
import time
from datetime import datetime, timedelta, timezone, date
from typing import Optional, Dict, List, Tuple

from utils import (
    CONFIG_DIR,
    get_env_float, get_env_int,
    VT1_HR,
    get_hr_zone, is_easy_hr,
    safe_float, safe_int,
    setup_logging,
    load_tokens, fetch_activities,
    get_next_marathon, get_training_phase, get_marathon_report_info,
)

# ============================================================================
# COACH-SPECIFIC CONFIGURATION
# ============================================================================

MAX_HARD_PERCENT = get_env_float('MAX_HARD_DAY_PERCENTAGE', 25.0, 5.0, 100.0)
PLANNED_REST_DAYS = get_env_int('PLANNED_REST_DAYS', 2, 0, 7)
STATE_FILE = os.path.join(CONFIG_DIR, 'coach_state.json')
MIN_ALERT_INTERVAL_SECONDS = 3600
last_alert_times: Dict[str, float] = {}

logger = setup_logging('training_coach', os.path.join(CONFIG_DIR, 'coach.log'))

# ============================================================================
# STATE MANAGEMENT
# ============================================================================

class CoachState:
    def __init__(self):
        self.last_run: Optional[str] = None
        self.last_alert_time: Optional[str] = None
        self.alert_count_24h: int = 0

    @classmethod
    def load(cls) -> 'CoachState':
        try:
            with open(STATE_FILE, 'r') as f:
                data = json.load(f)
                state = cls()
                state.last_run = data.get('last_run')
                state.last_alert_time = data.get('last_alert_time')
                state.alert_count_24h = int(data.get('alert_count_24h', 0))
                return state
        except (FileNotFoundError, json.JSONDecodeError, KeyError, TypeError):
            return cls()

    def save(self):
        try:
            with open(STATE_FILE, 'w') as f:
                json.dump(self.__dict__, f, indent=2, default=str)
            os.chmod(STATE_FILE, 0o600)
        except (IOError, OSError) as e:
            logger.error(f"Failed to save state: {e}")

    def should_alert(self, alert_type: str) -> bool:
        now = time.time()
        last_time = last_alert_times.get(alert_type, 0)
        if now - last_time < MIN_ALERT_INTERVAL_SECONDS:
            return False
        last_alert_times[alert_type] = now
        return True

# ============================================================================
# ANALYSIS FUNCTIONS
# ============================================================================

def analyze_weekly_load(activities: List[Dict]) -> Tuple[Optional[Dict], float]:
    if not activities:
        return None, 0.0

    now = datetime.now(timezone.utc)
    week_start = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=now.weekday())

    this_km = 0.0
    for a in activities:
        try:
            act_date = datetime.fromisoformat(a.get('start_date', '').replace('Z', '+00:00'))
            if act_date >= week_start:
                this_km += safe_float(a.get('distance'), 0) / 1000.0
        except (ValueError, TypeError):
            continue

    return None, this_km


def analyze_intensity(activities: List[Dict]) -> Optional[Dict]:
    """80/20 compliance using VT1-anchored HR zones."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    recent = []
    for a in activities:
        try:
            act_date = datetime.fromisoformat(a.get('start_date', '').replace('Z', '+00:00'))
            if act_date > cutoff:
                recent.append(a)
        except (ValueError, TypeError):
            continue

    if len(recent) < 3:
        return None

    zone_counts = {'Z1': 0, 'Z2': 0, 'Z3': 0, 'Z4': 0, 'Z5': 0}
    easy_count = hard_count = runs_with_hr = 0

    for a in recent:
        if a.get('type') == 'Run':
            avg_hr = safe_int(a.get('average_heartrate'), 0)
            if avg_hr > 0:
                runs_with_hr += 1
                zone_counts[get_hr_zone(avg_hr)] += 1
                if is_easy_hr(avg_hr):
                    easy_count += 1
                else:
                    hard_count += 1

    if runs_with_hr == 0:
        return None

    easy_pct = (easy_count / runs_with_hr) * 100
    hard_pct = (hard_count / runs_with_hr) * 100

    z12 = zone_counts['Z1'] + zone_counts['Z2']
    parts = []
    if z12 > 0:
        parts.append(f"Z1-Z2: {z12}")
    for z in ('Z3', 'Z4', 'Z5'):
        if zone_counts[z] > 0:
            parts.append(f"{z}: {zone_counts[z]}")
    zone_summary = " | ".join(parts)

    if hard_pct > MAX_HARD_PERCENT:
        return {
            'type': 'intensity_imbalance',
            'severity': 'medium',
            'message': (
                f"80/20 check: {easy_pct:.0f}% easy / {hard_pct:.0f}% hard "
                f"({zone_summary}). "
                f"Seiler (2010) found elite athletes keep ~80% of sessions below VT1."
            ),
            'recommendation': (
                f"Too many sessions above VT1 ({VT1_HR} bpm). Easy days should feel "
                "conversational. Stoggl & Sperlich (2014) showed polarized training "
                "(80% easy / 20% hard) produces better VO2max and lactate threshold gains."
            )
        }

    return None


def check_recovery_gap(activities: List[Dict]) -> Optional[Dict]:
    if not activities:
        return None
    try:
        last_activity = datetime.fromisoformat(activities[0].get('start_date', '').replace('Z', '+00:00'))
    except (ValueError, TypeError):
        return None

    now = datetime.now(timezone.utc)
    days_since = (now - last_activity).days

    if days_since < PLANNED_REST_DAYS:
        return None

    recent_consistency = 0
    for a in activities[:7]:
        try:
            act_date = datetime.fromisoformat(a.get('start_date', '').replace('Z', '+00:00'))
            if (now - act_date).days <= 10:
                recent_consistency += 1
        except (ValueError, TypeError):
            continue

    if recent_consistency >= 5 and days_since <= 5:
        return None

    if days_since >= 5:
        return {
            'type': 'recovery_gap',
            'severity': 'low',
            'message': (
                f"{days_since} days since last activity. "
                f"Mujika & Padilla (2000) found VO2max begins declining after ~10 days of inactivity."
            ),
            'recommendation': (
                "A gentle 20-min walk or easy jog can maintain adaptations without adding fatigue. "
                "Nieman (2000) showed moderate exercise also supports immune function."
            )
        }
    return None


def check_consistency_streak(activities: List[Dict]) -> Optional[Dict]:
    if not activities:
        return None
    streak = 0
    now = datetime.now(timezone.utc)
    for i, a in enumerate(activities):
        try:
            act_date = datetime.fromisoformat(a.get('start_date', '').replace('Z', '+00:00'))
            expected_date = now - timedelta(days=i)
            if abs((act_date.date() - expected_date.date()).days) <= 1:
                streak += 1
            else:
                break
        except (ValueError, TypeError):
            continue

    milestones = [7, 14, 30, 60, 100]
    for milestone in milestones:
        if streak == milestone:
            return {
                'type': 'streak_milestone',
                'severity': 'positive',
                'message': f"{milestone}-Day Streak!",
                'recommendation': "Consistency beats intensity. Well done."
            }
    return None


# ============================================================================
# MARATHON-AWARE CHECKS
# ============================================================================

def check_marathon_alignment(activities: List[Dict]) -> Optional[Dict]:
    marathon = get_next_marathon()
    if not marathon:
        return None

    race_date_str = marathon.get('race_date', '')
    race_name = marathon.get('race_name', 'Unknown')
    try:
        race_date = datetime.strptime(race_date_str, '%Y-%m-%d').date()
    except ValueError:
        return None

    days_to_race = (race_date - date.today()).days
    weeks_to_race = days_to_race / 7.0
    phase = get_training_phase(weeks_to_race)

    if phase == 'post_race':
        return None

    now = datetime.now(timezone.utc)
    monday_midnight = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=now.weekday())

    weekly_kms = []
    for week_offset in range(1, 3):
        week_start = monday_midnight - timedelta(weeks=week_offset)
        week_end = week_start + timedelta(days=7)
        km = 0.0
        for a in activities:
            try:
                act_date = datetime.fromisoformat(a.get('start_date', '').replace('Z', '+00:00'))
                if week_start <= act_date < week_end:
                    km += safe_float(a.get('distance'), 0) / 1000.0
            except (ValueError, TypeError):
                continue
        weekly_kms.append(km)

    cutoff_2w = now - timedelta(weeks=2)
    has_long_run = False
    for a in activities:
        if a.get('type') != 'Run':
            continue
        try:
            act_date = datetime.fromisoformat(a.get('start_date', '').replace('Z', '+00:00'))
            if act_date < cutoff_2w:
                continue
        except (ValueError, TypeError):
            continue
        if safe_float(a.get('distance'), 0) / 1000.0 >= 15:
            has_long_run = True
            break

    if phase == 'taper':
        if len(weekly_kms) >= 2 and weekly_kms[0] >= weekly_kms[1]:
            return {
                'type': 'marathon_alignment',
                'severity': 'medium',
                'message': (
                    f"{race_name} is {days_to_race} days away (Taper phase). "
                    f"Volume is not declining ({weekly_kms[0]:.0f} km last week vs {weekly_kms[1]:.0f} km prior)."
                ),
                'recommendation': (
                    "Start reducing volume to arrive fresh on race day. "
                    "Cut ~20-40% per week while keeping some short quality sessions."
                )
            }
    elif phase == 'peak':
        if not has_long_run:
            return {
                'type': 'marathon_alignment',
                'severity': 'medium',
                'message': (
                    f"{race_name} is {days_to_race} days away (Peak phase). "
                    f"No long run (>= 15km) found in the last 2 weeks."
                ),
                'recommendation': (
                    "Peak phase needs your longest runs. Schedule a long run this weekend "
                    "with the last portion at marathon pace (Z3)."
                )
            }
    elif phase in ('base', 'build'):
        if len(weekly_kms) >= 2 and weekly_kms[1] > 0:
            drop_pct = (1 - weekly_kms[0] / weekly_kms[1]) * 100
            if drop_pct > 30:
                return {
                    'type': 'marathon_alignment',
                    'severity': 'low',
                    'message': (
                        f"{race_name} is {days_to_race} days away ({phase.title()} phase). "
                        f"Volume dropped {drop_pct:.0f}% last week ({weekly_kms[0]:.0f} km vs {weekly_kms[1]:.0f} km)."
                    ),
                    'recommendation': (
                        "Large volume drops during base/build may slow your progression. "
                        "If this was planned recovery, that's fine. Otherwise, aim for consistency."
                    )
                }

    return None


# ============================================================================
# MAIN
# ============================================================================

def main() -> int:
    state = CoachState.load()
    state.last_run = datetime.now(timezone.utc).isoformat()

    access_token = load_tokens(logger)
    if not access_token:
        print(json.dumps({'error': 'No Strava tokens. Run auth.py first.'}))
        return 1

    activities = fetch_activities(access_token, logger, days=28)
    if not activities:
        print(json.dumps({'alerts': [], 'summary': 'No recent activities found.'}))
        state.save()
        return 0

    # Run all checks
    alerts = []

    _, weekly_km = analyze_weekly_load(activities)

    intensity_alert = analyze_intensity(activities)
    if intensity_alert and state.should_alert('intensity'):
        alerts.append(intensity_alert)

    recovery_alert = check_recovery_gap(activities)
    if recovery_alert and state.should_alert('recovery'):
        alerts.append(recovery_alert)

    streak_alert = check_consistency_streak(activities)
    if streak_alert and state.should_alert('streak'):
        alerts.append(streak_alert)

    marathon_alert = check_marathon_alignment(activities)
    if marathon_alert and state.should_alert('marathon'):
        alerts.append(marathon_alert)

    result = {
        'weekly_km': round(weekly_km, 1),
        'alerts': alerts,
        'checks_run': ['intensity', 'recovery', 'streak', 'marathon'],
        'generated_at': datetime.now(timezone.utc).isoformat(),
    }

    if alerts:
        state.last_alert_time = datetime.now(timezone.utc).isoformat()

    state.save()

    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
