#!/usr/bin/env python3
"""Skill audit script — check quality, cross-references, findability."""
import os, re, sys

def audit_skills(skills_dir="skills"):
    skills = {}
    for d in sorted(os.listdir(skills_dir)):
        f = os.path.join(skills_dir, d, "SKILL.md")
        if os.path.exists(f):
            skills[d] = open(f).read()

    existing = set(skills.keys())
    issues = []

    # Check frontmatter
    for name, content in skills.items():
        if not content.startswith("---"):
            issues.append(f"{name}: Missing frontmatter")
        for field in ["name:", "description:", "category:"]:
            if field not in content.split("---")[1]:
                issues.append(f"{name}: Missing {field.strip()}")

    # Check search keywords
    for name, content in skills.items():
        desc = re.search(r'description:\s*(.+)', content)
        if desc:
            clean = desc.group(1).split(' (also load:')[0]
            if len(clean) < 30:
                issues.append(f"{name}: Short description ({len(clean)} chars)")

    # Check cross-references
    ref_graph = {}
    for name, content in skills.items():
        refs = set()
        desc = re.search(r'description:\s*(.+)', content)
        if desc:
            also = re.findall(r'also load:\s*(.+)', desc.group(1))
            if also:
                for r in also[0].split(','):
                    r = r.strip().rstrip(')')
                    if r in existing:
                        refs.add(r)
        related = re.findall(r'`([a-zA-Z][-_\w]*)`', content)
        for r in related:
            if r in existing:
                refs.add(r)
        ref_graph[name] = refs

    # Check bidirectionality
    one_way = []
    for a, refs in ref_graph.items():
        for b in refs:
            if a not in ref_graph.get(b, set()):
                one_way.append((a, b))

    # Report
    print(f"Skills: {len(skills)}")
    print(f"Issues: {len(issues)}")
    for issue in issues:
        print(f"  ⚠ {issue}")
    print(f"One-way refs: {len(one_way)}")
    for a, b in one_way:
        print(f"  {a} → {b}")

    return len(issues) == 0 and len(one_way) == 0

if __name__ == "__main__":
    skills_dir = sys.argv[1] if len(sys.argv) > 1 else "skills"
    success = audit_skills(skills_dir)
    sys.exit(0 if success else 1)
