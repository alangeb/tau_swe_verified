---
name: tauskillmaintenance
description: Periodic skill maintenance — audit, update, create skills, verify cross-references, check findability, analyze usage patterns. (also load: skill_template, command_template, caveman, tau_audit)
category: maintenance
---

# Tau Skill Maintenance

## When
"skill audit", "maintain skills", "update skills", "skill maintenance", "review skills", "skill health", "skill usage", "skill gaps"

## 4-Phase Audit Process

### Phase 1: Audit Log Analysis
```bash
# Tool usage patterns
grep -rh "final_name='[^']*" ~/.local/tau/log/*_2026*_1.audit | \
  sed "s/.*final_name='"// | sort | uniq -c | sort -rn
```

### Phase 2: Skill Quality Audit
- [ ] Frontmatter: `name`, `description`, `category` present
- [ ] Description contains `(also load: related_skills)`
- [ ] Description has search-trigger keywords
- [ ] Concise — no tutorials, no obvious content
- [ ] Self-audience — assumes reader knows basics
- [ ] Has "When" section with trigger keywords
- [ ] Has "Related Skills" section with cross-references

### Phase 3: Findability & Cross-References
```python
# Check bidirectionality
import os, re
skills = {d: open(os.path.join("skills", d, "SKILL.md")).read()
          for d in os.listdir("skills")
          if os.path.exists(os.path.join("skills", d, "SKILL.md"))}
existing = set(skills.keys())
for name, content in skills.items():
    refs = set(re.findall(r'also load:\s*(.+)', content))
    # Check bidirectionality...
```

### Phase 4: Identify Missing Skills
1. Map high-frequency tools to skills
2. Find gaps (tools with no skill coverage)
3. Create skills for high-value patterns
4. Improve low-value skill discoverability

## Maintenance Checklist
- [ ] Run audit log analysis
- [ ] Check all skills against quality checklist
- [ ] Verify cross-reference bidirectionality
- [ ] Identify tool coverage gaps
- [ ] Create/improve skills as needed
- [ ] Update this skill with new patterns

## Related Skills
- `skill_template` — format and structure for new skills
- `command_template` — sibling concept for commands
- `caveman` — concise writing style for skills
- `tau_audit` — analyze agent logs for behavior patterns
