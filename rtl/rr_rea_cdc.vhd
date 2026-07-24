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
    -- Vendor-specific synthesis attributes. Vivado/Quartus both honor
    -- the canonical "ASYNC_REG" attribute on the destination flops to
    -- group them into the same slice and disable timing analysis on
    -- the source path. Harmless to other tools.
    attribute ASYNC_REG : string;
    attribute ASYNC_REG of s1 : signal is "TRUE";
    attribute ASYNC_REG of s2 : signal is "TRUE";
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
    attribute ASYNC_REG : string;
    attribute ASYNC_REG of s1 : signal is "TRUE";
    attribute ASYNC_REG of s2 : signal is "TRUE";
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
