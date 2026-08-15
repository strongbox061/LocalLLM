// Test-only (tests/run-pi-tests.sh loads it with -e for tests marked "needs-inbox"): turns on Plannotator's
// plannotator_inbox tool in a `pi -p` run. Plannotator leaves it off there, since no reply could wake a print-mode
// session, but J1 checks what an interactive session does: write PLAN.md and send it to the Inbox.
export default function (pi: any) {
  pi.on("before_agent_start", () => {
    const name = "plannotator_inbox";
    const known = (pi.getAllTools?.() ?? []).some((t: any) => t?.name === name);
    const active: string[] = pi.getActiveTools?.() ?? [];
    if (known && !active.includes(name)) pi.setActiveTools([...active, name]);
  });
}
