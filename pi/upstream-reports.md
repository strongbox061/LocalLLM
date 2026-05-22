# Drafts of upstream bug reports

Drafts only: nothing has been posted. Each one was checked against the versions below on 2026-11-06.

---

## 1. pi-agent-browser-native: browsers keep running after `pi -p` exits

**Post to:** https://github.com/fitchmultz/pi-agent-browser-native/issues (a search for leak, orphan and print found no matching issue)

**Title:** Managed browser daemons and Chrome processes survive after `pi -p` exits

**Versions:** pi-agent-browser-native 0.9.16, Pi 1.5.0, agent-browser 0.41.2, Arch Linux

**What happens**

Each `pi -p` run that uses `agent_browser` leaves its daemon and Chrome processes running after Pi exits. The daemon has its own process session and is re-parented to the user's service manager, so ending Pi doesn't stop it. In a batch of about 80 runs this reached 535 processes and 21.4 GB of RAM, which pushed the model into swap and slowed later runs by more than half (27 to 10.5 tokens/s).
