#!/usr/bin/env python3
"""debug_session.py — Template for interactive Python debug sessions."""
import sys
import os

def setup_debug(target_file, cwd=None):
    """Set up debug environment."""
    if cwd:
        os.chdir(cwd)
    sys.path.insert(0, '.')
    print(f"Debug target: {target_file}")
    print(f"CWD: {os.getcwd()}")
    print(f"Python path: {sys.path[0]}")

def create_test_script(target_file, test_func):
    """Create a test script with breakpoint."""
    script = f'''#!/usr/bin/env python3
import sys
sys.path.insert(0, '.')
from {os.path.splitext(os.path.basename(target_file))[0]} import {test_func}

# Add test data here
test_data = [...]

print("Starting debug session...")
breakpoint()
result = {test_func}(test_data)
print(f"Result: {{result}}")
'''
    return script

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: debug_session.py <target_file> <function_name>")
        sys.exit(1)
    setup_debug(sys.argv[1])
    print(create_test_script(sys.argv[1], sys.argv[2]))
