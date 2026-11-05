---
description: The code-finder finds the code, planner writes a plan (nothing is changed)
---
Use the subagent tool with the chain parameter for this workflow:

1. First, the "code-finder" agent finds all code relevant to: $@
2. Then, the "planner" agent writes an implementation plan for "$@" from the code-finder's findings (use the {previous} placeholder)

Run it as one chain, passing the code-finder's answer on with {previous}. Don't implement anything: return the plan.
