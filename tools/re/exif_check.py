"""檢查 zip/資料夾內 JPEG/WebP 的 EXIF(TIFF) 結構是否有越界位移。只印結構，不印標籤內容值。
用法: exif_check.py <zip 或資料夾>"""
import sys, os, struct, zipfile

def iter_files(p):
    if os.path.isdir(p):
        for r, _, fs in os.walk(p):
            for f in sorted(fs):
                fp = os.path.join(r, f)
                with open(fp, 'rb') as fh: yield os.path.relpath(fp, p), fh.read()
    else:
        z = zipfile.ZipFile(p)
        for i, zi in enumerate(z.infolist()):
            if zi.file_size: yield '#%d.%s' % (i, zi.filename.rsplit('.', 1)[-1]), z.read(zi)

def exif_blocks(b):
    if b[:2] == b'\xff\xd8':
        i = 2
        while i + 4 <= len(b) and b[i] == 0xFF:
            m = b[i + 1]
            if m in (0xD9, 0xDA): break
            L = struct.unpack('>H', b[i + 2:i + 4])[0]
            seg = b[i + 4:i + 2 + L]
            if m == 0xE1 and seg[:6] == b'Exif\0\0': yield 'APP1', seg[6:], L
            i += 2 + L
    elif b[:4] == b'RIFF' and b[8:12] == b'WEBP':
        i = 12
        while i + 8 <= len(b):
            cid = b[i:i + 4]; n = struct.unpack('<I', b[i + 4:i + 8])[0]
            if cid == b'EXIF':
                d = b[i + 8:i + 8 + n]
                if d[:6] == b'Exif\0\0': d = d[6:]
                yield 'EXIF', d, n
            i += 8 + n + (n & 1)

def check(t):
    issues = []
    if t[:2] == b'II': e = '<'
    elif t[:2] == b'MM': e = '>'
    else: return ['bad byteorder %r' % t[:4]]
    N = len(t)
    def u16(o): return struct.unpack(e + 'H', t[o:o + 2])[0]
    def u32(o): return struct.unpack(e + 'I', t[o:o + 4])[0]
    seen = set()
    def ifd(name, off, depth=0):
        if off in seen or depth > 4: return
        seen.add(off)
        if off + 2 > N: issues.append('%s offset %d >= len %d (OOB)' % (name, off, N)); return
        n = u16(off)
        end = off + 2 + 12 * n
        info = '%s@%d n=%d' % (name, off, n)
        if end + 4 > N: issues.append(info + ' entries end %d > len %d (OOB)' % (end + 4, N))
        for k in range(n):
            eo = off + 2 + 12 * k
            if eo + 12 > N: break
            tag, typ, cnt = struct.unpack(e + 'HHI', t[eo:eo + 8]); val = u32(eo + 8)
            sz = {1:1,2:1,3:2,4:4,5:8,6:1,7:1,8:2,9:4,10:8,11:4,12:8}.get(typ, 0) * cnt
            if sz > 4 and val + sz > N: issues.append('%s tag 0x%04x type %d cnt %d data@%d+%d > len %d (OOB)' % (name, tag, typ, cnt, val, sz, N))
            if tag in (0x8769, 0x8825, 0xA005): ifd({0x8769:'ExifIFD',0x8825:'GPS',0xA005:'Interop'}[tag], val, depth + 1)
        if end + 4 <= N:
            nxt = u32(end)
            if nxt: ifd(name + '.next', nxt, depth + 1)
        issues.append('ok ' + info)
    ifd('IFD0', u32(4))
    return issues

for name, b in iter_files(sys.argv[1]):
    for kind, t, L in exif_blocks(b):
        res = check(t)
        bad = [r for r in res if 'OOB' in r or 'bad' in r]
        print(name, kind, 'tifflen=%d' % len(t), 'BAD' if bad else 'ok', '; '.join(bad or [r for r in res][:4]))
