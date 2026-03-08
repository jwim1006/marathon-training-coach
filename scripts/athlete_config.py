#!/usr/bin/env python3
"""
Athlete Config — Manage athlete profile and training parameters.
Stores athlete configuration for zone calculations, TSS, and coaching context.

Usage:
  python scripts/athlete_config.py set --max-hr 201 --vt1-hr 175
  python scripts/athlete_config.py set --long-run-day saturday --rest-days monday,friday
  python scripts/athlete_config.py set --race-prs "marathon=3:15:00,half_marathon=1:32:00,10k=42:30"
  python scripts/athlete_config.py set --injury-history "IT band 2024,shin splints 2023"
  python scripts/athlete_config.py get
  python scripts/athlete_config.py remove --field long_run_day
"""

import os
import sys
import json
import argparse
from datetime import datetime
from typing import Dict

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))
except ImportError:
    pass

from utils import CONFIG_DIR

ATHLETE_CONFIG_FILE = os.path.join(CONFIG_DIR, 'athlete_config.json')
os.makedirs(CONFIG_DIR, mode=0o700, exist_ok=True)

REQUIRED_FIELDS = {'max_hr', 'vt1_hr'}
VALID_DAYS = {'monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday'}


def load_config() -> Dict:
    """Load athlete config from file"""
    try:
        with open(ATHLETE_CONFIG_FILE, 'r') as f:
            data = json.load(f)
            if isinstance(data, dict):
                return data
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return {}


def save_config(config: Dict):
    """Save athlete config to file"""
    with open(ATHLETE_CONFIG_FILE, 'w') as f:
        json.dump(config, f, indent=2)
    os.chmod(ATHLETE_CONFIG_FILE, 0o600)


def validate_max_hr(value: int) -> bool:
    return 140 <= value <= 230


def validate_vt1_hr(value: int, max_hr: int = None) -> bool:
    if not (100 <= value <= 230):
        return False
    if max_hr is not None and value >= max_hr:
        return False
    return True


def cmd_set(args):
    """Set one or more athlete config fields"""
    config = load_config()
    updates = {}

    # Required fields
    if args.max_hr is not None:
        if not validate_max_hr(args.max_hr):
            print(f"Error: max_hr must be between 140 and 230, got {args.max_hr}")
            return 1
        updates['max_hr'] = args.max_hr

    if args.vt1_hr is not None:
        # Use the new max_hr if provided, otherwise use existing
        effective_max_hr = updates.get('max_hr', config.get('max_hr'))
        if not validate_vt1_hr(args.vt1_hr, effective_max_hr):
            if effective_max_hr is not None and args.vt1_hr >= effective_max_hr:
                print(f"Error: vt1_hr ({args.vt1_hr}) must be less than max_hr ({effective_max_hr})")
            else:
                print(f"Error: vt1_hr must be between 100 and 230, got {args.vt1_hr}")
            return 1
        updates['vt1_hr'] = args.vt1_hr

    # If setting max_hr, validate against existing vt1_hr
    if 'max_hr' in updates and 'vt1_hr' not in updates:
        existing_vt1 = config.get('vt1_hr')
        if existing_vt1 is not None and existing_vt1 >= updates['max_hr']:
            print(f"Error: new max_hr ({updates['max_hr']}) must be greater than existing vt1_hr ({existing_vt1})")
            return 1

    # Optional fields
    if args.years_running is not None:
        updates['years_running'] = args.years_running

    if args.peak_weekly_km is not None:
        updates['peak_weekly_km'] = args.peak_weekly_km

    if args.hours_per_week is not None:
        updates['hours_per_week'] = args.hours_per_week

    if args.long_run_day is not None:
        day = args.long_run_day.lower()
        if day not in VALID_DAYS:
            print(f"Error: Invalid day '{args.long_run_day}'. Must be one of: {', '.join(sorted(VALID_DAYS))}")
            return 1
        updates['long_run_day'] = day

    if args.rest_days is not None:
        days = [d.strip().lower() for d in args.rest_days.split(',')]
        for d in days:
            if d not in VALID_DAYS:
                print(f"Error: Invalid day '{d}'. Must be one of: {', '.join(sorted(VALID_DAYS))}")
                return 1
        updates['rest_days'] = days

    if args.race_prs is not None:
        prs = {}
        for pair in args.race_prs.split(','):
            pair = pair.strip()
            if '=' not in pair:
                print(f"Error: Invalid race PR format '{pair}'. Use name=time (e.g., marathon=3:15:00)")
                return 1
            name, time_val = pair.split('=', 1)
            prs[name.strip()] = time_val.strip()
        updates['race_prs'] = prs

    if args.injury_history is not None:
        injuries = [i.strip() for i in args.injury_history.split(',')]
        updates['injury_history'] = injuries

    if not updates:
        print("Error: No fields to set. Provide at least one field.")
        return 1

    config.update(updates)
    config['updated_at'] = datetime.now().isoformat()
    save_config(config)

    print(json.dumps(config, indent=2))
    return 0


def cmd_get(args):
    """Show current athlete config"""
    config = load_config()
    if not config:
        print("No athlete config set. Use 'set' to configure.")
        return 0

    print(json.dumps(config, indent=2))
    return 0


def cmd_remove(args):
    """Remove an optional field"""
    field = args.field
    if field in REQUIRED_FIELDS:
        print(f"Error: Cannot remove required field '{field}'. Required fields: {', '.join(sorted(REQUIRED_FIELDS))}")
        return 1

    config = load_config()
    if field not in config:
        print(f"Field '{field}' not found in config.")
        return 1

    del config[field]
    config['updated_at'] = datetime.now().isoformat()
    save_config(config)

    print(f"Removed: {field}")
    print(json.dumps(config, indent=2))
    return 0


def main():
    parser = argparse.ArgumentParser(description='Athlete Config — Manage athlete profile and training parameters')
    subparsers = parser.add_subparsers(dest='command')

    # set
    set_parser = subparsers.add_parser('set', help='Set one or more athlete config fields')
    set_parser.add_argument('--max-hr', type=int, help='Maximum heart rate (140-230)')
    set_parser.add_argument('--vt1-hr', type=int, help='First ventilatory threshold heart rate (100-230, must be < max_hr)')
    set_parser.add_argument('--years-running', type=int, help='Years of running experience')
    set_parser.add_argument('--peak-weekly-km', type=float, help='Highest weekly km ever achieved')
    set_parser.add_argument('--hours-per-week', type=float, help='Available training hours per week')
    set_parser.add_argument('--long-run-day', help='Preferred long run day (e.g., saturday)')
    set_parser.add_argument('--rest-days', help='Rest days, comma-separated (e.g., monday,friday)')
    set_parser.add_argument('--race-prs', help='Race PRs as name=time pairs (e.g., "marathon=3:15:00,10k=42:30")')
    set_parser.add_argument('--injury-history', help='Injury history, comma-separated (e.g., "IT band 2024,shin splints 2023")')

    # get
    subparsers.add_parser('get', help='Show current athlete config')

    # remove
    remove_parser = subparsers.add_parser('remove', help='Remove an optional field')
    remove_parser.add_argument('--field', required=True, help='Field name to remove (cannot remove max_hr or vt1_hr)')

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 1

    commands = {
        'set': cmd_set,
        'get': cmd_get,
        'remove': cmd_remove,
    }

    return commands[args.command](args)


if __name__ == '__main__':
    sys.exit(main())
