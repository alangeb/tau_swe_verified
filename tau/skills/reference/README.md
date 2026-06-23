# Skills Reference

Comprehensive documentation for skills. These are **NOT** loaded into agent context — use `skills/` for runtime injection.

## Directory Structure

```
skills/              — Lightweight skills (~50 lines) for runtime injection
skills/reference/    — Comprehensive documentation for each skill
```

## Why Two Tiers?

Skills serve dual purposes:
1. **Runtime context**: Injected into agent conversations (needs minimal tokens)
2. **Documentation**: Reference material for understanding (needs completeness)

Compressing skills to save tokens destroys documentation value. The two-tier approach preserves both:
- `skills/` stays lightweight for injection
- `skills/reference/` preserves institutional knowledge

## Convention

- Each skill in `skills/` has a corresponding `skills/reference/<name>.md`
- Reference docs are comprehensive, examples-rich, self-contained
- Runtime skills are keyword-dense, cross-reference-heavy, minimal

## Maintenance

Run `python3 validate_skills.py` to check:
- Every runtime skill has a reference doc
- Reference docs meet minimum content thresholds
- Cross-references are valid
- Frontmatter is well-formed
