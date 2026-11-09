"""Authenticode verification through ``WinVerifyTrust`` (no shell-outs).

Used to attest that a frozen executable is the one Windows trusts.  The
function never raises: non-Windows platforms, missing files, and any ctypes
or trust-provider error are all reported as ``False``.
"""
from __future__ import annotations

import ctypes
import os
import sys

__all__ = ["verify_authenticode"]

# wintrust.h / softpub.h constants.
WTD_UI_NONE = 2
WTD_REVOKE_NONE = 0
WTD_CHOICE_FILE = 1
WTD_STATEACTION_IGNORE = 0
WTD_STATEACTION_VERIFY = 1
WTD_STATEACTION_CLOSE = 2
ERROR_SUCCESS = 0


class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_uint32),
        ("Data2", ctypes.c_uint16),
        ("Data3", ctypes.c_uint16),
        ("Data4", ctypes.c_uint8 * 8),
    ]


class _WintrustFileInfo(ctypes.Structure):
    """WINTRUST_FILE_INFO."""

    _fields_ = [
        ("cbStruct", ctypes.c_uint32),
        ("pcwszFilePath", ctypes.c_wchar_p),
        ("hFile", ctypes.c_void_p),
        ("pgKnownSubject", ctypes.c_void_p),
    ]


class _WintrustData(ctypes.Structure):
    """WINTRUST_DATA (file choice; includes the Windows 8+ trailing field)."""

    _fields_ = [
        ("cbStruct", ctypes.c_uint32),
        ("pPolicyCallbackData", ctypes.c_void_p),
        ("pSIPClientData", ctypes.c_void_p),
        ("dwUIChoice", ctypes.c_uint32),
        ("fdwRevocationChecks", ctypes.c_uint32),
        ("dwUnionChoice", ctypes.c_uint32),
        ("pFile", ctypes.POINTER(_WintrustFileInfo)),
        ("dwStateAction", ctypes.c_uint32),
        ("hWVTStateData", ctypes.c_void_p),
        ("pwszURLReference", ctypes.c_wchar_p),
        ("dwProvFlags", ctypes.c_uint32),
        ("dwUIContext", ctypes.c_uint32),
        ("pSignatureSettings", ctypes.c_void_p),
    ]


# WINTRUST_ACTION_GENERIC_VERIFY_V2 (softpub.h):
# {0000aac5-6bcd-4411-8cc2-00c04fc295ee}
_ACTION_GENERIC_VERIFY_V2 = _GUID(
    0x0000AAC5,
    0x6BCD,
    0x4411,
    (ctypes.c_uint8 * 8)(0x8C, 0xC2, 0x00, 0xC0, 0x4F, 0xC2, 0x95, 0xEE),
)


def verify_authenticode(path: str) -> bool:
    """Return ``True`` only if Windows trusts the file's Authenticode signature.

    Never raises: every failure mode (non-Windows, missing path, non-file,
    ctypes error, trust failure) yields ``False``.
    """
    wintrust = None
    state = None
    try:
        if sys.platform != "win32":
            return False
        if not path or not os.path.isfile(path):
            return False
        windll = getattr(ctypes, "windll", None)
        if windll is None:
            return False

        wintrust = windll.wintrust.WinVerifyTrust
        wintrust.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(_GUID),
            ctypes.c_void_p,
        ]
        wintrust.restype = ctypes.c_long

        file_info = _WintrustFileInfo()
        file_info.cbStruct = ctypes.sizeof(_WintrustFileInfo)
        file_info.pcwszFilePath = os.path.abspath(path)
        file_info.hFile = None
        file_info.pgKnownSubject = None

        state = _WintrustData()
        state.cbStruct = ctypes.sizeof(_WintrustData)
        state.dwUIChoice = WTD_UI_NONE
        state.fdwRevocationChecks = WTD_REVOKE_NONE
        state.dwUnionChoice = WTD_CHOICE_FILE
        state.pFile = ctypes.pointer(file_info)
        state.dwStateAction = WTD_STATEACTION_VERIFY
        state.hWVTStateData = None
        state.pwszURLReference = None
        state.dwProvFlags = 0
        state.dwUIContext = 0

        status = wintrust(
            None, ctypes.byref(_ACTION_GENERIC_VERIFY_V2), ctypes.byref(state)
        )
        return status == ERROR_SUCCESS
    except Exception:
        return False
    finally:
        # Always release the trust-provider state data once created.
        if wintrust is not None and state is not None:
            try:
                state.dwStateAction = WTD_STATEACTION_CLOSE
                wintrust(
                    None, ctypes.byref(_ACTION_GENERIC_VERIFY_V2), ctypes.byref(state)
                )
            except Exception:
                pass
