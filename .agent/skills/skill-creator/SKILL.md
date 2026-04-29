---
name: skill-creator
description: "Create, test, and iteratively improve AI agent skills with structured evaluation and benchmarking. Use when building new skills, optimizing existing ones, or setting up skill evaluation workflows."
source: "anthropics/skills/skill-creator"
---

# Skill Creator

A skill for creating new skills and iteratively improving them.

## Workflow

1. **Capture Intent**: What should the skill enable? When should it trigger? Expected output format?
2. **Interview & Research**: Edge cases, input/output formats, success criteria, dependencies
3. **Write SKILL.md**: Name, description (with "pushy" trigger guidance), body instructions
4. **Create Test Cases**: 2-3 realistic prompts, save to `evals/evals.json`
5. **Run Tests**: Spawn with-skill and baseline runs in parallel
6. **Review & Iterate**: Generate viewer, collect feedback, improve, repeat

## SKILL.md Anatomy

```
skill-name/
├── SKILL.md (required)
│   ├── YAML frontmatter (name, description required)
│   └── Markdown instructions
└── Bundled Resources (optional)
    ├── scripts/   - Executable code
    ├── references/ - Docs loaded as needed
    └── assets/    - Templates, icons, fonts
```

## Key Principles
- Keep SKILL.md under 500 lines
- Progressive disclosure: metadata → body → references
- Description is the primary triggering mechanism — make it "pushy"
- Explain the WHY behind instructions, not just rigid MUSTs
- Generalize from feedback — don't overfit to test cases
- Bundle repeated helper scripts in `scripts/`
