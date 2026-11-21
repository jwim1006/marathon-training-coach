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
    connect_provider, fetch_activities, fetch_activity_detail, cached_activity_detail,
)

logger = setup_logging('workout_analysis', os.path.join(CONFIG_DIR, 'workout_analysis.log'))

# Thresholds for structure detection
INTERVAL_MIN_REPS = 3       # need at least 3 work laps to call it intervals
INTERVAL_MAX_LAP_M = 2500   # work laps longer than this probably aren't intervals
LONG_RUN_MIN_KM = 15
REP_SPEED_RATIO = 0.88      # a lap this fast vs. the typical rep counts as part of a rep
AUTO_LAP_DISTANCES_M = (1000.0, 1609.34)
AUTO_LAP_TOLERANCE = 0.03
AUTO_LAP_SHARE = 0.8


def _lap_speed(lap: Dict) -> float:
    dist = safe_float(lap.get('distance'), 0)
    time_s = safe_float(lap.get('moving_time'), 0)
    return dist / time_s if time_s > 0 else 0.0


def _is_hard_short(lap: Dict, hr_floor: int) -> bool:
    return (safe_float(lap.get('distance'), 0) <= INTERVAL_MAX_LAP_M
            and safe_int(lap.get('average_heartrate'), 0) >= hr_floor
            and safe_float(lap.get('moving_time'), 0) >= 30)


def _is_auto_lapped(laps: List[Dict]) -> bool:
    """True when most laps are uniform 1 km / 1 mile watch auto-laps (not a structured workout)."""
    body = [l for l in laps[:-1] if safe_float(l.get('distance'), 0) >= 100]
    if len(body) < 5:
        return False
    uniform = sum(
        1 for l in body
        if any(abs(safe_float(l.get('distance'), 0) - d) <= d * AUTO_LAP_TOLERANCE
               for d in AUTO_LAP_DISTANCES_M)
    )
    return uniform / len(body) >= AUTO_LAP_SHARE


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
    # Count short hard laps (likely interval reps). Checked before the distance rule so a
    # long interval session (e.g. 16 x 800 = 15 km) isn't mistaken for a long run.
    hard_short = [l for l in laps if _is_hard_short(l, VT1_HR)]
    is_intervals = len(hard_short) >= INTERVAL_MIN_REPS

    if total_km >= LONG_RUN_MIN_KM:
        # 1 km / mile auto-laps above VT1 are an MP or progression long run, not reps.
        if is_intervals and not _is_auto_lapped(laps):
            return 'intervals'
        return 'long_run'

    if is_intervals:
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
    # Anchor reps: short laps clearly in Z4+. HR lags, so the first part of a rep can read
    # below the HR floor - pace, not HR, decides which laps belong to a rep.
    anchors = [l for l in laps if _is_hard_short(l, VT1_HR + 5)]
    if not anchors:
        return {'reps': [], 'warmup': None, 'cooldown': None}

    anchor_speeds = sorted(_lap_speed(l) for l in anchors)
    ref_speed = anchor_speeds[len(anchor_speeds) // 2]
    work_speed = ref_speed * REP_SPEED_RATIO

    def _is_work(lap: Dict) -> bool:
        return (_lap_speed(lap) >= work_speed
                and safe_float(lap.get('distance'), 0) <= INTERVAL_MAX_LAP_M)

    # Run-length group consecutive laps: watches often split one rep into several laps
    # (e.g. an 800 as 2 x 400), and a rep's recovery can span several laps.
    groups: List[List] = []   # [is_work, [laps]]
    for lap in laps:
        work = _is_work(lap)
        if groups and groups[-1][0] == work:
            groups[-1][1].append(lap)
        else:
            groups.append([work, [lap]])

    # A "rep" shorter than 30s is a GPS blip or a stride, not a rep - fold it back in.
    for g in groups:
        if g[0] and sum(safe_float(l.get('moving_time'), 0) for l in g[1]) < 30:
            g[0] = False
    merged: List[List] = []
    for work, group_laps in groups:
        if merged and merged[-1][0] == work:
            merged[-1][1].extend(group_laps)
        else:
            merged.append([work, list(group_laps)])

    work_idx = [i for i, g in enumerate(merged) if g[0]]
    if not work_idx:
        return {'reps': [], 'warmup': None, 'cooldown': None}
    first_rep, last_rep = work_idx[0], work_idx[-1]
    warmup = [l for g in merged[:first_rep] for l in g[1]]
    cooldown = [l for g in merged[last_rep + 1:] for l in g[1]]

    def _summarize_group(group: List[Dict], idx: int, role: str) -> Dict:
        dist = sum(safe_float(l.get('distance'), 0) for l in group)
        time_s = sum(safe_float(l.get('moving_time'), 0) for l in group)
        hr_weighted = [(safe_int(l.get('average_heartrate'), 0), safe_float(l.get('moving_time'), 0))
                       for l in group if safe_int(l.get('average_heartrate'), 0) > 0]
        hr_time = sum(t for _, t in hr_weighted)
        avg_hr = round(sum(h * t for h, t in hr_weighted) / hr_time) if hr_time else 0
        return {
            'index': idx,
            'role': role,
            'distance_m': round(dist, 1),
            'duration_s': int(time_s),
            'pace': _pace_from_speed(dist / time_s if time_s else 0),
            'avg_hr': avg_hr,
            'max_hr': max((safe_int(l.get('max_heartrate'), 0) for l in group), default=0),
            'zone': get_hr_zone(avg_hr) if avg_hr > 0 else None,
            'lap_count': len(group),
        }

    # Label every raw lap so the full lap list shows which rep/recovery it belongs to.
    lap_roles: Dict[int, str] = {id(l): 'warmup' for l in warmup}
    lap_roles.update({id(l): 'cooldown' for l in cooldown})

    reps = []
    recoveries = []
    for work, group_laps in merged[first_rep:last_rep + 1]:
        if work:
            reps.append(_summarize_group(group_laps, len(reps) + 1, 'rep'))
            label = f"rep {len(reps)}"
        else:
            recoveries.append(_summarize_group(group_laps, len(recoveries) + 1, 'recovery'))
            label = f"recovery {len(recoveries)}"
        lap_roles.update({id(l): label for l in group_laps})

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

    # Pace consistency (coefficient of variation of rep pace, s/km - fair across rep lengths)
    pace_cv_pct = None
    rep_paces = [r['duration_s'] / (r['distance_m'] / 1000.0)
                 for r in reps if r['duration_s'] > 0 and r['distance_m'] > 0]
    if len(rep_paces) >= 2:
        mean = sum(rep_paces) / len(rep_paces)
        var = sum((d - mean) ** 2 for d in rep_paces) / len(rep_paces)
        std = var ** 0.5
        pace_cv_pct = round((std / mean) * 100, 1) if mean > 0 else None

    return {
        'warmup': _aggregate(warmup),
        'reps': reps,
        'recoveries': recoveries,
        'cooldown': _aggregate(cooldown),
        'hr_drift_bpm': hr_drift,
        'pace_cv_pct': pace_cv_pct,
        '_lap_roles': lap_roles,   # internal; consumed by analyze_activity
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
    blocks = _pace_blocks(laps)

    # MP / quality work anywhere in the run, not just the last 40 minutes: a block of
    # 3 km+ above VT1 and faster than the run's average pace.
    run_pace = (full_agg['duration_s'] / (full_agg['distance_m'] / 1000.0)
                if full_agg and full_agg['distance_m'] else 0)
    quality_blocks = [
        b for b in blocks
        if b['distance_m'] >= 3000 and b['avg_hr'] >= VT1_HR and b['pace_s_per_km'] < run_pace
    ]
    for b in quality_blocks:
        b['quality'] = True

    return {
        'full_run': full_agg,
        'blocks': blocks,
        'last_40min': tail_agg,
        'mp_segment_detected': bool(quality_blocks),
        'mp_finish_detected': bool(tail_agg and tail_agg['avg_hr'] >= VT1_HR),
        'lap_count': len(clean),
    }


LONG_RUN_BLOCK_TOL_S = 12   # s/km: a lap further than this from the block's pace starts a new block


def _pace_blocks(laps: List[Dict]) -> List[Dict]:
    """Split a long run into consecutive blocks of similar pace (e.g. easy / MP / float)."""
    laps = [l for l in laps if safe_float(l.get('distance'), 0) > 0 and safe_float(l.get('moving_time'), 0) > 0]

    def _pace(group: List[Dict]) -> float:
        d = sum(safe_float(l.get('distance'), 0) for l in group)
        t = sum(safe_float(l.get('moving_time'), 0) for l in group)
        return t / (d / 1000.0) if d else 0.0

    groups: List[List[Dict]] = []
    for lap in laps:
        # Short laps (end-of-run stubs, GPS blips) join the current block rather than splitting it.
        if groups and (safe_float(lap.get('distance'), 0) < 200
                       or abs(_pace([lap]) - _pace(groups[-1])) <= LONG_RUN_BLOCK_TOL_S):
            groups[-1].append(lap)
        else:
            groups.append([lap])

    # Second pass: re-join neighbouring blocks that ended up at the same pace.
    merged: List[List[Dict]] = []
    for g in groups:
        if merged and abs(_pace(g) - _pace(merged[-1])) <= LONG_RUN_BLOCK_TOL_S:
            merged[-1].extend(g)
        else:
            merged.append(g)

    blocks = []
    km = 0.0
    lap_no = 1
    for g in merged:
        dist = sum(safe_float(l.get('distance'), 0) for l in g)
        time_s = sum(safe_float(l.get('moving_time'), 0) for l in g)
        hr_pairs = [(safe_int(l.get('average_heartrate'), 0), safe_float(l.get('moving_time'), 0))
                    for l in g if safe_int(l.get('average_heartrate'), 0) > 0]
        hr_t = sum(t for _, t in hr_pairs)
        avg_hr = round(sum(h * t for h, t in hr_pairs) / hr_t) if hr_t else 0
        pace_s = _pace(g)
        blocks.append({
            'index': len(blocks) + 1,
            'from_km': round(km, 2),
            'to_km': round(km + dist / 1000.0, 2),
            'laps': f"{lap_no}-{lap_no + len(g) - 1}" if len(g) > 1 else str(lap_no),
            'distance_m': round(dist, 1),
            'duration_s': int(time_s),
            'pace': format_pace(pace_s / 60.0),
            'pace_s_per_km': round(pace_s, 1),
            'avg_hr': avg_hr,
            'zone': get_hr_zone(avg_hr) if avg_hr > 0 else None,
        })
        km += dist / 1000.0
        lap_no += len(g)
    return blocks


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
        blocks = analysis.get('blocks') or []
        if len(blocks) > 1:
            notes.append("Blocks: " + " | ".join(
                f"km {b['from_km']:.0f}-{b['to_km']:.0f} {b['pace']} @ {b['avg_hr']}"
                for b in blocks if b['distance_m'] >= 500))
        for b in blocks:
            if b.get('quality'):
                notes.append(f"Quality block km {b['from_km']:.0f}-{b['to_km']:.0f}: "
                             f"{b['distance_m'] / 1000:.1f} km @ {b['pace']}, HR {b['avg_hr']} (above VT1).")
        tail = analysis.get('last_40min')
        if tail:
            notes.append(f"Last 40min: {tail['pace']} @ {tail['avg_hr']} bpm"
                         + (" - MP finish." if analysis.get('mp_finish_detected') else "."))
        if not analysis.get('mp_segment_detected'):
            notes.append("No sustained block above VT1 - pure aerobic long run.")

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
        analysis = {}

    # Every recorded lap, unfiltered, so the coach can always check any block itself.
    lap_roles = analysis.pop('_lap_roles', {})
    all_laps = []
    for i, lap in enumerate(laps):
        summary = _summarize_lap(lap, i + 1)
        if id(lap) in lap_roles:
            summary['role'] = lap_roles[id(lap)]
        all_laps.append(summary)

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
        'laps': all_laps,
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

    # A known activity already in the detail cache needs no sign-in at all.
    detail = cached_activity_detail(args.activity_id, logger) if args.activity_id else None
    if detail:
        print(json.dumps(analyze_activity(detail), indent=2, ensure_ascii=False))
        return 0

    if not connect_provider(logger):
        print(json.dumps({'error': 'No data provider available. Run auth.py (Strava) '
                                   'or set GARMIN_EMAIL/GARMIN_PASSWORD (Garmin).'}))
        return 1

    if args.activity_id:
        activity_id = args.activity_id
    else:
        activities = fetch_activities(logger, days=28)
        picked = pick_activity(activities, args.type)
        if not picked:
            label = args.type or 'run'
            print(json.dumps({'error': f'No recent {label} found.'}))
            return 0
        activity_id = picked.get('id')

    detail = fetch_activity_detail(activity_id, logger)
    if not detail:
        print(json.dumps({'error': f'Could not fetch activity {activity_id}.'}))
        return 1

    result = analyze_activity(detail)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
