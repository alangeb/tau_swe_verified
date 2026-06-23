---
description: Tau dreaming
---
/heartbeat 180
---
/heartbeat on
---
Your heartbeat is enabled (180 seconds). You can use it - either to wait - or to get back on track if you lost track.
Your task is to be supervisor + manager. Try to avoid doing any work yourself. For the few things you will eventually need todo heavily rely on fork and subagent.

This is an endless loop - unless you envounter a critical problem you just continue doing this again and again.

Use your info tool. Ensure you are in tau src folder (you should see i.e. sanity.sh file, tau.py, etc.).
Use your run background skill - when you run ./tau.py run it in background (only 1 instance).
Take long sleeps in between checks, some of these runs will take hours, as general pattern maybe check after 1min, 5min, 15min, then every 30min from there on.
If a task runs more than 4h you should start to get suspicious, closely inspect, determine how to recover or kill it.
Maybe try that out, run ./tau.py "hi" or ./tau.py "/help" (you can even add multiple prompts).

Look around, familiarize yourself so you understand the task.

Let's do the following things, step by step:
1.) WHILE folder ../tasks/1_todo contains markdown (.md) files,
    DO run (this should remove 1 file): ./tau.py "/_taudotask"
    (and repeat this until there are no .md files left in the folder)
2.) Run 3 times: ./tau.py "/_taurearch"
3.) Run ./tau.py "/_tautestcommands"
    IF you encountered any failures, fork an agent to fix them. Then re-run.
4.) Run ./tau.py "/_tautestsanity"
    IF you encountered any failures, fork an agent to fix them. Then re-run.
5.) Run ./tau.py "/_tauskillmaintenance"
6.) Run ./tau.py "/_taudoc"
7.) Run ./tau.py "/_taulogreview"
    Think about output. Create new tasks to improve tau (you) in ../tasks/1_todo (.md files).
Now start over at 1.

Between steps review changes. If they are solid commit them to git (git commit).

Supervise the process. If something goes wrong, try to fix it. If you end up at a critical problem STOP, wait for user, I will help. Be careful with the system you are running on, assume its a production system.

$*
