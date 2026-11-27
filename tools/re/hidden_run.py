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

--modules：結束前列出行程內非系統模組（Windows 目錄以外），路徑以 <exe>/<target> 遮蔽。
用來判定 Formats/*.dll 有無載入、資料夾內預放的 DLL 有無被載入（DLL 搜尋路徑）。
"""
import argparse
import ctypes as C
import ctypes.wintypes as W
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from make_sandbox import reset_window  # noqa: E402

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

TH32CS_SNAPMODULE = 0x8
TH32CS_SNAPMODULE32 = 0x10


class MODULEENTRY32W(C.Structure):
    _fields_ = [('dwSize', W.DWORD), ('th32ModuleID', W.DWORD), ('th32ProcessID', W.DWORD),
                ('GlblcntUsage', W.DWORD), ('ProccntUsage', W.DWORD), ('modBaseAddr', C.c_void_p),
                ('modBaseSize', W.DWORD), ('hModule', W.HMODULE), ('szModule', W.WCHAR * 256),
                ('szExePath', W.WCHAR * 260)]


k32.CreateToolhelp32Snapshot.restype = W.HANDLE
k32.CreateToolhelp32Snapshot.argtypes = [W.DWORD, W.DWORD]
k32.Module32FirstW.argtypes = [W.HANDLE, C.POINTER(MODULEENTRY32W)]
k32.Module32NextW.argtypes = [W.HANDLE, C.POINTER(MODULEENTRY32W)]
WINDIR = os.path.normcase(os.environ.get('SystemRoot', r'C:\Windows'))


def modules(pid, exe, target):
    """非系統模組，路徑遮蔽成 <exe>/... 或 <target>/...。"""
    h = k32.CreateToolhelp32Snapshot(TH32CS_SNAPMODULE | TH32CS_SNAPMODULE32, pid)
    if h in (None, W.HANDLE(-1).value):
        return ['<snapshot err %d>' % C.get_last_error()]
    out, me = [], MODULEENTRY32W()
    me.dwSize = C.sizeof(me)
    roots = [(os.path.normcase(os.path.dirname(exe)), '<exe>'), (os.path.normcase(os.path.dirname(target)), '<target>')]
    ok = k32.Module32FirstW(h, C.byref(me))
    while ok:
        p = os.path.normcase(me.szExePath)
        if not p.startswith(WINDIR):
            for r, tag in roots:
                if p.startswith(r + os.sep):
                    p = tag + p[len(r):]
                    break
            else:
                p = '<other>' + os.sep + os.path.basename(p)
            out.append(p)
        ok = k32.Module32NextW(h, C.byref(me))
    k32.CloseHandle(h)
    return sorted(out)


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


def run_one(exe, target, seconds, want_mods=False):
    if reset_window(os.path.dirname(exe)):
        print('  [修正] 沙箱 ini 的視窗座標被寫成最小化（-32000），已改回', flush=True)
    si = STARTUPINFO()
    si.cb = C.sizeof(si)
    si.dwFlags = STARTF_USESHOWWINDOW
    si.wShowWindow = SW_SHOWMINNOACTIVE
    pi = PROCESS_INFORMATION()
    cmd = C.create_unicode_buffer(f'"{exe}" "{target}"')
    if not k32.CreateProcessW(exe, cmd, None, None, False, CREATE_UNICODE_ENVIRONMENT, None,
                              os.path.dirname(exe), C.byref(si), C.byref(pi)):
        return f'CreateProcess err {C.get_last_error()}', '-'
    title, res, stole, mods = '', None, False, None
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
            if want_mods:
                mods = modules(pi.dwProcessId, exe, target)
            k32.TerminateProcess(pi.hProcess, 1)
            k32.WaitForSingleObject(pi.hProcess, 5000)
    finally:
        k32.CloseHandle(pi.hThread)
        k32.CloseHandle(pi.hProcess)
    m = TITLE_RX.search(title)
    t = f'({m.group(1)}) {m.group(2) or ""}'.strip() if m else '-'
    return res + (' FOCUS' if stole else ''), t, mods


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
    ap.add_argument('--modules', action='store_true')
    ap.add_argument('targets', nargs='+')
    a = ap.parse_args()
    for f in expand(a.targets):
        if not os.path.exists(f):
            print(f'{os.path.basename(f):<24} 不存在，略過（開不存在的檔會讓 Hamana 正常結束並寫壞 ini）', flush=True)
            continue
        row, extra = [os.path.basename(f).ljust(24)], []
        for i, exe in enumerate(a.exe):
            res, t, mods = run_one(os.path.abspath(exe), os.path.abspath(f), a.seconds, a.modules)
            row.append(f'{res:<22} {t:<22}')
            if mods is not None:
                extra.append(f'  exe#{i} modules: ' + ' '.join(mods))
        print(' | '.join(row), flush=True)
        for e in extra:
            print(e, flush=True)


if __name__ == '__main__':
    sys.exit(main())
