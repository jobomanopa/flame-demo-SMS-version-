#!/usr/bin/env python3
"""Build Flame_Demo.sms from the NES ROM + flame_sms.asm."""
import subprocess, sys

NES = sys.argv[1] if len(sys.argv) > 1 else "/mnt/user-data/uploads/Flame_Demo__PD_.nes"
OUT = sys.argv[2] if len(sys.argv) > 2 else "Flame_Demo.sms"
Z80ASM = "/tmp/z80asm/usr/bin/z80asm"

TILES, FRAMES, HEADER = 0x0400, 0x1FF0, 0x7FF0

nes = open(NES, "rb").read()
assert nes[:4] == b"NES\x1a" and nes[4] == 2 and nes[5] == 1
mapper = (nes[6] >> 4) | (nes[7] & 0xF0)
assert mapper == 0
prg = nes[16:16 + 0x8000]
chr_ = nes[16 + 0x8000:16 + 0x8000 + 0x2000]

# --- animation data: 32 frames of 32x30 tile indices at $8097 ------------
data = prg[0x97:0x97 + 32 * 960]
frames = []
for f in range(32):
    fr = data[f * 960:(f + 1) * 960]
    assert not any(fr[:6 * 32]), "top rows must be blank to crop them"
    frames.append(fr[6 * 32:])            # rows 6..29 -> 24 rows
anim = b"".join(frames)
assert len(anim) == 0x6000 and FRAMES + len(anim) == HEADER

# --- tiles: BG pattern table $0000 (256 tiles) -> (plane0,plane1) per row --
tiles = bytearray()
for t in range(256):
    for r in range(8):
        tiles += bytes([chr_[t * 16 + r], chr_[t * 16 + 8 + r]])
assert len(tiles) == 4096

# --- code ------------------------------------------------------------------
subprocess.check_call([Z80ASM, "-o", "/tmp/code.bin", "flame_sms.asm"])
code = open("/tmp/code.bin", "rb").read()
assert len(code) < TILES, len(code)

rom = bytearray(0x8000)
rom[:len(code)] = code
rom[TILES:TILES + len(tiles)] = tiles
rom[FRAMES:FRAMES + len(anim)] = anim

# --- SMS header ------------------------------------------------------------
checksum = sum(rom[:HEADER]) & 0xFFFF
rom[HEADER:HEADER + 8] = b"TMR SEGA"
rom[HEADER + 8:HEADER + 10] = b"\x00\x00"
rom[HEADER + 10:HEADER + 12] = checksum.to_bytes(2, "little")
rom[HEADER + 12:HEADER + 15] = b"\x00\x00\x00"   # product code / version
rom[HEADER + 15] = 0x4C                          # region 4 = SMS export, size C = 32 KB
open(OUT, "wb").write(rom)
print(f"{OUT}: {len(rom)} bytes, code {len(code)} bytes, checksum {checksum:04X}")
