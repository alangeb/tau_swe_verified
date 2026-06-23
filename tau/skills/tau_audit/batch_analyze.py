#!/usr/bin/env python3
"""
tau_audit/batch_analyze.py — Batch analyze multiple Tau .audit files

Usage:
  python3 batch_analyze.py <directory> [--top N] [--sort FIELD] [--format table|json|csv]
  python3 batch_analyze.py <file1> <file2> ... [--top N] [--sort FIELD] [--format table|json|csv]

Sort fields: errors, duration, lines, assistant_turns, tool_calls, health
"""

import sys
import os
import json
import csv
import argparse
from pathlib import Path

# Add parent directory to path for import
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from analyze_audit import analyze_audit

def find_audit_files(path):
    """Find all .audit files in a path (file or directory)."""
    p = Path(path)
    if p.is_file():
        return [p]
    elif p.is_dir():
        return sorted(p.glob('*.audit'))
    else:
        print(f"Error: Path not found: {path}", file=sys.stderr)
        return []

def analyze_batch(files, sort_by='errors', top_n=None, output_format='table'):
    """Analyze multiple audit files and return results."""
    results = []
    
    for fpath in files:
        try:
            data = analyze_audit(str(fpath))
            data['_filepath'] = str(fpath)
            data['_basename'] = fpath.name
            results.append(data)
        except Exception as e:
            print(f"Warning: Failed to analyze {fpath}: {e}", file=sys.stderr)
    
    # Sort results
    sort_key_map = {
        'errors': lambda x: x['errors']['total_count'],
        'duration': lambda x: x['time'].get('session_duration_seconds') or 0,
        'lines': lambda x: x['conversation']['total_lines'],
        'assistant_turns': lambda x: x['conversation']['assistant_turns'],
        'tool_calls': lambda x: sum(x['tools']['calls'].values()),
        'health': lambda x: {'unhealthy': 0, 'degraded': 1, 'healthy': 2}.get(x['summary']['health'], 1),
    }
    
    sort_key = sort_key_map.get(sort_by, sort_key_map['errors'])
    reverse = sort_by == 'health'  # Healthy first
    results.sort(key=sort_key, reverse=reverse)
    
    if top_n:
        results = results[:top_n]
    
    return results

def format_table(results):
    """Format results as a table."""
    if not results:
        return "No results."
    
    # Header
    header = f"{'File':<35} {'Lines':>8} {'Users':>5} {'Assist':>6} {'Tools':>6} {'Errors':>6} {'Health':<10} {'Duration':>10}"
    print(header)
    print("-" * len(header))
    
    for r in results:
        basename = r['_basename']
        if len(basename) > 34:
            basename = '...' + basename[-31:]
        
        conv = r['conversation']
        errors = r['errors']['total_count']
        health = r['summary']['health']
        dur = r['time'].get('session_duration_seconds') or 0
        
        if dur > 3600:
            dur_str = f"{dur/3600:.1f}h"
        elif dur > 60:
            dur_str = f"{dur/60:.0f}m"
        else:
            dur_str = f"{dur:.0f}s"
        
        tool_calls = sum(r['tools']['calls'].values())
        
        print(f"{basename:<35} {conv['total_lines']:>8,} {conv['user_turns']:>5} {conv['assistant_turns']:>6} {tool_calls:>6} {errors:>6} {health:<10} {dur_str:>10}")

def format_json(results):
    """Format results as JSON."""
    # Remove internal fields
    clean = []
    for r in results:
        c = {k: v for k, v in r.items() if not k.startswith('_')}
        clean.append(c)
    print(json.dumps(clean, indent=2, default=str))

def format_csv(results):
    """Format results as CSV."""
    if not results:
        return
    
    fieldnames = ['file', 'total_lines', 'user_turns', 'assistant_turns', 
                  'tool_calls', 'errors', 'health', 'efficiency', 'duration_seconds']
    writer = csv.DictWriter(sys.stdout, fieldnames=fieldnames)
    writer.writeheader()
    
    for r in results:
        row = {
            'file': r['_basename'],
            'total_lines': r['conversation']['total_lines'],
            'user_turns': r['conversation']['user_turns'],
            'assistant_turns': r['conversation']['assistant_turns'],
            'tool_calls': sum(r['tools']['calls'].values()),
            'errors': r['errors']['total_count'],
            'health': r['summary']['health'],
            'efficiency': r['summary']['efficiency'],
            'duration_seconds': r['time'].get('session_duration_seconds') or 0,
        }
        writer.writerow(row)

def main():
    parser = argparse.ArgumentParser(description='Batch analyze Tau audit files')
    parser.add_argument('paths', nargs='+', help='Audit files or directory')
    parser.add_argument('--top', type=int, default=None, help='Show only top N results')
    parser.add_argument('--sort', choices=['errors', 'duration', 'lines', 'assistant_turns', 'tool_calls', 'health'], 
                        default='errors', help='Sort field')
    parser.add_argument('--format', choices=['table', 'json', 'csv'], default='table', help='Output format')
    
    args = parser.parse_args()
    
    # Collect all audit files
    all_files = []
    for p in args.paths:
        all_files.extend(find_audit_files(p))
    
    if not all_files:
        print("No audit files found.", file=sys.stderr)
        sys.exit(1)
    
    print(f"Analyzing {len(all_files)} audit files...", file=sys.stderr)
    results = analyze_batch(all_files, sort_by=args.sort, top_n=args.top, output_format=args.format)
    
    if args.format == 'table':
        format_table(results)
    elif args.format == 'json':
        format_json(results)
    elif args.format == 'csv':
        format_csv(results)

if __name__ == '__main__':
    main()
