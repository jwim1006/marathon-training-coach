#!/usr/bin/env python3
"""
Marathon Status — Assess readiness for next upcoming race.
Reads marathon config + Strava data and outputs structured JSON
for the AI agent to interpret and give personalized coaching advice.

Usage:
  python scripts/marathon_status.py
  python scripts/marathon_status.py --race-name "Taipei Marathon"
  python scripts/marathon_status.py --json  (machine-readable output)
"""

import os
import sys
import json
from datetime import datetime, timedelta, timezone, date
from typing import Optional, Dict, List

from utils import (
    CONFIG_DIR, setup_logging,
    get_hr_zone, is_easy_hr, safe_float, safe_int,
    format_pace, format_duration,
    load_tokens, fetch_activities,
    load_marathons, get_next_marathon, find_marathon,
    get_training_phase, get_plan_week, PHASES,
)

logger = setup_logging('marathon_status', os.path.join(CONFIG_DIR, 'marathon_status.log'))

# Long run targets by plan week (in minutes)
LONG_RUN_TARGETS_MIN = {
    1: 90, 2: 90, 3: 120, 4: 120,       # Base
    5: 135, 6: 105, 7: 150, 8: 135,      # Build (wk6 = race sim 1:45)
    9: 150, 10: 150, 11: 150, 12: 120,   # Peak
    13: 120, 14: 120, 15: 90, 16: 20,    # Taper
}

# Volume change expectations by phase (relative to previous phase peak)
PHASE_VOLUME_GUIDANCE = {
    'pre_training': 'Build aerobic base gradually. Follow 10% rule for weekly mileage increases.',
    'base': 'Gradually increase weekly volume. Target a ~10% weekly mileage increase.',
    'build': 'Continue building volume with more quality sessions. Aim for your highest consistent mileage.',
    'peak': 'Maintain or slightly exceed your highest volume. This is the toughest phase.',
    'taper': 'Reduce volume while keeping some intensity. Trust your fitness.',
    'post_race': 'Take 1-2 weeks easy. Reverse taper back into training.',
}

# ============================================================================
# ANALYSIS
# ============================================================================

def _build_long_run(a: Dict) -> Optional[Dict]:
    """Build a long run record from an activity, or None if not a long run"""
    if a.get('type') != 'Run':
        return None
    distance_km = safe_float(a.get('distance'), 0) / 1000.0
    if distance_km < 15:
        return None
    moving_time_min = safe_float(a.get('moving_time'), 0) / 60.0
    pace_decimal = moving_time_min / distance_km if distance_km > 0 else 0
    return {
        'date': a.get('start_date', '')[:10],
        'distance_km': round(distance_km, 2),
        'duration_min': round(moving_time_min, 1),
        'avg_hr': safe_int(a.get('average_heartrate'), 0),
        'pace_min_km': format_pace(pace_decimal) if pace_decimal > 0 else '0:00',
    }


def analyze_long_runs(activities: List[Dict]) -> Dict:
    """Analyze long runs across the full training block.
    Returns all-time top 5 by distance + recent (last 3 weeks) count."""
    cutoff_3w = datetime.now(timezone.utc) - timedelta(weeks=3)

    all_long = []
    recent_count = 0

    for a in activities:
        lr = _build_long_run(a)
        if not lr:
            continue
        all_long.append(lr)
        try:
            act_date = datetime.fromisoformat(a.get('start_date', '').replace('Z', '+00:00'))
            if act_date >= cutoff_3w:
                recent_count += 1
        except (ValueError, TypeError):
            pass

    all_long.sort(key=lambda r: r['distance_km'], reverse=True)
    longest = all_long[0] if all_long else None

    return {
        'total_long_runs': len(all_long),
        'recent_long_runs': recent_count,
        'longest': longest,
        'top_long_runs': all_long[:5],
    }

def analyze_weekly_volume(activities: List[Dict], num_weeks: int = 4) -> List[Dict]:
    """Calculate weekly volume for the last N completed weeks"""
    now = datetime.now(timezone.utc)
    monday_midnight = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=now.weekday())
    weeks = []

    for i in range(num_weeks):
        week_start = monday_midnight - timedelta(weeks=i)
        week_end = week_start + timedelta(days=7)
        week_km = 0.0
        week_time_min = 0.0
        run_count = 0

        for a in activities:
            try:
                act_date = datetime.fromisoformat(a.get('start_date', '').replace('Z', '+00:00'))
                if week_start <= act_date < week_end:
                    distance = safe_float(a.get('distance'), 0) / 1000.0
                    duration = safe_float(a.get('moving_time'), 0) / 60.0
                    week_km += distance
                    week_time_min += duration
                    if a.get('type') == 'Run':
                        run_count += 1
            except (ValueError, TypeError):
                continue

        label = f"{week_start.strftime('%d/%m')}-{(week_end - timedelta(days=1)).strftime('%d/%m')}"
        is_current = (i == 0)
        weeks.append({
            'label': label,
            'km': round(week_km, 1),
            'time_min': round(week_time_min, 1),
            'runs': run_count,
            'is_current_week': is_current,
        })

    return list(reversed(weeks))

def estimate_race_pace(activities: List[Dict]) -> Optional[Dict]:
    """Estimate marathon finish time from recent Z3 (marathon pace) runs.
    Also considers tempo and threshold efforts for a broader picture."""
    cutoff = datetime.now(timezone.utc) - timedelta(weeks=4)

    z3_paces = []  # marathon pace efforts
    z4_paces = []  # threshold efforts
    long_run_paces = []  # easy long run paces (Z2)

    for a in activities:
        if a.get('type') != 'Run':
            continue
        try:
            act_date = datetime.fromisoformat(a.get('start_date', '').replace('Z', '+00:00'))
            if act_date < cutoff:
                continue
        except (ValueError, TypeError):
            continue

        distance_km = safe_float(a.get('distance'), 0) / 1000.0
        moving_time_min = safe_float(a.get('moving_time'), 0) / 60.0
        avg_hr = safe_int(a.get('average_heartrate'), 0)

        if distance_km < 3 or moving_time_min < 10 or avg_hr <= 0:
            continue

        pace = moving_time_min / distance_km  # min/km
        zone = get_hr_zone(avg_hr)

        if zone == 'Z3':
            z3_paces.append(pace)
        elif zone == 'Z4':
            z4_paces.append(pace)
        elif zone in ('Z1', 'Z2') and distance_km >= 15:
            long_run_paces.append(pace)

    result = {}

    if z3_paces:
        avg_z3_pace = sum(z3_paces) / len(z3_paces)
        estimated_finish_min = avg_z3_pace * 42.195
        result['marathon_pace_estimate'] = {
            'pace_min_km': format_pace(avg_z3_pace),
            'estimated_finish_min': round(estimated_finish_min, 1),
            'estimated_finish_time': format_duration(estimated_finish_min),
            'based_on_runs': len(z3_paces),
            'method': 'Average pace from Z3 (marathon effort) runs',
        }

    if z4_paces:
        avg_z4_pace = sum(z4_paces) / len(z4_paces)
        # Threshold pace -> marathon pace: roughly 105-108% of threshold pace
        estimated_marathon_pace = avg_z4_pace * 1.06
        estimated_finish_min = estimated_marathon_pace * 42.195
        result['threshold_extrapolation'] = {
            'threshold_pace_min_km': format_pace(avg_z4_pace),
            'estimated_marathon_pace': format_pace(estimated_marathon_pace),
            'estimated_finish_min': round(estimated_finish_min, 1),
            'estimated_finish_time': format_duration(estimated_finish_min),
            'based_on_runs': len(z4_paces),
            'method': 'Threshold pace x 1.06 (Daniels formula approximation)',
        }

    if long_run_paces:
        avg_lr_pace = sum(long_run_paces) / len(long_run_paces)
        result['long_run_pace'] = {
            'pace_min_km': format_pace(avg_lr_pace),
            'based_on_runs': len(long_run_paces),
        }

    return result if result else None

def detect_taper(weekly_volumes: List[Dict]) -> Dict:
    """Detect if runner is tapering based on volume trend"""
    completed = [w for w in weekly_volumes if not w['is_current_week']]
    if len(completed) < 2:
        return {'is_tapering': False, 'confidence': 'low', 'reason': 'Not enough data'}

    # Check if last 2 completed weeks show declining volume
    recent = completed[-2:]
    if recent[1]['km'] < recent[0]['km'] * 0.85:
        drop_pct = (1 - recent[1]['km'] / recent[0]['km']) * 100 if recent[0]['km'] > 0 else 0
        return {
            'is_tapering': True,
            'confidence': 'medium',
            'volume_drop_pct': round(drop_pct, 1),
            'reason': f"Volume dropped {drop_pct:.0f}% week-over-week",
        }

    # Check 3-week declining trend
    if len(completed) >= 3:
        last3 = completed[-3:]
        if last3[2]['km'] < last3[1]['km'] < last3[0]['km']:
            return {
                'is_tapering': True,
                'confidence': 'high',
                'reason': '3 consecutive weeks of declining volume',
            }

    return {'is_tapering': False, 'confidence': 'medium', 'reason': 'Volume stable or increasing'}

def generate_recommendations(phase: str, plan_week: Optional[int], long_run_analysis: Dict,
                             weekly_volumes: List[Dict], pace_estimate: Optional[Dict],
                             taper_info: Dict, target_time: Optional[str],
                             weeks_to_race: float) -> List[str]:
    """Generate phase-specific recommendations for the AI agent"""
    recs = []

    # Phase-specific volume guidance
    recs.append(PHASE_VOLUME_GUIDANCE.get(phase, ''))

    # Long run assessment
    if plan_week and plan_week in LONG_RUN_TARGETS_MIN:
        target_min = LONG_RUN_TARGETS_MIN[plan_week]
        longest = long_run_analysis.get('longest')
        if longest:
            actual_min = longest['duration_min']
            if actual_min < target_min * 0.8:
                recs.append(
                    f"Long run gap: Your longest recent run was {actual_min:.0f}min, "
                    f"but plan week {plan_week} targets ~{target_min}min. "
                    f"Build up gradually."
                )
            elif actual_min >= target_min:
                recs.append(f"Long run on track: {actual_min:.0f}min meets the ~{target_min}min target.")
        else:
            if phase not in ('taper', 'post_race') and target_min >= 90:
                recs.append(
                    f"No long runs (>= 15km) found recently. "
                    f"Plan week {plan_week} targets ~{target_min}min long run."
                )

    # Taper alignment
    if phase == 'taper' and not taper_info['is_tapering']:
        recs.append(
            "You should be tapering now but volume isn't declining. "
            "Start reducing volume to arrive fresh on race day."
        )
    elif phase in ('base', 'build', 'peak') and taper_info['is_tapering']:
        recs.append(
            "Volume is declining but you're not in taper phase yet. "
            "Make sure this is intentional (recovery week) and not undertaking."
        )

    # Race pace assessment
    if pace_estimate and target_time:
        target_seconds = _parse_time_to_seconds(target_time)
        if target_seconds:
            target_min = target_seconds / 60.0
            mp = pace_estimate.get('marathon_pace_estimate')
            if mp:
                diff_min = mp['estimated_finish_min'] - target_min
                if diff_min > 10:
                    recs.append(
                        f"Pace gap: Current Z3 pace projects {mp['estimated_finish_time']}, "
                        f"which is {diff_min:.0f}min slower than your {target_time} target. "
                        f"Focus on marathon-pace sessions."
                    )
                elif diff_min > -5:
                    recs.append(
                        f"Pace is close: Z3 pace projects {mp['estimated_finish_time']} "
                        f"vs {target_time} target. Keep building confidence at race pace."
                    )
                else:
                    recs.append(
                        f"Pace is strong: Z3 pace projects {mp['estimated_finish_time']} "
                        f"vs {target_time} target. You're ahead of schedule."
                    )

    # Week-specific tips
    if phase == 'base':
        recs.append("Keep most runs easy (Z1-Z2). Introduce strides and light VO2max work.")
    elif phase == 'build':
        recs.append("Add threshold (Z4) sessions and race simulations. Long runs should include Z3 finishes.")
    elif phase == 'peak':
        recs.append("This is the hardest phase. Prioritize sleep and nutrition. Include race-pace long runs.")
    elif phase == 'taper':
        if weeks_to_race <= 1:
            recs.append("Race week: Very light running + strides only. Trust your training. Carb load 2-3 days out.")
        elif weeks_to_race <= 2:
            recs.append("Final taper: Minimal intensity, short easy runs. Focus on rest and race-day logistics.")
        else:
            recs.append("Early taper: Reduce volume ~20-40% but maintain some quality to stay sharp.")

    return [r for r in recs if r]

def _parse_time_to_seconds(time_str: str) -> Optional[int]:
    """Parse H:MM:SS to seconds"""
    if not time_str:
        return None
    parts = time_str.split(':')
    if len(parts) != 3:
        return None
    try:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    except (ValueError, TypeError):
        return None

def analyze_strengths_limiters(activities: List[Dict], long_run_analysis: Dict,
                                weekly_volumes: List[Dict]) -> Dict:
    """Analyze athlete's strengths and limiters for marathon preparation.
    Scores: endurance, speed, volume consistency, recovery discipline."""
    strengths = []
    limiters = []

    # --- Endurance Score ---
    longest = long_run_analysis.get('longest')
    if longest:
        longest_km = longest['distance_km']
        if longest_km >= 30:
            strengths.append(f"Endurance: longest run {longest_km:.1f}km shows strong distance capacity")
        elif longest_km >= 25:
            pass  # neutral
        elif longest_km < 20:
            limiters.append(f"Endurance: longest run only {longest_km:.1f}km — need to build toward 30-35km")
    else:
        limiters.append("Endurance: no long runs (15km+) found — long run development is critical")

    # 90+ min run frequency
    cutoff_8w = datetime.now(timezone.utc) - timedelta(weeks=8)
    long_session_count = 0
    for a in activities:
        if a.get('type') != 'Run':
            continue
        try:
            act_date = datetime.fromisoformat(a.get('start_date', '').replace('Z', '+00:00'))
            if act_date < cutoff_8w:
                continue
        except (ValueError, TypeError):
            continue
        moving_time_min = safe_float(a.get('moving_time'), 0) / 60.0
        if moving_time_min >= 90:
            long_session_count += 1

    if long_session_count >= 6:
        strengths.append(f"Long run consistency: {long_session_count} runs of 90+ min in last 8 weeks")
    elif long_session_count <= 2:
        limiters.append(f"Long run frequency: only {long_session_count} runs of 90+ min in last 8 weeks — aim for weekly")

    # --- Speed Score ---
    cutoff_4w = datetime.now(timezone.utc) - timedelta(weeks=4)
    z3_count = 0
    z4_count = 0
    for a in activities:
        if a.get('type') != 'Run':
            continue
        try:
            act_date = datetime.fromisoformat(a.get('start_date', '').replace('Z', '+00:00'))
            if act_date < cutoff_4w:
                continue
        except (ValueError, TypeError):
            continue
        avg_hr = safe_int(a.get('average_heartrate'), 0)
        if avg_hr > 0:
            zone = get_hr_zone(avg_hr)
            if zone == 'Z3':
                z3_count += 1
            elif zone in ('Z4', 'Z5'):
                z4_count += 1

    quality_sessions = z3_count + z4_count
    if quality_sessions >= 6:
        strengths.append(f"Speed work: {quality_sessions} quality sessions (Z3+) in last 4 weeks")
    elif quality_sessions <= 2:
        limiters.append(f"Speed work: only {quality_sessions} quality sessions in last 4 weeks — add tempo/threshold work")

    # --- Volume Consistency ---
    completed = [w for w in weekly_volumes if not w.get('is_current_week', False)]
    if len(completed) >= 3:
        kms = [w['km'] for w in completed]
        avg_km = sum(kms) / len(kms)
        if avg_km > 0:
            variance = sum((k - avg_km) ** 2 for k in kms) / len(kms)
            cv = (variance ** 0.5) / avg_km  # coefficient of variation
            if cv < 0.15:
                strengths.append(f"Volume consistency: low variance (CV {cv:.0%}) — steady training")
            elif cv > 0.35:
                limiters.append(f"Volume consistency: high variance (CV {cv:.0%}) — aim for steadier weekly km")

    # --- Recovery Discipline (80/20 compliance) ---
    cutoff_2w = datetime.now(timezone.utc) - timedelta(weeks=2)
    easy_count = 0
    hard_count = 0
    for a in activities:
        if a.get('type') != 'Run':
            continue
        try:
            act_date = datetime.fromisoformat(a.get('start_date', '').replace('Z', '+00:00'))
            if act_date < cutoff_2w:
                continue
        except (ValueError, TypeError):
            continue
        avg_hr = safe_int(a.get('average_heartrate'), 0)
        if avg_hr > 0:
            if is_easy_hr(avg_hr):
                easy_count += 1
            else:
                hard_count += 1

    total_with_hr = easy_count + hard_count
    if total_with_hr >= 4:
        easy_pct = (easy_count / total_with_hr) * 100
        if easy_pct >= 75:
            strengths.append(f"Recovery discipline: {easy_pct:.0f}% easy runs — good 80/20 compliance")
        elif easy_pct < 60:
            limiters.append(f"Recovery discipline: only {easy_pct:.0f}% easy runs — too many hard days, risk of burnout")

    return {
        'strengths': strengths,
        'limiters': limiters,
    }

# ============================================================================
# MAIN ASSESSMENT
# ============================================================================

def assess_marathon_readiness(marathon: Dict, activities: List[Dict]) -> Dict:
    """Build the full readiness assessment for the AI agent"""
    race_name = marathon.get('race_name', 'Unknown')
    race_date_str = marathon.get('race_date', '')
    target_time = marathon.get('target_time')
    distance_km = marathon.get('distance_km', 42.195)

    # Calculate countdown
    race_date = datetime.strptime(race_date_str, '%Y-%m-%d').date()
    today = date.today()
    days_to_race = (race_date - today).days
    weeks_to_race = days_to_race / 7.0

    # Determine training phase
    phase = get_training_phase(weeks_to_race)
    plan_week = get_plan_week(weeks_to_race)
    phase_info = PHASES.get(phase, {})

    # Analyze training data
    long_run_analysis = analyze_long_runs(activities)
    weekly_volumes = analyze_weekly_volume(activities)
    pace_estimate = estimate_race_pace(activities)
    taper_info = detect_taper(weekly_volumes)
    strengths_limiters = analyze_strengths_limiters(activities, long_run_analysis, weekly_volumes)

    # Compute weekly averages from completed weeks
    completed_weeks = [w for w in weekly_volumes if not w['is_current_week']]
    avg_weekly_km = sum(w['km'] for w in completed_weeks) / len(completed_weeks) if completed_weeks else 0

    # Target pace
    target_pace_str = None
    if target_time:
        target_seconds = _parse_time_to_seconds(target_time)
        if target_seconds and distance_km > 0:
            pace_sec = target_seconds / distance_km
            pace_min = int(pace_sec // 60)
            pace_s = int(pace_sec % 60)
            target_pace_str = f"{pace_min}:{pace_s:02d}/km"

    # Generate recommendations
    recommendations = generate_recommendations(
        phase, plan_week, long_run_analysis, weekly_volumes,
        pace_estimate, taper_info, target_time, weeks_to_race
    )

    return {
        'race': {
            'name': race_name,
            'date': race_date_str,
            'distance_km': distance_km,
            'target_time': target_time,
            'target_pace': target_pace_str,
            'days_to_race': days_to_race,
            'weeks_to_race': round(weeks_to_race, 1),
        },
        'training_phase': {
            'phase': phase,
            'label': phase_info.get('label', phase),
            'plan_week': plan_week,
            'description': phase_info.get('description', ''),
        },
        'long_run_analysis': long_run_analysis,
        'weekly_volume': {
            'weeks': weekly_volumes,
            'avg_completed_km': round(avg_weekly_km, 1),
        },
        'race_pace_readiness': pace_estimate,
        'taper_detection': taper_info,
        'strengths_limiters': strengths_limiters,
        'recommendations': recommendations,
        'generated_at': datetime.now(timezone.utc).isoformat(),
    }

# ============================================================================
# OUTPUT FORMATTING
# ============================================================================

def print_human_readable(assessment: Dict):
    """Print a human-readable summary (also useful for agent context)"""
    race = assessment['race']
    phase = assessment['training_phase']
    lr = assessment['long_run_analysis']
    vol = assessment['weekly_volume']
    pace = assessment.get('race_pace_readiness')
    taper = assessment['taper_detection']
    recs = assessment['recommendations']

    logger.info(f"Marathon Readiness: {race['name']}")
    logger.info("=" * 50)
    logger.info(f"Race Date: {race['date']} ({race['days_to_race']} days / {race['weeks_to_race']} weeks)")
    if race['target_time']:
        logger.info(f"Target: {race['target_time']} ({race['target_pace']})")
    logger.info(f"Distance: {race['distance_km']} km")
    logger.info("")

    logger.info(f"Phase: {phase['label']}")
    if phase['plan_week']:
        logger.info(f"Plan Week: {phase['plan_week']} of 16")
    logger.info(f"  {phase['description']}")
    logger.info("")

    logger.info(f"Avg Weekly Volume: {vol['avg_completed_km']} km")
    logger.info("Recent Weeks:")
    for w in vol['weeks']:
        marker = " (current)" if w['is_current_week'] else ""
        logger.info(f"  {w['label']}: {w['km']} km, {w['runs']} runs{marker}")
    logger.info("")

    if lr['longest']:
        l = lr['longest']
        logger.info(f"Longest Run: {l['distance_km']} km in {l['duration_min']:.0f}min @ {l['pace_min_km']}/km ({l['date']})")
    else:
        logger.info("Longest Run: No long runs (>= 15km) found")
    if lr['total_long_runs'] > 1:
        logger.info(f"Top long runs:")
        for r in lr['top_long_runs'][1:]:
            logger.info(f"  {r['date']}: {r['distance_km']} km in {r['duration_min']:.0f}min @ {r['pace_min_km']}/km")
    logger.info(f"Total long runs (>= 15km): {lr['total_long_runs']} ({lr['recent_long_runs']} in last 3 weeks)")
    logger.info("")

    if pace:
        if 'marathon_pace_estimate' in pace:
            mp = pace['marathon_pace_estimate']
            logger.info(f"Race Pace Estimate (Z3): {mp['pace_min_km']}/km -> {mp['estimated_finish_time']}")
            logger.info(f"  Based on {mp['based_on_runs']} Z3 run(s)")
        if 'threshold_extrapolation' in pace:
            tp = pace['threshold_extrapolation']
            logger.info(f"Threshold Extrapolation: {tp['threshold_pace_min_km']}/km -> {tp['estimated_finish_time']}")
            logger.info(f"  Based on {tp['based_on_runs']} Z4 run(s)")
    else:
        logger.info("Race Pace Estimate: Insufficient HR data for pace estimation")
    logger.info("")

    logger.info(f"Taper Detected: {'Yes' if taper['is_tapering'] else 'No'} ({taper['reason']})")
    logger.info("")

    logger.info("Recommendations:")
    for i, rec in enumerate(recs, 1):
        logger.info(f"  {i}. {rec}")

# ============================================================================
# CLI
# ============================================================================

def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description='Marathon Status - Assess race readiness')
    parser.add_argument('--race-name', help='Specific race to assess (default: next upcoming)')
    parser.add_argument('--json', action='store_true', help='Output raw JSON for agent consumption')
    args = parser.parse_args()

    # Load marathon config
    marathons = load_marathons()
    if not marathons:
        logger.error("No marathons configured. Run: python scripts/marathon_config.py set ...")
        return 1

    # Find target marathon
    if args.race_name:
        marathon = find_marathon(marathons, args.race_name)
        if not marathon:
            logger.error(f"Marathon '{args.race_name}' not found.")
            return 1
    else:
        marathon = get_next_marathon(marathons)
        if not marathon:
            logger.error("No upcoming marathons found.")
            return 1

    # Load Strava data
    access_token = load_tokens(logger)
    if not access_token:
        logger.error("No Strava tokens. Run: python scripts/auth.py")
        return 1

    # Calculate how many days of activities to fetch
    start_date_str = marathon.get('start_date')
    if start_date_str:
        try:
            start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
            days_since_start = (date.today() - start_date).days + 7  # +7 buffer
            fetch_days = max(days_since_start, 1)
        except (ValueError, TypeError):
            fetch_days = 120
    else:
        fetch_days = 120

    activities = fetch_activities(access_token, logger, days=fetch_days)
    if not activities:
        logger.info("No recent activities found.")

    # Generate assessment
    assessment = assess_marathon_readiness(marathon, activities)

    # Output
    if args.json:
        print(json.dumps(assessment, indent=2, ensure_ascii=False))
    else:
        print_human_readable(assessment)

    return 0

if __name__ == '__main__':
    sys.exit(main())
