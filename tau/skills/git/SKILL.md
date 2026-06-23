---
name: git
description: Git worktree operations — commit changes, sync with master, verify diffs (also load: code-review-workflow, review, python_best_practices, git-advanced, security-audit)
category: development
---

# Git Worktree Operations

## When
"git commit", "git sync", "merge master", "rebase worktree", "git worktree", "sync with master", "commit changes"

## FACTS

| Key | Value |
|-----|-------|
| Worktree | `$(pwd)` |
| Branch | `$(git branch --show-current)` — NOT folder name |
| Main repo | `$(cat .git \| sed 's/^gitdir: \(.*\)\/.git\/worktrees\/.*$/\1/')` |

## NEVER

- Switch branches — worktree is LOCKED to one branch
- Use folder name as branch name — always `git branch --show-current`
- Remove the worktree
- Use `git merge` for sync — use `git rebase` + `git merge --ff-only`
- Blindly accept `--theirs` or `--ours` on conflicts — examine both sides
- Force-push or reset master — master is sacred

## Verify Changes

```bash
git -C . diff --stat HEAD                 # Summary
git -C . diff HEAD                          # Full
# git -C . diff HEAD -- <file>              # Specific
```

### Checklist
- [ ] Changes match intent
- [ ] No unintended modifications
- [ ] Formatting consistent
- [ ] No leftover debug code

## Commit Changes

1. Verify worktree: `test -f .git` (FILE, not directory)
2. Verify branch: `git branch --show-current`
3. `git status` — if clean, report and STOP
4. `git diff` — review changes (use Verify Changes checklist)
5. `git add -A`
6. `git commit` — specific message based on actual changes
7. `git log --oneline -3` — verify
8. Report progress

## Sync Worktree with Master

### 1. Verify identity
```
test -f .git  # worktree: .git is FILE
MAIN_REPO=$(cat .git | sed 's/^gitdir: \(.*\)\/.git\/worktrees\/.*$/\1/')
test -d "$MAIN_REPO/.git"  # main: .git is DIR
BRANCH=$(git branch --show-current)
```

### 2. Rebase worktree onto master
```
git fetch "$MAIN_REPO" master
git rebase FETCH_HEAD "$BRANCH"
# If conflicts: examine --ours (master) and --theirs (worktree), resolve intelligently
# If you cannot resolve: STOP
```

### 3. Fast-forward merge into master
```
ORIG_DIR=$(pwd)  # save worktree root
cd "$MAIN_REPO"
git checkout master
git merge --ff-only "$BRANCH"
# If --ff-only fails: STOP — something is wrong
```

### 4. Reset worktree to master
```
cd "$ORIG_DIR"  # back to worktree
git reset --hard FETCH_HEAD  # or: git fetch "$MAIN_REPO" master && git reset --hard FETCH_HEAD
```

### 5. Verify sync
```
git rev-parse HEAD == $(cd "$MAIN_REPO" && git rev-parse master)
```

## REPORT

- **ERROR:** rebase failed and cannot resolve, --ff-only failed, identity check failed
- **WARNING:** sync verification failed (worktree ≠ master)
- Always include full diagnostic context

## Related Skills

- `code-review-workflow` — full review pipeline
- `python_best_practices` — linting/formatting
- `review` — detailed code review
