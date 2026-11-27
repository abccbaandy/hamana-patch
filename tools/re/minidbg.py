"""最小 Win32 debugger（ctypes，64-bit Python 可除錯 32-bit WOW64 行程）。

用法: minidbg.py [--bp VA ...] [--timeout 秒] [--maxhits N] [--loads] [--cwd DIR] -- <exe> [args...]
- --loads：逐筆印出 LOAD_DLL 事件（路徑以 <exe>/<target>/<win>/<other> 遮蔽），可抓到用完即 FreeLibrary 的 DLL。
- --cwd：行程起始工作目錄（預設 exe 目錄）。檔案總管開檔時通常是該檔所在資料夾。
- 在指定 VA 下 int3；命中時印暫存器、EBP 鏈、堆疊中看似 return address 的值
  （前一指令為 call 且落在已載入模組內），並標註模組名+偏移。
- 印出 first-chance exception（不吞掉，照樣交回程式處理）。
- 以 SW_SHOWNOACTIVATE 啟動（不搶焦點；視窗靠沙箱 ini 放在畫面外）。
- Hamana.exe 無 ASLR（ImageBase 0x400000），VA 可直接用。
預設 bp：0x5bfbf4 = CRT _invalid_parameter（Hamana 1.48 的 0xC000000D 閃退入口）。
"""
import ctypes as C, ctypes.wintypes as W, sys, time, struct, os

k32 = C.WinDLL('kernel32', use_last_error=True)
DEBUG_ONLY_THIS_PROCESS = 2
DBG_CONTINUE = 0x00010002
DBG_EXCEPTION_NOT_HANDLED = 0x80010001


class STARTUPINFO(C.Structure):
    _fields_ = [('cb', W.DWORD), ('lpReserved', W.LPWSTR), ('lpDesktop', W.LPWSTR), ('lpTitle', W.LPWSTR),
                ('dwX', W.DWORD), ('dwY', W.DWORD), ('dwXSize', W.DWORD), ('dwYSize', W.DWORD),
                ('dwXCountChars', W.DWORD), ('dwYCountChars', W.DWORD), ('dwFillAttribute', W.DWORD),
                ('dwFlags', W.DWORD), ('wShowWindow', W.WORD), ('cbReserved2', W.WORD),
                ('lpReserved2', C.c_void_p), ('hStdInput', W.HANDLE), ('hStdOutput', W.HANDLE), ('hStdError', W.HANDLE)]


class PROCESS_INFORMATION(C.Structure):
    _fields_ = [('hProcess', W.HANDLE), ('hThread', W.HANDLE), ('dwProcessId', W.DWORD), ('dwThreadId', W.DWORD)]


class EXCEPTION_RECORD(C.Structure):
    pass


EXCEPTION_RECORD._fields_ = [('ExceptionCode', W.DWORD), ('ExceptionFlags', W.DWORD),
                             ('ExceptionRecord', C.c_void_p), ('ExceptionAddress', C.c_void_p),
                             ('NumberParameters', W.DWORD), ('ExceptionInformation', C.c_size_t * 15)]


class EXCEPTION_DEBUG_INFO(C.Structure):
    _fields_ = [('ExceptionRecord', EXCEPTION_RECORD), ('dwFirstChance', W.DWORD)]


class CREATE_PROCESS_DEBUG_INFO(C.Structure):
    _fields_ = [('hFile', W.HANDLE), ('hProcess', W.HANDLE), ('hThread', W.HANDLE), ('lpBaseOfImage', C.c_void_p),
                ('dwDebugInfoFileOffset', W.DWORD), ('nDebugInfoSize', W.DWORD), ('lpThreadLocalBase', C.c_void_p),
                ('lpStartAddress', C.c_void_p), ('lpImageName', C.c_void_p), ('fUnicode', W.WORD)]


class LOAD_DLL_DEBUG_INFO(C.Structure):
    _fields_ = [('hFile', W.HANDLE), ('lpBaseOfDll', C.c_void_p), ('dwDebugInfoFileOffset', W.DWORD),
                ('nDebugInfoSize', W.DWORD), ('lpImageName', C.c_void_p), ('fUnicode', W.WORD)]


class EXIT_PROCESS_DEBUG_INFO(C.Structure):
    _fields_ = [('dwExitCode', W.DWORD)]


class U(C.Union):
    _fields_ = [('Exception', EXCEPTION_DEBUG_INFO), ('CreateProcessInfo', CREATE_PROCESS_DEBUG_INFO),
                ('LoadDll', LOAD_DLL_DEBUG_INFO), ('ExitProcess', EXIT_PROCESS_DEBUG_INFO), ('pad', C.c_byte * 176)]


class DEBUG_EVENT(C.Structure):
    _fields_ = [('dwDebugEventCode', W.DWORD), ('dwProcessId', W.DWORD), ('dwThreadId', W.DWORD), ('u', U)]


class FSA(C.Structure):
    _fields_ = [('a', W.DWORD * 7), ('RegisterArea', C.c_byte * 80), ('Cr0NpxState', W.DWORD)]


class WOW64_CONTEXT(C.Structure):
    _fields_ = [('ContextFlags', W.DWORD), ('Dr', W.DWORD * 6), ('FloatSave', FSA),
                ('SegGs', W.DWORD), ('SegFs', W.DWORD), ('SegEs', W.DWORD), ('SegDs', W.DWORD),
                ('Edi', W.DWORD), ('Esi', W.DWORD), ('Ebx', W.DWORD), ('Edx', W.DWORD), ('Ecx', W.DWORD),
                ('Eax', W.DWORD), ('Ebp', W.DWORD), ('Eip', W.DWORD), ('SegCs', W.DWORD), ('EFlags', W.DWORD),
                ('Esp', W.DWORD), ('SegSs', W.DWORD), ('ExtendedRegisters', C.c_byte * 512)]


WOW64_CONTEXT_FULL = 0x10000 | 1 | 2 | 4

k32.WaitForDebugEvent.argtypes = [C.POINTER(DEBUG_EVENT), W.DWORD]
k32.ContinueDebugEvent.argtypes = [W.DWORD, W.DWORD, W.DWORD]
k32.ReadProcessMemory.argtypes = [W.HANDLE, C.c_void_p, C.c_void_p, C.c_size_t, C.POINTER(C.c_size_t)]
k32.WriteProcessMemory.argtypes = [W.HANDLE, C.c_void_p, C.c_void_p, C.c_size_t, C.POINTER(C.c_size_t)]
k32.OpenThread.restype = W.HANDLE
k32.OpenThread.argtypes = [W.DWORD, W.BOOL, W.DWORD]
k32.CloseHandle.argtypes = [W.HANDLE]
k32.TerminateProcess.argtypes = [W.HANDLE, W.UINT]
k32.Wow64GetThreadContext.argtypes = [W.HANDLE, C.POINTER(WOW64_CONTEXT)]
k32.Wow64SetThreadContext.argtypes = [W.HANDLE, C.POINTER(WOW64_CONTEXT)]
k32.GetFinalPathNameByHandleW.argtypes = [W.HANDLE, W.LPWSTR, W.DWORD, W.DWORD]
k32.FlushInstructionCache.argtypes = [W.HANDLE, C.c_void_p, C.c_size_t]


def main():
    argv = sys.argv[1:]
    bps, timeout, maxhits, loads, cwd = [], 60.0, 3, False, None
    while argv and argv[0] != '--':
        if argv[0] == '--bp':
            bps.append(int(argv[1], 16)); argv = argv[2:]
        elif argv[0] == '--timeout':
            timeout = float(argv[1]); argv = argv[2:]
        elif argv[0] == '--cwd':
            cwd = argv[1]; argv = argv[2:]
        elif argv[0] == '--loads':
            loads = True; argv = argv[1:]
        elif argv[0] == '--maxhits':
            maxhits = int(argv[1]); argv = argv[2:]
        else:
            sys.exit('bad arg ' + argv[0])
    argv = argv[1:]
    if not bps:
        bps = [0x5bfbf4]
    cmd = ' '.join('"%s"' % a for a in argv)
    si = STARTUPINFO(); si.cb = C.sizeof(si); pi = PROCESS_INFORMATION()
    si.dwFlags, si.wShowWindow = 1, 4  # STARTF_USESHOWWINDOW + SW_SHOWNOACTIVATE：不搶焦點。不可用最小化（7）：除錯下 D3D 以 client 高 0 建 device 失敗並跳對話框
    if not k32.CreateProcessW(None, C.create_unicode_buffer(cmd), None, None, False,
                              DEBUG_ONLY_THIS_PROCESS, None, cwd or os.path.dirname(argv[0]), C.byref(si), C.byref(pi)):
        sys.exit('CreateProcess failed %d' % C.get_last_error())
    hp = pi.hProcess
    mods = {}

    def rd1(addr, n):
        buf = C.create_string_buffer(n); got = C.c_size_t()
        k32.ReadProcessMemory(hp, addr, buf, n, C.byref(got))
        return buf.raw[:got.value]

    def rd(addr, n):
        # 跨頁讀取時 RPM 整段失敗 → 逐頁讀到第一個不可讀頁為止
        out = b''
        while n > 0:
            chunk = min(n, 0x1000 - (addr & 0xfff))
            b = rd1(addr, chunk)
            out += b
            if len(b) < chunk:
                break
            addr += chunk; n -= chunk
        return out

    def wr(addr, data):
        got = C.c_size_t()
        k32.WriteProcessMemory(hp, addr, data, len(data), C.byref(got))
        k32.FlushInstructionCache(hp, addr, len(data))

    def modsize(base):
        h = rd(base, 0x400)
        if h[:2] != b'MZ':
            return 0x1000
        e = struct.unpack_from('<I', h, 0x3c)[0]
        return struct.unpack_from('<I', h, e + 0x50)[0]

    def fullpath(hf):
        b = C.create_unicode_buffer(1024)
        if hf and k32.GetFinalPathNameByHandleW(hf, b, 1024, 0):
            return b.value[4:] if b.value.startswith('\\\\?\\') else b.value
        return '?'

    def fname(hf):
        return os.path.basename(fullpath(hf))

    roots = [(os.path.normcase(os.path.dirname(os.path.abspath(argv[0]))), '<exe>'),
             (os.path.normcase(os.environ.get('SystemRoot', r'C:\Windows')), '<win>')]
    if len(argv) > 1:
        roots.insert(1, (os.path.normcase(os.path.dirname(os.path.abspath(argv[1]))), '<target>'))

    def masked(path):
        q = os.path.normcase(path)
        for r, tag in roots:
            if q.startswith(r + os.sep):
                return tag + q[len(r):]
        return '<other>' + os.sep + os.path.basename(q)

    def sym(a):
        for base, (n, sz) in mods.items():
            if base <= a < base + sz:
                return '%s+%x' % (n, a - base)
        return None

    def is_ret(a):
        b = rd(a - 7, 7)
        if len(b) < 7:
            return False
        return (b[2] == 0xE8 or (b[5] == 0xFF and (b[6] & 0x38) == 0x10) or (b[4] == 0xFF and (b[5] & 0x38) == 0x10)
                or (b[1] == 0xFF and (b[2] & 0x38) == 0x10) or (b[3] == 0xFF and (b[4] & 0x38) == 0x10))

    orig = {}
    stepping = {}
    hits = 0
    t0 = time.time()
    ev = DEBUG_EVENT()
    while True:
        if not k32.WaitForDebugEvent(C.byref(ev), 500):
            if time.time() - t0 > timeout:
                print('[timeout %.0fs] 行程仍存活 -> ALIVE' % timeout, flush=True)
                k32.TerminateProcess(hp, 0)
                timeout = 1e9
            continue
        code = ev.dwDebugEventCode
        st = DBG_CONTINUE
        if code == 3:
            info = ev.u.CreateProcessInfo
            base = info.lpBaseOfImage
            mods[base] = (fname(info.hFile), modsize(base))
            for va in bps:
                orig[va] = rd(va, 1); wr(va, b'\xcc')
        elif code == 6:
            base = ev.u.LoadDll.lpBaseOfDll
            fp = fullpath(ev.u.LoadDll.hFile)
            mods[base] = (os.path.basename(fp), modsize(base))
            if loads:
                print('[load] t=%.1fs %08x %s' % (time.time() - t0, base, masked(fp)), flush=True)
        elif code == 5:
            print('[exit] code=0x%08x  t=%.1fs' % (ev.u.ExitProcess.dwExitCode & 0xffffffff, time.time() - t0))
            break
        elif code == 1:
            er = ev.u.Exception.ExceptionRecord
            ec = er.ExceptionCode
            addr = er.ExceptionAddress or 0
            th = k32.OpenThread(0x1FFFFF, False, ev.dwThreadId)
            if ec in (0x80000003, 0x4000001F) and addr in orig:
                ctx = WOW64_CONTEXT(); ctx.ContextFlags = WOW64_CONTEXT_FULL
                k32.Wow64GetThreadContext(th, C.byref(ctx))
                hits += 1
                print('\n=== BP %08x hit #%d tid=%d t=%.1fs' % (addr, hits, ev.dwThreadId, time.time() - t0))
                print(' eax=%08x ebx=%08x ecx=%08x edx=%08x esi=%08x edi=%08x ebp=%08x esp=%08x' % (
                    ctx.Eax, ctx.Ebx, ctx.Ecx, ctx.Edx, ctx.Esi, ctx.Edi, ctx.Ebp, ctx.Esp))
                stk = rd(ctx.Esp, 0x3000)
                print(' stack[0..]:', ' '.join('%08x' % v for v in struct.unpack_from("<%dI" % min(12, len(stk)//4), stk, 0)))
                print(' -- stack return-address candidates --')
                n = 0
                for off in range(0, len(stk) - 3, 4):
                    v = struct.unpack_from('<I', stk, off)[0]
                    s = sym(v)
                    if s and is_ret(v):
                        print('  [esp+%04x] %08x %s' % (off, v, s)); n += 1
                        if n > 40:
                            break
                print(' -- ebp chain --')
                e = ctx.Ebp
                for _ in range(20):
                    d = rd(e, 8)
                    if len(d) < 8:
                        break
                    nxt, ra = struct.unpack('<II', d)
                    print('  ebp=%08x ret=%08x %s' % (e, ra, sym(ra)))
                    if nxt <= e:
                        break
                    e = nxt
                sys.stdout.flush()
                wr(addr, orig[addr]); ctx.Eip = addr; ctx.EFlags |= 0x100
                k32.Wow64SetThreadContext(th, C.byref(ctx)); stepping[ev.dwThreadId] = addr
                if hits >= maxhits:
                    print('[maxhits] 結束'); k32.TerminateProcess(hp, 0)
            elif ec in (0x80000004, 0x4000001E) and ev.dwThreadId in stepping:
                wr(stepping.pop(ev.dwThreadId), b'\xcc')
            elif ec in (0x80000003, 0x4000001F):
                pass
            else:
                fc = ev.u.Exception.dwFirstChance
                info = ' '.join('%x' % er.ExceptionInformation[i] for i in range(min(er.NumberParameters, 4)))
                if ec not in (0x406D1388, 0xE06D7363, 0x40010006, 0x4001000A):
                    print('[exc] %s code=%08x at %08x %s info=%s t=%.1fs' % (
                        '1st' if fc else '2nd', ec, addr, sym(addr), info, time.time() - t0), flush=True)
                st = DBG_EXCEPTION_NOT_HANDLED
            k32.CloseHandle(th)
        k32.ContinueDebugEvent(ev.dwProcessId, ev.dwThreadId, st)
    print('modules:', ', '.join('%s@%x' % (n, b) for b, (n, s) in sorted(mods.items())
                                if not n.lower().startswith(('api-', 'ext-'))))


if __name__ == '__main__':
    main()
