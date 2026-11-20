"""macOS microphone permission helpers.

Authorization uses AVFoundation via pyobjc when it is installed. The UI still
treats an opened-but-silent stream as "no microphone": macOS can grant a
stream of zeros after the user denies access.
"""

from __future__ import annotations

import platform
import subprocess
import sys
from collections.abc import Callable

_STATUS = {
    0: "not_determined",
    1: "restricted",
    2: "denied",
    3: "authorized",
}


def authorization_status() -> str:
    if sys.platform != "darwin":
        return "unknown"
    try:
        import AVFoundation
    except Exception:
        return "unknown"
    try:
        raw = AVFoundation.AVCaptureDevice.authorizationStatusForMediaType_(
            AVFoundation.AVMediaTypeAudio
        )
        return _STATUS.get(int(raw), "unknown")
    except Exception:
        return "unknown"


def request_microphone_access(callback: Callable[[bool], None]) -> None:
    """Ask for microphone access and invoke ``callback(granted)``.

    Off macOS, or when pyobjc is missing, the callback runs immediately with
    True so the caller can fall back to opening the input device.
    """
    if sys.platform != "darwin":
        callback(True)
        return
    try:
        import AVFoundation
    except Exception:
        callback(True)
        return

    def _handler(granted) -> None:
        try:
            callback(bool(granted))
        except Exception:
            pass

    try:
        AVFoundation.AVCaptureDevice.requestAccessForMediaType_completionHandler_(
            AVFoundation.AVMediaTypeAudio,
            _handler,
        )
    except Exception:
        callback(True)


def microphone_settings_url() -> str:
    version = platform.mac_ver()[0]
    major = 12
    if version:
        try:
            major = int(version.split(".")[0])
        except ValueError:
            major = 12
    if major >= 13:
        return "x-apple.systempreferences:com.apple.settings.PrivacySecurity.extension?Privacy_Microphone"
    return "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone"


def open_microphone_settings() -> None:
    if sys.platform != "darwin":
        return
    try:
        subprocess.run(["open", microphone_settings_url()], check=False, timeout=10)
    except Exception:
        return
