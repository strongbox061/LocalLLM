// Test plugin: enables the plannotator_inbox tool for interactive sessions
export default function (pi: any) {
  pi.on("before_agent_start", () => {
    const name = "plannotator_inbox";
    try {
      const known = (pi.getAllTools?.() ?? []).some((t: any) => t?.name === name);
      const active: string[] = pi.getActiveTools?.() ?? [];
      if (known && !active.includes(name)) pi.setActiveTools([...active, name]);
    } catch {
      // Plugin API may throw in unexpected states; skip silently.
    }
  });
}
