; ---------------------------------------------------------------------------
; Flame Demo (PD) - Sega Master System port
;
; Original: NES, mapper 0.  The 6502 main loop is idle; the NMI handler streams
; 96 tile indices (3 rows) per frame into the hidden nametable and flips
; nametables every 10 frames.  32 pre-rendered 32x30 flame frames loop forever.
; The flame only occupies NES rows 6-29 (rows 0-5 are black in every frame),
; i.e. exactly the 24 rows that fit on a 192-line SMS display.
;
; This port keeps the same structure:
;   NES NMI            -> SMS VBlank IRQ (IM 1, vector $0038)
;   NES nametable flip -> VDP register 2 (nametable base $3000 <-> $3800)
;   NES 2bpp CHR       -> expanded to 4bpp tiles (planes 2/3 = 0) at boot
;   NES 3-color palette $06/$17/$28 on $0D -> SMS CRAM $02/$07/$0B on $00
;
; ROM map (32 KB, no mapper):
;   $0000  vectors + code
;   $0400  tile pixels, 256 tiles x 8 rows x (plane0,plane1) = 4096 bytes
;   $1FF0  animation data, 32 frames x 768 bytes (24 rows x 32 tiles)
;   $7FF0  SMS header ("TMR SEGA")
; Assembled with z80asm 1.8; build.py glues the data blocks in.
; ---------------------------------------------------------------------------

VDP_DATA:    equ 0xBE
VDP_CTRL:    equ 0xBF

TILES:       equ 0x0400
FRAMES:      equ 0x1FF0
FRAMES_END:  equ 0x7FF0          ; FRAMES + 32*768

v_tick:      equ 0xC000          ; 0..9 : VBlank counter within one picture
v_src:       equ 0xC001          ; word : read pointer into animation data
v_dst:       equ 0xC003          ; word : VRAM write pointer in the back nametable
v_base:      equ 0xC005          ; word : base of the back nametable ($3000/$3800)

            org 0x0000
reset:      di
            im 1
            ld sp,0xDFF0
            jp main

            defs 0x38-$
irq:        jp vblank

            defs 0x66-$
nmi:        retn                ; PAUSE button: ignored

; ---------------------------------------------------------------------------
main:
            in a,(VDP_CTRL)     ; reset VDP address latch
            ld hl,vdp_regs
            ld bc,0x16BF        ; 22 bytes -> port $BF
            otir

            ; clear all 16 KB of VRAM (display is off)
            xor a
            out (VDP_CTRL),a
            ld a,0x40
            out (VDP_CTRL),a
            ld bc,0x4000
clr:        xor a
            out (VDP_DATA),a
            dec bc
            ld a,b
            or c
            jr nz,clr

            ; CRAM: 4 colors, rest black (also makes the backdrop black)
            xor a
            out (VDP_CTRL),a
            ld a,0xC0
            out (VDP_CTRL),a
            ld hl,palette
            ld b,4
pal1:       ld a,(hl)
            out (VDP_DATA),a
            inc hl
            djnz pal1
            xor a
            ld b,28
pal2:       out (VDP_DATA),a
            djnz pal2

            ; tiles: (plane0,plane1) pairs -> 4 bytes per row
            xor a
            out (VDP_CTRL),a
            ld a,0x40
            out (VDP_CTRL),a
            ld hl,TILES
            ld bc,2048
tile:       ld a,(hl)
            out (VDP_DATA),a
            inc hl
            ld a,(hl)
            out (VDP_DATA),a
            inc hl
            xor a
            out (VDP_DATA),a
            out (VDP_DATA),a
            dec bc
            ld a,b
            or c
            jr nz,tile

            ; sprite attribute table at $3F00: Y=$D0 ends the list (no sprites)
            xor a
            out (VDP_CTRL),a
            ld a,0x7F
            out (VDP_CTRL),a
            ld a,0xD0
            out (VDP_DATA),a

            ; state: show nametable $3800 (blank), build the next at $3000
            xor a
            ld (v_tick),a
            ld hl,FRAMES
            ld (v_src),hl
            ld hl,0x3000
            ld (v_dst),hl
            ld (v_base),hl

            in a,(VDP_CTRL)     ; drop any stale frame-IRQ flag
            ld a,0xE0           ; R1: display on, VBlank IRQ on
            out (VDP_CTRL),a
            ld a,0x81
            out (VDP_CTRL),a
            ei
idle:       halt
            jr idle

; ---------------------------------------------------------------------------
; VBlank handler: ticks 0-7 copy one 3-row strip (96 tiles) into the back
; nametable; tick 7 also flips it onto the screen; ticks 8-9 are idle, so
; each picture is shown for exactly 10 frames, like the NES original.
vblank:
            push af
            push bc
            push de
            push hl
            in a,(VDP_CTRL)     ; acknowledge frame IRQ
            ld a,(v_tick)
            cp 8
            jr nc,vb_next

            ld hl,(v_dst)       ; VRAM write address
            ld a,l
            out (VDP_CTRL),a
            ld a,h
            or 0x40
            out (VDP_CTRL),a

            ld hl,(v_src)
            ld b,96
vb_copy:    ld a,(hl)           ; tile index
            out (VDP_DATA),a
            inc hl
            xor a               ; attributes: palette 0, no flip, tile < 256
            out (VDP_DATA),a
            djnz vb_copy

            ld a,h              ; end of animation data? -> loop
            cp 0x7F
            jr nz,vb_nowrap
            ld a,l
            cp 0xF0
            jr nz,vb_nowrap
            ld hl,FRAMES
vb_nowrap:  ld (v_src),hl

            ld hl,(v_dst)       ; next strip = 3 rows * 64 bytes further on
            ld de,192
            add hl,de
            ld (v_dst),hl

            ld a,(v_tick)
            cp 7
            jr nz,vb_next

            ; picture complete: point R2 at it, then build the next one in
            ; the other nametable
            ld a,(v_base+1)
            rrca
            rrca
            and 0x0E
            or 0xF1
            out (VDP_CTRL),a
            ld a,0x82
            out (VDP_CTRL),a
            ld a,(v_base+1)
            xor 0x08            ; $30 <-> $38
            ld (v_base+1),a
            ld h,a
            ld l,0
            ld (v_dst),hl

vb_next:    ld a,(v_tick)
            inc a
            cp 10
            jr nz,vb_store
            xor a
vb_store:   ld (v_tick),a
            pop hl
            pop de
            pop bc
            pop af
            ei
            reti

; ---------------------------------------------------------------------------
; VDP registers (value, $80|reg)
vdp_regs:   defb 0x06,0x80      ; R0: mode 4, no left-column mask, no line IRQ
            defb 0x80,0x81      ; R1: display off until init is done
            defb 0xFF,0x82      ; R2: nametable at $3800
            defb 0xFF,0x83
            defb 0xFF,0x84
            defb 0xFF,0x85      ; R5: sprite table at $3F00
            defb 0xFF,0x86
            defb 0x00,0x87      ; R7: backdrop = CRAM[16] (black)
            defb 0x00,0x88      ; R8: no X scroll
            defb 0x00,0x89      ; R9: no Y scroll
            defb 0xFF,0x8A      ; R10: line counter off

; NES $0D,$06,$17,$28  ->  SMS 00BBGGRR
palette:    defb 0x00,0x02,0x07,0x0B
code_end:
