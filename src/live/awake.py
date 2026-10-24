"""Keep this process out of App Nap and timer throttling (macOS), via
NSProcessInfo.beginActivityWithOptions:reason: called through ctypes (no PyObjC).

Python runs as Python.app, so macOS may nap it when no window of it is in front: a service
started from a terminal slowed recognition and translation ~100x once the speaker paused.
The Mac app guards only its own process (LiveActivity.swift), so the app server and the
speech engine each call `hold()` at start. Idle sleep stays allowed: the Mac app blocks it
while captions are live."""
from __future__ import annotations

import ctypes
import ctypes.util
import sys

# NSActivityUserInitiatedAllowingIdleSystemSleep | NSActivityLatencyCritical
OPTIONS = (0x00FFFFFF & ~(1 << 20)) | 0xFF00000000
_token = None


def hold(reason: str = "Live captions") -> bool:
    """True when the activity is held (kept for the life of the process)."""
    global _token
    if _token is not None:
        return True
    if sys.platform != "darwin":
        return False
    try:
        objc = ctypes.cdll.LoadLibrary(ctypes.util.find_library("objc"))
        ctypes.cdll.LoadLibrary(ctypes.util.find_library("Foundation"))
        objc.objc_getClass.restype = ctypes.c_void_p
        objc.objc_getClass.argtypes = [ctypes.c_char_p]
        objc.sel_registerName.restype = ctypes.c_void_p
        objc.sel_registerName.argtypes = [ctypes.c_char_p]
        send = objc.objc_msgSend
        sel = objc.sel_registerName

        def call(restype, *argtypes):              # objc_msgSend with one exact signature
            return ctypes.CFUNCTYPE(restype, ctypes.c_void_p, ctypes.c_void_p, *argtypes)(
                ctypes.cast(send, ctypes.c_void_p).value)
        msg = call(ctypes.c_void_p)
        info = msg(objc.objc_getClass(b"NSProcessInfo"), sel(b"processInfo"))
        text = call(ctypes.c_void_p, ctypes.c_char_p)(objc.objc_getClass(b"NSString"),
                                                       sel(b"stringWithUTF8String:"), reason.encode())
        tok = call(ctypes.c_void_p, ctypes.c_uint64, ctypes.c_void_p)(
            info, sel(b"beginActivityWithOptions:reason:"), OPTIONS, text)
        if not tok:
            return False
        _token = msg(tok, sel(b"retain"))
        return True
    except Exception:                              # noqa: BLE001  best effort: never block a start
        return False
