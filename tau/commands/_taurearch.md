---
description: Tau re-architecture
---
/pyprep
---
Use your ast-grep and code-simplifier skills.

**ANALYSIS LIMIT: Maximum 3 analysis tool calls (info, pyscan, pyanalyze, think). After 3 calls, you MUST start implementing.**

Review the entire code architecture, ensure you understand the code.
Assume the implementation is correct, especially edge cases, assume they are implemented with purpose.

Your task is to review the architecture of the project.
Decide on one thing to improve. Be careful not to miss edge cases. You can create new files, move code, clean up interfaces, encapsulate code, inline, standardize - whatever most important.

**MANDATORY: After deciding what to improve, immediately start implementing. Do NOT create elaborate plans. Do NOT analyze further. Implement directly.**

Use your plan tool to create tasks to improve/fix the one most important thing. Do not change functionality. Assume everything is done for a purpose. But do make it more clean.

Then execute on all the changes. Implement the changes.

Heavily rely on subagent and fork: Do only what you must yourself, delegate the rest to fork or subagent.

$*
---
/gitcrit
---
Review your changes. Fix what needs fixing. Stay close to original instructions.
---
/gitcrit
---
Review your changes. Fix what needs fixing. Stay close to original instructions.
---
Run pytests, sanity tests. Fix code if it is broken.
If you manage to fix everything, commit the changes to git (git commit).
If you encounter permanent failures, revert all changes to original.
Report on what was done.
