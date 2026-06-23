#!/bin/bash
# worktree_ops.sh — Git worktree operation helpers
# Source or run individual functions

# Verify worktree identity
wt_verify() {
    test -f .git || { echo "ERROR: Not a worktree"; return 1; }
    local branch
    branch=$(git branch --show-current)
    local main_repo
    main_repo=$(cat .git | sed 's/^gitdir: \(.*\)\/.git\/worktrees\/.*$/\1/')
    echo "Branch: $branch"
    echo "Main repo: $main_repo"
}

# Quick status check
wt_status() {
    wt_verify || return 1
    git status --short
    git diff --stat HEAD
}

# List worktrees
wt_list() {
    local main_repo="$1"
    [ -z "$main_repo" ] && main_repo="."
    git -C "$main_repo" worktree list
}
