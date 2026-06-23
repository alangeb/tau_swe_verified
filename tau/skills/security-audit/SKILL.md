---
name: security-audit
description: Security checks — dependency audit, secrets detection, sensitive file handling (also load: dependency_management, bug_investigation)
category: security
---

# Security Audit

## When
"security check", "dependency audit", "secrets scan", "sensitive files", "vulnerability check"

## Dependency Audit
```bash
pip audit                              # Check for vulnerabilities
pip check                              # Check for conflicts
pip list --outdated                   # Find outdated packages
```

## Secrets Detection
```bash
grep -rn "password\|secret\|api_key\|token" . --include="*.py"
grep -rn "AKIA[0-9A-Z]{16}" .          # AWS keys
grep -rn "ghp_[a-zA-Z0-9]{36}" .       # GitHub tokens
```

## Sensitive Files
```bash
find . -name "*.pem" -o -name "*.key" -o -name "*.crt"
find . -name ".env" -o -name "*.env.*"
find . -name "*.pgpass" -o -name ".netrc"
```

## Checklist
- [ ] Dependencies audited for vulnerabilities
- [ ] No hardcoded secrets in code
- [ ] Sensitive files in .gitignore
- [ ] No credentials in commit history
- [ ] File permissions correct (600 for sensitive)

## Related Skills
- `dependency_management` — manage Python dependencies
- `bug_investigation` — investigate security issues
- `code-review-workflow` — review code for security issues
- `git` — check commit history for leaked secrets
