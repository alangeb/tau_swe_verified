#!/usr/bin/env python3
"""command_gen.py — Command template generator."""
import os

def create_md_command(name, description, content, path="commands"):
    """Create markdown command template."""
    cmd_path = os.path.join(path, f"{name}.md")
    content = f"""---
description: {description}
---
{content}
"""
    os.makedirs(path, exist_ok=True)
    with open(cmd_path, "w") as f:
        f.write(content)
    return cmd_path

def create_py_command(name, description, run_code, path="commands"):
    """Create Python command template."""
    cmd_path = os.path.join(path, f"{name}.py")
    content = f'''name = "{name}"
description = "{description}"

def run(agent, args):
    {run_code}
'''
    os.makedirs(path, exist_ok=True)
    with open(cmd_path, "w") as f:
        f.write(content)
    return cmd_path
