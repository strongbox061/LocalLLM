---
description: Worker implements, reviewer reviews, worker applies the review
---
Use the subagent tool with the chain parameter for this workflow:

1. First, the "worker" agent implements: $@
2. Then, the "reviewer" agent reviews that implementation (use the {previous} placeholder)
3. Finally, the "worker" agent applies the review's findings (use the {previous} placeholder)

Run it as one chain, passing each answer on with {previous}.
