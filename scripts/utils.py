#!/usr/bin/env python3
"""
Shared utilities for the Marathon Training Coach scripts.
"""

import os
import sys
import io
import json
import re
import logging
from datetime import datetime, timedelta, timezone, date
from typing import Optional, Dict, List, Any

# Load .env file if present (in deployment, real env vars are used)
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))
except ImportError:
    pass

# ============================================================================
# CONFIGURATION
# ============================================================================

def get_config_dir() -> str:
    xdg_config = os.environ.get('XDG_CONFIG_HOME')
    if xdg_config:
        return os.path.join(xdg_config, 'marathon-training-coach')
    return os.path.expanduser('~/.config/marathon-training-coach')

CONFIG_DIR = get_config_dir()
MARATHONS_FILE = os.path.join(CONFIG_DIR, 'marathons.json')
WORKOUT_NOTES_FILE = os.path.join(CONFIG_DIR, 'workout_notes.json')
ATHLETE_CONFIG_FILE = os.path.join(CONFIG_DIR, 'athlete_config.json')

os.makedirs(CONFIG_DIR, mode=0o700, exist_ok=True)


def get_env_float(name: str, default: float, min_val: float, max_val: float) -> float:
    try:
        val = float(os.environ.get(name, default))
        return max(min_val, min(max_val, val))
    except (ValueError, TypeError):
        return default


def get_env_int(name: str, default: int, min_val: int, max_val: int) -> int:
    try:
        val = int(os.environ.get(name, default))
        return max(min_val, min(max_val, val))
    except (ValueError, TypeError):
        return default


VERBOSE = os.environ.get('VERBOSE', '').lower() in ('true', '1', 'yes')

# HR config
def _load_hr_config():
    """Load HR config from athlete_config.json, falling back to env vars."""
    try:
        with open(os.path.join(get_config_dir(), 'athlete_config.json'), 'r') as f:
            data = json.load(f)
            if isinstance(data, dict) and 'max_hr' in data and 'vt1_hr' in data:
                max_hr = max(140, min(230, int(data['max_hr'])))
                vt1_hr = max(100, min(230, int(data['vt1_hr'])))
                return max_hr, vt1_hr
    except (FileNotFoundError, json.JSONDecodeError, ValueError, TypeError):
        pass
    # Fallback to env vars
    max_hr = get_env_int('MAX_HEART_RATE', 190, 140, 230)
    vt1_hr = get_env_int('VT1_HEART_RATE', int(max_hr * 0.75), 100, 230)
    return max_hr, vt1_hr

MAX_HR, VT1_HR = _load_hr_config()

# ============================================================================
# HR ZONES (VT1-anchored 5-zone model)
# ============================================================================

def _hr_zone_boundaries():
    """Compute 5-zone boundaries anchored to VT1 and MAX_HR.
    Z1: Recovery (<65% max), Z2: Aerobic (65% max to VT1),
    Z3: Tempo (VT1 to VT1+38% of remaining), Z4: Threshold (to VT1+77%), Z5: Max."""
    z1_ceil = int(MAX_HR * 0.65)
    z2_ceil = VT1_HR
    above_vt1 = MAX_HR - VT1_HR
    z3_ceil = VT1_HR + int(above_vt1 * 0.38)
    z4_ceil = VT1_HR + int(above_vt1 * 0.77)
    return z1_ceil, z2_ceil, z3_ceil, z4_ceil

HR_Z1_CEIL, HR_Z2_CEIL, HR_Z3_CEIL, HR_Z4_CEIL = _hr_zone_boundaries()


def get_hr_zone(avg_hr: int) -> str:
    if avg_hr < HR_Z1_CEIL:
        return 'Z1'
    elif avg_hr < HR_Z2_CEIL:
        return 'Z2'
    elif avg_hr < HR_Z3_CEIL:
        return 'Z3'
    elif avg_hr < HR_Z4_CEIL:
        return 'Z4'
    else:
        return 'Z5'


def is_easy_hr(avg_hr: int) -> bool:
    """Below VT1 = easy (Z1-Z2). At or above VT1 = hard (Z3-Z5)."""
    return avg_hr < VT1_HR

# ============================================================================
# SAFE CONVERSIONS
# ============================================================================

def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (ValueError, TypeError):
        return default

# ============================================================================
# FORMATTING
# ============================================================================

def format_pace(decimal_minutes: float) -> str:
    """Format decimal minutes as M:SS (e.g., 4.82 -> 4:49)"""
    mins = int(decimal_minutes)
    secs = int((decimal_minutes - mins) * 60)
    return f"{mins}:{secs:02d}"


def format_duration(total_minutes: float) -> str:
    """Format minutes as H:MM:SS"""
    total_seconds = int(total_minutes * 60)
    h = total_seconds // 3600
    m = (total_seconds % 3600) // 60
    s = total_seconds % 60
    return f"{h}:{m:02d}:{s:02d}"

# ============================================================================
# LOGGING
# ============================================================================

class SensitiveDataFilter(logging.Filter):
    REDACTION_PATTERNS = [
        (re.compile(r'[a-fA-F0-9]{20,}'), '[REDACTED]'),
        (re.compile(r'Bearer\s+\S+', re.IGNORECASE), 'Bearer [REDACTED]'),
        (re.compile(r'[A-Za-z0-9_\-]{20,}'), '[REDACTED]'),
        (re.compile(r'webhooks/[0-9]+/[a-zA-Z0-9_\-]+'), 'webhooks/[REDACTED]'),
        (re.compile(r'hooks\.slack\.com/services/[A-Za-z0-9/]+'), 'hooks.slack.com/services/[REDACTED]'),
    ]

    def filter(self, record: logging.LogRecord) -> bool:
        if record.msg and isinstance(record.msg, str):
            for pattern, replacement in self.REDACTION_PATTERNS:
                record.msg = pattern.sub(replacement, record.msg)
        return True


def setup_logging(name: str, log_file: str) -> logging.Logger:
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG if VERBOSE else logging.INFO)
    logger.handlers = []

    try:
        file_handler = logging.FileHandler(log_file, mode='a', encoding='utf-8')
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        ))
        file_handler.addFilter(SensitiveDataFilter())
        logger.addHandler(file_handler)
    except (IOError, OSError) as e:
        print(f"Warning: Could not create log file: {e}", file=sys.stderr)

    # Log to stderr, never stdout: these scripts emit JSON on stdout for the agent
    # to parse, and a log line interleaved with it corrupts the payload.
    console_stream = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
    console_handler = logging.StreamHandler(console_stream)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(logging.Formatter('%(message)s'))
    console_handler.addFilter(SensitiveDataFilter())
    logger.addHandler(console_handler)

    return logger

# ============================================================================
# ACTIVITY DATA (provider-backed)
# ============================================================================
# Strava and Garmin live in strava_fetcher.py / garmin_fetcher.py, both built on
# the ActivityProvider base (activity_provider.py) which owns caching and merging.
# providers.py picks the active one and handles the Strava -> Garmin cutover.
# Providers own their own credentials, so nothing is threaded through these calls.

def connect_provider(logger: logging.Logger) -> Optional[str]:
    """Resolve and authenticate the active data provider.

    Returns the provider's name ('strava' / 'garmin') for logging and messages, or
    None when no provider can authenticate.
    """
    import providers
    provider = providers.get_provider(logger)
    return provider.name if provider else None


def fetch_activities(logger: logging.Logger, days: int = 28) -> List[Dict]:
    """Return the last `days` of activities, newest first, cache-first."""
    import providers
    provider = providers.get_provider(logger)
    if not provider:
        return []
    return provider.fetch_activities(days=days)


def fetch_activity_detail(activity_id: int,
                          logger: logging.Logger) -> Optional[Dict]:
    """Return one activity with laps, cached permanently."""
    import providers
    provider = providers.get_provider(logger)
    if not provider:
        return None
    return provider.fetch_activity_detail(activity_id)


# ============================================================================
# ATHLETE CONFIG
# ============================================================================

def load_athlete_config() -> Dict:
    """Load athlete config. Returns dict with at least max_hr and vt1_hr."""
    try:
        with open(ATHLETE_CONFIG_FILE, 'r') as f:
            data = json.load(f)
            if isinstance(data, dict) and 'max_hr' in data and 'vt1_hr' in data:
                return data
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return {}


def get_strength_target_per_week(default: int = 2) -> int:
    """Read strength_target_per_week from athlete config (default 2, range 0-7)."""
    cfg = load_athlete_config()
    val = cfg.get('strength_target_per_week', default)
    try:
        val = int(val)
    except (TypeError, ValueError):
        return default
    return max(0, min(7, val))


def get_strength_min_duration_min(default: int = 15) -> int:
    """Read strength_min_duration_min from athlete config (default 15, range 5-60).

    Sessions shorter than this are excluded from strength counts — filters out
    accidental short Strava 'Workout' entries (warm-ups, mobility, etc).
    """
    cfg = load_athlete_config()
    val = cfg.get('strength_min_duration_min', default)
    try:
        val = int(val)
    except (TypeError, ValueError):
        return default
    return max(5, min(60, val))

# ============================================================================
# STRENGTH ACTIVITY HELPERS
# ============================================================================

# Strava activity types treated as strength / cross-training sessions.
# 'Workout' is a generic catch-all in Strava — included but flagged as ambiguous
# in summarize_strength so consumers can hedge wording.
STRENGTH_ACTIVITY_TYPES = {'WeightTraining', 'Crossfit', 'Workout'}
STRENGTH_AMBIGUOUS_TYPES = {'Workout'}


def is_strength_activity(activity: Dict) -> bool:
    """True if Strava activity is a strength / cross-training session."""
    t = activity.get('sport_type') or activity.get('type')
    return t in STRENGTH_ACTIVITY_TYPES


def qualifies_as_strength(activity: Dict, min_duration_min: int) -> bool:
    """True if activity is a strength type AND meets the minimum duration."""
    if not is_strength_activity(activity):
        return False
    duration_min = safe_float(activity.get('moving_time'), 0) / 60.0
    return duration_min >= min_duration_min


def summarize_strength(activities: List[Dict], target_per_week: int = 2,
                       weeks: int = 4, min_duration_min: Optional[int] = None) -> Dict:
    """Strength-session compliance summary.

    Tracked separately from running load — does NOT contribute to TSS, CTL,
    ATL, TSB, ACWR, or 80-20 calculations.

    Week boundaries mirror weekly_report.calculate_weeks: Monday-anchored UTC
    midnight, going backwards from current week.

    Sessions shorter than `min_duration_min` are excluded (defaults to athlete
    config value, then 15 min) — filters accidental short 'Workout' entries.
    """
    if min_duration_min is None:
        min_duration_min = get_strength_min_duration_min()

    now = datetime.now(timezone.utc)
    monday_midnight = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=now.weekday())

    weekly_breakdown = []
    total_sessions = 0
    total_ambiguous = 0
    total_duration_s = 0.0
    last_session_date = None

    for i in range(weeks):
        week_start = monday_midnight - timedelta(weeks=i)
        week_end = week_start + timedelta(days=7)
        count = 0
        ambiguous_count = 0
        for a in activities:
            if not qualifies_as_strength(a, min_duration_min):
                continue
            try:
                act_date = datetime.fromisoformat(a.get('start_date', '').replace('Z', '+00:00'))
            except (ValueError, TypeError):
                continue
            if not (week_start <= act_date < week_end):
                continue
            count += 1
            t = a.get('sport_type') or a.get('type')
            if t in STRENGTH_AMBIGUOUS_TYPES:
                ambiguous_count += 1
            total_duration_s += safe_float(a.get('moving_time'), 0)
            iso = act_date.date().isoformat()
            if last_session_date is None or iso > last_session_date:
                last_session_date = iso
        total_sessions += count
        total_ambiguous += ambiguous_count
        label = f"{week_start.strftime('%d/%m')}-{(week_end - timedelta(days=1)).strftime('%d/%m')}"
        weekly_breakdown.append({
            'label': label,
            'week_start': week_start.date().isoformat(),
            'count': count,
            'ambiguous_count': ambiguous_count,
        })

    weekly_breakdown.reverse()
    this_week_count = weekly_breakdown[-1]['count'] if weekly_breakdown else 0
    avg_duration_min = round((total_duration_s / 60.0) / total_sessions, 1) if total_sessions else 0.0
    sessions_per_week_avg = round(total_sessions / weeks, 2) if weeks else 0.0

    return {
        'target_per_week': target_per_week,
        'min_duration_min': min_duration_min,
        'this_week_count': this_week_count,
        'last_4w_count': total_sessions,
        'last_4w_ambiguous_count': total_ambiguous,
        'sessions_per_week_avg': sessions_per_week_avg,
        'avg_duration_min': avg_duration_min,
        'last_session_date': last_session_date,
        'weekly_breakdown': weekly_breakdown,
    }

# ============================================================================
# MARATHON CONFIG
# ============================================================================

def load_marathons() -> List[Dict]:
    try:
        with open(MARATHONS_FILE, 'r') as f:
            data = json.load(f)
            if isinstance(data, list):
                return data
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return []


def get_next_marathon(marathons: Optional[List[Dict]] = None) -> Optional[Dict]:
    if marathons is None:
        marathons = load_marathons()
    today = date.today().isoformat()
    upcoming = [m for m in marathons if m.get('race_date', '') >= today]
    upcoming.sort(key=lambda m: m.get('race_date', ''))
    return upcoming[0] if upcoming else None


def find_marathon(marathons: List[Dict], race_name: str) -> Optional[Dict]:
    name_lower = race_name.lower()
    for m in marathons:
        if m.get('race_name', '').lower() == name_lower:
            return m
    return None

# ============================================================================
# TRAINING PHASE
# ============================================================================

PHASES = {
    'pre_training': {'label': 'Pre-Training', 'description': 'More than 16 weeks to race. Build general fitness.'},
    'base': {'label': 'Base', 'weeks': '1-4', 'description': 'Aerobic base + VO2max introduction, gradual mileage build'},
    'build': {'label': 'Build', 'weeks': '5-8', 'description': 'Threshold work + race simulations, long run progression'},
    'peak': {'label': 'Peak', 'weeks': '9-12', 'description': 'Highest volume, longest runs, race-specific intensity'},
    'taper': {'label': 'Taper', 'weeks': '13-16', 'description': 'Volume reduction, maintain sharpness, race prep'},
    'post_race': {'label': 'Post-Race', 'description': 'Race is past. Recovery and reflection.'},
}

PHASE_LABELS = {k: v['label'] for k, v in PHASES.items()}


def get_training_phase(weeks_to_race: float) -> str:
    if weeks_to_race <= 0:
        return 'post_race'
    if weeks_to_race <= 4:
        return 'taper'
    if weeks_to_race <= 8:
        return 'peak'
    if weeks_to_race <= 12:
        return 'build'
    if weeks_to_race <= 16:
        return 'base'
    return 'pre_training'


def get_plan_week(weeks_to_race: float) -> Optional[int]:
    """Map weeks-to-race to plan week number (1-16). None if outside 16-week window."""
    if weeks_to_race <= 0 or weeks_to_race > 16:
        return None
    return 17 - int(round(weeks_to_race))


def get_marathon_report_info() -> Optional[Dict]:
    """Get marathon countdown info for reports"""
    marathon = get_next_marathon()
    if not marathon:
        return None
    race_date_str = marathon.get('race_date', '')
    try:
        race_date = datetime.strptime(race_date_str, '%Y-%m-%d').date()
    except ValueError:
        return None
    days = (race_date - date.today()).days
    if days < 0:
        return None
    weeks = days / 7.0
    phase = get_training_phase(weeks)
    plan_week = get_plan_week(weeks)
    return {
        'race_name': marathon.get('race_name', 'Unknown'),
        'race_date': race_date_str,
        'target_time': marathon.get('target_time'),
        'days_to_race': days,
        'weeks_to_race': round(weeks, 1),
        'phase': phase,
        'phase_label': PHASE_LABELS.get(phase, phase),
        'plan_week': plan_week,
    }
