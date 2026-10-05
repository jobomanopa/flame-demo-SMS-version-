#!/usr/bin/env python3
"""Run the original NES code (py65) and the SMS port (Z80 emulator + VDP model)
and compare the pictures that each one displays."""
import sys
import numpy as np
import z80
from py65.devices.mpu6502 import MPU

NES = "/mnt/user-data/uploads/Flame_Demo__PD_.nes"
SMS = "Flame_Demo.sms"
N_PICTURES = 70          # > 2 full loops of the 32-picture animation

# ---------------------------------------------------------------- NES side
nes = open(NES, "rb").read()
prg = nes[16:16 + 0x8000]


class PPU:
    def __init__(self):
        self.vram = bytearray(0x4000)
        self.latch = False
        self.addr = 0
        self.ctrl = 0
        self.writes = 0

    def nt_index(self, a):                       # vertical mirroring
        return (a >> 10) & 1

    def read(self, a):
        return 0x80 if a == 0x2002 else 0        # vblank flag always set

    def write(self, a, v):
        if a == 0x2000:
            self.ctrl = v
        elif a == 0x2006:
            if not self.latch:
                self.addr = (v & 0x3F) << 8
            else:
                self.addr |= v
            self.latch = not self.latch
        elif a == 0x2007:
            ad = self.addr & 0x3FFF
            if 0x2000 <= ad < 0x3000:
                nt = self.nt_index(ad)
                self.vram[0x2000 + nt * 0x400 + (ad & 0x3FF)] = v
            self.addr = (self.addr + (32 if self.ctrl & 4 else 1)) & 0x3FFF
        elif a == 0x2005:
            pass


def run_nes():
    ppu = PPU()
    m = MPU()
    for i, b in enumerate(prg):
        m.memory[0x8000 + i] = b
    mem = m.memory
    from py65.memory import ObservableMemory
    om = ObservableMemory(subject=mem)
    om.subscribe_to_read(range(0x2000, 0x2008), lambda a: ppu.read(a))
    om.subscribe_to_write(range(0x2000, 0x2008), lambda a, v: ppu.write(a, v))
    m.memory = om
    m.pc = prg[0x7FFC] | prg[0x7FFD] << 8
    # run RESET until it parks in its idle JMP loop
    for _ in range(2_000_000):
        if m.memory[m.pc] == 0x4C and m.pc == 0x8094:
            break
        m.step()
    else:
        raise SystemExit("NES reset never reached idle loop")
    pics = []
    last_shown = ppu.ctrl & 1
    nmi = prg[0x7FFA] | prg[0x7FFB] << 8
    for frame in range(N_PICTURES * 10 + 20):
        # hardware NMI entry
        m.stPushWord(m.pc)
        m.stPush(m.p)
        m.pc = nmi
        for _ in range(100000):
            m.step()
            if m.pc == 0x8094:
                break
        shown = ppu.ctrl & 1
        if shown != last_shown:                 # nametable flip -> new picture
            nt = bytes(ppu.vram[0x2000 + shown * 0x400:0x2000 + shown * 0x400 + 960])
            pics.append((frame, nt))
            last_shown = shown
    return pics


# ---------------------------------------------------------------- SMS side
class VDP:
    def __init__(self):
        self.vram = bytearray(0x4000)
        self.cram = bytearray(32)
        self.regs = [0] * 16
        self.latch = None
        self.addr = 0
        self.code = 0
        self.data_writes_in_ctrl_halfway = 0

    def out_ctrl(self, v):
        if self.latch is None:
            self.latch = v
            self.addr = (self.addr & 0x3F00) | v
        else:
            self.addr = (self.addr & 0x00FF) | ((v & 0x3F) << 8)
            self.code = v >> 6
            if self.code == 2:
                self.regs[v & 0x0F] = self.latch
            self.latch = None

    def out_data(self, v):
        assert self.latch is None, "data write with half-written address"
        if self.code == 3:
            self.cram[self.addr & 0x1F] = v
        else:
            self.vram[self.addr & 0x3FFF] = v
        self.addr = (self.addr + 1) & 0x3FFF

    def status(self):
        self.latch = None
        return 0x80


def run_sms():
    rom = open(SMS, "rb").read()
    m = z80.Z80Machine()
    m.set_memory_block(0, rom)
    vdp = VDP()
    log = {"irq_cycles": [], "bad_ports": []}

    def out_cb(port, v):
        p = port & 0xFF
        if p == 0xBF:
            vdp.out_ctrl(v)
        elif p == 0xBE:
            vdp.out_data(v)
        else:
            log["bad_ports"].append((port, v))

    def in_cb(port):
        return vdp.status() if (port & 0xFF) == 0xBF else 0xFF

    m.set_output_callback(out_cb)
    m.set_input_callback(in_cb)
    m.pc = 0
    m.sp = 0

    FRAME = 262 * 228            # NTSC SMS: 59736 CPU cycles
    VBL = 192 * 228              # IRQ at start of line 192
    pics, last_r2, vbl_count = [], None, 0

    def run_ticks(n):
        m.ticks_to_stop = n
        m.run()

    guard = 0
    while len(pics) < N_PICTURES:
        run_ticks(VBL)
        irq_enabled = bool(vdp.regs[1] & 0x20)
        if irq_enabled and not m.int_disabled:
            vbl_count += 1
            ret_addr = idle_range[1]          # RETI returns to 'jr idle'
            m.set_breakpoint(ret_addr)
            m.ticks_to_stop = 10 ** 9
            m.on_handle_active_int()
            m.run()
            assert m.pc == ret_addr, hex(m.pc)
            done = 10 ** 9 - m.ticks_to_stop
            m.clear_breakpoint(ret_addr)
            log["irq_cycles"].append(done)
        r2 = vdp.regs[2]
        if irq_enabled and r2 != last_r2 and last_r2 is not None:
            base = (r2 & 0x0E) << 10
            tiles = bytearray()
            for row in range(24):
                for col in range(32):
                    o = base + row * 64 + col * 2
                    assert vdp.vram[o + 1] == 0, "attribute byte must be 0"
                    tiles.append(vdp.vram[o])
            pics.append((vbl_count, bytes(tiles)))
        if irq_enabled:
            last_r2 = r2
        run_ticks(FRAME - VBL)
        guard += 1
        if guard > N_PICTURES * 12 + 100:
            raise SystemExit("not enough pictures produced")
    return rom, vdp, pics, log


# idle loop addresses are filled in after we know the label addresses
def find_idle(rom):
    # 'halt; jr -3' (76 18 FD) is the idle loop
    i = bytes(rom).find(bytes([0x76, 0x18, 0xFD]))
    assert i > 0
    return range(i, i + 3)


if __name__ == "__main__":
    rom0 = open(SMS, "rb").read()
    idle_range = find_idle(rom0)
    nes_pics = run_nes()
    rom, vdp, sms_pics, log = run_sms()

    print("NES pictures captured:", len(nes_pics),
          " SMS pictures captured:", len(sms_pics))
    # timing: frames between flips
    nf = [b[0] - a[0] for a, b in zip(nes_pics, nes_pics[1:])]
    sf = [b[0] - a[0] for a, b in zip(sms_pics, sms_pics[1:])]
    print("NES flip interval (NMIs):", sorted(set(nf)),
          " SMS flip interval (VBlanks):", sorted(set(sf)))

    # content: SMS rows 0..23 == NES rows 6..29, for every picture
    n = min(len(nes_pics), len(sms_pics))
    bad = 0
    for i in range(n):
        want = nes_pics[i][1][6 * 32:]
        if sms_pics[i][1] != want:
            bad += 1
            print("picture", i, "differs")
    print("pictures compared:", n, " mismatches:", bad)
    # also: NES top 6 rows really are blank in every picture
    print("NES rows 0-5 blank in all pictures:",
          all(not any(p[1][:192]) for p in nes_pics))

    # tile pixels in VRAM == NES CHR pixels
    chr_ = nes[16 + 0x8000:16 + 0x8000 + 0x2000]
    ok = True
    for t in range(256):
        for r in range(8):
            p0, p1 = chr_[t * 16 + r], chr_[t * 16 + 8 + r]
            o = t * 32 + r * 4
            if tuple(vdp.vram[o:o + 4]) != (p0, p1, 0, 0):
                ok = False
    print("tile VRAM matches NES CHR:", ok)
    print("CRAM[0:4]:", list(vdp.cram[:4]), " CRAM rest zero:", not any(vdp.cram[4:]))
    print("regs:", [hex(x) for x in vdp.regs[:11]])
    print("SAT first Y byte:", hex(vdp.vram[0x3F00]))
    ic = log["irq_cycles"]
    print("IRQ handler cycles (incl. interrupt accept): max", max(ic),
          " (NTSC VBlank budget ~", (262 - 192) * 228, ")")
    print("stray port writes:", log["bad_ports"][:5])
    sys.exit(1 if bad or not ok or len(nes_pics) < 40 else 0)
