---
description: The code-finder finds the code, planner writes a plan, worker carries it out
---
Use the subagent tool with the chain parameter for this workflow:

1. First, the "code-finder" agent finds all code relevant to: $@
2. Then, the "planner" agent writes an implementation plan for "$@"
3. Finally, the "worker" agent carries out the plan (use the {previous} placeholder)

Run it as one chain, passing each answer on with {previous}.
