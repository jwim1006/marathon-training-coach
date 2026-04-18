#!/usr/bin/env python3
"""
Weekly Training Report — Summary with trends and recommendations.
Outputs structured JSON for the AI agent (OpenClaw) to interpret.
"""

import os
import sys
import json
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, List

from utils import (
    CONFIG_DIR,
    safe_float, safe_int,
    get_hr_zone, is_easy_hr,
    setup_logging,
    load_tokens, fetch_activities,
    get_marathon_report_info,
)
from marathon_status import estimate_race_pace, analyze_long_runs

logger = setup_logging('weekly_report', os.path.join(CONFIG_DIR, 'report.log'))

# ============================================================================
# ANALYSIS FUNCTIONS
# ============================================================================

def calculate_weeks(activities: List[Dict]) -> List[Dict]:
    weeks = []
    now = datetime.now(timezone.utc)
    monday_midnight = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=now.weekday())

    for i in range(4):
        week_start = monday_midnight - timedelta(weeks=i)
        week_end = week_start + timedelta(days=7)

        week_acts = []
        for a in activities:
            try:
                act_date = datetime.fromisoformat(a.get('start_date', '').replace('Z', '+00:00'))
                if week_start <= act_date < week_end:
                    week_acts.append(a)
            except (ValueError, TypeError):
                continue

        km = sum(safe_float(a.get('distance'), 0) for a in week_acts) / 1000.0
        time_mins = sum(safe_float(a.get('moving_time'), 0) for a in week_acts) / 60
        runs = len([a for a in week_acts if a.get('type') == 'Run'])

        label = f"{week_start.strftime('%d/%m')}-{(week_end - timedelta(days=1)).strftime('%d/%m')}"
        weeks.append({
            'label': label,
            'km': round(km, 1),
            'time_min': round(time_mins, 1),
            'runs': runs,
        })

    return list(reversed(weeks))


def analyze_intensity_distribution(activities: List[Dict]) -> Optional[Dict]:
    runs = [a for a in activities if a.get('type') == 'Run']
    if not runs:
        return None

    zone_counts = {'Z1': 0, 'Z2': 0, 'Z3': 0, 'Z4': 0, 'Z5': 0}
    easy_count = hard_count = runs_with_hr = 0

    for r in runs:
        avg_hr = safe_int(r.get('average_heartrate'), 0)
        if avg_hr > 0:
            runs_with_hr += 1
            zone_counts[get_hr_zone(avg_hr)] += 1
            if is_easy_hr(avg_hr):
                easy_count += 1
            else:
                hard_count += 1

    if runs_with_hr == 0:
        return None

    return {
        'easy_pct': round((easy_count / runs_with_hr) * 100, 1),
        'hard_pct': round((hard_count / runs_with_hr) * 100, 1),
        'zone_counts': zone_counts,
        'total': runs_with_hr,
    }


def generate_report(activities: List[Dict]) -> Dict:
    weeks = calculate_weeks(activities)
    intensity = analyze_intensity_distribution(activities)
    current_week = weeks[-1] if weeks else {'km': 0.0, 'runs': 0}

    eight_twenty_ok = intensity is not None and intensity['easy_pct'] >= 75

    # Zone summary
    zone_summary = ""
    if intensity and intensity.get('zone_counts'):
        zc = intensity['zone_counts']
        z12 = zc.get('Z1', 0) + zc.get('Z2', 0)
        parts = []
        if z12 > 0:
            parts.append(f"Z1-Z2: {z12}")
        for z in ('Z3', 'Z4', 'Z5'):
            if zc.get(z, 0) > 0:
                parts.append(f"{z}: {zc[z]}")
        zone_summary = " | ".join(parts)

    # Week-over-week delta (current vs previous completed week)
    wow_delta_km = None
    wow_delta_pct = None
    if len(weeks) >= 2:
        prev_km = weeks[-2]['km']
        curr_km = current_week['km']
        wow_delta_km = round(curr_km - prev_km, 1)
        if prev_km > 0:
            wow_delta_pct = round(((curr_km - prev_km) / prev_km) * 100, 1)

    # Race pace projection and long-run MP tracking
    pace_estimate = estimate_race_pace(activities)
    long_run_analysis = analyze_long_runs(activities)

    return {
        'week_km': current_week['km'],
        'week_runs': current_week['runs'],
        'four_week_avg_km': round(sum(w['km'] for w in weeks) / len(weeks), 1) if weeks else 0,
        'wow_delta_km': wow_delta_km,
        'wow_delta_pct': wow_delta_pct,
        'intensity': intensity,
        'eight_twenty_ok': eight_twenty_ok,
        'zone_summary': zone_summary,
        'weekly_data': weeks,
        'race_pace_readiness': pace_estimate,
        'long_run_summary': {
            'recent_long_runs': long_run_analysis.get('recent_long_runs', 0),
            'mp_finish_long_runs_last_4w': long_run_analysis.get('mp_finish_long_runs_last_4w', 0),
            'longest': long_run_analysis.get('longest'),
        },
        'marathon': get_marathon_report_info(),
        'generated_at': datetime.now(timezone.utc).isoformat(),
    }

# ============================================================================
# MAIN
# ============================================================================

def main() -> int:
    access_token = load_tokens(logger)
    if not access_token:
        print(json.dumps({'error': 'No Strava tokens. Run auth.py first.'}))
        return 1

    activities = fetch_activities(access_token, logger, days=28)
    if not activities:
        print(json.dumps({'error': 'No activities found.'}))
        return 0

    report = generate_report(activities)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
