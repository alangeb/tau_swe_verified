---
name: git-advanced
description: Analyze git history, bisect bugs, cherry-pick commits, revert changes, manage stashes. Advanced git operations. (also load: git, code-review-workflow)
category: development
---

# Git Advanced

## When
"git log analysis", "git bisect", "git stash", "cherry-pick", "git revert", "git blame"

## Log Analysis
```bash
git log --oneline -20                    # Recent commits
git log --stat --since="1 week ago"     # Changes by date
git log --author="name" --oneline        # Author history
git log -S"search_string" --oneline     # Find when string was added
```

## Bisect
```bash
git bisect start                       # Begin bisect
git bisect bad                         # Mark current as bad
git bisect good v1.0                  # Mark known good
# Git auto-checks midpoints
git bisect reset                       # End bisect
```

## Stash
```bash
git stash save "WIP: description"     # Save changes
git stash list                        # List stashes
git stash apply                       # Restore changes
git stash drop                        # Remove stash
```

## Cherry-Pick
```bash
git cherry-pick <commit>              # Apply single commit
git cherry-pick <start>..<end>        # Apply range
git cherry-pick --abort              # Cancel cherry-pick
```

## Revert
```bash
git revert <commit>                   # Create reverse commit
git revert --no-commit <commit>      # Revert without commit
```

## Related Skills
- `git` — basic worktree operations
- `code-review-workflow` — review changes before committing
- `bug_investigation` — use bisect for bug hunting
- `tau_audit` — analyze commit patterns
