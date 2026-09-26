-- SPDX-FileCopyrightText: 2026 Daniel J. Mazure
-- SPDX-License-Identifier: MIT
--
-- rr_rea_cdc — clock-domain crossing helpers for the REA IP.
--
-- Two reusable primitives:
--
--   rr_rea_sync_word    — two-flop synchronizer for a multi-bit
--                         "slow-changing" signal (config that's
--                         latched on arm, status flags). The user
--                         is responsible for keeping the source
--                         stable for ≥2 dest-clock periods around
--                         any sample point — typical for static
--                         config registers.
--
--   rr_rea_pulse_xfer   — toggle-pulse-coupled CDC. Source side
--                         flips a bit on each pulse; dest side
--                         two-flop-syncs the toggle and edge-
--                         detects to produce a single-cycle pulse.
--                         Survives arbitrary clock-ratio sampling.
--
-- Both pinned by REA-REQ-020 / 021.

library ieee;
    use ieee.std_logic_1164.all;
    use ieee.numeric_std.all;

-- ── Two-flop word synchronizer ────────────────────────────────────────

library ieee;
    use ieee.std_logic_1164.all;

entity rr_rea_sync_word is
    generic (
        G_WIDTH : positive := 32
    );
    port (
        dst_clk_i : in  std_logic;
        din_i     : in  std_logic_vector(G_WIDTH - 1 downto 0);
        dout_o    : out std_logic_vector(G_WIDTH - 1 downto 0)
    );
end entity;

architecture rtl of rr_rea_sync_word is
    signal s1 : std_logic_vector(G_WIDTH - 1 downto 0) := (others => '0');
    signal s2 : std_logic_vector(G_WIDTH - 1 downto 0) := (others => '0');
    -- How each vendor tool recognises this synchronizer, from its own
    -- reports (REA-P3.8, 2026-09-26, util_field: G_SAMPLE_W 80, QUAL 1):
    --
    --   Vivado 2024.1 (xc7z020) honours ASYNC_REG. All 1208 s1 and 1208 s2
    --   flops carry it, and report_cdc rates every crossing Safe: CDC-3
    --   "1-bit synchronized with ASYNC_REG property", CDC-6 "Multi-bit
    --   synchronized with ASYNC_REG property". ASYNC_REG keeps the pair
    --   together and out of SRLs and retiming. It does NOT exempt the
    --   source path from timing: constraints/rr_rea_scoped.xdc bounds it.
    --
    --   Quartus Pro 25.3.1 (Agilex 5) ignores ASYNC_REG ("Invalid
    --   assignment name" in syn.ae.rpt). Its synchronizer identification
    --   is Auto: report_metastability lists every u_cdc_* chain (1207)
    --   when both clocks are constrained, which constraints/rr_rea_scoped.sdc
    --   ensures, and none while the JTAG clock is unconstrained.
    --
    --   Libero 2025.2 (Synplify, MPFS095T) does not read ASYNC_REG. Its CDC
    --   report found every chain, but rated 17 of 27 "Divergence detected
    --   in the crossover path", and it forwards only safe chains to
    --   placement as synchronizers (designer cdc_synchronizer.csv). The
    --   divergence is the source register also fanning out in its own
    --   domain, e.g. into the register read-back. That is safe here: each
    --   source is a flop (REA-REQ-961) and, per the same report, no source
    --   feeds two synchronizers, so nothing reconverges downstream.
    --   syn_safe_cdc on s1 records that review: all 27 then report
    --   SAFE_CDC YES and all 27 reach placement as synchronizers.
    --
    -- Tools that do not know an attribute ignore it.
    attribute ASYNC_REG : string;
    attribute ASYNC_REG of s1 : signal is "TRUE";
    attribute ASYNC_REG of s2 : signal is "TRUE";
    attribute syn_safe_cdc : boolean;
    attribute syn_safe_cdc of s1 : signal is true;
begin
    process (dst_clk_i)
    begin
        if rising_edge(dst_clk_i) then
            s1 <= din_i;
            s2 <= s1;
        end if;
    end process;
    dout_o <= s2;
end architecture;

-- ── Toggle-pulse cross-domain transfer ────────────────────────────────

library ieee;
    use ieee.std_logic_1164.all;

entity rr_rea_pulse_xfer is
    port (
        src_toggle_i : in  std_logic;     -- toggle level on src_clk
                                        -- (caller flips it on each event)
        dst_clk_i    : in  std_logic;
        dst_rst_i    : in  std_logic;
        dst_pulse_o  : out std_logic      -- 1-cycle pulse on dst_clk_i
                                        -- per source-side toggle edge
    );
end entity;

architecture rtl of rr_rea_pulse_xfer is
    signal s1, s2, s3 : std_logic := '0';
    -- Same attributes, same measured recognition as rr_rea_sync_word.
    attribute ASYNC_REG : string;
    attribute ASYNC_REG of s1 : signal is "TRUE";
    attribute ASYNC_REG of s2 : signal is "TRUE";
    attribute syn_safe_cdc : boolean;
    attribute syn_safe_cdc of s1 : signal is true;
begin
    -- Destination: two-flop sync the toggle level, then one extra
    -- register for edge detect. Pulse out for one dst_clk_i per
    -- transition of the source toggle.
    process (dst_clk_i, dst_rst_i)
    begin
        if dst_rst_i = '1' then
            s1 <= '0'; s2 <= '0'; s3 <= '0';
        elsif rising_edge(dst_clk_i) then
            s1 <= src_toggle_i;
            s2 <= s1;
            s3 <= s2;
        end if;
    end process;

    dst_pulse_o <= s2 xor s3;
end architecture;

-- ── Async-assert / sync-deassert reset synchronizer (RTL-P3.1115) ──────────
-- A raw async reset released relative to a clock creates a recovery/removal
-- (metastability) hazard on the release edge — STA flags it (Quartus recovery
-- slack; not the combinational-glitch fallacy). This primitive asserts the reset
-- ASYNCHRONOUSLY (immediate, so in-flight state is safe) but deasserts it
-- SYNCHRONOUSLY, held for G_STAGES clk_i edges so the release meets recovery/
-- removal in this clock domain. Feed the raw reset in, drive domain registers
-- from srst_o.

library ieee;
    use ieee.std_logic_1164.all;

entity rr_rea_rst_sync is
    generic (
        G_STAGES : positive := 2   -- release-path settling flops
    );
    port (
        clk_i  : in  std_logic;
        arst_i : in  std_logic;    -- raw async reset in
        srst_o : out std_logic     -- async-assert, sync-deassert reset out
    );
end entity;

architecture rtl of rr_rea_rst_sync is
    signal sync_r : std_logic_vector(G_STAGES - 1 downto 0) := (others => '1');
    attribute ASYNC_REG : string;
    attribute ASYNC_REG of sync_r : signal is "TRUE";
begin
    process (clk_i, arst_i)
    begin
        if arst_i = '1' then
            sync_r <= (others => '1');                        -- async assert
        elsif rising_edge(clk_i) then
            sync_r <= sync_r(G_STAGES - 2 downto 0) & '0';    -- sync deassert
        end if;
    end process;
    srst_o <= sync_r(G_STAGES - 1);
end architecture;
