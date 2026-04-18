#!/usr/bin/env python3
"""
Workout Analysis - Deep-dive on a single activity using Strava laps.

Detects structure (intervals / long run / tempo / easy), groups laps into
warmup / reps / recovery / cooldown, and outputs per-rep HR + pace with
drift and quality assessment.

Outputs JSON for the AI agent (OpenClaw) to deliver conversational feedback.

Usage:
  python3 scripts/workout_analysis.py                          # most recent run
  python3 scripts/workout_analysis.py --activity-id 18027968819
  python3 scripts/workout_analysis.py --type long_run          # most recent long run
  python3 scripts/workout_analysis.py --type intervals         # most recent interval session
"""

import argparse
import json
import os
import sys
from typing import Dict, List, Optional

from utils import (
    CONFIG_DIR, VT1_HR,
    get_hr_zone, format_pace, safe_float, safe_int,
    setup_logging,
    load_tokens, fetch_activities, fetch_activity_detail,
)

logger = setup_logging('workout_analysis', os.path.join(CONFIG_DIR, 'workout_analysis.log'))

# Thresholds for structure detection
INTERVAL_MIN_REPS = 3       # need at least 3 work laps to call it intervals
INTERVAL_MAX_LAP_M = 2500   # work laps longer than this probably aren't intervals
LONG_RUN_MIN_KM = 15


def _pace_from_speed(avg_speed: float) -> str:
    """Convert m/s to min/km string."""
    if not avg_speed or avg_speed <= 0:
        return "0:00"
    pace_min_per_km = (1000.0 / avg_speed) / 60.0
    return format_pace(pace_min_per_km)


def _summarize_lap(lap: Dict, idx: int) -> Dict:
    dist = safe_float(lap.get('distance'), 0)
    time_s = safe_float(lap.get('moving_time'), 0)
    avg_hr = safe_int(lap.get('average_heartrate'), 0)
    max_hr = safe_int(lap.get('max_heartrate'), 0)
    avg_speed = safe_float(lap.get('average_speed'), 0)
    return {
        'index': idx,
        'distance_m': round(dist, 1),
        'duration_s': int(time_s),
        'pace': _pace_from_speed(avg_speed),
        'avg_hr': avg_hr,
        'max_hr': max_hr,
        'zone': get_hr_zone(avg_hr) if avg_hr > 0 else None,
    }


def classify_structure(laps: List[Dict], total_km: float) -> str:
    """Return 'intervals', 'long_run', 'tempo', or 'easy'."""
    if total_km >= LONG_RUN_MIN_KM:
        return 'long_run'

    # Count short hard laps (likely interval reps)
    hard_short = [
        l for l in laps
        if safe_float(l.get('distance'), 0) <= INTERVAL_MAX_LAP_M
        and safe_int(l.get('average_heartrate'), 0) >= VT1_HR
        and safe_float(l.get('moving_time'), 0) >= 30
    ]
    if len(hard_short) >= INTERVAL_MIN_REPS:
        return 'intervals'

    # Tempo: a long sustained lap in Z3+
    tempo_laps = [
        l for l in laps
        if safe_float(l.get('distance'), 0) >= 2000
        and safe_int(l.get('average_heartrate'), 0) >= VT1_HR
    ]
    if tempo_laps:
        return 'tempo'

    return 'easy'


def analyze_intervals(laps: List[Dict]) -> Dict:
    """Group laps into warmup / reps / recovery / cooldown for an interval session."""
    # Drop tiny GPS artifacts (< 50m or < 10s)
    clean = [
        l for l in laps
        if safe_float(l.get('distance'), 0) >= 50 and safe_float(l.get('moving_time'), 0) >= 10
    ]

    # Find work reps: short-ish laps in Z4+
    reps_idx = [
        i for i, l in enumerate(clean)
        if safe_float(l.get('distance'), 0) <= INTERVAL_MAX_LAP_M
        and safe_int(l.get('average_heartrate'), 0) >= VT1_HR + 5
        and safe_float(l.get('moving_time'), 0) >= 30
    ]

    if not reps_idx:
        return {'reps': [], 'warmup': None, 'cooldown': None}

    first_rep, last_rep = reps_idx[0], reps_idx[-1]
    warmup = clean[:first_rep]
    cooldown = clean[last_rep + 1:]
    middle = clean[first_rep:last_rep + 1]

    # Within the work block, separate reps from recoveries by HR zone
    reps = []
    recoveries = []
    rep_counter = 0
    for lap in middle:
        avg_hr = safe_int(lap.get('average_heartrate'), 0)
        if avg_hr >= VT1_HR + 5 and safe_float(lap.get('distance'), 0) <= INTERVAL_MAX_LAP_M:
            rep_counter += 1
            rep_summary = _summarize_lap(lap, rep_counter)
            rep_summary['role'] = 'rep'
            reps.append(rep_summary)
        else:
            rec_summary = _summarize_lap(lap, len(recoveries) + 1)
            rec_summary['role'] = 'recovery'
            recoveries.append(rec_summary)

    def _aggregate(group: List[Dict]) -> Optional[Dict]:
        if not group:
            return None
        total_dist = sum(safe_float(l.get('distance'), 0) for l in group)
        total_time = sum(safe_float(l.get('moving_time'), 0) for l in group)
        avg_hr_vals = [safe_int(l.get('average_heartrate'), 0) for l in group if safe_int(l.get('average_heartrate'), 0) > 0]
        return {
            'distance_m': round(total_dist, 1),
            'duration_s': int(total_time),
            'pace': _pace_from_speed(total_dist / total_time if total_time else 0),
            'avg_hr': round(sum(avg_hr_vals) / len(avg_hr_vals)) if avg_hr_vals else 0,
            'lap_count': len(group),
        }

    # HR drift across reps (first vs last)
    hr_drift = None
    if len(reps) >= 2 and reps[0]['avg_hr'] and reps[-1]['avg_hr']:
        hr_drift = reps[-1]['avg_hr'] - reps[0]['avg_hr']

    # Pace consistency (coefficient of variation of rep durations)
    pace_cv_pct = None
    rep_durations = [r['duration_s'] for r in reps if r['duration_s'] > 0]
    if len(rep_durations) >= 2:
        mean = sum(rep_durations) / len(rep_durations)
        var = sum((d - mean) ** 2 for d in rep_durations) / len(rep_durations)
        std = var ** 0.5
        pace_cv_pct = round((std / mean) * 100, 1) if mean > 0 else None

    return {
        'warmup': _aggregate(warmup),
        'reps': reps,
        'recoveries': recoveries,
        'cooldown': _aggregate(cooldown),
        'hr_drift_bpm': hr_drift,
        'pace_cv_pct': pace_cv_pct,
    }


def analyze_long_run(laps: List[Dict], total_time_s: float) -> Dict:
    """For long runs, check whether a MP segment exists (Z3+ HR in latter portion)."""
    clean = [l for l in laps if safe_float(l.get('distance'), 0) >= 500]

    # Last ~40 min by lap aggregation
    target_s = 40 * 60
    tail: List[Dict] = []
    acc = 0.0
    for lap in reversed(clean):
        if acc >= target_s:
            break
        tail.insert(0, lap)
        acc += safe_float(lap.get('moving_time'), 0)

    def _agg(group: List[Dict]) -> Optional[Dict]:
        if not group:
            return None
        total_dist = sum(safe_float(l.get('distance'), 0) for l in group)
        total_time = sum(safe_float(l.get('moving_time'), 0) for l in group)
        hrs = [safe_int(l.get('average_heartrate'), 0) for l in group if safe_int(l.get('average_heartrate'), 0) > 0]
        return {
            'distance_m': round(total_dist, 1),
            'duration_s': int(total_time),
            'pace': _pace_from_speed(total_dist / total_time if total_time else 0),
            'avg_hr': round(sum(hrs) / len(hrs)) if hrs else 0,
        }

    tail_agg = _agg(tail)
    full_agg = _agg(clean)

    mp_detected = bool(tail_agg and tail_agg['avg_hr'] >= VT1_HR)

    return {
        'full_run': full_agg,
        'last_40min': tail_agg,
        'mp_segment_detected': mp_detected,
        'lap_count': len(clean),
    }


def build_assessment(structure: str, analysis: Dict) -> List[str]:
    """Plain-text bullets for the agent to weave into conversation."""
    notes: List[str] = []
    if structure == 'intervals':
        reps = analysis.get('reps', [])
        if reps:
            avg_pace = [r['pace'] for r in reps if r['pace'] != '0:00']
            notes.append(f"{len(reps)} work reps at {avg_pace[0] if avg_pace else 'n/a'} starting pace.")

        drift = analysis.get('hr_drift_bpm')
        if drift is not None:
            if drift >= 10:
                notes.append(f"HR drift +{drift} bpm rep1→last - rep 1 likely too fast OR under-fueled.")
            elif drift >= 5:
                notes.append(f"HR drift +{drift} bpm - mild, acceptable for threshold work.")
            else:
                notes.append(f"HR drift {drift:+d} bpm - excellent control across reps.")

        cv = analysis.get('pace_cv_pct')
        if cv is not None:
            if cv <= 2:
                notes.append(f"Pace consistency excellent (+/-{cv}%).")
            elif cv <= 4:
                notes.append(f"Pace consistency good (+/-{cv}%).")
            else:
                notes.append(f"Pace consistency loose (+/-{cv}%) - try to even out rep durations.")

    elif structure == 'long_run':
        tail = analysis.get('last_40min')
        if tail:
            notes.append(f"Last 40min: {tail['pace']} @ {tail['avg_hr']} bpm.")
        if analysis.get('mp_segment_detected'):
            notes.append("MP segment confirmed - this counts as an MP-finish long run.")
        else:
            notes.append("No MP finish detected (last 40min stayed in Z2). Pure aerobic long run.")

    elif structure == 'tempo':
        notes.append("Sustained tempo effort - good for lactate threshold and MP familiarity.")

    else:
        notes.append("Easy aerobic session - supports 80/20 balance.")

    return notes


def analyze_activity(detail: Dict) -> Dict:
    laps = detail.get('laps') or []
    total_km = safe_float(detail.get('distance'), 0) / 1000.0
    total_time_s = safe_float(detail.get('moving_time'), 0)

    structure = classify_structure(laps, total_km)

    if structure == 'intervals':
        analysis = analyze_intervals(laps)
    elif structure == 'long_run':
        analysis = analyze_long_run(laps, total_time_s)
    else:
        # Tempo / easy - just give a flat lap summary
        analysis = {
            'laps': [_summarize_lap(l, i + 1) for i, l in enumerate(laps) if safe_float(l.get('distance'), 0) >= 200],
        }

    return {
        'activity_id': detail.get('id'),
        'name': detail.get('name'),
        'date': (detail.get('start_date') or '')[:10],
        'distance_km': round(total_km, 2),
        'duration_min': round(total_time_s / 60.0, 1),
        'avg_hr': safe_int(detail.get('average_heartrate'), 0),
        'detected_structure': structure,
        'analysis': analysis,
        'assessment': build_assessment(structure, analysis),
    }


def pick_activity(activities: List[Dict], filter_type: Optional[str]) -> Optional[Dict]:
    """Find the most recent activity matching the requested type."""
    runs = [a for a in activities if a.get('type') == 'Run']
    if not runs:
        return None

    if not filter_type:
        return runs[0]

    if filter_type == 'long_run':
        for a in runs:
            if safe_float(a.get('distance'), 0) / 1000.0 >= LONG_RUN_MIN_KM:
                return a
    elif filter_type == 'intervals':
        # Heuristic without fetching laps: high avg HR + moderate distance
        for a in runs:
            dist_km = safe_float(a.get('distance'), 0) / 1000.0
            avg_hr = safe_int(a.get('average_heartrate'), 0)
            if 5 <= dist_km <= 15 and avg_hr >= VT1_HR:
                return a
    elif filter_type in ('tempo', 'easy'):
        for a in runs:
            avg_hr = safe_int(a.get('average_heartrate'), 0)
            if filter_type == 'easy' and avg_hr and avg_hr < VT1_HR:
                return a
            if filter_type == 'tempo' and avg_hr and avg_hr >= VT1_HR:
                return a
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description='Deep-dive analysis of a single workout using Strava laps.')
    parser.add_argument('--activity-id', type=int, help='Analyze a specific Strava activity ID')
    parser.add_argument('--type', choices=['long_run', 'intervals', 'tempo', 'easy'],
                        help='Pick the most recent activity of this type')
    args = parser.parse_args()

    access_token = load_tokens(logger)
    if not access_token:
        print(json.dumps({'error': 'No Strava tokens. Run auth.py first.'}))
        return 1

    if args.activity_id:
        activity_id = args.activity_id
    else:
        activities = fetch_activities(access_token, logger, days=28)
        picked = pick_activity(activities, args.type)
        if not picked:
            label = args.type or 'run'
            print(json.dumps({'error': f'No recent {label} found.'}))
            return 0
        activity_id = picked.get('id')

    detail = fetch_activity_detail(access_token, activity_id, logger)
    if not detail:
        print(json.dumps({'error': f'Could not fetch activity {activity_id}.'}))
        return 1

    result = analyze_activity(detail)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
