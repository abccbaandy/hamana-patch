"""反組譯 PE 指定 VA 附近的指令。用法: disasm.py <exe> <VA hex> [before_bytes] [count]"""
import sys, pefile, capstone
pe = pefile.PE(sys.argv[1], fast_load=True)
va = int(sys.argv[2], 16); back = int(sys.argv[3], 0) if len(sys.argv) > 3 else 0x40
cnt = int(sys.argv[4], 0) if len(sys.argv) > 4 else 40
base = pe.OPTIONAL_HEADER.ImageBase
rva = va - base - back
data = pe.get_memory_mapped_image()[rva:rva + back + 0x200]
md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
for i, ins in enumerate(md.disasm(data, base + rva)):
    if i >= cnt: break
    mark = '>>' if ins.address == va else '  '
    print(f"{mark}{ins.address:08x}: {ins.bytes.hex():<16} {ins.mnemonic} {ins.op_str}")
