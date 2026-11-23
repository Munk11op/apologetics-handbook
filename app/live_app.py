"""Handbook Live: listens to the sound you choose and shows the matching handbook answers in a window that stays
on top of your other apps.

You can listen to:
  - one app (Discord, Zoom, your browser, TikTok...): only that app is heard
  - any speakers or headphones (everything that device plays, "desktop audio")
  - any microphone or input (including Voicemeeter, Wave Link, VB-Cable and other virtual devices)
and add a second source (for example your own microphone, optionally marked as "me" so it never triggers answers).

Start it from the Start menu or desktop shortcut (or "Handbook Live.bat"). Options:
  --browser      open in your web browser instead of the always-on-top window
  --model NAME   speech model: tiny.en (fastest), base.en (default), small.en (most accurate, slower)
  --check        list every sound device and app with its current level, then quit
  --test FILE    feed a WAV file through the app instead of listening (for testing)
"""
import argparse
import ctypes
import json
import os
import queue
import re
import socket
import sys
import threading
import time
import warnings
import wave
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import numpy as np

warnings.filterwarnings("ignore")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")

HERE = os.path.dirname(os.path.abspath(__file__))
MAC = sys.platform == "darwin"
# A Mac can't record what its speakers play. A free virtual audio driver such as BlackHole shows up as an input
# instead, so on a Mac "your computer's sound" means that input when it exists, and the microphone otherwise.
VIRTUAL_INPUT_WORDS = ("blackhole", "loopback", "soundflower", "virtual")


def mac_sound_input(sc):
    mics = sc.all_microphones(include_loopback=False)
    virt = next((m for m in mics if any(w in m.name.lower() for w in VIRTUAL_INPUT_WORDS)), None)
    return virt or sc.default_microphone(), virt is not None
PAGE = "Apologetics_Handbook.html"
SETTINGS = os.path.join(HERE, "settings.json")
APP_VERSION = "13.0.0"   # kept in step with the handbook's VERSION file by release.py


def newer(a, b):
    """True when version a (like "11.2.0") is newer than b."""
    def parts(v):
        return [int(x) for x in re.findall(r"\d+", v)[:3]] + [0, 0, 0]
    return parts(a)[:3] > parts(b)[:3]


def check_for_update(app, force=False):
    """Once a day: when the handbook's update page lists a newer version, download the files that changed (each
    checked against its SHA-256 fingerprint before anything is replaced) and tell the open page. Sends nothing about
    you. On by default; "update_check": false in settings.json turns it off. See updater.py."""
    import updater
    settings = load_settings()
    if settings.get("update_check") is False or not updater.UPDATE_BASE:
        return None
    if not force and time.time() - float(settings.get("update_last") or 0) < 86400:
        return None
    try:
        m = updater.fetch_manifest()
    except Exception as e:  # noqa: BLE001
        say(f"Couldn't check for updates ({e}). Trying again tomorrow.")
        return None
    settings = load_settings()
    settings["update_last"] = time.time()
    save_settings(settings)
    version = str((m or {}).get("version", ""))
    if not version or not newer(version, APP_VERSION):
        return ""
    say(f"Updating to version {version}…")

    def progress(i, n):
        app.hub.publish("update", {"state": "downloading", "version": version, "done": i, "total": n})
    try:
        changed = updater.apply(m, HERE, progress=progress)
    except Exception as e:  # noqa: BLE001
        say(f"The update to {version} didn't finish ({e}); nothing was changed.")
        app.hub.publish("update", {"state": "failed", "version": version})
        return None
    info = {"state": "ready", "version": version, "notes": [str(x) for x in (m.get("notes") or [])][:6],
            "restart": any(p.endswith(".py") for p in changed), "changed": len(changed)}
    app.hub.update = info
    app.hub.publish("update", info)
    say(f"Updated to {version} ({len(changed)} files). Close Handbook Live and open it again to finish.")
    return version
LOG = os.path.join(HERE, "live.log")
RATE = 16000
BLOCK = 1600  # 0.1 s
PREFERRED_PORT = 47615
# Spelling hints so the speech model writes these words the way the handbook does.
PROMPT = ("Qur'an 4:157, 5:47, 61:6. Allah, Muhammad, Isa, Injil, Torah, Zabur, tahrif, Trinity, tawhid, shirk, hadith, "
          "Bukhari, surah, ayah, Paraclete, Deuteronomy, crucifixion, resurrection, Paul, Catholic, pope, Theotokos, "
          "Eucharist, purgatory, Jehovah, Watchtower, Joseph Smith, Gnostic, Hebrew Israelites.")
JUNK = {"", "you", "thank you", "thank you.", "thanks for watching", "thanks for watching!", "bye", "bye.", "so", "okay", "."}

# When started without a console (from the shortcut), keep a log file instead.
if sys.stdout is None or sys.stderr is None:
    try:
        sys.stdout = sys.stderr = open(LOG, "a", encoding="utf-8", buffering=1)
    except Exception:  # noqa: BLE001
        pass


def say(msg):
    try:
        print(f"  {msg}", flush=True)
    except Exception:  # noqa: BLE001
        pass


def com_init():
    """Windows audio needs COM set up on every thread that touches it."""
    if sys.platform == "win32":
        try:
            ctypes.windll.ole32.CoInitializeEx(None, 0)
        except Exception:  # noqa: BLE001
            pass


_SC = None


def soundcard():
    """Imports the soundcard library once. It sets up Windows audio (COM) on the thread that imports it and
    fails if that thread is already set up, so the import happens on a fresh helper thread."""
    global _SC
    if _SC is None:
        box = {}

        def imp():
            try:
                import soundcard as m
                box["m"] = m
            except Exception as e:  # noqa: BLE001
                box["e"] = e
        th = threading.Thread(target=imp, daemon=True)
        th.start()
        th.join()
        if "e" in box:
            raise box["e"]
        _SC = box["m"]
    return _SC


def rms(x):
    if not len(x):
        return 0.0
    x = np.nan_to_num(np.asarray(x, dtype=np.float32))
    return float(np.sqrt(np.mean(np.square(x))))


def load_settings():
    try:
        with open(SETTINGS, encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return {}


def save_settings(data):
    try:
        with open(SETTINGS, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception:  # noqa: BLE001
        pass


def run_with_timeout(fn, seconds):
    """Runs fn() on its own thread; returns its result, or None if it takes too long (some devices block)."""
    box = {}

    def go():
        com_init()
        try:
            box["r"] = fn()
        except Exception as e:  # noqa: BLE001
            box["e"] = e
    th = threading.Thread(target=go, daemon=True)
    th.start()
    th.join(seconds)
    if "e" in box:
        raise box["e"]
    return box.get("r")


# ---------------------------------------------------------------- events to the page
class Hub:
    """Sends status changes, sound levels, and lines of speech to every open page."""

    def __init__(self):
        self.lock = threading.Lock()
        self.clients = []
        self.history = []
        self.status = {"state": "starting", "message": "Starting…", "paused": False,
                       "main": None, "extra": None, "extra_me": False}
        self.update = None   # set when an update has been downloaded; every page that opens hears about it

    @staticmethod
    def fmt(kind, data):
        return f"event: {kind}\ndata: {json.dumps(data)}\n\n"

    def subscribe(self):
        q = queue.Queue(maxsize=2000)
        with self.lock:
            self.clients.append(q)
            q.put(self.fmt("status", self.status))
            if self.update:
                q.put(self.fmt("update", self.update))
            for item in self.history[-40:]:
                q.put(self.fmt("line", item))
        return q

    def unsubscribe(self, q):
        with self.lock:
            if q in self.clients:
                self.clients.remove(q)

    def publish(self, kind, data):
        with self.lock:
            if kind == "status":
                self.status = data
            elif kind == "line":
                self.history.append(data)
                del self.history[:-200]
            msg = self.fmt(kind, data)
            for q in list(self.clients):
                try:
                    q.put_nowait(msg)
                except queue.Full:
                    pass

    def set_status(self, state=None, message=None, **extra):
        s = dict(self.status)
        if state is not None:
            s["state"] = state
        if message is not None:
            s["message"] = message
        s.update(extra)
        self.publish("status", s)


# ---------------------------------------------------------------- speech to text
class Transcriber(threading.Thread):
    def __init__(self, app, model_name):
        super().__init__(daemon=True)
        self.app, self.model_name = app, model_name
        self.q = queue.Queue(maxsize=6)
        self.ready = threading.Event()
        self.failed = None

    def submit(self, who, at, audio):
        item = (who, at, audio)
        try:
            self.q.put_nowait(item)
        except queue.Full:  # running behind: drop the oldest clip so answers stay live
            try:
                self.q.get_nowait()
            except queue.Empty:
                pass
            try:
                self.q.put_nowait(item)
            except queue.Full:
                pass

    def run(self):
        try:
            from faster_whisper import WhisperModel
            say(f"Loading the speech model ({self.model_name}). The first time, this downloads about 150 MB...")
            threads = max(2, min(8, (os.cpu_count() or 4) // 2))
            self.model = WhisperModel(self.model_name, device="cpu", compute_type="int8", cpu_threads=threads)
        except Exception as e:  # noqa: BLE001
            self.failed = str(e)
            self.app.hub.set_status("error", f"The speech model couldn't load. Check your internet connection for the "
                                             f"first download, then start Handbook Live again. ({e})")
            say(f"Speech model failed to load: {e}")
            return
        self.ready.set()
        say("Speech model ready.")
        self.app.update_status()
        while True:
            who, at, audio = self.q.get()
            if audio is None:
                return
            try:
                segs, _ = self.model.transcribe(audio, language="en", beam_size=1, without_timestamps=True,
                                                condition_on_previous_text=False, vad_filter=False,
                                                initial_prompt=PROMPT)
                text = " ".join(s.text.strip() for s in segs).strip()
            except Exception as e:  # noqa: BLE001
                say(f"Couldn't read a clip: {e}")
                continue
            text = " ".join(text.replace("[", " ").replace("]", " ").split())
            if text.lower() in JUNK or text.lower().startswith("(") or len(text) < 3:
                continue
            self.app.hub.publish("line", {"text": text, "at": round(at, 1), "who": who})


class Chunker:
    """Cuts a stream of sound into clips at natural pauses (3.5 to 9 seconds long).
    It adapts to how loud the source is, so quiet and very loud devices both work."""

    def __init__(self, who, app):
        self.who, self.app = who, app
        self.buf, self.n, self.quiet = [], 0, 0.0
        self.speech = 0.01  # running estimate of how loud speech is in this source

    def feed(self, x):
        x = np.nan_to_num(np.asarray(x, dtype=np.float32))
        r = rms(x)
        if r > 0.0006:
            self.speech = max(0.001, 0.97 * self.speech + 0.03 * min(r, 50.0))
        self.buf.append(x)
        self.n += len(x)
        self.quiet = self.quiet + len(x) / RATE if r < max(0.0005, self.speech * 0.25) else 0.0
        sec = self.n / RATE
        if sec >= 1.0 and self.quiet >= sec:  # nothing but silence so far: start over
            self.buf, self.n = [], 0
        elif (sec >= 3.5 and self.quiet >= 0.4) or sec >= 9:
            self.flush()

    def flush(self):
        if not self.n:
            return
        a = np.concatenate(self.buf).astype(np.float32)
        self.buf, self.n, self.quiet = [], 0, 0.0
        if rms(a) < max(0.0004, self.speech * 0.15) or self.app.paused:
            return
        peak = float(np.abs(a).max())
        if peak > 0:
            a = a * (0.5 / peak)  # even out quiet and over-loud sources
        self.app.transcriber.submit(self.who, time.time() - self.app.t0 - len(a) / RATE, a)


# ---------------------------------------------------------------- sound sources
def root_pid(pid):
    """The top process of an app (browsers and Discord play sound from a helper process)."""
    import psutil
    try:
        p = psutil.Process(pid)
        name = p.name().lower()
        while True:
            par = p.parent()
            if not par or par.name().lower() != name:
                return p.pid
            p = par
    except Exception:  # noqa: BLE001
        return pid


def find_app_pid(exe):
    import psutil

    from app_audio import list_audio_apps
    for a in list_audio_apps():
        if a["exe"].lower() == exe.lower():
            return root_pid(a["pids"][0])
    for p in psutil.process_iter(["name", "pid"]):
        if (p.info.get("name") or "").lower() == exe.lower():
            return root_pid(p.info["pid"])
    return None


def list_devices():
    """Every input (microphones and virtual inputs) and output (speakers, headphones, virtual outputs)."""
    sc = soundcard()

    def go():
        dspk, dmic = sc.default_speaker(), sc.default_microphone()
        ins = [{"id": m.id, "name": m.name, "default": m.id == dmic.id} for m in sc.all_microphones(include_loopback=False)]
        outs = [] if MAC else [{"id": s.id, "name": s.name, "default": s.id == dspk.id} for s in sc.all_speakers()]
        return {"inputs": ins, "outputs": outs}
    return run_with_timeout(go, 8) or {"inputs": [], "outputs": []}


def describe(src):
    if not src:
        return ""
    k = src.get("kind")
    if k == "all":
        return "everything on your default speakers"
    return src.get("name") or "that device"


class Capture(threading.Thread):
    """Listens to one source: {"kind": "app", "exe", "name"} | {"kind": "output", "id", "name"} (what a speaker
    or headphone device plays) | {"kind": "input", "id", "name"} (a microphone or virtual input) | {"kind": "all"}."""

    def __init__(self, app, slot, source, me=False):
        super().__init__(daemon=True)
        self.app, self.slot, self.source, self.me = app, slot, source, me
        self.who = "you" if me else "them"
        self.stop = threading.Event()
        self.chunker = Chunker(self.who, app)
        self.device_name = ""
        self.waiting = None
        self.last_level = 0.0

    def feed(self, x):
        self.chunker.feed(x)
        now = time.time()
        if now - self.last_level > 0.2:
            self.last_level = now
            lvl = min(1.0, rms(x) / max(self.chunker.speech, 0.002) * 0.12)
            self.app.hub.publish("level", {self.slot: round(lvl, 3)})

    def run(self):
        com_init()
        while not self.stop.is_set():
            try:
                kind = self.source.get("kind")
                if kind == "app":
                    self.run_app()
                else:
                    self.run_device()
            except Exception as e:  # noqa: BLE001
                say(f"Capture problem ({describe(self.source)}): {e}")
                self.app.hub.set_status(message=f"Couldn't hear {describe(self.source)}: {e}. Trying again in a few seconds...")
                self.stop.wait(3)
        self.chunker.flush()

    def open_device(self):
        sc = soundcard()
        kind = self.source.get("kind")
        if kind == "all" and MAC:
            mic, virtual = mac_sound_input(sc)
            if not virtual:
                self.app.hub.set_status(message="On a Mac, Handbook Live hears your microphone. To hear your computer's sound "
                                                "(a video or a call), install the free BlackHole audio driver, then choose it under Listen to.")
            return mic, mic.name, None
        if kind == "all":
            spk = sc.default_speaker()
            return sc.get_microphone(id=spk.id, include_loopback=True), spk.name, lambda: sc.default_speaker().id != spk.id
        want = self.source.get("id")
        if kind == "output":
            spk = next((s for s in sc.all_speakers() if s.id == want), None)
            return (sc.get_microphone(id=spk.id, include_loopback=True), spk.name, None) if spk else (None, "", None)
        mic = next((m for m in sc.all_microphones(include_loopback=False) if m.id == want), None)
        return (mic, mic.name, None) if mic else (None, "", None)

    def run_device(self):
        dev, name, changed = self.open_device()
        if dev is None:
            if self.waiting != "device":
                self.waiting = "device"
                self.app.update_status()
            self.stop.wait(2)
            return
        self.device_name, self.waiting = name, None
        with dev.recorder(samplerate=RATE, channels=1, blocksize=BLOCK) as rec:
            say(f"Hearing: {name}" + (" (marked as you)" if self.me else ""))
            self.app.update_status()
            checked = time.time()
            while not self.stop.is_set():
                data = rec.record(numframes=BLOCK)
                self.feed((data[:, 0] if data.ndim > 1 else data).astype(np.float32))
                if changed and time.time() - checked > 4:  # follow the default speakers if they change
                    checked = time.time()
                    if changed():
                        say("Default speakers changed. Switching...")
                        return

    def run_app(self):
        import psutil

        from app_audio import AppCapture
        exe, name = self.source["exe"], self.source["name"]
        pid = find_app_pid(exe)
        if not pid:
            if self.waiting != exe:
                self.waiting = exe
                self.app.update_status()
            self.stop.wait(2)
            return
        with AppCapture(pid) as cap:
            self.device_name, self.waiting = name, None
            say(f"Hearing only {name} (process {pid}).")
            self.app.update_status()
            checked = time.time()
            while not self.stop.is_set():
                self.feed(cap.read(BLOCK))
                if time.time() - checked > 5:  # the app closed or restarted: find it again
                    checked = time.time()
                    if not psutil.pid_exists(pid) or find_app_pid(exe) != pid:
                        return


class FileFeed(threading.Thread):
    """--test: plays a WAV file into the pipeline in real time (nothing is played out loud)."""

    def __init__(self, app, path):
        super().__init__(daemon=True)
        self.app, self.path = app, path
        self.chunker = Chunker("them", app)
        self.stop = threading.Event()
        self.device_name = os.path.basename(path)
        self.waiting = None

    def run(self):
        self.app.transcriber.ready.wait()
        w = wave.open(self.path)
        ch, sr = w.getnchannels(), w.getframerate()
        a = np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768
        a = a.reshape(-1, ch).mean(axis=1)
        n = int(len(a) * RATE / sr)
        a = np.interp(np.linspace(0, len(a) - 1, n), np.arange(len(a)), a).astype(np.float32)
        a = np.concatenate([a, np.zeros(RATE, np.float32)])
        self.app.update_status()
        for i in range(0, len(a), BLOCK):
            if self.stop.is_set():
                return
            self.chunker.feed(a[i:i + BLOCK])
            time.sleep(BLOCK / RATE)
        self.chunker.flush()
        self.app.hub.set_status("stopped", "The test file finished.")


def scan_levels(seconds=1.5):
    """Listens briefly to every input, output, and app at once and reports how loud each one is,
    so you can see which device actually carries your voice or the call."""
    sc = soundcard()
    devs = list_devices()
    results, threads = [], []

    def measure(entry, opener):
        def go():
            com_init()
            try:
                dev = opener()
                with dev.recorder(samplerate=RATE, channels=1, blocksize=BLOCK) as rec:
                    levels = [rms(rec.record(numframes=BLOCK)) for _ in range(int(seconds * 10))]
                entry["level"] = round(max(levels), 4)
            except Exception as e:  # noqa: BLE001
                entry["error"] = str(e)[:80]
        t = threading.Thread(target=go, daemon=True)
        t.start()
        threads.append(t)

    for d in devs["inputs"]:
        e = dict(d, kind="input", level=0.0)
        results.append(e)
        measure(e, lambda i=d["id"]: sc.get_microphone(id=i, include_loopback=False))
    for d in ([] if MAC else devs["outputs"]):
        e = dict(d, kind="output", level=0.0)
        results.append(e)
        measure(e, lambda i=d["id"]: sc.get_microphone(id=i, include_loopback=True))
    try:
        from app_audio import AppCapture, list_audio_apps
        for a in list_audio_apps():
            e = {"kind": "app", "exe": a["exe"], "name": a["name"], "level": 0.0}
            results.append(e)

            def go(entry=e, pid=root_pid(a["pids"][0])):
                try:
                    with AppCapture(pid) as cap:
                        entry["level"] = round(max(rms(cap.read(BLOCK)) for _ in range(int(seconds * 10))), 4)
                except Exception as err:  # noqa: BLE001
                    entry["error"] = str(err)[:80]
            t = threading.Thread(target=go, daemon=True)
            t.start()
            threads.append(t)
    except Exception as e:  # noqa: BLE001
        say(f"Couldn't list apps: {e}")
    deadline = time.time() + seconds + 3
    for t in threads:
        t.join(max(0.05, deadline - time.time()))
    for e in results:
        e["level"] = round(min(float(e.get("level") or 0.0), 99.0), 4)
    return sorted(results, key=lambda e: -(e.get("level") or 0))


# ---------------------------------------------------------------- the app
DEFAULT_MAIN = {"kind": "all"}


class App:
    def __init__(self, args):
        self.args = args
        self.hub = Hub()
        self.t0 = time.time()
        self.paused = False
        self.transcriber = Transcriber(self, args.model)
        self.settings = load_settings()
        self.main_src = self.extra_src = None
        self.extra_me = False
        self.main = self.extra = None
        self.lock = threading.Lock()

    def start(self):
        self.transcriber.start()
        if self.args.test:
            self.main_src = {"kind": "test", "name": os.path.basename(self.args.test)}
            self.main = FileFeed(self, self.args.test)
            self.main.start()
        else:
            s = self.settings
            main = s.get("main") or s.get("source") or DEFAULT_MAIN  # "source" is the older setting name
            self.set_sources(main, s.get("extra"), bool(s.get("extra_me")), remember=False)
        self.update_status()

    @staticmethod
    def valid(src):
        return isinstance(src, dict) and src.get("kind") in ("app", "output", "input", "all")

    def set_sources(self, main=False, extra=False, extra_me=None, remember=True):
        """main/extra: a source dict, None to turn it off, or False to leave it as it is."""
        with self.lock:
            if main is not False:
                if self.main:
                    self.main.stop.set()
                self.main_src = main if self.valid(main) else None
                self.main = Capture(self, "main", self.main_src) if self.main_src else None
                if self.main:
                    self.main.start()
            restart_extra = extra is not False or (extra_me is not None and extra_me != self.extra_me)
            if extra_me is not None:
                self.extra_me = bool(extra_me)
            if restart_extra:
                if extra is not False:
                    self.extra_src = extra if self.valid(extra) else None
                if self.extra:
                    self.extra.stop.set()
                self.extra = Capture(self, "extra", self.extra_src, self.extra_me) if self.extra_src else None
                if self.extra:
                    self.extra.start()
            if remember:
                self.settings.update(main=self.main_src, extra=self.extra_src, extra_me=self.extra_me)
                self.settings.pop("source", None)
                self.settings.pop("mic", None)
                save_settings(self.settings)
        self.update_status()

    def update_status(self):
        if self.transcriber.failed:
            return
        extra = {"paused": self.paused, "main": self.main_src, "extra": self.extra_src, "extra_me": self.extra_me}
        waiting = [c for c in (self.main, self.extra) if c and getattr(c, "waiting", None)]
        if not self.main_src and not self.extra_src:
            self.hub.set_status("choose", "Choose what to listen to: an app, your speakers or headphones, or a "
                                          "microphone. Not sure which device has the sound? Press Scan for sound.", **extra)
        elif waiting:
            c = waiting[0]
            what = describe(c.source)
            msg = (f"{what} isn't open right now. Open it, and Live will start hearing it." if c.source.get("kind") == "app"
                   else f"{what} isn't connected right now. Plug it in or choose another device.")
            self.hub.set_status("waiting", msg, **extra)
        elif not self.transcriber.ready.is_set():
            self.hub.set_status("loading", "Loading the speech model. The first time, this downloads about 150 MB, "
                                           "once. Speech is saved up until it's ready.", **extra)
        elif self.paused:
            self.hub.set_status("paused", "Paused. Nothing is being heard.", **extra)
        else:
            parts = [describe(self.main_src)] if self.main_src else []
            if self.extra_src:
                parts.append(describe(self.extra_src) + (" (your words, no answers)" if self.extra_me else ""))
            msg = "Listening to " + " and ".join(parts) + ". Answers appear as claims are made."
            self.hub.set_status("listening", msg, **extra)

    def control(self, data):
        if "main" in data or "extra" in data or "extra_me" in data:
            self.set_sources(data.get("main", False), data.get("extra", False), data.get("extra_me"))
        if "paused" in data:
            self.paused = bool(data["paused"])
            self.update_status()
        return {"ok": True}

    def shutdown(self):
        for c in (self.main, self.extra):
            if c:
                c.stop.set()
        self.transcriber.q.put((None, 0, None))


def make_handler(app, port):
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *a, **k):
            super().__init__(*a, directory=HERE, **k)

        def log_message(self, *a):
            pass

        def guard(self):
            # Only this computer's own page may talk to the app (blocks other websites).
            if self.headers.get("Host") not in allowed_hosts:
                self.send_error(403)
                return False
            origin = self.headers.get("Origin")
            if origin and origin not in {f"http://{h}" for h in allowed_hosts}:
                self.send_error(403)
                return False
            return True

        def send_json(self, obj, code=200):
            out = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

        def do_GET(self):
            if not self.guard():
                return
            path = self.path.split("?")[0]
            if path in ("/", ""):
                self.send_response(302)
                self.send_header("Location", f"/{PAGE}?app=1")
                self.end_headers()
                return
            if path == "/live/sources":
                out = {"inputs": [], "outputs": [], "apps": []}
                try:
                    out.update(list_devices())
                except Exception as e:  # noqa: BLE001
                    out["error"] = str(e)
                try:
                    from app_audio import list_audio_apps
                    skip = {os.path.basename(sys.executable).lower(), "msedgewebview2.exe"}
                    out["apps"] = [{"exe": a["exe"], "name": a["name"], "active": a["active"]}
                                   for a in list_audio_apps() if a["exe"].lower() not in skip]
                except Exception as e:  # noqa: BLE001
                    out["apps_error"] = str(e)
                return self.send_json(out)
            if path == "/live/scan":
                try:
                    return self.send_json({"results": scan_levels()})
                except Exception as e:  # noqa: BLE001
                    return self.send_json({"results": [], "error": str(e)})
            if path == "/live/events":
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                q = app.hub.subscribe()
                try:
                    while True:
                        try:
                            msg = q.get(timeout=15)
                        except queue.Empty:
                            msg = ": ping\n\n"
                        self.wfile.write(msg.encode("utf-8"))
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
                    pass
                finally:
                    app.hub.unsubscribe(q)
                return
            if path.endswith((".py", ".json", ".bat", ".txt", ".log")) or "/." in path:
                self.send_error(404)
                return
            return super().do_GET()

        def do_POST(self):
            if not self.guard():
                return
            if self.path.split("?")[0] != "/live/control":
                self.send_error(404)
                return
            try:
                n = int(self.headers.get("Content-Length") or 0)
                data = json.loads(self.rfile.read(min(n, 10000)) or b"{}")
                self.send_json(app.control(data))
            except Exception as e:  # noqa: BLE001
                self.send_json({"ok": False, "error": str(e)}, 400)

    return Handler


def free_port(preferred):
    for p in (preferred, 0):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", p))
                return s.getsockname()[1]
            except OSError:
                continue
    raise RuntimeError("no free port")


def check_sound():
    """--check: lists every sound device and app with its current level."""
    say("Listening to every device for 1.5 seconds (play something, or speak, to see it light up)...")
    for e in scan_levels():
        kind = {"input": "mic/input", "output": "speakers", "app": "app"}[e["kind"]]
        bar = "#" * min(30, int((e.get("level") or 0) * 300))
        err = f"  ({e['error']})" if e.get("error") else ""
        say(f"{kind:10} {e['name'][:52]:52} {e.get('level', 0):7.4f} {bar}{err}")


class Api:
    """Lets the page pin or unpin the window."""

    def __init__(self):
        self._window = None  # private, so the window library doesn't try to expose it to the page

    def set_on_top(self, on):
        win = self._window
        if win:  # change it on another thread, so the page never waits on the window
            threading.Thread(target=lambda: setattr(win, "on_top", bool(on)), daemon=True).start()
        return bool(on)

    def set_compact(self, on):
        """D2: a slim always-on-top strip that shows only the latest match (the page hides the rest)."""
        win = self._window
        if win:
            def go():
                try:
                    if on:
                        self._size = (win.width, win.height)
                        win.resize(520, 190)
                    else:
                        w, h = getattr(self, "_size", None) or (500, 880)
                        win.resize(w, h)
                except Exception:  # noqa: BLE001
                    pass
            threading.Thread(target=go, daemon=True).start()
        return bool(on)


def hotkey_loop(api):
    """D1: Ctrl+Alt+H shows or hides the Handbook Live window from anywhere. If another program already uses that
    key, Windows refuses it and nothing happens."""
    try:
        import ctypes.wintypes
        user32 = ctypes.windll.user32
        if not user32.RegisterHotKey(None, 1, 0x0002 | 0x0001 | 0x4000, ord("H")):  # Ctrl + Alt, no auto-repeat
            return
        msg, shown = ctypes.wintypes.MSG(), True
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == 0x0312 and api._window:  # WM_HOTKEY
                try:
                    (api._window.hide if shown else api._window.show)()
                    shown = not shown
                except Exception:  # noqa: BLE001
                    pass
    except Exception:  # noqa: BLE001
        pass


def main():
    ap = argparse.ArgumentParser(description="Handbook Live")
    ap.add_argument("--browser", action="store_true", help="open in your web browser instead of the app window")
    ap.add_argument("--no-open", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--mic", action="store_true", help=argparse.SUPPRESS)  # older option, now chosen in the window
    ap.add_argument("--model", default="base.en", choices=["tiny.en", "base.en", "small.en"])
    ap.add_argument("--port", type=int, default=PREFERRED_PORT, help=argparse.SUPPRESS)
    ap.add_argument("--check", action="store_true", help="list every sound device and app with its current level")
    ap.add_argument("--test", metavar="FILE", help="feed a WAV file through the app instead of listening")
    ap.add_argument("--no-update", action="store_true", help="don't check the update page this time")
    ap.add_argument("--update-now", action="store_true", help=argparse.SUPPRESS)  # check right away (tests)
    args = ap.parse_args()

    sys.path.insert(0, HERE)
    print("\n  Handbook Live\n  -------------", flush=True)
    if args.check:
        check_sound()
        return
    if not os.path.exists(os.path.join(HERE, PAGE)):
        say(f"Can't find {PAGE} next to this program. Keep the files in the Handbook Live folder together.")
        sys.exit(1)

    app = App(args)
    port = free_port(args.port)
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(app, port))
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{port}/{PAGE}?app=1"
    app.start()
    if not (args.test or args.no_update):
        def later():
            time.sleep(2 if args.update_now else 20)
            check_for_update(app, force=args.update_now)
        threading.Thread(target=later, daemon=True).start()
    say("Running. Close the Handbook Live window to stop." if not (args.browser or args.no_open)
        else f"Running at {url}  (press Ctrl+C here to stop)")

    try:
        if args.browser or args.no_open:
            if args.browser:
                webbrowser.open(url)
            while True:
                time.sleep(1)
        else:
            try:
                import webview
            except Exception as e:  # noqa: BLE001
                say(f"The app window couldn't open ({e}). Opening your browser instead. Press Ctrl+C here to stop.")
                webbrowser.open(url)
                while True:
                    time.sleep(1)
            api = Api()
            w, h = 500, 880
            try:
                scr = webview.screens[0]
                h = min(920, scr.height - 90)
                x, y = scr.width - w - 24, 40
            except Exception:  # noqa: BLE001
                x = y = None
            win = webview.create_window("Handbook Live", url, width=w, height=h, x=x, y=y, on_top=True,
                                        min_size=(320, 150), js_api=api, background_color="#F4F5F1")
            api._window = win
            threading.Thread(target=hotkey_loop, args=(api,), daemon=True).start()
            webview.start(private_mode=False, storage_path=os.path.join(HERE, ".webview"))
    except KeyboardInterrupt:
        pass
    finally:
        app.shutdown()
        server.shutdown()
        say("Stopped. Nothing was saved.")


if __name__ == "__main__":
    main()
