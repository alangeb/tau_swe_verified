---
description: Tau do task
---
/pyprep
---
Use your ast-grep and code-simplifier skills.

**ANALYSIS LIMIT: Maximum 3 analysis tool calls (info, pyscan, pyanalyze, think). After 3 calls, you MUST start implementing.**

Look into folder structure ../tasks, you'll see the folders 1_todo, 2_inprogress, 3_done, 3_failed.

Chose an arbitrary file from 1_todo.
Move (mv) the file from 1_todo into 2_inprogress.
Read the file - the file describes the task/goal/activity you should perform.

**MANDATORY: After reading the task file, immediately start implementing. Do NOT create elaborate plans. Do NOT analyze further. Implement directly.**

Be careful not to miss edge cases. Goal is to perform the task/goal/activity from file you read.

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
If you manage to fix everything, move (mv) the 1 file in ../tasks/2_inprogress folder into ../task/3_done folder, and commit all changes to git (git commit).
If you encounter permanent failures, move (mv) the 1 file in ../tasks/2_inprogress folder into ../task/3_failed folder, then revert all other changes to original.
Report on what was done.
