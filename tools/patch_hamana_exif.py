#!/usr/bin/env python3
"""修正 Hamana 遇到 EXIF 位移損壞的 JPEG 時閃退（0xC000000D）。

背景（BUG-3，見 docs/bugs.md）：
  Hamana 讀 JPEG 時會解析 APP1 EXIF。解析器把 EXIF 放在 std::vector<BYTE>，
  以 VS2005 checked iterator（_SECURE_SCL）存取；IFD / 值的位移超出緩衝區時，
  checked iterator 呼叫 CRT `_invalid_parameter_noinfo` → `_invoke_watson`
  → 行程以 0xC000000D 直接終止，沒有任何例外處理機會。
  常見觸發：轉檔工具改寫 JPEG 時沒有更新 ExifIFD 指標（指到幾十 MB 外）。

修法（改程式碼，但檔案大小不變、可完整還原）：
  hook CRT `_invalid_parameter`（VA 0x5bfbf4），跳到 .text 內一段 0xCC 填充區的 stub：

      reg = fs:[0]                       ; 最內層 SEH 註冊記錄
      if reg->handler == EXIF 解析函式 X 的 EH thunk:
          esp = reg - X.regoff           ; 回到 X 的 baseline 堆疊
          jmp X.exit                     ; X 自己的正常出口（還原 fs:[0]、pop、ret）
      else:
          執行原本的 _invalid_parameter（行為與未 patch 完全相同）

  效果：EXIF 越界時只放棄該段 EXIF 解析（已解析的欄位保留），圖片照常顯示。
  非 EXIF 路徑的 invalid parameter 行為不變。
  代價：被跳過的函式內若有已建構的區域物件（CString / std::string），其解構子不執行
  → 每次觸發洩漏數十 bytes，可忽略。

三個 EXIF 解析函式（Hamana 1.48，皆為 /EHsc 的 C++ EH frame，fs:[0] = baseline+regoff）：
  0x454d90  IFD0 解析（呼叫下兩者）   thunk 0x60d4d5  regoff 0xac  exit 0x455592
  0x453600  Exif/Interop 子 IFD 解析   thunk 0x60d3f4  regoff 0xd8  exit 0x4543ea
  0x4546c0  GPS IFD 解析               thunk 0x60d45f  regoff 0xb8  exit 0x454d71

用法：
    python patch_hamana_exif.py <Hamana.exe 路徑>
    python patch_hamana_exif.py <Hamana.exe 路徑> --check
    python patch_hamana_exif.py <Hamana.exe 路徑> --revert   # 只還原本 patch 的 bytes
"""
import argparse
import os
import shutil
import struct

IMAGE_BASE = 0x400000

HOOK_VA = 0x5bfbf4                        # CRT _invalid_parameter 入口
HOOK_ORIG = bytes.fromhex('558bec ff359c3c6900')   # push ebp; mov ebp,esp; push [0x693c9c]
HOOK_RESUME = HOOK_VA + len(HOOK_ORIG)    # 0x5bfbfd

CAVE_VA = 0x53d0b0                        # 0x53d095..0x53e000 為 0xCC 填充
CAVE_MAX = 0x80

# (EH thunk, regoff, exit VA, exit 指令前綴簽章)
EXIF_FUNCS = [
    (0x60d3f4, 0xd8, 0x4543ea, '8b8c24d8000000 64890d00000000'),   # 子 IFD
    (0x60d4d5, 0xac, 0x455592, '8b8c24ac000000 64890d00000000'),   # IFD0
    (0x60d45f, 0xb8, 0x454d71, '8b8c24b8000000 64890d00000000'),   # GPS
]
# 函式入口簽章：push -1; push <thunk>
FUNC_ENTRIES = {0x60d3f4: 0x453600, 0x60d4d5: 0x454d90, 0x60d45f: 0x4546c0}


def rel32(src_next, dst):
    return struct.pack('<i', dst - src_next)


def build_stub():
    s = bytearray()
    s += bytes.fromhex('64a100000000')            # mov eax, fs:[0]
    s += bytes.fromhex('83f8ff')                  # cmp eax, -1
    jz_fix = len(s); s += b'\x74\x00'             # je orig（稍後回填）
    s += bytes.fromhex('8b4804')                  # mov ecx, [eax+4]
    for thunk, regoff, exit_va, _ in EXIF_FUNCS:
        s += b'\x81\xf9' + struct.pack('<I', thunk)   # cmp ecx, thunk
        s += b'\x75\x0b'                              # jne next（6+5 bytes 之後）
        s += b'\x8d\xa0' + struct.pack('<i', -regoff)  # lea esp, [eax-regoff]
        s += b'\xe9' + rel32(CAVE_VA + len(s) + 5, exit_va)  # jmp exit
    orig = len(s)
    s[jz_fix + 1] = orig - (jz_fix + 2)
    s += HOOK_ORIG                                # 原本的前三條指令
    s += b'\xe9' + rel32(CAVE_VA + len(s) + 5, HOOK_RESUME)
    assert len(s) <= CAVE_MAX
    return bytes(s)


def hook_bytes():
    j = b'\xe9' + rel32(HOOK_VA + 5, CAVE_VA)
    return j + b'\x90' * (len(HOOK_ORIG) - len(j))


def sections(d):
    e = struct.unpack_from('<I', d, 0x3c)[0]
    if d[e:e + 4] != b'PE\0\0':
        raise SystemExit('不是 PE 檔')
    n = struct.unpack_from('<H', d, e + 6)[0]
    opt = struct.unpack_from('<H', d, e + 20)[0]
    base = struct.unpack_from('<I', d, e + 24 + 28)[0]
    out = []
    for i in range(n):
        o = e + 24 + opt + 40 * i
        vsz, va, rsz, rptr = struct.unpack_from('<IIII', d, o + 8)
        out.append((va, max(vsz, rsz), rptr))
    return base, out


def off(secs, va):
    rva = va - IMAGE_BASE
    for sva, size, rptr in secs:
        if sva <= rva < sva + size:
            return rptr + rva - sva
    raise SystemExit('VA %x 不在任何 section 內' % va)


def state_of(d):
    """回傳 'original' / 'patched' / None（版本不符）。"""
    base, secs = sections(d)
    if base != IMAGE_BASE:
        return None
    # 結構簽章：三個 EXIF 函式入口與出口
    for thunk, regoff, exit_va, exit_sig in EXIF_FUNCS:
        fo = off(secs, FUNC_ENTRIES[thunk])
        if d[fo:fo + 7] != b'\x6a\xff\x68' + struct.pack('<I', thunk):
            return None
        eo = off(secs, exit_va)
        sig = bytes.fromhex(exit_sig.replace(' ', ''))
        if d[eo:eo + len(sig)] != sig:
            return None
    ho, co = off(secs, HOOK_VA), off(secs, CAVE_VA)
    stub = build_stub()
    hook = d[ho:ho + len(HOOK_ORIG)]
    cave = d[co:co + CAVE_MAX]
    if hook == HOOK_ORIG and cave == b'\xcc' * CAVE_MAX:
        return 'original'
    if hook == hook_bytes() and cave[:len(stub)] == stub and cave[len(stub):] == b'\xcc' * (CAVE_MAX - len(stub)):
        return 'patched'
    return None


def do_check(path):
    d = open(path, 'rb').read()
    st = state_of(d)
    if st == 'patched':
        print('%s：已套用 EXIF 越界防護 patch' % path)
    elif st == 'original':
        print('%s：未套用（EXIF 位移損壞的 JPEG 會閃退 0xC000000D）' % path)
    else:
        print('%s：無法辨識，可能不是預期的 Hamana 版本（或被其他工具改過此區）' % path)
    return st


def do_patch(path):
    d = bytearray(open(path, 'rb').read())
    st = state_of(bytes(d))
    if st == 'patched':
        print('已經是 patch 過的版本，不需重複執行')
        return
    if st is None:
        raise SystemExit('簽章不符；此 Hamana 版本不受支援')
    backup = path + '.orig'
    if not os.path.exists(backup):
        shutil.copy(path, backup)
        print('已備份原檔 → %s' % backup)
    _, secs = sections(bytes(d))
    ho, co = off(secs, HOOK_VA), off(secs, CAVE_VA)
    stub = build_stub()
    d[co:co + len(stub)] = stub
    d[ho:ho + len(HOOK_ORIG)] = hook_bytes()
    open(path, 'wb').write(bytes(d))
    print('patch 完成（檔案大小不變）')
    print('  hook _invalid_parameter @ VA %x（file %d）' % (HOOK_VA, ho))
    print('  stub %d bytes        @ VA %x（file %d）' % (len(stub), CAVE_VA, co))


def do_revert(path):
    d = bytearray(open(path, 'rb').read())
    st = state_of(bytes(d))
    if st == 'original':
        print('本 patch 未套用，不需還原')
        return
    if st is None:
        raise SystemExit('簽章不符，無法安全還原；請改用 .orig 備份')
    _, secs = sections(bytes(d))
    ho, co = off(secs, HOOK_VA), off(secs, CAVE_VA)
    d[ho:ho + len(HOOK_ORIG)] = HOOK_ORIG
    d[co:co + CAVE_MAX] = b'\xcc' * CAVE_MAX
    open(path, 'wb').write(bytes(d))
    print('已還原 EXIF patch（其他 patch 不受影響）：%s' % path)


def main():
    ap = argparse.ArgumentParser(description='修正 Hamana 遇到 EXIF 位移損壞的 JPEG 時閃退')
    ap.add_argument('exe', help='Hamana.exe 路徑')
    g = ap.add_mutually_exclusive_group()
    g.add_argument('--revert', action='store_true', help='還原本 patch 改動的 bytes')
    g.add_argument('--check', action='store_true', help='只檢查目前狀態')
    args = ap.parse_args()
    if not os.path.isfile(args.exe):
        raise SystemExit('檔案不存在：%s' % args.exe)
    if args.check:
        do_check(args.exe)
    elif args.revert:
        do_revert(args.exe)
    else:
        do_patch(args.exe)


if __name__ == '__main__':
    main()
