---
description: Worker implements code, then a reviewer checks it
---
When implementing a task that benefits from review, use the subagent tool with the chain parameter to run both agents in sequence.

First, send the task to the worker agent to produce an implementation. Then pass the result to the reviewer agent for feedback. The worker can iterate on the changes if needed.
