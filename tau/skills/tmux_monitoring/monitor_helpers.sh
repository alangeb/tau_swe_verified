#!/bin/bash
# Tmux monitoring helpers — session management, polling, completion detection

# Get session status
tmux_session_status() {
    local session="$1"
    if tmux has-session -t "$session" 2>/dev/null; then
        echo "active"
        return 0
    else
        echo "dead"
        return 1
    fi
}

# Poll for completion with smart detection
tmux_poll() {
    local session="$1"
    local max_wait="${2:-300}"
    local idle="${3:-30}"
    local keywords="${4:-complete|error|done|FAILED|SUCCESS}"
    
    local start=$SECONDS
    local last_output=""
    
    while (( SECONDS - start < max_wait )); do
        if ! tmux has-session -t "$session" 2>/dev/null; then
            echo "SESSION_DEAD"
            return 1
        fi
        
        local output
        output=$(tmux capture-pane -t "$session" -p -S -50 2>/dev/null | tail -20)
        
        if echo "$output" | grep -qiE "$keywords"; then
            echo "KEYWORD_MATCH: $output"
            return 0
        fi
        
        if [ "$output" = "$last_output" ]; then
            local idle_time=$(( SECONDS - ${TMUX_LAST_ACTIVE:-$SECONDS} ))
            if (( idle_time > idle )); then
                echo "IDLE: No output for ${idle_time}s"
                return 2
            fi
        else
            TMUX_LAST_ACTIVE=$SECONDS
        fi
        last_output="$output"
        
        sleep 5
    done
    
    echo "TIMEOUT: Waited ${max_wait}s"
    return 3
}

# Calculate recommended poll interval based on task duration
poll_interval() {
    local duration=$1
    if (( duration < 5 )); then
        echo 30
    elif (( duration <= 60 )); then
        echo 60
    elif (( duration <= 300 )); then
        echo 120
    else
        echo 300
    fi
}

# List active sessions with status
tmux_list_sessions() {
    tmux list-sessions 2>/dev/null | while read -r line; do
        local name=$(echo "$line" | cut -d: -f1)
        local windows=$(echo "$line" | cut -d: -f2 | tr -d ' ')
        echo "$name: $windows windows"
    done
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    case "${1:-}" in
        status) tmux_session_status "$2" ;;
        poll) tmux_poll "$2" "$3" "$4" "$5" ;;
        interval) poll_interval "$2" ;;
        list) tmux_list_sessions ;;
        *) echo "Usage: $0 {status|poll|interval|list} [args...]"; exit 1 ;;
    esac
fi
