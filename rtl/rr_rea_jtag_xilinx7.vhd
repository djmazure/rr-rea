-- SPDX-FileCopyrightText: 2026 Daniel J. Mazure
-- SPDX-License-Identifier: MIT
--
-- rr_rea_xilinx7 — Xilinx 7-series BSCANE2 wrapper for rr_rea_top.
--
-- Instantiates UNISIM.BSCANE2 with the given JTAG_CHAIN parameter
-- (default 1 = USER1) and connects its TAP signals straight to
-- rr_rea_top. This is the ONLY block in the IP that depends on a
-- vendor primitive — its sim companion is rr_rea_xilinx7_sim.vhd
-- (behavioral mock; same port signature).

library ieee;
    use ieee.std_logic_1164.all;

library unisim;
    use unisim.vcomponents.BSCANE2;

entity rr_rea_xilinx7 is
    generic (
        G_SAMPLE_W    : positive := 12;
        G_DEPTH       : positive := 4096;
        G_TIMESTAMP_W : natural  := 32;
        G_NUM_CHAN    : positive := 1;
        G_NUM_SOURCE  : positive := 1;  -- RTL-P2.837 write-side source bits
        G_CTRL_CHAIN  : integer  := 1   -- BSCANE2 USER1
    );
    port (
        sample_clk_i  : in  std_logic;
        sample_rst_i  : in  std_logic;
        probe_i    : in  std_logic_vector(G_SAMPLE_W - 1 downto 0);
        -- RTL-P3.266: optional external board-pin trigger. Route a package
        -- pin here in your top + XDC (scope trigger-out, another FPGA's
        -- trigger_o, a button); enable via TRIG_MODE ext_en[3]. Defaults
        -- '0' so designs that don't use it are unchanged.
        ext_trigger_i : in std_logic := '0';
        -- RTL-P2.837: write-side source bit(s) — JTAG-writable control lines
        -- into the design (sample_clk_i domain, crossed via rr_rea_sync_word).
        -- Wire so bit=0 holds the gated DUT signal safe; the host raises it
        -- over JTAG to release (e.g. a BIST arm gate). Reset default = 0.
        -- Leave `open` if unused. Needs `set_clock_groups -asynchronous`
        -- between the JTAG (tck_i) and sample clocks — see SPEC.md.
        source_o  : out std_logic_vector(G_NUM_SOURCE - 1 downto 0);
        trigger_o : out std_logic
    );
end entity;

architecture rtl of rr_rea_xilinx7 is
    signal tck_i     : std_logic;
    signal tdi_i     : std_logic;
    signal tdo_o     : std_logic;
    signal capture_i : std_logic;
    signal shift_en_i: std_logic;
    signal update_i  : std_logic;
    signal sel_i     : std_logic;
    -- Power-on tied-low reset for the JTAG domain — BSCANE2 does not
    -- expose a reset, so we rely on the iface FSM's natural init via
    -- `arst_i='0'` in normal operation.
    signal arst_i    : std_logic := '0';

    -- ── Vivado optimizer guard ───────────────────────────────────
    -- Without these, Vivado prunes the whole REA hierarchy: from the
    -- design's perspective rr_rea_xilinx7's TDO output goes into the
    -- BSCANE2 hard macro, and Vivado's constant-folder treats the
    -- BSCAN's CAPTURE/SHIFT/UPDATE/SEL outputs as static, so it
    -- "proves" the trigger path is unreachable. KEEP_HIERARCHY +
    -- DONT_TOUCH on the BSCANE2 instance + the rr_rea_top instance
    -- pin every block in place.
    attribute DONT_TOUCH     : string;
    attribute KEEP_HIERARCHY : string;
    attribute DONT_TOUCH     of u_bscane2 : label is "TRUE";
    attribute KEEP_HIERARCHY of u_top     : label is "TRUE";
    attribute DONT_TOUCH     of u_top     : label is "TRUE";
begin

    u_bscane2 : BSCANE2
        generic map (
            JTAG_CHAIN => G_CTRL_CHAIN
        )
        port map (
            CAPTURE => capture_i,
            DRCK    => open,
            RESET   => open,
            RUNTEST => open,
            SEL     => sel_i,
            SHIFT   => shift_en_i,
            TCK     => tck_i,
            TDI     => tdi_i,
            TMS     => open,
            UPDATE  => update_i,
            TDO     => tdo_o
        );

    u_top : entity work.rr_rea_top
        generic map (
            G_SAMPLE_W    => G_SAMPLE_W,
            G_DEPTH       => G_DEPTH,
            G_TIMESTAMP_W => G_TIMESTAMP_W,
            G_NUM_CHAN    => G_NUM_CHAN,
            G_NUM_SOURCE  => G_NUM_SOURCE
        )
        port map (
            sample_clk_i  => sample_clk_i,
            sample_rst_i  => sample_rst_i,
            probe_i    => probe_i,
            ext_trigger_i => ext_trigger_i,
            source_o  => source_o,
            trigger_o => trigger_o,
            arst_i        => arst_i,
            tck_i         => tck_i,
            tdi_i         => tdi_i,
            tdo_o         => tdo_o,
            capture_i     => capture_i,
            shift_en_i    => shift_en_i,
            update_i      => update_i,
            sel_i         => sel_i
        );

end architecture;
