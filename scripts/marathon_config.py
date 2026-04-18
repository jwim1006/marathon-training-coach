#!/usr/bin/env python3
"""
Marathon Config — Manage upcoming race goals.
Stores a list of marathons for the AI agent to reference when coaching.

Usage:
  python scripts/marathon_config.py set --race-date 2026-10-18 --target-time 3:30:00 --race-name "Taipei Marathon"
  python scripts/marathon_config.py set --race-date 2026-12-06 --target-time 3:25:00 --race-name "Fuji Marathon" --distance 42.195
  python scripts/marathon_config.py list
  python scripts/marathon_config.py get --race-name "Taipei Marathon"
  python scripts/marathon_config.py get --next
  python scripts/marathon_config.py remove --race-name "Taipei Marathon"
"""

import os
import sys
import json
import argparse
import re
from datetime import datetime, date
from typing import List, Dict, Optional

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))
except ImportError:
    pass

# Config
def get_config_dir() -> str:
    xdg_config = os.environ.get('XDG_CONFIG_HOME')
    if xdg_config:
        return os.path.join(xdg_config, 'marathon-training-coach')
    return os.path.expanduser('~/.config/marathon-training-coach')

CONFIG_DIR = get_config_dir()
MARATHONS_FILE = os.path.join(CONFIG_DIR, 'marathons.json')
os.makedirs(CONFIG_DIR, mode=0o700, exist_ok=True)


def load_marathons() -> List[Dict]:
    """Load marathons list from file"""
    try:
        with open(MARATHONS_FILE, 'r') as f:
            data = json.load(f)
            if isinstance(data, list):
                return data
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return []


def save_marathons(marathons: List[Dict]):
    """Save marathons list to file"""
    # Sort by race date
    marathons.sort(key=lambda m: m.get('race_date', ''))
    with open(MARATHONS_FILE, 'w') as f:
        json.dump(marathons, f, indent=2)
    os.chmod(MARATHONS_FILE, 0o600)


def validate_date(date_str: str) -> bool:
    """Validate YYYY-MM-DD format"""
    try:
        datetime.strptime(date_str, '%Y-%m-%d')
        return True
    except ValueError:
        return False


def validate_time(time_str: str) -> bool:
    """Validate H:MM:SS or HH:MM:SS format"""
    return bool(re.match(r'^\d{1,2}:\d{2}:\d{2}$', time_str))


def parse_target_seconds(time_str: str) -> int:
    """Convert H:MM:SS to total seconds"""
    parts = time_str.split(':')
    return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])


def format_time(seconds: int) -> str:
    """Format seconds as H:MM:SS"""
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    return f"{h}:{m:02d}:{s:02d}"


def get_next_marathon(marathons: List[Dict]) -> Optional[Dict]:
    """Get the next upcoming marathon"""
    today = date.today().isoformat()
    upcoming = [m for m in marathons if m.get('race_date', '') >= today]
    if upcoming:
        return upcoming[0]  # already sorted by date
    return None


def find_marathon(marathons: List[Dict], race_name: str) -> Optional[Dict]:
    """Find marathon by name (case-insensitive)"""
    name_lower = race_name.lower()
    for m in marathons:
        if m.get('race_name', '').lower() == name_lower:
            return m
    return None


def cmd_set(args):
    """Add or update a marathon"""
    if not validate_date(args.race_date):
        print(f"Error: Invalid date format '{args.race_date}'. Use YYYY-MM-DD.")
        return 1

    if args.target_time and not validate_time(args.target_time):
        print(f"Error: Invalid time format '{args.target_time}'. Use H:MM:SS.")
        return 1

    if args.start_date and not validate_date(args.start_date):
        print(f"Error: Invalid start date format '{args.start_date}'. Use YYYY-MM-DD.")
        return 1

    if args.finish_time and not validate_time(args.finish_time):
        print(f"Error: Invalid finish time format '{args.finish_time}'. Use H:MM:SS.")
        return 1

    marathons = load_marathons()

    # Check if updating existing
    existing = find_marathon(marathons, args.race_name)
    if existing:
        existing['race_date'] = args.race_date
        if args.start_date:
            existing['start_date'] = args.start_date
        if args.target_time:
            existing['target_time'] = args.target_time
            existing['target_seconds'] = parse_target_seconds(args.target_time)
        if args.distance:
            existing['distance_km'] = args.distance
        if args.notes:
            existing['notes'] = args.notes
        if args.finish_time:
            existing['finish_time'] = args.finish_time
            existing['finish_seconds'] = parse_target_seconds(args.finish_time)
            existing['status'] = 'completed'
            # Calculate avg pace
            dist = existing.get('distance_km', 42.195)
            pace_sec = parse_target_seconds(args.finish_time) / dist
            pace_min = int(pace_sec // 60)
            pace_s = int(pace_sec % 60)
            existing['avg_pace_min_km'] = f"{pace_min}:{pace_s:02d}"
            if args.avg_hr:
                existing['avg_hr'] = args.avg_hr
        existing['updated_at'] = datetime.now().isoformat()
        print(f"Updated: {args.race_name}")
    else:
        marathon = {
            'race_name': args.race_name,
            'race_date': args.race_date,
            'distance_km': args.distance or 42.195,
            'updated_at': datetime.now().isoformat()
        }
        if args.start_date:
            marathon['start_date'] = args.start_date
        if args.target_time:
            marathon['target_time'] = args.target_time
            marathon['target_seconds'] = parse_target_seconds(args.target_time)
        if args.notes:
            marathon['notes'] = args.notes
        if args.finish_time:
            marathon['finish_time'] = args.finish_time
            marathon['finish_seconds'] = parse_target_seconds(args.finish_time)
            marathon['status'] = 'completed'
            dist = args.distance or 42.195
            pace_sec = parse_target_seconds(args.finish_time) / dist
            pace_min = int(pace_sec // 60)
            pace_s = int(pace_sec % 60)
            marathon['avg_pace_min_km'] = f"{pace_min}:{pace_s:02d}"
            if args.avg_hr:
                marathon['avg_hr'] = args.avg_hr
        marathons.append(marathon)
        print(f"Added: {args.race_name}")

    save_marathons(marathons)

    # Show summary
    race_date = datetime.strptime(args.race_date, '%Y-%m-%d').date()
    days_until = (race_date - date.today()).days
    weeks_until = days_until / 7
    print(f"  Date: {args.race_date} ({days_until} days / {weeks_until:.1f} weeks away)")
    if args.start_date:
        start = datetime.strptime(args.start_date, '%Y-%m-%d').date()
        training_weeks = (race_date - start).days / 7
        weeks_in = (date.today() - start).days / 7
        print(f"  Training: {args.start_date} -> {args.race_date} ({training_weeks:.0f} weeks, {weeks_in:.1f} weeks in)")
    if args.target_time:
        pace_sec = parse_target_seconds(args.target_time) / (args.distance or 42.195)
        pace_min = int(pace_sec // 60)
        pace_s = int(pace_sec % 60)
        print(f"  Target: {args.target_time} (pace: {pace_min}:{pace_s:02d}/km)")
    if args.finish_time:
        print(f"  Finish: {args.finish_time}")
    print(f"  Distance: {args.distance or 42.195} km")
    return 0


def cmd_list(args):
    """List all marathons"""
    marathons = load_marathons()
    if not marathons:
        print("No marathons configured. Use 'set' to add one.")
        return 0

    today = date.today()
    print(json.dumps(marathons, indent=2))
    print(f"\nTotal: {len(marathons)} race(s)")

    next_race = get_next_marathon(marathons)
    if next_race:
        race_date = datetime.strptime(next_race['race_date'], '%Y-%m-%d').date()
        days = (race_date - today).days
        print(f"Next: {next_race['race_name']} in {days} days ({days / 7:.1f} weeks)")
    return 0


def cmd_get(args):
    """Get a specific marathon or the next one"""
    marathons = load_marathons()
    if not marathons:
        print("No marathons configured.")
        return 1

    if args.next:
        race = get_next_marathon(marathons)
        if not race:
            print("No upcoming marathons.")
            return 1
    elif args.race_name:
        race = find_marathon(marathons, args.race_name)
        if not race:
            print(f"Marathon '{args.race_name}' not found.")
            return 1
    else:
        print("Specify --race-name or --next")
        return 1

    print(json.dumps(race, indent=2))
    return 0


def cmd_remove(args):
    """Remove a marathon"""
    marathons = load_marathons()
    original_count = len(marathons)
    name_lower = args.race_name.lower()
    marathons = [m for m in marathons if m.get('race_name', '').lower() != name_lower]

    if len(marathons) == original_count:
        print(f"Marathon '{args.race_name}' not found.")
        return 1

    save_marathons(marathons)
    print(f"Removed: {args.race_name}")
    return 0


def main():
    parser = argparse.ArgumentParser(description='Marathon Config — Manage upcoming race goals')
    subparsers = parser.add_subparsers(dest='command')

    # set
    set_parser = subparsers.add_parser('set', help='Add or update a marathon')
    set_parser.add_argument('--race-name', required=True, help='Race name')
    set_parser.add_argument('--race-date', required=True, help='Race date (YYYY-MM-DD)')
    set_parser.add_argument('--start-date', help='Training start date (YYYY-MM-DD)')
    set_parser.add_argument('--target-time', help='Target finish time (H:MM:SS)')
    set_parser.add_argument('--distance', type=float, help='Distance in km (default: 42.195)')
    set_parser.add_argument('--notes', help='Additional notes')
    set_parser.add_argument('--finish-time', help='Finish time for completed race (H:MM:SS)')
    set_parser.add_argument('--avg-hr', type=float, help='Average heart rate for completed race')

    # list
    subparsers.add_parser('list', help='List all marathons')

    # get
    get_parser = subparsers.add_parser('get', help='Get marathon details')
    get_parser.add_argument('--race-name', help='Race name')
    get_parser.add_argument('--next', action='store_true', help='Get next upcoming marathon')

    # remove
    remove_parser = subparsers.add_parser('remove', help='Remove a marathon')
    remove_parser.add_argument('--race-name', required=True, help='Race name to remove')

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 1

    commands = {
        'set': cmd_set,
        'list': cmd_list,
        'get': cmd_get,
        'remove': cmd_remove,
    }

    return commands[args.command](args)


if __name__ == '__main__':
    sys.exit(main())
