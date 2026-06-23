#!/usr/bin/env python3
"""
tau_audit/analyze_audit.py — Comprehensive Tau .audit file analyzer

Extracts: session metadata, conversation stats, tool usage, errors, loops,
skill usage, fork usage, time gaps, content quality, failure modes.

Usage: python3 analyze_audit.py <audit_file> [--json] [--top N]
"""

import sys
import re
import json
import time
from collections import Counter, defaultdict
from datetime import datetime

def parse_timestamp(line):
    """Extract timestamp from audit line like [2026-06-23T17:49:05+00:00]"""
    m = re.match(r'\[(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+)', line)
    if m:
        try:
            return datetime.fromisoformat(m.group(1))
        except:
            return None
    return None

def parse_session_start(line):
    """Extract metadata from SESSION_START line."""
    info = {}
    m = re.search(r'pid=(\d+)', line)
    if m: info['pid'] = int(m.group(1))
    m = re.search(r"model='([^']+)'", line)
    if m: info['model'] = m.group(1)
    m = re.search(r'tools=(\d+)', line)
    if m: info['tools_count'] = int(m.group(1))
    m = re.search(r"cwd='([^']+)'", line)
    if m: info['cwd'] = m.group(1)
    # Extract tool names from tool_schema JSON
    schema_m = re.search(r'tool_schema:\s*(\[.*\])', line)
    if schema_m:
        try:
            schema = json.loads(schema_m.group(1))
            info['tools'] = [t['function']['name'] for t in schema if 'function' in t]
        except:
            info['tools'] = []
    return info

def extract_tool_schema(line):
    """Extract tool names from SESSION_START line."""
    schema_m = re.search(r'tool_schema:\s*(\[.*\])', line)
    if schema_m:
        try:
            schema = json.loads(schema_m.group(1))
            return [t['function']['name'] for t in schema if 'function' in t]
        except:
            pass
    return []

def analyze_audit(filepath, output_json=False):
    """Main analysis function."""
    
    # Phase 1: Metadata extraction
    session_meta = {}
    tool_names = []
    
    # Phase 2: Conversation structure
    user_turns = 0
    assistant_turns = 0
    assistant_contents = []
    user_contents = []
    
    # Phase 3: Tool usage (structured TOOL_CALL entries)
    tool_calls = Counter()
    tool_results = Counter()
    tool_durations = defaultdict(list)
    tool_errors = Counter()
    tool_call_details = []  # (tool_name, status, duration_ms, args_preview)
    
    # Phase 4: Errors
    errors = []
    error_types = Counter()
    error_locations = []  # (turn_number, error_type, timestamp)
    
    # Phase 5: Skills
    skill_calls = Counter()
    
    # Phase 6: Fork/Subagent
    fork_calls = 0
    subagent_calls = 0
    fork_tasks = []
    
    # Phase 7: Time analysis
    turn_timestamps = []  # (timestamp, role)
    entry_timestamps = []  # (timestamp, entry_type)
    
    # Phase 8: Content quality
    uncertainty_words = ['probably', 'maybe', 'i think', 'i\'m not sure', 'possibly', 'likely']
    confidence_words = ['confirmed', 'verified', 'tested', 'works', 'definitely', 'certain']
    self_correction_words = ['actually', 'wait', 'no,', 'correction', 'oops']
    
    content_quality = {
        'uncertainty_count': 0,
        'confidence_count': 0,
        'self_correction_count': 0,
        'long_responses': [],
        'short_responses': [],
    }
    
    # Phase 9: Loop detection
    recent_fingerprints = []  # (turn_number, fingerprint, normalized_content)
    loop_candidates = []
    
    # Phase 10: Entry type counts
    entry_types = Counter()
    
    # CONSOLE_WARNING entries
    console_warnings = []
    
    # Read and parse
    try:
        with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
            lines = f.readlines()
    except FileNotFoundError:
        print(f"Error: File not found: {filepath}", file=sys.stderr)
        sys.exit(1)
    
    total_lines = len(lines)
    current_role = None
    current_content = []
    current_timestamp = None
    turn_number = 0
    in_tool_call = False
    in_tool_result = False
    current_tool_name = None
    current_tool_status = None
    current_tool_duration = None
    
    for line_idx, line in enumerate(lines):
        line = line.rstrip('\n')
        
        # Check for entry start
        entry_m = re.match(r'^\[(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+)\]\s+(\w+)', line)
        if entry_m:
            # Flush previous content
            if current_role and current_content:
                content_text = '\n'.join(current_content)
                if current_role == 'USER':
                    user_contents.append(content_text)
                elif current_role == 'ASSISTANT':
                    assistant_contents.append(content_text)
            
            ts_str = entry_m.group(1)
            entry_type = entry_m.group(2)
            
            try:
                ts = datetime.fromisoformat(ts_str)
            except:
                ts = None
            
            entry_timestamps.append((ts, entry_type))
            entry_types[entry_type] += 1
            
            # Handle different entry types
            if entry_type == 'SESSION_START':
                session_meta = parse_session_start(line)
                tool_names = extract_tool_schema(line)
                current_role = None
                current_content = []
                continue
            
            elif entry_type == 'USER':
                current_role = 'USER'
                current_content = []
                user_turns += 1
                turn_number += 1
                turn_timestamps.append((ts, 'USER'))
                continue
            
            elif entry_type == 'ASSISTANT':
                current_role = 'ASSISTANT'
                current_content = []
                assistant_turns += 1
                turn_number += 1
                turn_timestamps.append((ts, 'ASSISTANT'))
                continue
            
            elif entry_type == 'TOOL_CALL':
                # Parse structured tool call
                in_tool_call = True
                name_m = re.search(r"original_name='([^']+)'", line)
                if not name_m:
                    name_m = re.search(r"final_name='([^']+)'", line)
                if name_m:
                    current_tool_name = name_m.group(1)
                    tool_calls[current_tool_name] += 1
                
                fixes_m = re.search(r"fixes=(\w+)", line)
                current_tool_status = 'success'
                if fixes_m and fixes_m.group(1) != 'none':
                    current_tool_status = 'fixed'
                
                current_content = []
                continue
            
            elif entry_type == 'TOOL_RESULT':
                # Parse structured tool result
                in_tool_result = True
                status_m = re.search(r'status=(\w+)', line)
                if status_m:
                    current_tool_status = status_m.group(1)
                    tool_results[current_tool_status] += 1
                
                dur_m = re.search(r'duration_ms=(\d+)', line)
                if dur_m:
                    current_tool_duration = int(dur_m.group(1))
                    if current_tool_name:
                        tool_durations[current_tool_name].append(current_tool_duration)
                
                if current_tool_name and current_tool_status:
                    tool_call_details.append({
                        'tool': current_tool_name,
                        'status': current_tool_status,
                        'duration_ms': current_tool_duration,
                    })
                
                # Track tool errors
                if current_tool_status == 'error':
                    tool_errors[current_tool_name] += 1
                
                current_content = []
                in_tool_call = False
                in_tool_result = False
                current_tool_name = None
                current_tool_status = None
                current_tool_duration = None
                continue
            
            elif entry_type == 'CONSOLE_WARNING':
                console_warnings.append({
                    'timestamp': ts_str,
                    'content': ''
                })
                current_role = None
                current_content = []
                continue
            
            else:
                # Unknown entry type
                current_role = None
                current_content = []
                continue
        
        # Accumulate content for USER/ASSISTANT entries
        if line.startswith('  | ') and current_role in ('USER', 'ASSISTANT'):
            content_text = line[4:]  # Remove '  | ' prefix
            current_content.append(content_text)
            
            # Analyze content for patterns
            if current_role == 'ASSISTANT' and turn_number > 0:
                # Check for errors (avoid false positives like "No errors found")
                if re.search(r'Error:|Exception:|Traceback|FAILED|failed to|timed out|timeout|retry', content_text):
                    # Avoid false positives from tool result output
                    if not content_text.strip().startswith('**') and not content_text.strip().startswith('##'):
                        errors.append({
                            'turn': turn_number,
                            'timestamp': ts_str if current_timestamp else None,
                            'content': content_text[:200]
                        })
                        # Classify error type
                        if 'timeout' in content_text.lower() or 'timed out' in content_text.lower():
                            error_types['timeout'] += 1
                        elif 'exception' in content_text.lower() or 'traceback' in content_text.lower():
                            error_types['exception'] += 1
                        elif 'failed' in content_text.lower() or 'failed to' in content_text.lower():
                            error_types['failed'] += 1
                        elif 'retry' in content_text.lower():
                            error_types['retry'] += 1
                        else:
                            error_types['other_error'] += 1
                        error_locations.append((turn_number, content_text[:100]))
                
                # Check for skill/fork/subagent calls
                skill_m = re.findall(r"skill\(['\"]([^'\"]+)['\"]\)", content_text)
                for s in skill_m:
                    skill_calls[s] += 1
                
                fork_m = re.findall(r'fork\(\s*task\s*=\s*["\']([^"\']+)["\']', content_text)
                for f in fork_m:
                    fork_calls += 1
                    if len(f) < 200:
                        fork_tasks.append(f)
                
                subagent_m = re.findall(r'subagent\(\s*task\s*=\s*["\']([^"\']+)["\']', content_text)
                for s in subagent_m:
                    subagent_calls += 1
                
                # Content quality analysis
                content_lower = content_text.lower()
                for uw in uncertainty_words:
                    if uw in content_lower:
                        content_quality['uncertainty_count'] += 1
                        break
                
                for cw in confidence_words:
                    if cw in content_lower:
                        content_quality['confidence_count'] += 1
                        break
                
                for sc in self_correction_words:
                    if sc in content_lower:
                        content_quality['self_correction_count'] += 1
                        break
                
                # Track long/short responses
                if len(content_text) > 1000:
                    content_quality['long_responses'].append({
                        'turn': turn_number,
                        'length': len(content_text)
                    })
                if len(content_text) < 30 and len(content_text) > 0:
                    content_quality['short_responses'].append({
                        'turn': turn_number,
                        'length': len(content_text)
                    })
                
                # Loop detection (improved with content filtering and similarity)
                # Skip boilerplate responses that cause false positives
                content_stripped = content_text.strip()
                boilerplate_patterns = [
                    r'Hi! I\'m', r"Hi! I'm", r'Done\. All three tools', r'All three tools have been',
                    r'Ready for your next', r'How can I help', r'Hello', r'Greetings',
                    r'Thank you', r'You\'re welcome', r'No problem', r'Here\'s', r"Here's",
                    r'Let me know', r'Feel free', r'Please let me know',
                    r'That\'s all', r'That is all', r'Hope this helps',
                    r'Is there anything', r'Anything else',
                ]
                is_boilerplate = any(re.search(p, content_stripped, re.IGNORECASE) for p in boilerplate_patterns)
                
                # Only detect loops in substantive content
                if len(content_text) > 100 and not is_boilerplate:
                    # Use content fingerprint (normalized) for better detection
                    content_normalized = re.sub(r'\s+', ' ', content_stripped.lower())[:200]
                    content_fingerprint = hash(content_normalized)
                    
                    # Track recent fingerprints
                    recent_fingerprints.append((turn_number, content_fingerprint, content_normalized[:80]))
                    
                    # Check for similar fingerprints in recent turns
                    for prev_turn, prev_fp, prev_norm in recent_fingerprints[-15:]:
                        if prev_fp == content_fingerprint and abs(turn_number - prev_turn) >= 3:
                            # Same content but not consecutive - likely a loop
                            loop_candidates.append({
                                'turns': [prev_turn, turn_number],
                                'content_preview': content_text[:100],
                                'severity': 'high' if turn_number - prev_turn < 10 else 'medium'
                            })
                    
                    # Keep window manageable
                    if len(recent_fingerprints) > 30:
                        recent_fingerprints.pop(0)
        
        # Accumulate content for CONSOLE_WARNING
        if line.startswith('  | ') and entry_type == 'CONSOLE_WARNING' and console_warnings:
            content_text = line[4:]
            if console_warnings:
                console_warnings[-1]['content'] += content_text
    
    # Final flush
    if current_role and current_content:
        content_text = '\n'.join(current_content)
        if current_role == 'USER':
            user_contents.append(content_text)
        elif current_role == 'ASSISTANT':
            assistant_contents.append(content_text)
    
    # Time analysis
    time_gaps = []
    for i in range(1, len(entry_timestamps)):
        if entry_timestamps[i][0] and entry_timestamps[i-1][0]:
            gap = (entry_timestamps[i][0] - entry_timestamps[i-1][0]).total_seconds()
            time_gaps.append(gap)
    
    # Session duration — use first SESSION_START timestamp, not first entry
    # (LLM_CALL entries from previous sessions can appear before SESSION_START)
    # Also handle negative durations by using min/max timestamps from all entries
    session_duration = None
    session_start_timestamps = [ts for ts, etype in entry_timestamps if etype == 'SESSION_START' and ts]
    all_timestamps = [ts for ts, _ in entry_timestamps if ts]
    if all_timestamps:
        first_ts = min(all_timestamps)
        last_ts = max(all_timestamps)
        session_duration = (last_ts - first_ts).total_seconds()
        # If negative (shouldn't happen with min/max), force to absolute
        if session_duration < 0:
            session_duration = abs(session_duration)
    
    # Detect negative duration (timestamp ordering issue)
    negative_duration = session_duration is not None and session_duration < 0
    
    # Detect TOOL_CALL vs TOOL_RESULT mismatch (incomplete operations)
    tool_call_count = tool_calls.total()
    tool_result_count = tool_results.total()
    unmatched_tool_calls = tool_call_count - tool_result_count
    incomplete_operations = unmatched_tool_calls > 0
    
    # Detect ghost sessions: many SESSION_STARTs with zero FORK/SUBAGENT markers
    session_start_count = entry_types.get('SESSION_START', 0)
    fork_start_count = entry_types.get('FORK_START', 0)
    subagent_start_count = entry_types.get('SUBAGENT_START', 0)
    has_nesting = any('nesting=' in line for line in lines if any(etype in line for etype in ('TOOL_CALL', 'TOOL_RESULT', 'LLM_CALL', 'FORK_START', 'FORK_END', 'SUBAGENT_START', 'SUBAGENT_END')))
    ghost_session = session_start_count > 5 and (fork_start_count + subagent_start_count) == 0 and not has_nesting
    
    # Extract fork tasks from FORK_START entries
    fork_tasks_from_entries = []
    for line_idx, line in enumerate(lines):
        if 'FORK_START' in line:
            task_m = re.search(r"task='([^']+)'", line)
            if task_m:
                task = task_m.group(1)
                if len(task) < 200:
                    fork_tasks_from_entries.append(task)
    
    # Extract subagent tasks from SUBAGENT_START entries
    subagent_tasks_from_entries = []
    for line_idx, line in enumerate(lines):
        if 'SUBAGENT_START' in line:
            task_m = re.search(r"task='([^']+)'", line)
            if task_m:
                task = task_m.group(1)
                if len(task) < 200:
                    subagent_tasks_from_entries.append(task)
    
    # Detect LLM_CALL before first SESSION_START (edge case)
    llm_before_session_start = False
    first_session_start_idx = None
    for i, (ts, etype) in enumerate(entry_timestamps):
        if etype == 'SESSION_START':
            first_session_start_idx = i
            break
    if first_session_start_idx is not None:
        for i in range(first_session_start_idx):
            if entry_timestamps[i][1] == 'LLM_CALL':
                llm_before_session_start = True
                break
    
    # Calculate tool stats
    avg_tool_durations = {}
    for tool, durations in tool_durations.items():
        if durations:
            avg_tool_durations[tool] = sum(durations) / len(durations)
    
    # Build results
    results = {
        'metadata': session_meta,
        'conversation': {
            'total_lines': total_lines,
            'user_turns': user_turns,
            'assistant_turns': assistant_turns,
            'avg_assistant_length': sum(len(c) for c in assistant_contents) / max(1, len(assistant_contents)),
            'max_assistant_length': max((len(c) for c in assistant_contents), default=0),
            'min_assistant_length': min((len(c) for c in assistant_contents) if assistant_contents else [0]),
        },
        'entry_types': dict(entry_types.most_common(20)),
        'tools': {
            'available': tool_names,
            'calls': dict(tool_calls.most_common(20)),
            'results': dict(tool_results.most_common(10)),
            'errors': dict(tool_errors.most_common(10)),
            'most_used': tool_calls.most_common(1)[0] if tool_calls else None,
            'avg_durations': avg_tool_durations,
        },
        'errors': {
            'total_count': len(errors),
            'types': dict(error_types.most_common(10)),
            'locations': error_locations[:20],
            'sample_messages': [e['content'] for e in errors[:5]],
        },
        'skills': dict(skill_calls.most_common(10)),
        'forking': {
            'fork_calls': fork_calls,
            'subagent_calls': subagent_calls,
            'sample_tasks': fork_tasks[:10],
            'fork_tasks_from_entries': fork_tasks_from_entries[:10],
            'subagent_tasks_from_entries': subagent_tasks_from_entries[:10],
        },
        'time': {
            'session_duration_seconds': session_duration,
            'avg_entry_gap_seconds': sum(time_gaps) / max(1, len(time_gaps)),
            'max_entry_gap_seconds': max(time_gaps) if time_gaps else 0,
            'min_entry_gap_seconds': min(time_gaps) if time_gaps else 0,
        },
        'content_quality': {
            'uncertainty_count': content_quality['uncertainty_count'],
            'confidence_count': content_quality['confidence_count'],
            'self_correction_count': content_quality['self_correction_count'],
            'long_responses_count': len(content_quality['long_responses']),
            'short_responses_count': len(content_quality['short_responses']),
        },
        'loops': {
            'candidates': loop_candidates[:10],
            'candidate_count': len(loop_candidates),
        },
        'edge_cases': {
            'negative_duration': negative_duration,
            'llm_before_session_start': llm_before_session_start,
            'ghost_session': ghost_session,
            'incomplete_operations': incomplete_operations,
            'unmatched_tool_calls': unmatched_tool_calls,
            'session_start_count': session_start_count,
            'fork_start_count': fork_start_count,
            'subagent_start_count': subagent_start_count,
        },
        'console_warnings': {
            'count': len(console_warnings),
            'sample': [w['content'][:200] for w in console_warnings[:5]],
        },
        'summary': {
            'health': 'healthy' if len(errors) < 3 and len(loop_candidates) == 0 else 
                     'degraded' if len(errors) < 10 else 'unhealthy',
            'efficiency': 'high' if assistant_turns < 20 and len(errors) < 3 else
                         'medium' if assistant_turns < 50 else 'low',
            'tool_success_rate': tool_results.get('success', 0) / max(1, sum(tool_results.values())) * 100 if tool_results else 0,
        }
    }
    
    return results

def print_results(results):
    """Print formatted analysis results."""
    print("=" * 70)
    print("TAU AUDIT ANALYSIS REPORT")
    print("=" * 70)
    
    # Metadata
    meta = results['metadata']
    print(f"\n## Session Metadata")
    print(f"  PID: {meta.get('pid', 'N/A')}")
    print(f"  Model: {meta.get('model', 'N/A')}")
    print(f"  Tools available: {meta.get('tools_count', 'N/A')}")
    print(f"  Working directory: {meta.get('cwd', 'N/A')}")
    
    # Conversation
    conv = results['conversation']
    print(f"\n## Conversation Structure")
    print(f"  Total lines: {conv['total_lines']:,}")
    print(f"  User turns: {conv['user_turns']}")
    print(f"  Assistant turns: {conv['assistant_turns']}")
    print(f"  Avg assistant response: {conv['avg_assistant_length']:,.0f} chars")
    print(f"  Max response: {conv['max_assistant_length']:,} chars")
    print(f"  Min response: {conv['min_assistant_length']:,} chars")
    
    # Entry types
    print(f"\n## Entry Types")
    for etype, count in results['entry_types'].items():
        print(f"  {etype}: {count}")
    
    # Tools
    tools = results['tools']
    print(f"\n## Tool Usage")
    if tools['calls']:
        print(f"  Tool calls: {dict(tools['calls'])}")
    if tools['results']:
        print(f"  Tool results: {dict(tools['results'])}")
    if tools['errors']:
        print(f"  Tool errors: {dict(tools['errors'])}")
    if tools['avg_durations']:
        print(f"  Avg durations (ms):")
        for tool, dur in sorted(tools['avg_durations'].items(), key=lambda x: -x[1])[:10]:
            print(f"    {tool}: {dur:.0f}ms")
    
    # Errors
    errors = results['errors']
    print(f"\n## Errors ({errors['total_count']} total)")
    if errors['types']:
        print(f"  Types: {dict(errors['types'])}")
    if errors['sample_messages']:
        print(f"  Sample errors:")
        for msg in errors['sample_messages'][:3]:
            print(f"    - {msg[:150]}")
    
    # Skills
    if results['skills']:
        print(f"\n## Skills Used")
        for skill, count in results['skills'].items():
            print(f"  {skill}: {count}")
    
    # Forking
    fork_info = results['forking']
    print(f"\n## Forking")
    print(f"  Fork calls: {fork_info['fork_calls']}")
    print(f"  Subagent calls: {fork_info['subagent_calls']}")
    
    # Time
    time_info = results['time']
    print(f"\n## Time Analysis")
    if time_info['session_duration_seconds']:
        dur = time_info['session_duration_seconds']
        print(f"  Duration: {dur:.0f}s ({dur/60:.1f}min)")
    print(f"  Avg entry gap: {time_info['avg_entry_gap_seconds']:.1f}s")
    print(f"  Max entry gap: {time_info['max_entry_gap_seconds']:.1f}s")
    
    # Content quality
    cq = results['content_quality']
    print(f"\n## Content Quality")
    print(f"  Uncertainty indicators: {cq['uncertainty_count']}")
    print(f"  Confidence indicators: {cq['confidence_count']}")
    print(f"  Self-corrections: {cq['self_correction_count']}")
    print(f"  Long responses (>1KB): {cq['long_responses_count']}")
    print(f"  Short responses (<30 chars): {cq['short_responses_count']}")
    
    # Loops
    loops = results['loops']
    print(f"\n## Loop Detection")
    print(f"  Candidates: {loops['candidate_count']}")
    if loops['candidates']:
        print(f"  Top candidates:")
        for c in loops['candidates'][:3]:
            print(f"    Turns {c['turns']}: {c['content_preview'][:80]}")
    
    # Console warnings
    cw = results['console_warnings']
    if cw['count']:
        print(f"\n## Console Warnings ({cw['count']} total)")
        for w in cw['sample'][:3]:
            print(f"  - {w[:150]}")
    
    # Summary
    summary = results['summary']
    print(f"\n## Summary")
    print(f"  Health: {summary['health']}")
    print(f"  Efficiency: {summary['efficiency']}")
    print(f"  Tool success rate: {summary['tool_success_rate']:.1f}%")
    print("=" * 70)

def main():
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <audit_file> [--json] [--top N]", file=sys.stderr)
        sys.exit(1)
    
    filepath = sys.argv[1]
    output_json = '--json' in sys.argv
    top_n = 10
    
    results = analyze_audit(filepath)
    
    if output_json:
        print(json.dumps(results, indent=2, default=str))
    else:
        print_results(results)

if __name__ == '__main__':
    main()
