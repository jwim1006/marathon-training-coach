#!/usr/bin/env python3
"""
Workout Notes — Persist and retrieve post-workout check-in notes.
The AI agent calls this after a workout conversation to save structured notes,
and before advising to check for patterns in past notes.

Usage:
  # Save a note
  python scripts/workout_notes.py add \
    --date 2026-03-08 \
    --type "long run" \
    --feel 7 \
    --summary "25km long run, felt strong through 20km, legs heavy last 5km" \
    --notes "Energy good, hydration OK, pushed too hard on hills"

  # List recent notes
  python scripts/workout_notes.py list
  python scripts/workout_notes.py list --last 10

  # Get pattern summary (for agent to read before advising)
  python scripts/workout_notes.py patterns
"""

import os
import sys
import json
import argparse
from datetime import datetime, timezone
from typing import Dict, List

from utils import CONFIG_DIR, WORKOUT_NOTES_FILE, setup_logging

logger = setup_logging('workout_notes', os.path.join(CONFIG_DIR, 'workout_notes.log'))


def load_notes() -> List[Dict]:
    try:
        with open(WORKOUT_NOTES_FILE, 'r') as f:
            data = json.load(f)
            if isinstance(data, list):
                return data
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return []


def save_notes(notes: List[Dict]):
    with open(WORKOUT_NOTES_FILE, 'w') as f:
        json.dump(notes, f, indent=2, ensure_ascii=False)
    os.chmod(WORKOUT_NOTES_FILE, 0o600)


def add_note(date: str, workout_type: str, feel: int, summary: str,
             notes: str = "") -> Dict:
    """Add a workout note. Returns the saved entry."""
    entry = {
        'date': date,
        'type': workout_type,
        'feel': max(1, min(10, feel)),  # RPE 1-10
        'summary': summary,
        'notes': notes,
        'saved_at': datetime.now(timezone.utc).isoformat(),
    }
    all_notes = load_notes()
    all_notes.append(entry)
    save_notes(all_notes)
    return entry


def list_notes(last_n: int = 5) -> List[Dict]:
    """Return the most recent N notes."""
    all_notes = load_notes()
    return all_notes[-last_n:]


def analyze_patterns() -> Dict:
    """Analyze workout notes for recurring patterns. Agent reads this before advising."""
    all_notes = load_notes()
    count = len(all_notes)

    if count < 5:
        return {
            'count': count,
            'enough_data': False,
            'message': f"Only {count} check-ins recorded. Need 5+ to identify patterns.",
        }

    # Feel scores
    feels = [n['feel'] for n in all_notes if 'feel' in n]
    avg_feel = sum(feels) / len(feels) if feels else 0

    # Feel trend (last 5 vs all-time)
    recent_feels = feels[-5:]
    recent_avg = sum(recent_feels) / len(recent_feels) if recent_feels else 0

    # Workout types
    types = {}
    for n in all_notes:
        wt = n.get('type', 'unknown').lower()
        if wt not in types:
            types[wt] = {'count': 0, 'feels': []}
        types[wt]['count'] += 1
        if 'feel' in n:
            types[wt]['feels'].append(n['feel'])

    type_summary = {}
    for wt, data in types.items():
        avg = sum(data['feels']) / len(data['feels']) if data['feels'] else 0
        type_summary[wt] = {
            'count': data['count'],
            'avg_feel': round(avg, 1),
        }

    # Extract patterns from notes text
    patterns = []

    if recent_avg < avg_feel - 1:
        patterns.append(f"Recent feel scores trending down ({recent_avg:.1f} vs {avg_feel:.1f} average)")
    elif recent_avg > avg_feel + 1:
        patterns.append(f"Recent feel scores trending up ({recent_avg:.1f} vs {avg_feel:.1f} average)")

    # Check for consistently low feel on specific types
    for wt, data in type_summary.items():
        if data['count'] >= 2 and data['avg_feel'] <= 4:
            patterns.append(f"{wt} sessions consistently feel hard (avg feel: {data['avg_feel']})")
        elif data['count'] >= 2 and data['avg_feel'] >= 8:
            patterns.append(f"{wt} sessions consistently feel good (avg feel: {data['avg_feel']})")

    return {
        'count': count,
        'enough_data': True,
        'avg_feel': round(avg_feel, 1),
        'recent_avg_feel': round(recent_avg, 1),
        'by_type': type_summary,
        'patterns': patterns,
        'recent_notes': all_notes[-3:],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description='Workout Notes - Persist post-workout check-ins')
    sub = parser.add_subparsers(dest='command')

    # add
    add_parser = sub.add_parser('add', help='Save a workout note')
    add_parser.add_argument('--date', required=True, help='Workout date (YYYY-MM-DD)')
    add_parser.add_argument('--type', required=True, help='Workout type (e.g., long run, tempo, easy)')
    add_parser.add_argument('--feel', type=int, required=True, help='How it felt (1-10, where 10=amazing)')
    add_parser.add_argument('--summary', required=True, help='Brief summary of the workout')
    add_parser.add_argument('--notes', default='', help='Additional notes (challenges, observations)')

    # list
    list_parser = sub.add_parser('list', help='Show recent notes')
    list_parser.add_argument('--last', type=int, default=5, help='Number of recent notes (default: 5)')

    # patterns
    sub.add_parser('patterns', help='Analyze patterns across check-ins')

    args = parser.parse_args()

    if args.command == 'add':
        entry = add_note(args.date, args.type, args.feel, args.summary, args.notes)
        print(json.dumps(entry, indent=2, ensure_ascii=False))
        return 0

    elif args.command == 'list':
        notes = list_notes(args.last)
        print(json.dumps(notes, indent=2, ensure_ascii=False))
        return 0

    elif args.command == 'patterns':
        patterns = analyze_patterns()
        print(json.dumps(patterns, indent=2, ensure_ascii=False))
        return 0

    else:
        parser.print_help()
        return 1


if __name__ == '__main__':
    sys.exit(main())
