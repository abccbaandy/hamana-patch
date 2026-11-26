#!/usr/bin/env python3
"""背景跑沙箱 Hamana 的逐檔測試矩陣：不搶前景焦點、不出現在使用者畫面。取代 run_matrix.ps1。

用法：
    python tools/re/hidden_run.py --exe <沙箱 Hamana.exe> [--exe <另一個>] [--seconds 8] <檔案或資料夾> [...]
資料夾 = 逐一測試其中每個檔案（不遞迴）；子資料夾則取其第一個檔案（配合「每樣本一資料夾」）。

輸出每檔每 exe：ALIVE / CRASH 0x...（若曾取得前景再加 FOCUS），以及標題中的 (i/n) [WxHxbpp]（不輸出檔名）。

作法（實測，2026-11-26）：
- CreateProcess + STARTF_USESHOWWINDOW + SW_SHOWMINNOACTIVE：最小化啟動、不啟動視窗，圖照常解碼。
- 沙箱 Hamana.ini 的 [Window] 再設到畫面外（Left=-5000 Right=-3700 Top=-3000 Bottom=-2100，IsFullscreen=0），
  Hamana 自行還原視窗時也看不到。
- 不可行：CreateDesktop 建隱藏桌面再在上面跑 → 視窗建得出來，但標題停在 "Hamana"、圖不載入（guess：D3D9 在非輸入桌面建不了 device）。
- run_matrix.ps1 的 Start-Process 以一般方式顯示 → 會搶焦點。
"""
import argparse
import ctypes as C
import ctypes.wintypes as W
import os
import re
import sys
import time

k32 = C.WinDLL('kernel32', use_last_error=True)
u32 = C.WinDLL('user32', use_last_error=True)

CREATE_UNICODE_ENVIRONMENT = 0x400
STARTF_USESHOWWINDOW = 0x1
SW_SHOWMINNOACTIVE = 7
STILL_ACTIVE = 259


class STARTUPINFO(C.Structure):
    _fields_ = [('cb', W.DWORD), ('lpReserved', W.LPWSTR), ('lpDesktop', W.LPWSTR), ('lpTitle', W.LPWSTR),
                ('dwX', W.DWORD), ('dwY', W.DWORD), ('dwXSize', W.DWORD), ('dwYSize', W.DWORD),
                ('dwXCountChars', W.DWORD), ('dwYCountChars', W.DWORD), ('dwFillAttribute', W.DWORD),
                ('dwFlags', W.DWORD), ('wShowWindow', W.WORD), ('cbReserved2', W.WORD),
                ('lpReserved2', C.c_void_p), ('hStdInput', W.HANDLE), ('hStdOutput', W.HANDLE),
                ('hStdError', W.HANDLE)]


class PROCESS_INFORMATION(C.Structure):
    _fields_ = [('hProcess', W.HANDLE), ('hThread', W.HANDLE), ('dwProcessId', W.DWORD), ('dwThreadId', W.DWORD)]


WNDENUMPROC = C.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)
u32.EnumWindows.argtypes = [WNDENUMPROC, W.LPARAM]
u32.GetWindowThreadProcessId.argtypes = [W.HWND, C.POINTER(W.DWORD)]
u32.GetWindowTextW.argtypes = [W.HWND, W.LPWSTR, C.c_int]
u32.GetForegroundWindow.restype = W.HWND
k32.CreateProcessW.argtypes = [W.LPCWSTR, W.LPWSTR, C.c_void_p, C.c_void_p, W.BOOL, W.DWORD, C.c_void_p,
                               W.LPCWSTR, C.POINTER(STARTUPINFO), C.POINTER(PROCESS_INFORMATION)]
k32.GetExitCodeProcess.argtypes = [W.HANDLE, C.POINTER(W.DWORD)]
k32.TerminateProcess.argtypes = [W.HANDLE, W.UINT]
k32.WaitForSingleObject.argtypes = [W.HANDLE, W.DWORD]
k32.CloseHandle.argtypes = [W.HANDLE]

TITLE_RX = re.compile(r'\((\d+/\d+)\)\s*(\[[^\]]+\])?')


def titles(pid):
    out = []

    def cb(hwnd, _):
        p = W.DWORD()
        u32.GetWindowThreadProcessId(hwnd, C.byref(p))
        if p.value == pid:
            buf = C.create_unicode_buffer(512)
            u32.GetWindowTextW(hwnd, buf, 512)
            if buf.value:
                out.append(buf.value)
        return True

    u32.EnumWindows(WNDENUMPROC(cb), 0)
    return out


def fg_pid():
    p = W.DWORD()
    u32.GetWindowThreadProcessId(u32.GetForegroundWindow(), C.byref(p))
    return p.value


def run_one(exe, target, seconds):
    si = STARTUPINFO()
    si.cb = C.sizeof(si)
    si.dwFlags = STARTF_USESHOWWINDOW
    si.wShowWindow = SW_SHOWMINNOACTIVE
    pi = PROCESS_INFORMATION()
    cmd = C.create_unicode_buffer(f'"{exe}" "{target}"')
    if not k32.CreateProcessW(exe, cmd, None, None, False, CREATE_UNICODE_ENVIRONMENT, None,
                              os.path.dirname(exe), C.byref(si), C.byref(pi)):
        return f'CreateProcess err {C.get_last_error()}', '-'
    title, res, stole = '', None, False
    t0 = time.time()
    try:
        while time.time() - t0 < seconds:
            time.sleep(0.5)
            code = W.DWORD()
            k32.GetExitCodeProcess(pi.hProcess, C.byref(code))
            if code.value != STILL_ACTIVE:
                res = f'CRASH 0x{code.value:08X} @{time.time() - t0:.0f}s'
                break
            if fg_pid() == pi.dwProcessId:
                stole = True
            for t in titles(pi.dwProcessId):
                if TITLE_RX.search(t):
                    title = t
        if res is None:
            res = 'ALIVE'
            k32.TerminateProcess(pi.hProcess, 1)
            k32.WaitForSingleObject(pi.hProcess, 5000)
    finally:
        k32.CloseHandle(pi.hThread)
        k32.CloseHandle(pi.hProcess)
    m = TITLE_RX.search(title)
    t = f'({m.group(1)}) {m.group(2) or ""}'.strip() if m else '-'
    return res + (' FOCUS' if stole else ''), t


def expand(paths):
    for p in paths:
        if os.path.isdir(p):
            for name in sorted(os.listdir(p)):
                q = os.path.join(p, name)
                if os.path.isdir(q):
                    fs = sorted(f for f in os.listdir(q) if os.path.isfile(os.path.join(q, f)))
                    if fs:
                        yield os.path.join(q, fs[0])
                else:
                    yield q
        else:
            yield p


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--exe', action='append', required=True)
    ap.add_argument('--seconds', type=float, default=8)
    ap.add_argument('targets', nargs='+')
    a = ap.parse_args()
    for f in expand(a.targets):
        row = [os.path.basename(f).ljust(24)]
        for exe in a.exe:
            res, t = run_one(os.path.abspath(exe), f, a.seconds)
            row.append(f'{res:<22} {t:<22}')
        print(' | '.join(row), flush=True)


if __name__ == '__main__':
    sys.exit(main())
