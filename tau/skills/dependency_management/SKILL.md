---
name: dependency_management
description: Manage Python dependencies — pip install, venv setup, requirements files, package isolation (also load: python_best_practices, project-onboard, security-audit)
category: development
---

# Dependency Management

## When
"install package", "pip install", "venv setup", "requirements", "missing import"

## Virtual Environment
```bash
# Create
python3 -m venv .venv
source .venv/bin/activate

# Check active
which python                                              # Should point to .venv
pip list                                                   # Installed packages
```

## Install Patterns
```bash
# Single package
pip install package_name

# From requirements
pip install -r requirements.txt

# Specific version
pip install package==1.2.3

# Dev dependencies
pip install -r dev-requirements.txt
```

## Requirements File Management
```bash
# Generate
pip freeze > requirements.txt

# Update single
pip install --upgrade package
pip freeze > requirements.txt

# Check for outdated
pip list --outdated
```

## Common Issues
- **Permission denied**: Use `pip install --user` or venv
- **Conflicting versions**: Check `pip check`
- **Missing build deps**: `apt install python3-dev build-essential`
- **Binary wheels**: `pip install --no-binary :all:` for source build

## Related Skills
- `python_best_practices` — linting/formatting installed tools
- `project-onboard` — discover project dependencies
- `tau_testsuite` — test dependencies
