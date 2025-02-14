// loader.js: voice fixes for Open WebUI, in every browser (desktop and phone), with nothing to install on the device.
//
// Open WebUI loads /static/loader.js on every page (an empty file of its own, meant for this). open-webui/
// install_loader.py puts this file there each time Open WebUI starts, on whichever PC runs it (README, "Voice in
// Open WebUI"). It changes three things, all in the browser:
//   1. Voice mode's answers play in browsers that block sound started without a tap (Brave, Safari on iPhone and
//      iPad, some phone browsers).
//   2. Voice mode's microphone is less sensitive to voices in another room (SPEECH_THRESHOLD_DB).
//   3. The browser's automatic gain control is off for voice mode's microphone (AUTO_GAIN).
// To change 2 or 3, edit the two values below; the next start of Open WebUI uses them (on the helper PC:
// ./localllm.sh deploy), then reload the page.
(() => {
  if (window.__localllmVoiceFix) return;
  window.__localllmVoiceFix = true;

  // Sound below this level doesn't count as you talking (Open WebUI's own: -55 dB). Higher (closer to 0) = less
  // sensitive: raise it if voices or laughter from another room still get picked up, lower it if it misses you.
  // -60 to -10.
  const SPEECH_THRESHOLD_DB = -40;
  // The browser's automatic gain control boosts distant voices while you are silent: off by default.
  const AUTO_GAIN = false;

  const nativePlay = HTMLMediaElement.prototype.play;
  const nativePause = HTMLMediaElement.prototype.pause;
  const log = (msg) => console.info(`[voice-fix] ${msg}`);

  // 1a. Unlock the audio players during a tap.
  //
  // Open WebUI's voice mode copies every sentence into one shared <audio> player and calls play() on it seconds
  // after your last tap (Open WebUI issue #28681). Strict browsers allow that only for a player that has already
  // played something during a tap. So during every tap or key press, and when a player appears right after one (voice
  // mode's player is created by the tap that opens it), each player that hasn't played yet plays an empty clip.
  const SILENCE = "data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YQAAAAA=";
  const unlocked = new WeakSet();
  // (#audioElement, the player for "Read aloud" and automatic playback, already does this itself.)
  const unlock = (el) => {
    if (el.id === "audioElement" || unlocked.has(el) || !el.paused || el.getAttribute("src")) return;
    unlocked.add(el);
    el.src = SILENCE;
    nativePlay.call(el).then(
      () => {
        if (el.getAttribute("src") !== SILENCE) return; // Open WebUI gave it a sentence meanwhile: leave it
        nativePause.call(el);
        el.removeAttribute("src");
        el.load();
      },
      () => {
        unlocked.delete(el);
        if (el.getAttribute("src") === SILENCE) {
          el.removeAttribute("src");
          el.load();
        }
      }
    );
  };
  const unlockAll = () => {
    try { document.querySelectorAll("audio").forEach(unlock); } catch {}
  };
  for (const type of ["pointerdown", "touchend", "click", "keydown"]) {
    window.addEventListener(type, unlockAll, { capture: true, passive: true });
  }
  const tapIsFresh = () => navigator.userActivation?.isActive === true;
  new MutationObserver((records) => {
    if (!tapIsFresh()) return;
    for (const r of records) {
      for (const n of r.addedNodes) {
        if (n instanceof HTMLAudioElement) unlock(n);
        else if (n instanceof Element) n.querySelectorAll("audio").forEach(unlock);
      }
    }
  }).observe(document.documentElement, { childList: true, subtree: true });

  // 1b. When a browser still refuses a sentence (NotAllowedError), play the same clip through a fresh audio object
  // (a "stand-in"), which browsers that allow autoplay for the site accept, and pass its playing/waiting/ended/error
  // events on to the original player, so voice mode sees the sentence play and end and moves on. pause() on the
  // original (leaving voice mode, interrupting) stops the stand-in too. When play() is allowed, nothing changes.
  const standIns = new WeakMap(); // original player -> its current stand-in
  const FORWARDED = ["playing", "waiting", "ended", "error"];
  const stopStandIn = (el) => {
    const s = standIns.get(el);
    if (!s) return;
    standIns.delete(el);
    s.__voiceFixStopped = true;
    nativePause.call(s);
  };
  const playThroughStandIn = (el) => {
    stopStandIn(el);
    const s = new Audio(el.currentSrc || el.src);
    s.__voiceFixStandIn = true;
    s.playbackRate = el.playbackRate;
    s.volume = el.volume;
    s.muted = el.muted;
    for (const type of FORWARDED) {
      s.addEventListener(type, () => {
        if (!s.__voiceFixStopped && standIns.get(el) === s) el.dispatchEvent(new Event(type));
      });
    }
    s.addEventListener("ended", () => standIns.get(el) === s && standIns.delete(el));
    standIns.set(el, s);
    return nativePlay.call(s);
  };
  HTMLMediaElement.prototype.play = function () {
    const el = this;
    stopStandIn(el);
    const p = nativePlay.call(el);
    if (el.__voiceFixStandIn || !(el instanceof HTMLAudioElement)) return p;
    return p.catch((err) => {
      const src = el.currentSrc || el.src;
      if (!err || err.name !== "NotAllowedError" || !src || src === SILENCE) throw err;
      log("play() refused by the browser; playing the clip through a fresh audio object");
      return playThroughStandIn(el);
    });
  };
  HTMLMediaElement.prototype.pause = function () {
    const hadStandIn = standIns.has(this);
    stopStandIn(this);
    const r = nativePause.call(this);
    // The original was already paused, so pausing it fires no "pause" event; send one, as a playing player would,
    // so Open WebUI's handler for the interrupted sentence runs.
    if (hadStandIn) this.dispatchEvent(new Event("pause"));
    return r;
  };

  // 2. A less sensitive microphone. Voice mode treats any sound above -55 dB as "you started talking" (a fixed value,
  // set as minDecibels of its microphone AnalyserNode on every frame); that one value becomes SPEECH_THRESHOLD_DB.
  // Open WebUI's other values (e.g. -1 dB while an answer plays) are untouched.
  (() => {
    const OPEN_WEBUI_THRESHOLD_DB = -55;
    const OPEN_WEBUI_MAX_DB = -30; // its maxDecibels while listening
    const threshold = Math.max(-60, Math.min(SPEECH_THRESHOLD_DB, -10));
    const minDesc = Object.getOwnPropertyDescriptor(AnalyserNode.prototype, "minDecibels");
    const maxDesc = Object.getOwnPropertyDescriptor(AnalyserNode.prototype, "maxDecibels");
    if (!minDesc?.set || !maxDesc?.set) return;
    Object.defineProperty(AnalyserNode.prototype, "minDecibels", {
      ...minDesc,
      set(value) {
        if (value === OPEN_WEBUI_THRESHOLD_DB) {
          value = threshold;
          // The browser requires minDecibels < maxDecibels: raise the maximum first if it is in the way.
          if (maxDesc.get.call(this) <= value) maxDesc.set.call(this, Math.min(0, value + 5));
        }
        minDesc.set.call(this, value);
      }
    });
    Object.defineProperty(AnalyserNode.prototype, "maxDecibels", {
      ...maxDesc,
      set(value) {
        // For a threshold of -30 dB or higher, keep the maximum a little above it (detection only uses the minimum).
        if (value === OPEN_WEBUI_MAX_DB && this.minDecibels >= value) value = Math.min(0, this.minDecibels + 5);
        maxDesc.set.call(this, value);
      }
    });
  })();

  // 3. No automatic gain control: voice mode asks for it, and while you are silent it turns a voice in the next room
  // up to almost normal speech level. Echo cancellation and noise suppression stay as Open WebUI asks.
  (() => {
    const md = navigator.mediaDevices;
    if (AUTO_GAIN || !md?.getUserMedia) return;
    const nativeGetUserMedia = md.getUserMedia.bind(md);
    md.getUserMedia = (constraints) => {
      if (constraints?.audio) {
        const audio = typeof constraints.audio === "object" ? { ...constraints.audio } : {};
        audio.autoGainControl = false;
        constraints = { ...constraints, audio };
      }
      return nativeGetUserMedia(constraints);
    };
  })();

  try { log(`loaded: threshold=${SPEECH_THRESHOLD_DB}dB, autoGain=${AUTO_GAIN}`); } catch {}
})();
