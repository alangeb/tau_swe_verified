---
description: Tau log review — analyze audit logs for errors, patterns, and improvement opportunities
---

# /_taulogreview — Log Review

look through our logs in $HOME/.local/tau/log .
be very carefully, there might be many, some of them are huge.
you need to think about a clever approach to do this without just attempting to load huge files into your context (otherwise your context will overfill and you will fail).
if you use a fork or subagent you will probably be able to look at 50kb pieces at a time, even that is hard.

---

plan carefully on how to best do this. we are interested in finding common errors and failures, then group them into categories, then count them. analyze our failures, where did we go wrong.

---

from there we would attempt to improve so we would do better in the future.
