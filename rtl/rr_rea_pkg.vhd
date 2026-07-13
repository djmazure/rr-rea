-- SPDX-FileCopyrightText: 2026 Daniel J. Mazure
-- SPDX-License-Identifier: MIT
--
-- rr_rea_pkg — shared types and constants for the RouteRTL Embedded
-- Analyzer (REA) IP family. Pulled in by every block in the hierarchy.
--
-- The JTAG register addresses below are FROZEN — they form the SW-
-- interface contract with the fcapz host library (subset of the
-- fcapz_ela register map). Adding registers is fine; renumbering
-- existing ones breaks the host SW.

library ieee;
    use ieee.std_logic_1164.all;
    use ieee.numeric_std.all;

package rr_rea_pkg is

    -- rr-regbank-begin REGBANK_ADDRESSES
    constant C_REGBANK_ADDR_VERSION            : unsigned(15 downto 0) := x"0000";  -- RO
    constant C_REGBANK_ADDR_CTRL               : unsigned(15 downto 0) := x"0004";  -- WO
    constant C_REGBANK_ADDR_STATUS             : unsigned(15 downto 0) := x"0008";  -- RO
    constant C_REGBANK_ADDR_SAMPLE_W           : unsigned(15 downto 0) := x"000C";  -- RO
    constant C_REGBANK_ADDR_DEPTH              : unsigned(15 downto 0) := x"0010";  -- RO
    constant C_REGBANK_ADDR_PRETRIG            : unsigned(15 downto 0) := x"0014";  -- RW
    constant C_REGBANK_ADDR_POSTTRIG           : unsigned(15 downto 0) := x"0018";  -- RW
    constant C_REGBANK_ADDR_CAPTURE_LEN        : unsigned(15 downto 0) := x"001C";  -- RO
    constant C_REGBANK_ADDR_TRIG_MODE          : unsigned(15 downto 0) := x"0020";  -- RW
    constant C_REGBANK_ADDR_TRIG_VALUE         : unsigned(15 downto 0) := x"0024";  -- RW
    constant C_REGBANK_ADDR_TRIG_MASK          : unsigned(15 downto 0) := x"0028";  -- RW
    constant C_REGBANK_ADDR_TRIG_WORD_SEL      : unsigned(15 downto 0) := x"002C";  -- RW
    constant C_REGBANK_ADDR_COND_SEL           : unsigned(15 downto 0) := x"0030";  -- RW
    constant C_REGBANK_ADDR_COND_CFG           : unsigned(15 downto 0) := x"0034";  -- RW
    constant C_REGBANK_ADDR_COND_VAL           : unsigned(15 downto 0) := x"0038";  -- RW
    constant C_REGBANK_ADDR_SOURCE             : unsigned(15 downto 0) := x"003C";  -- RW
    constant C_REGBANK_ADDR_SEQ_BASE           : unsigned(15 downto 0) := x"0040";  -- RW
    constant C_REGBANK_ADDR_CHAN_SEL           : unsigned(15 downto 0) := x"00A0";  -- RW
    constant C_REGBANK_ADDR_NUM_CHAN           : unsigned(15 downto 0) := x"00A4";  -- RO
    constant C_REGBANK_ADDR_DECIM              : unsigned(15 downto 0) := x"00B0";  -- RW
    constant C_REGBANK_ADDR_TIMESTAMP_W        : unsigned(15 downto 0) := x"00C4";  -- RO
    constant C_REGBANK_ADDR_START_PTR          : unsigned(15 downto 0) := x"00C8";  -- RO
    constant C_REGBANK_ADDR_DATA_WORD_SEL      : unsigned(15 downto 0) := x"00CC";  -- RW
    constant C_REGBANK_ADDR_FEATURES           : unsigned(15 downto 0) := x"00D0";  -- RO
    constant C_REGBANK_ADDR_BUILD_ID           : unsigned(15 downto 0) := x"00D4";  -- RO
    constant C_REGBANK_ADDR_DATA_BASE          : unsigned(15 downto 0) := x"0100";  -- RO
    -- rr-regbank-end REGBANK_ADDRESSES

    -- ── Magic + version ──────────────────────────────────────────
    -- 'REA' (0x524541) in the upper 24 bits + minor version in low 8.
    -- RTL-P3.740: the minor MUST track the feature tier so the host can refuse
    -- (not silently degrade) a trigger the core can't honour. v0.4 = rich
    -- comparator op nibble (P3.644-646) + per-condition array (P3.647) + capture
    -- decimation + external-pin trigger + multiword TRIG_VALUE/MASK (P2.658b).
    -- v0.5 adds paged full-width capture readback (RTL-P1.91).
    -- v0.6 (RTL-P2.876) raises the sample-width ceiling 256 → 1024 and extends
    -- the per-condition field_lsb to 11 bits (lsb_hi in COND_CFG[30:28]) so a
    -- comparator field can sit above bit 255; advertised by FEATURES[17]
    -- (wide_cond). NB the on-silicon v0.5 magic 0x52454105 in closed tickets
    -- (RTL-P3.1198 identity, T2.119 handoff) is HISTORICAL fact — not rewritten.
    constant C_REA_VERSION : std_logic_vector(31 downto 0) := x"52454106";

    -- ── JTAG register map (host SW contract — DO NOT renumber) ───
    constant C_ADDR_VERSION     : unsigned(15 downto 0) := C_REGBANK_ADDR_VERSION;
    constant C_ADDR_CTRL        : unsigned(15 downto 0) := C_REGBANK_ADDR_CTRL;
    constant C_ADDR_STATUS      : unsigned(15 downto 0) := C_REGBANK_ADDR_STATUS;
    constant C_ADDR_SAMPLE_W    : unsigned(15 downto 0) := C_REGBANK_ADDR_SAMPLE_W;
    constant C_ADDR_DEPTH       : unsigned(15 downto 0) := C_REGBANK_ADDR_DEPTH;
    constant C_ADDR_PRETRIG     : unsigned(15 downto 0) := C_REGBANK_ADDR_PRETRIG;
    constant C_ADDR_POSTTRIG    : unsigned(15 downto 0) := C_REGBANK_ADDR_POSTTRIG;
    constant C_ADDR_CAPTURE_LEN : unsigned(15 downto 0) := C_REGBANK_ADDR_CAPTURE_LEN;
    constant C_ADDR_TRIG_MODE   : unsigned(15 downto 0) := C_REGBANK_ADDR_TRIG_MODE;
    constant C_ADDR_TRIG_VALUE  : unsigned(15 downto 0) := C_REGBANK_ADDR_TRIG_VALUE;
    constant C_ADDR_TRIG_MASK   : unsigned(15 downto 0) := C_REGBANK_ADDR_TRIG_MASK;
    -- RTL-P2.658(b): word-select for the multiword (banked) TRIG_VALUE /
    -- TRIG_MASK when G_SAMPLE_W > 32. Writes/reads to 0x24/0x28 target
    -- the 32-bit word indexed by this register. Resets to 0, so a legacy
    -- host (or any G_SAMPLE_W<=32 probe) that never touches it always
    -- hits word 0 — byte-identical to the pre-v0.4 single-word behaviour.
    constant C_ADDR_TRIG_WORD_SEL : unsigned(15 downto 0) := C_REGBANK_ADDR_TRIG_WORD_SEL;
    -- RTL-P3.647: per-condition comparator array (mixed-op AND triggers).
    -- Paged like TRIG_WORD_SEL: COND_SEL picks slot k; COND_CFG/COND_VAL
    -- write that slot's {valid,op,width,field_lsb} + 32-bit value. Enabled by
    -- TRIG_MODE bit[2]. Inert (and the legacy single-comparator path
    -- unchanged) when array_enable=0 / no slots valid.
    constant C_ADDR_COND_SEL    : unsigned(15 downto 0) := C_REGBANK_ADDR_COND_SEL;
    constant C_ADDR_COND_CFG    : unsigned(15 downto 0) := C_REGBANK_ADDR_COND_CFG;
    constant C_ADDR_COND_VAL    : unsigned(15 downto 0) := C_REGBANK_ADDR_COND_VAL;
    -- RTL-P2.837: write-side SOURCE register — ISSP-style JTAG-writable
    -- control bits driven INTO the design (the counterpart to the read-only
    -- probe path). Low G_NUM_SOURCE bits are exposed on rr_rea_top's
    -- `source_out` port, crossed jtag_clk → sample_clk by rr_rea_sync_word.
    -- Resets to 0 so every source bit powers up in its safe/inactive state —
    -- the gated DUT signal stays held until the host explicitly writes it
    -- (no auto-release on config load / arm). 0x3C sits below SEQ_BASE (0x40),
    -- so it never aliases the per-stage sequencer window.
    constant C_ADDR_SOURCE      : unsigned(15 downto 0) := C_REGBANK_ADDR_SOURCE;
    constant C_ADDR_CHAN_SEL    : unsigned(15 downto 0) := C_REGBANK_ADDR_CHAN_SEL;
    constant C_ADDR_NUM_CHAN    : unsigned(15 downto 0) := C_REGBANK_ADDR_NUM_CHAN;
    constant C_ADDR_DECIM       : unsigned(15 downto 0) := C_REGBANK_ADDR_DECIM;

    -- ── Sequencer registers (REA-REQ-607, v0.3) ──────────────────
    -- Per-stage block at ADDR_SEQ_BASE + N * SEQ_STRIDE:
    --   +0x00  cfg          (mode bits + count_target)
    --   +0x04  value_a
    --   +0x08  mask_a
    --   +0x0C  value_b      (reserved for v0.4 compound conditions)
    --   +0x10  mask_b       (reserved for v0.4 compound conditions)
    -- Layout matches fcapz_ela.v exactly so any future host SW
    -- reuse keeps the same wire format.
    --
    -- WIDTH CONTRACT (RTL-P3.691, sibling of RTL-P2.658b): the per-stage
    -- value_a/mask_a (and value_b/mask_b) RW slots are NOT YET implemented
    -- in rr_rea_regbank. The capture-FSM already carries them full-width
    -- (G_SAMPLE_W bits per stage), so WHEN these JTAG slots are added they
    -- MUST be banked exactly like TRIG_VALUE/TRIG_MASK — a 32-bit window
    -- into trig_words(G_SAMPLE_W) words, paged by TRIG_WORD_SEL (0x2C) or a
    -- SEQ-local equivalent. SEQ_STRIDE stays 20 bytes (one 32-bit window per
    -- field) so the address map and fcapz wire format are preserved; the
    -- extra words are reached by paging, never by widening the slot. Do NOT
    -- reintroduce a single-32-bit value_a/mask_a — that is the exact cap
    -- P2.658b removed from the legacy path.
    constant C_ADDR_SEQ_BASE    : unsigned(15 downto 0) := C_REGBANK_ADDR_SEQ_BASE;
    constant C_SEQ_STRIDE       : positive := 20;  -- bytes per stage
    constant C_ADDR_TIMESTAMP_W : unsigned(15 downto 0) := C_REGBANK_ADDR_TIMESTAMP_W;
    constant C_ADDR_START_PTR   : unsigned(15 downto 0) := C_REGBANK_ADDR_START_PTR;
    -- RTL-P1.91: independent bank selector for capture data. DATA_BASE keeps
    -- one address per capture cell; this selects which 32-bit word of that
    -- cell is visible. Reset 0 preserves the historical low-word window.
    constant C_ADDR_DATA_WORD_SEL : unsigned(15 downto 0) := C_REGBANK_ADDR_DATA_WORD_SEL;
    -- RTL-P3.1198: content/feature fingerprint identity registers. VERSION
    -- (0x00) is a hand-set magic — a diverged fork copies it verbatim, so it
    -- cannot identify what is actually on-chip. These two RO registers let a
    -- host detect a stale/incomplete build:
    --   FEATURES  — a bitmap DERIVED FROM the synth-time generics, so a build
    --               compiled with a different configuration (fewer comparator
    --               slots, no wide sample) genuinely reports a different value
    --               (catches CONFIGURATION drift; cannot be forged by copying).
    --   BUILD_ID  — a 32-bit source/content fingerprint fed by the G_BUILD_ID
    --               generic (default 0 = "not injected"). The routertl build
    --               flow injects a hash of the IP source tree here so a fork
    --               with identical generics but a diverged implementation
    --               reports a different id (catches SOURCE drift).
    constant C_ADDR_FEATURES    : unsigned(15 downto 0) := C_REGBANK_ADDR_FEATURES;
    constant C_ADDR_BUILD_ID    : unsigned(15 downto 0) := C_REGBANK_ADDR_BUILD_ID;
    constant C_ADDR_DATA_BASE   : unsigned(15 downto 0) := C_REGBANK_ADDR_DATA_BASE;

    -- ── FEATURES register (0xD0) field layout (RTL-P3.1198) ──────
    -- Packed generic-derived configuration fingerprint. Every field is a
    -- function of a synth-time generic — none is hand-set — so the value
    -- self-describes the build and a diverged copy cannot report canonical's
    -- value unless it was built with canonical's generics.
    --   [7:0]  TRIG_CONDS  = G_TRIG_CONDS   (comparator-array slots)
    --   [15:8] NUM_SOURCE  = G_NUM_SOURCE   (write-side source bits)
    --   [16]   WIDE_SAMPLE = '1' when G_SAMPLE_W > C_DATA_WORD_W (paged readback)
    --   [17]   WIDE_COND   = '1' when G_SAMPLE_W > C_COND_LSB8_REACH (RTL-P2.876):
    --                        this build decodes the 11-bit extended field_lsb, so
    --                        a comparator condition may address bits >= 256. The
    --                        host writes lsb_hi (COND_CFG[30:28]) only when set,
    --                        and REFUSES lsb>=256 when clear (no silent truncation).
    --   [31:18] reserved (0)
    constant C_FEAT_TRIG_CONDS_LSB : natural := 0;
    constant C_FEAT_NUM_SOURCE_LSB : natural := 8;
    constant C_FEAT_WIDE_SAMPLE_BIT : natural := 16;
    constant C_FEAT_WIDE_COND_BIT   : natural := 17;

    -- ── CTRL register bit assignments ────────────────────────────
    constant C_CTRL_BIT_ARM     : natural := 0;
    constant C_CTRL_BIT_RESET   : natural := 1;

    -- ── STATUS register bit assignments ──────────────────────────
    constant C_STATUS_BIT_ARMED      : natural := 0;
    constant C_STATUS_BIT_TRIGGERED  : natural := 1;
    constant C_STATUS_BIT_DONE       : natural := 2;
    constant C_STATUS_BIT_OVERFLOW   : natural := 3;

    -- ── TRIG_MODE values ─────────────────────────────────────────
    constant C_TRIG_MODE_VALUE_MATCH : std_logic_vector(31 downto 0) := x"00000001";

    -- bit[1] = enable multi-stage sequencer (v0.3, REA-REQ-601).
    -- When 0, the FSM uses the flat single-comparator path
    -- (TRIG_VALUE / TRIG_MASK at 0x24/0x28, REA-REQ-100..106).
    -- When 1, per-stage seq_value_k / seq_mask_k at ADDR_SEQ_BASE+
    -- drive the trigger; the final stage's match fires capture.
    constant C_TRIG_MODE_BIT_SEQ_EN : natural := 1;

    -- bit[2] = enable per-condition comparator array (RTL-P3.647). When 1, the
    -- FSM ANDs the per-condition {op,value,mask} slots (COND_SEL/CFG/VAL) into
    -- the trigger instead of the single-comparator path — this is how a
    -- mixed-op AND ('counter < 5 AND state == 1', two edges, …) is expressed.
    -- seq_en (bit[1]) takes precedence if both are set. 0 → legacy path.
    constant C_TRIG_MODE_BIT_ARRAY_EN : natural := 2;

    -- bit[3]  = enable external board-pin trigger (RTL-P3.266). When 1, the
    --           wrapper's `ext_trigger_in` package-pin input participates in
    --           the fire decision (the user routes a board pin → the ELA
    --           wrapper's ext_trigger_in in their HDL + XDC). When 0, the pin
    --           is ignored and behaviour is exactly the internal-only path.
    -- bit[8]  = external-trigger combine mode: 0 = OR with the internal
    --           trigger (fire on EITHER — cross-board "any of us trips"),
    --           1 = AND (fire only when the internal condition AND the pin are
    --           both asserted — scope-gated / armed-window captures).
    -- Distinct from the trig_xbar `trigger_in` CDC pulse (REA-REQ-400/401),
    -- which is an on-chip cross-core sync and always an independent OR.
    constant C_TRIG_MODE_BIT_EXT_EN  : natural := 3;
    constant C_TRIG_MODE_BIT_EXT_AND : natural := 8;

    -- ── Per-condition slot CFG word layout (COND_CFG, RTL-P3.647/P2.876) ──
    -- {valid[31], lsb_hi[30:28], op[27:24], field_width[23:16], field_lsb_lo[15:8],
    --  rsvd[7:0]}. field_lsb = (lsb_hi & lsb_lo) is an 11-bit offset (0..2047,
    -- RTL-P2.876) so a comparator field can sit anywhere in a probe up to
    -- C_MAX_SAMPLE_W. field_width stays 8 bits (a field is ≤255 bits wide; a
    -- wider full-width EQ uses the single-comparator path, RTL-P2.658b). lsb_hi
    -- occupies the previously-reserved [30:28] gap — a legacy host wrote 0
    -- there, so an <=256-bit-reach config decodes byte-identically (back-compat).
    -- rsvd[7:0] stays free (headroom for RTL-P2.881's comparator redesign — this
    -- encoding does not consume it).
    constant C_COND_VALID_BIT  : natural := 31;
    constant C_COND_LSB_HI_LSB : natural := 28;   -- 3 bits [30:28] → 11-bit lsb
    constant C_COND_OP_LSB     : natural := 24;   -- 4 bits, reuses C_TRIG_OP_*
    constant C_COND_WIDTH_LSB  : natural := 16;   -- 8 bits
    constant C_COND_LSB_LSB    : natural := 8;    -- 8 bits (low byte of field_lsb)

    -- bits[7:4] = comparator op for the single-comparator (legacy) path
    -- (RTL-P3.644/645/646). bit[0]=value_match stays set for back-compat;
    -- op 0 (EQ) == the historical masked-equality behaviour, so a host that
    -- writes the bare x"00000001" still gets EQ. The op applies to the masked
    -- field (probe_in and TRIG_MASK) vs (TRIG_VALUE and TRIG_MASK):
    --   EQ  ==   NE  /=   LT  <   GT  >  (unsigned, masked field)
    --   RISE/FALL — any masked bit transitioning 0->1 / 1->0 (needs prev sample)
    constant C_TRIG_OP_LSB   : natural := 4;
    constant C_TRIG_OP_WIDTH : natural := 4;
    constant C_TRIG_OP_EQ    : natural := 0;
    constant C_TRIG_OP_NE    : natural := 1;
    constant C_TRIG_OP_LT    : natural := 2;
    constant C_TRIG_OP_GT    : natural := 3;
    constant C_TRIG_OP_RISE  : natural := 4;
    constant C_TRIG_OP_FALL  : natural := 5;

    -- ── Trigger value/mask width support (RTL-P2.658) ────────────
    -- The trigger comparator covers the full G_SAMPLE_W; the JTAG
    -- datapath is 32-bit, so wide value/mask are banked into
    -- trig_words(G_SAMPLE_W) = ceil(W/32) words paged via TRIG_WORD_SEL.
    -- C_MAX_SAMPLE_W is the fail-fast elaboration ceiling. RTL-P2.876 raised
    -- it 256 → 1024 bits (32 words) for the field's 704-bit probe; the banked
    -- trigger value/mask storage sizes to trig_words(G_SAMPLE_W) for the
    -- INSTANTIATED width (linear), not to the ceiling. TRIG_WORD_SEL is 8-bit
    -- so it addresses up to 256 words (well beyond 32). Trigger configuration
    -- and capture readback each page every legal sample width through their
    -- independent selectors. NB the per-slice serial comparator storage still
    -- scales ~O(width^2) (RTL-P2.881, out of scope here); this encoding does
    -- not preclude that redesign — the ceiling and paging are orthogonal to it.
    constant C_TRIG_WORD_W  : positive := 32;
    constant C_MAX_SAMPLE_W : positive := 1024;

    -- RTL-P2.876: the 8-bit COND_CFG field_lsb_lo alone reaches bits 0..255.
    -- A probe wider than this needs the 3-bit lsb_hi extension to address a
    -- field above bit 255; FEATURES[17]=wide_cond is set exactly when
    -- G_SAMPLE_W exceeds this reach, telling the host lsb>=256 is honoured.
    constant C_COND_LSB8_REACH : positive := 256;

    -- Capture readback has its own selector and width contract. Keep it
    -- separate from trigger-value paging even though both use 32-bit JTAG
    -- words.
    constant C_DATA_WORD_W : positive := 32;

    -- Fixed trigger-comparator partition (REA-REQ-320/326).
    constant C_SLICE_W : positive := 8;

    type t_cmp_token is record
        eq : std_logic;
        gt : std_logic;
        lt : std_logic;
    end record;

    -- ── Helpers ──────────────────────────────────────────────────
    function clog2(n : natural) return natural;

    function max_nat(left_value, right_value : natural) return natural;

    function cmp_slice(
        probe_slice : std_logic_vector;
        value_slice : std_logic_vector
    ) return t_cmp_token;

    function cmp_combine(
        high_token : t_cmp_token;
        low_token  : t_cmp_token
    ) return t_cmp_token;

    -- Number of 32-bit words needed to hold a w-bit value: ceil(w/32).
    function trig_words(w : positive) return positive;

end package;

package body rr_rea_pkg is

    function clog2(n : natural) return natural is
        variable r : natural := 0;
        variable v : natural := 1;
    begin
        while v < n loop
            v := v * 2;
            r := r + 1;
        end loop;
        return r;
    end function;

    function max_nat(left_value, right_value : natural) return natural is
    begin
        if left_value > right_value then
            return left_value;
        end if;
        return right_value;
    end function;

    function cmp_slice(
        probe_slice : std_logic_vector;
        value_slice : std_logic_vector
    ) return t_cmp_token is
        variable result : t_cmp_token;
    begin
        if unsigned(probe_slice) = unsigned(value_slice) then
            result := (eq => '1', gt => '0', lt => '0');
        elsif unsigned(probe_slice) > unsigned(value_slice) then
            result := (eq => '0', gt => '1', lt => '0');
        else
            result := (eq => '0', gt => '0', lt => '1');
        end if;
        return result;
    end function;

    function cmp_combine(
        high_token : t_cmp_token;
        low_token  : t_cmp_token
    ) return t_cmp_token is
        variable result : t_cmp_token;
    begin
        result.eq := high_token.eq and low_token.eq;
        result.gt := high_token.gt or (high_token.eq and low_token.gt);
        result.lt := high_token.lt or (high_token.eq and low_token.lt);
        return result;
    end function;

    function trig_words(w : positive) return positive is
    begin
        return (w + C_TRIG_WORD_W - 1) / C_TRIG_WORD_W;
    end function;

end package body;
