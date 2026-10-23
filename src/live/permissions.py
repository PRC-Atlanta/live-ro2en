"""macOS privacy permissions, read without extra packages (ctypes + the Objective-C runtime).
Owned by S1. Used by the engine (mic_denied) and GET /api/permissions.

macOS does not fail an audio stream when the user said no to the microphone: the app just
gets silence. So the engine asks AVFoundation instead of guessing from the samples. The
answer is for the "responsible" app (the Mac app, or Terminal in dev), which is the one the
user sees in System Settings → Privacy & Security → Microphone."""
from __future__ import annotations

import ctypes
import sys

# AVAuthorizationStatus
_STATUS = {0: "undetermined", 1: "restricted", 2: "denied", 3: "granted"}


def microphone() -> str:
    """granted | denied | restricted | undetermined | unknown (not a Mac, or no answer)."""
    if sys.platform != "darwin":
        return "unknown"
    try:
        objc = ctypes.CDLL("/usr/lib/libobjc.A.dylib")
        av = ctypes.CDLL("/System/Library/Frameworks/AVFoundation.framework/AVFoundation")
        objc.objc_getClass.restype = ctypes.c_void_p
        objc.objc_getClass.argtypes = [ctypes.c_char_p]
        objc.sel_registerName.restype = ctypes.c_void_p
        objc.sel_registerName.argtypes = [ctypes.c_char_p]
        cls = objc.objc_getClass(b"AVCaptureDevice")
        media = ctypes.c_void_p.in_dll(av, "AVMediaTypeAudio")
        if not cls or not media.value:
            return "unknown"
        send = objc.objc_msgSend
        send.restype = ctypes.c_long
        send.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
        return _STATUS.get(int(send(cls, objc.sel_registerName(b"authorizationStatusForMediaType:"), media)),
                           "unknown")
    except (OSError, ValueError, AttributeError):
        return "unknown"


def status() -> dict:
    """GET /api/permissions."""
    return {"microphone": microphone()}
