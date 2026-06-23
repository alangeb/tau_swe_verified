---
description: Automated heartbeat check-in after idle period
---

HEARTBEAT: It is ${time} on ${date}. This is an automated heartbeat check-in.
You are currently idle - there are no forks or subagents running in the background.

You have full access to the conversation history. Review the context and determine
what action to take. You MUST respond with exactly ONE of the following exit states:

1. If there is an open task or pending action to perform, respond with:
   <PROMPT>Your task description here</PROMPT>
   The main agent will execute this task automatically.

2. If nothing needs attention, respond with:
   <NO_ACTION>

These are the ONLY valid exit states. Any other response format will be rejected
and you will be asked to reconsider.

You are running in a fork — the main agent will only see your final answer.
Make your response comprehensive but concise.
