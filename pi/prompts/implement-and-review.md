---
description: Worker implements, reviewer reviews, worker applies feedback
---
Use the subagent tool with the chain parameter for this workflow:

1. First, the "worker" agent implements: $@
2. Then, the "reviewer" agent reviews that implementation (use the {previous} placeholder)
3. Finally, the "worker" agent applies the review's findings (use the {previous} placeholder)

Run as a single chain, forwarding each response with {previous}.

