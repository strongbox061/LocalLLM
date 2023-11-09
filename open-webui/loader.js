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
  const log = (msg) => console.info(`[voice-fix] ${msg}`);

  // Fix 1: autoplay / unsound-start. Some browsers block audio that plays without a user gesture.
  // Wrap play() so it always resolves, even if the browser returns a promise that would reject.
  HTMLMediaElement.prototype.play = function () {
    const result = nativePlay.apply(this, arguments);
    if (result && result.catch) {
      return result.catch(() => undefined);
    }
    return result;
  };

  // Fix 3: turn off automatic gain control for voice mode's microphone. While you are silent,
  // the browser normally boosts distant voices; we disable that.
  if (!AUTO_GAIN) {
    const origGetUserMedia = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
    navigator.mediaDevices.getUserMedia = function (constraints) {
      if (constraints && constraints.audio) {
        const audio = typeof constraints.audio === "object" ? { ...constraints.audio } : {};
        audio.autoGainControl = false;
        constraints = { ...constraints, audio };
      }
      return origGetUserMedia(constraints);
    };
  }

  log(`loaded: threshold=${SPEECH_THRESHOLD_DB}dB, autoGain=${AUTO_GAIN}`);
})();
