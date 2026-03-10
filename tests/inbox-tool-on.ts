// Test plugin: enables the plannotator_inbox tool for interactive sessions
export default function (pi: any) {
  pi.on("before_agent_start", () => {
    const name = "plannotator_inbox";
    if (pi.getAllTools?.()?.some((t: any) => t?.name === name)) {
      const active = pi.getActiveTools?.() ?? [];
      if (!active.includes(name)) pi.setActiveTools([...active, name]);
    }
  });
}
