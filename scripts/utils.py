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
import urllib.request
from datetime import datetime, timedelta, timezone, date
from typing import Optional, Dict, List, Any
from urllib.error import HTTPError, URLError

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
TOKEN_FILE = os.path.join(CONFIG_DIR, 'strava_tokens.json')
MARATHONS_FILE = os.path.join(CONFIG_DIR, 'marathons.json')
ACTIVITIES_CACHE_FILE = os.path.join(CONFIG_DIR, 'activities_cache.json')
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

    console_stream = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    console_handler = logging.StreamHandler(console_stream)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(logging.Formatter('%(message)s'))
    console_handler.addFilter(SensitiveDataFilter())
    logger.addHandler(console_handler)

    return logger

# ============================================================================
# STRAVA API
# ============================================================================

def validate_token_data(data: Dict) -> bool:
    if not isinstance(data, dict):
        return False
    access_token = data.get('access_token')
    if not isinstance(access_token, str) or len(access_token) < 10:
        return False
    return True


def load_tokens(logger: logging.Logger) -> Optional[str]:
    try:
        with open(TOKEN_FILE, 'r') as f:
            data = json.load(f)
        if not validate_token_data(data):
            logger.error("Invalid token data structure")
            return None
        access_token = data.get('access_token')
        refresh_token = data.get('refresh_token')
        expires_at = data.get('expires_at', 0)
        if expires_at and expires_at < (datetime.now().timestamp() + 300):
            logger.debug("Token expired, refreshing...")
            if refresh_token:
                return refresh_access_token(refresh_token, logger)
            return None
        return access_token
    except FileNotFoundError:
        logger.error("Token file not found. Run auth.py first.")
        return None
    except (json.JSONDecodeError, IOError, OSError) as e:
        logger.error(f"Cannot read token file: {e}")
        return None


def refresh_access_token(refresh_token: str, logger: logging.Logger) -> Optional[str]:
    import urllib.parse
    client_id = os.environ.get('STRAVA_CLIENT_ID', '').strip()
    client_secret = os.environ.get('STRAVA_CLIENT_SECRET', '').strip()
    if not client_id or not client_secret:
        logger.error("STRAVA_CLIENT_ID and STRAVA_CLIENT_SECRET required")
        return None
    url = 'https://www.strava.com/oauth/token'
    data = {
        'client_id': client_id,
        'client_secret': client_secret,
        'refresh_token': refresh_token,
        'grant_type': 'refresh_token'
    }
    req = urllib.request.Request(
        url,
        data=urllib.parse.urlencode(data).encode(),
        headers={'Content-Type': 'application/x-www-form-urlencoded'},
        method='POST'
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            new_tokens = json.loads(response.read().decode())
            if not validate_token_data(new_tokens):
                logger.error("Invalid token response from server")
                return None
            with open(TOKEN_FILE, 'w') as f:
                json.dump(new_tokens, f, indent=2)
            os.chmod(TOKEN_FILE, 0o600)
            logger.info("Token refreshed successfully")
            return new_tokens.get('access_token')
    except HTTPError as e:
        if e.code == 401:
            logger.error("Authentication failed - credentials may be invalid")
        else:
            logger.error(f"Token refresh failed: HTTP {e.code}")
        return None
    except (URLError, TimeoutError) as e:
        logger.error(f"Network error during token refresh: {e}")
        return None
    except json.JSONDecodeError:
        logger.error("Invalid response from token server")
        return None


def _load_activity_cache(logger: logging.Logger) -> List[Dict]:
    """Load cached activities from disk"""
    try:
        with open(ACTIVITIES_CACHE_FILE, 'r') as f:
            data = json.load(f)
            if isinstance(data, list):
                return data
    except FileNotFoundError:
        pass
    except (json.JSONDecodeError, IOError) as e:
        logger.debug(f"Cache read error (will rebuild): {e}")
    return []


def _save_activity_cache(activities: List[Dict], logger: logging.Logger):
    """Save activities to cache, sorted newest first"""
    activities.sort(key=lambda a: a.get('start_date', ''), reverse=True)
    try:
        with open(ACTIVITIES_CACHE_FILE, 'w') as f:
            json.dump(activities, f, separators=(',', ':'))
        os.chmod(ACTIVITIES_CACHE_FILE, 0o600)
    except (IOError, OSError) as e:
        logger.debug(f"Cache write error: {e}")


def _fetch_from_api(access_token: str, logger: logging.Logger, after_ts: int) -> List[Dict]:
    """Fetch activities from Strava API since a given timestamp"""
    url = f'https://www.strava.com/api/v3/athlete/activities?after={after_ts}&per_page=200'
    headers = {
        'Authorization': f'Bearer {access_token}',
        'User-Agent': 'TrainingCoach/2.0'
    }
    max_retries = 3
    for attempt in range(max_retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=30) as response:
                activities = json.loads(response.read().decode())
                if not isinstance(activities, list):
                    logger.error("Invalid API response format")
                    return []
                validated = []
                for a in activities:
                    if isinstance(a, dict) and re.match(r'^\d{4}-\d{2}-\d{2}T', a.get('start_date', '')):
                        validated.append(a)
                logger.debug(f"Fetched {len(validated)} new activities from API")
                return validated
        except HTTPError as e:
            if e.code == 401:
                logger.error("Authentication expired")
                return []
            logger.warning(f"HTTP error (attempt {attempt + 1}): {e.code}")
            if attempt == max_retries - 1:
                return []
        except (URLError, TimeoutError) as e:
            logger.error(f"Network error (attempt {attempt + 1}): {e}")
            if attempt == max_retries - 1:
                return []
        except json.JSONDecodeError:
            logger.error("Invalid JSON response from API")
            return []
    return []


def fetch_activities(access_token: str, logger: logging.Logger, days: int = 28) -> List[Dict]:
    """Fetch activities with local cache.
    Loads cache, fetches only new activities since last cached entry (with 3-day overlap),
    merges by activity ID, saves cache, and returns activities within the requested window."""
    cached = _load_activity_cache(logger)

    # Determine how far back to fetch from API
    if cached:
        # Find the most recent cached activity date, fetch from 3 days before that
        latest_date = max(a.get('start_date', '') for a in cached)
        try:
            latest_dt = datetime.fromisoformat(latest_date.replace('Z', '+00:00'))
            fetch_after = int((latest_dt - timedelta(days=3)).timestamp())
        except (ValueError, TypeError):
            fetch_after = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp())
        logger.debug(f"Cache has {len(cached)} activities, fetching new since {latest_date[:10]}")
    else:
        # No cache — fetch the full requested range
        fetch_after = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp())
        logger.debug(f"No cache, fetching last {days} days")

    # Fetch new activities from API
    new_activities = _fetch_from_api(access_token, logger, fetch_after)

    # Merge: deduplicate by activity ID
    by_id = {}
    for a in cached:
        aid = a.get('id')
        if aid:
            by_id[aid] = a
    for a in new_activities:
        aid = a.get('id')
        if aid:
            by_id[aid] = a  # new data overwrites cached (in case activity was edited)

    all_activities = list(by_id.values())

    # Save merged cache
    _save_activity_cache(all_activities, logger)

    # Filter to requested time window
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    filtered = []
    for a in all_activities:
        try:
            act_date = datetime.fromisoformat(a.get('start_date', '').replace('Z', '+00:00'))
            if act_date > cutoff:
                filtered.append(a)
        except (ValueError, TypeError):
            continue

    filtered.sort(key=lambda a: a.get('start_date', ''), reverse=True)
    logger.debug(f"Returning {len(filtered)} activities (cache: {len(all_activities)} total)")
    return filtered

# ============================================================================
# TSS / CTL / ATL / TSB (Training Stress Metrics)
# ============================================================================

def calculate_hr_tss(duration_sec: float, avg_hr: float) -> float:
    """Calculate heart rate-based Training Stress Score.
    Formula: (duration_min / 60) * (avg_hr / VT1_HR)^2 * 100
    Approximates TSS using HR intensity relative to VT1."""
    if duration_sec <= 0 or avg_hr <= 0 or VT1_HR <= 0:
        return 0.0
    duration_min = duration_sec / 60.0
    intensity_factor = avg_hr / VT1_HR
    return (duration_min / 60.0) * (intensity_factor ** 2) * 100


def calculate_ctl_atl_tsb(activities: List[Dict]) -> Dict[str, Optional[float]]:
    """Calculate Chronic Training Load (42-day), Acute Training Load (7-day), and TSB.
    CTL = exponentially weighted average of daily TSS over 42 days (fitness).
    ATL = exponentially weighted average of daily TSS over 7 days (fatigue).
    TSB = CTL - ATL (form / freshness)."""
    if not activities:
        return {'ctl': None, 'atl': None, 'tsb': None}

    now = datetime.now(timezone.utc)
    # Build daily TSS array for last 42 days
    daily_tss = [0.0] * 42
    for a in activities:
        try:
            act_date = datetime.fromisoformat(a.get('start_date', '').replace('Z', '+00:00'))
            days_ago = (now - act_date).days
            if 0 <= days_ago < 42:
                duration_sec = safe_float(a.get('moving_time'), 0)
                avg_hr = safe_float(a.get('average_heartrate'), 0)
                if duration_sec > 0 and avg_hr > 0:
                    daily_tss[days_ago] += calculate_hr_tss(duration_sec, avg_hr)
        except (ValueError, TypeError):
            continue

    # Exponentially weighted moving averages
    ctl = 0.0  # 42-day time constant
    atl = 0.0  # 7-day time constant
    # Process from oldest to newest
    for i in range(41, -1, -1):
        ctl = ctl + (daily_tss[i] - ctl) * (1.0 / 42.0)
        atl = atl + (daily_tss[i] - atl) * (1.0 / 7.0)

    tsb = ctl - atl
    return {
        'ctl': round(ctl, 1),
        'atl': round(atl, 1),
        'tsb': round(tsb, 1),
    }

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
