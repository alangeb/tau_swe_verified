#!/usr/bin/env python3
"""Tool template helper - generate tool templates."""

from typing import Optional


def generate_tool_template(name: str, description: str, args: list[dict] = None) -> str:
    """Generate a tool template."""
    args_code = ""
    if args:
        for arg in args:
            args_code += f"    {arg['name']}: {arg['type']} = Field(description=\"{arg['description']}\")\n"
    
    template = f"""from __future__ import annotations

name = "{name}"
description = """{description}."""
timeout = 180

from pydantic import BaseModel, Field

class Args(BaseModel):
{args_code or '    pass'}

def run(agent: 'TauBot', tool_call_id: str | None) -> str:
    return "result"
"""
    return template


if __name__ == "__main__":
    import sys
    name = sys.argv[1] if len(sys.argv) > 1 else "new_tool"
    description = sys.argv[2] if len(sys.argv) > 2 else "Brief description"
    template = generate_tool_template(name, description)
    print(template)
