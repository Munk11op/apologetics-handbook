"""Hear one app's sound on Windows 10 (build 20348+) and Windows 11, using WASAPI "process loopback".

    with AppCapture(pid) as cap:
        block = cap.read(1600)   # float32 mono samples at 16 kHz (silence if the app is quiet)

list_audio_apps() returns the apps that currently have a sound session (Discord, Chrome, Zoom...).
"""
import ctypes
import threading
import time
from ctypes import POINTER, Structure, byref, c_int, c_ubyte, c_ulonglong, c_ushort, c_void_p, cast
from ctypes.wintypes import BOOL, DWORD, HANDLE, LPCWSTR, ULONG, WORD

import comtypes
import numpy as np
from comtypes import COMMETHOD, GUID, HRESULT, IUnknown

VAD_PROCESS_LOOPBACK = "VAD\\Process_Loopback"
ACTIVATION_TYPE_PROCESS_LOOPBACK = 1
LOOPBACK_INCLUDE_TREE = 0
VT_BLOB = 65
SHAREMODE_SHARED = 0
FLAG_LOOPBACK = 0x00020000
FLAG_EVENTCALLBACK = 0x00040000
FLAG_SRC_DEFAULT_QUALITY = 0x08000000
FLAG_AUTOCONVERTPCM = 0x80000000
BUFFER_FLAGS_SILENT = 0x2
WAVE_FORMAT_PCM = 1


class PROCESS_LOOPBACK_PARAMS(Structure):
    _fields_ = [("TargetProcessId", DWORD), ("ProcessLoopbackMode", c_int)]


class ACTIVATION_PARAMS(Structure):
    _fields_ = [("ActivationType", c_int), ("ProcessLoopbackParams", PROCESS_LOOPBACK_PARAMS)]


class BLOB(Structure):
    _fields_ = [("cbSize", ULONG), ("pBlobData", POINTER(c_ubyte))]


class PROPVARIANT(Structure):
    _fields_ = [("vt", c_ushort), ("r1", c_ushort), ("r2", c_ushort), ("r3", c_ushort), ("blob", BLOB)]


class WAVEFORMATEX(Structure):
    _pack_ = 1
    _fields_ = [("wFormatTag", WORD), ("nChannels", WORD), ("nSamplesPerSec", DWORD), ("nAvgBytesPerSec", DWORD),
                ("nBlockAlign", WORD), ("wBitsPerSample", WORD), ("cbSize", WORD)]


class IAudioCaptureClient(IUnknown):
    _iid_ = GUID("{C8ADBD64-E71E-48a0-A4DE-185C395CD317}")
    _methods_ = [
        COMMETHOD([], HRESULT, "GetBuffer", (["out"], POINTER(c_void_p), "data"), (["out"], POINTER(DWORD), "frames"),
                  (["out"], POINTER(DWORD), "flags"), (["out"], POINTER(c_ulonglong), "pos"),
                  (["out"], POINTER(c_ulonglong), "qpc")),
        COMMETHOD([], HRESULT, "ReleaseBuffer", (["in"], DWORD, "frames")),
        COMMETHOD([], HRESULT, "GetNextPacketSize", (["out"], POINTER(DWORD), "frames")),
    ]


class IAudioClient(IUnknown):
    _iid_ = GUID("{1CB9AD4C-DBFA-4c32-B178-C2F568A703B2}")
    _methods_ = [
        COMMETHOD([], HRESULT, "Initialize", (["in"], c_int, "mode"), (["in"], DWORD, "flags"),
                  (["in"], c_ulonglong, "duration"), (["in"], c_ulonglong, "period"),
                  (["in"], POINTER(WAVEFORMATEX), "fmt"), (["in"], c_void_p, "session")),
        COMMETHOD([], HRESULT, "GetBufferSize", (["out"], POINTER(DWORD), "frames")),
        COMMETHOD([], HRESULT, "GetStreamLatency", (["out"], POINTER(c_ulonglong), "latency")),
        COMMETHOD([], HRESULT, "GetCurrentPadding", (["out"], POINTER(DWORD), "frames")),
        COMMETHOD([], HRESULT, "IsFormatSupported", (["in"], c_int, "mode"), (["in"], c_void_p, "fmt"),
                  (["out"], POINTER(c_void_p), "closest")),
        COMMETHOD([], HRESULT, "GetMixFormat", (["out"], POINTER(c_void_p), "fmt")),
        COMMETHOD([], HRESULT, "GetDevicePeriod", (["out"], POINTER(c_ulonglong), "default"),
                  (["out"], POINTER(c_ulonglong), "minimum")),
        COMMETHOD([], HRESULT, "Start"),
        COMMETHOD([], HRESULT, "Stop"),
        COMMETHOD([], HRESULT, "Reset"),
        COMMETHOD([], HRESULT, "SetEventHandle", (["in"], HANDLE, "event")),
        COMMETHOD([], HRESULT, "GetService", (["in"], POINTER(GUID), "iid"), (["out"], POINTER(c_void_p), "service")),
    ]


class IActivateAudioInterfaceAsyncOperation(IUnknown):
    _iid_ = GUID("{72A22D78-CDE4-431D-B8CC-843A71199B6D}")
    _methods_ = [
        COMMETHOD([], HRESULT, "GetActivateResult", (["out"], POINTER(HRESULT), "hr"),
                  (["out"], POINTER(POINTER(IUnknown)), "iface")),
    ]


class IActivateAudioInterfaceCompletionHandler(IUnknown):
    _iid_ = GUID("{41D949AB-9862-444A-80F6-C261334DA5EB}")
    _methods_ = [
        COMMETHOD([], HRESULT, "ActivateCompleted",
                  (["in"], POINTER(IActivateAudioInterfaceAsyncOperation), "operation")),
    ]


class IAgileObject(IUnknown):
    _iid_ = GUID("{94ea2b94-e9cc-49e0-c0ff-ee64ca8f5b90}")
    _methods_ = []


class _Done(comtypes.COMObject):
    _com_interfaces_ = [IActivateAudioInterfaceCompletionHandler, IAgileObject]

    def __init__(self):
        super().__init__()
        self.event = threading.Event()

    def ActivateCompleted(self, operation):  # noqa: N802 (COM name)
        self.event.set()
        return 0


_mmdev = ctypes.WinDLL("Mmdevapi.dll")
_ActivateAsync = _mmdev.ActivateAudioInterfaceAsync
_ActivateAsync.argtypes = [LPCWSTR, POINTER(GUID), POINTER(PROPVARIANT),
                           POINTER(IActivateAudioInterfaceCompletionHandler),
                           POINTER(POINTER(IActivateAudioInterfaceAsyncOperation))]
_ActivateAsync.restype = HRESULT
_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_k32.CreateEventW.restype = HANDLE
_k32.CreateEventW.argtypes = [c_void_p, BOOL, BOOL, LPCWSTR]
_k32.WaitForSingleObject.argtypes = [HANDLE, DWORD]
_k32.WaitForSingleObject.restype = DWORD
_k32.CloseHandle.argtypes = [HANDLE]


def _com_init():
    """COM must be set up on each thread; a thread that already has it set up is fine as it is."""
    try:
        comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
    except OSError:
        pass


def _fmt(rate, channels):
    f = WAVEFORMATEX()
    f.wFormatTag, f.nChannels, f.nSamplesPerSec, f.wBitsPerSample, f.cbSize = WAVE_FORMAT_PCM, channels, rate, 16, 0
    f.nBlockAlign = channels * 2
    f.nAvgBytesPerSec = rate * f.nBlockAlign
    return f


class AppCapture:
    """Captures the sound of one process and everything it starts (its whole process tree)."""

    def __init__(self, pid, rate=16000):
        self.pid, self.rate = int(pid), rate
        self.client = self.capture = self.event = None
        self.pending = np.zeros(0, np.float32)
        self.src_rate, self.channels = rate, 1

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *exc):
        self.close()

    def open(self):
        _com_init()
        params = ACTIVATION_PARAMS(ACTIVATION_TYPE_PROCESS_LOOPBACK, PROCESS_LOOPBACK_PARAMS(self.pid, LOOPBACK_INCLUDE_TREE))
        self._params = params  # keep alive while the async call runs
        pv = PROPVARIANT()
        pv.vt = VT_BLOB
        pv.blob.cbSize = ctypes.sizeof(params)
        pv.blob.pBlobData = cast(ctypes.pointer(params), POINTER(c_ubyte))
        done = _Done()
        handler = done.QueryInterface(IActivateAudioInterfaceCompletionHandler)
        op = POINTER(IActivateAudioInterfaceAsyncOperation)()
        iid = IAudioClient._iid_
        _ActivateAsync(VAD_PROCESS_LOOPBACK, byref(iid), byref(pv), handler, byref(op))
        if not done.event.wait(5):
            raise RuntimeError("Windows didn't answer the request to hear that app")
        hr, unk = op.GetActivateResult()
        if hr < 0:
            raise OSError(f"Windows refused to capture that app (error 0x{hr & 0xFFFFFFFF:08X})")
        self.client = unk.QueryInterface(IAudioClient)
        flags = FLAG_LOOPBACK | FLAG_EVENTCALLBACK | FLAG_AUTOCONVERTPCM | FLAG_SRC_DEFAULT_QUALITY
        last = None
        for rate, ch in ((self.rate, 1), (48000, 2), (44100, 2)):
            try:
                self._wfx = _fmt(rate, ch)
                self.client.Initialize(SHAREMODE_SHARED, flags, 2000000, 0, byref(self._wfx), None)
                self.src_rate, self.channels = rate, ch
                break
            except Exception as e:  # noqa: BLE001
                last = e
                # a failed Initialize leaves the client unusable, so get a fresh one
                done = _Done()
                handler = done.QueryInterface(IActivateAudioInterfaceCompletionHandler)
                op = POINTER(IActivateAudioInterfaceAsyncOperation)()
                _ActivateAsync(VAD_PROCESS_LOOPBACK, byref(iid), byref(pv), handler, byref(op))
                done.event.wait(5)
                hr, unk = op.GetActivateResult()
                self.client = unk.QueryInterface(IAudioClient)
        else:
            raise OSError(f"Couldn't set up capture for that app: {last}")
        self.event = _k32.CreateEventW(None, False, False, None)
        self.client.SetEventHandle(self.event)
        svc = self.client.GetService(byref(IAudioCaptureClient._iid_))
        self.capture = cast(svc, POINTER(IAudioCaptureClient))
        self.client.Start()

    def _pull(self):
        out = []
        while True:
            n = self.capture.GetNextPacketSize()
            if not n:
                break
            data, frames, flags, _, _ = self.capture.GetBuffer()
            if frames:
                if flags & BUFFER_FLAGS_SILENT or not data:
                    pcm = np.zeros(frames * self.channels, np.int16)
                else:
                    pcm = np.ctypeslib.as_array(cast(data, POINTER(ctypes.c_int16)), shape=(frames * self.channels,)).copy()
                out.append(pcm)
            self.capture.ReleaseBuffer(frames)
        if not out:
            return np.zeros(0, np.float32)
        a = np.concatenate(out).astype(np.float32) / 32768
        if self.channels > 1:
            a = a.reshape(-1, self.channels).mean(axis=1)
        if self.src_rate != self.rate:
            n = int(len(a) * self.rate / self.src_rate)
            a = np.interp(np.linspace(0, len(a) - 1, n), np.arange(len(a)), a).astype(np.float32) if n else a[:0]
        return a

    def read(self, frames):
        """Returns `frames` samples. When the app is quiet, Windows sends nothing, so silence is filled in."""
        deadline = time.time() + frames / self.rate
        while len(self.pending) < frames:
            wait = max(0, deadline - time.time())
            _k32.WaitForSingleObject(self.event, int(wait * 1000) + 1)
            got = self._pull()
            if len(got):
                self.pending = np.concatenate([self.pending, got])
            elif time.time() >= deadline:
                self.pending = np.concatenate([self.pending, np.zeros(frames - len(self.pending), np.float32)])
        block, self.pending = self.pending[:frames], self.pending[frames:]
        return block

    def close(self):
        try:
            if self.client:
                self.client.Stop()
        except Exception:  # noqa: BLE001
            pass
        if self.event:
            _k32.CloseHandle(self.event)
        self.client = self.capture = self.event = None


FRIENDLY = {"chrome": "Google Chrome", "msedge": "Microsoft Edge", "firefox": "Firefox", "brave": "Brave",
            "opera": "Opera", "discord": "Discord", "zoom": "Zoom", "ms-teams": "Microsoft Teams", "teams": "Microsoft Teams",
            "spotify": "Spotify", "tiktok": "TikTok", "whatsapp": "WhatsApp", "telegram": "Telegram", "skype": "Skype",
            "slack": "Slack", "obs64": "OBS Studio", "vlc": "VLC", "steam": "Steam", "steamwebhelper": "Steam",
            "claude": "Claude", "voicemeeterpro": "Voicemeeter", "voicemeeter": "Voicemeeter", "applemusic": "Apple Music"}
SKIP = {"audiodg.exe", "shellexperiencehost.exe", "python.exe", "pythonw.exe"}


def list_audio_apps():
    """Apps that have a sound session right now, newest activity first: [{name, exe, pids, active}]."""
    _com_init()
    from pycaw.pycaw import AudioUtilities
    apps = {}
    for s in AudioUtilities.GetAllSessions():
        p = s.Process
        if not p:
            continue
        try:
            exe = p.name()
        except Exception:  # noqa: BLE001
            continue
        if exe.lower() in SKIP:
            continue
        key = exe.lower()
        a = apps.setdefault(key, {"exe": exe, "name": FRIENDLY.get(key[:-4] if key.endswith(".exe") else key,
                                                                   exe[:-4] if exe.lower().endswith(".exe") else exe),
                                  "pids": [], "active": False})
        a["pids"].append(p.pid)
        a["active"] = a["active"] or s.State == 1
    return sorted(apps.values(), key=lambda a: (not a["active"], a["name"].lower()))
