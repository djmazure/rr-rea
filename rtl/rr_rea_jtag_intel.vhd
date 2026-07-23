-- SPDX-FileCopyrightText: 2026 Daniel J. Mazure
-- SPDX-License-Identifier: MIT
--
-- rr_rea_intel — Intel/Altera sld_virtual_jtag wrapper for rr_rea_top.
--
-- Mirrors rr_rea_xilinx7 (BSCANE2 variant). Instantiates Quartus's
-- sld_virtual_jtag megafunction with the given G_CTRL_CHAIN as its
-- sld_instance_index, then wires its virtual TAP signals straight to
-- rr_rea_top. This is the only block in the IP that depends on an
-- Intel vendor primitive.
--
-- Reference: fpgacapZero v0.3.0 rtl/jtag_tap/jtag_tap_intel.v
-- (Apache-2.0, Leonardo Capossio / bard0). Same pattern, ported to
-- VHDL and adapted to rr_rea_top's port shape.
--
-- Chain selection note: unlike BSCANE2 (which is selected by the
-- USERn IR), each sld_virtual_jtag instance gets its own virtual IR
-- index automatically managed by the sld_node. The G_CTRL_CHAIN
-- generic maps to sld_instance_index (1-based), matching the
-- IR_TABLE_INTEL_VJTAG host-side convention in
-- sdk/cli/rea/transport.py.

library ieee;
    use ieee.std_logic_1164.all;

entity rr_rea_intel is
    generic (
        G_SAMPLE_W    : positive := 12;
        G_DEPTH       : positive := 4096;
        G_TIMESTAMP_W : natural  := 32;
        G_NUM_CHAN    : positive := 1;
        G_NUM_SOURCE  : positive := 1;  -- RTL-P2.837 write-side source bits
        G_CTRL_CHAIN  : positive := 1   -- sld_instance_index (1-based)
    );
    port (
        sample_clk_i  : in  std_logic;
        sample_rst_i  : in  std_logic;
        probe_i    : in  std_logic_vector(G_SAMPLE_W - 1 downto 0);
        -- RTL-P2.837: write-side source bit(s) — JTAG-writable control lines
        -- into the design (sample_clk_i domain, crossed via rr_rea_sync_word).
        -- Wire so bit=0 holds the gated DUT signal safe; the host raises it
        -- over JTAG (System Console) to release. Reset default = 0. Leave
        -- `open` if unused. Needs `set_clock_groups -asynchronous` between the
        -- sld_virtual_jtag tck_i and the sample clock — see SPEC.md.
        source_o  : out std_logic_vector(G_NUM_SOURCE - 1 downto 0);
        trigger_o : out std_logic
    );
end entity;

architecture rtl of rr_rea_intel is

    component sld_virtual_jtag is
        generic (
            sld_auto_instance_index : string  := "NO";
            sld_instance_index      : integer := 0;
            sld_ir_width            : integer := 1
        );
        port (
            tck_i               : out std_logic;
            tdi_i               : out std_logic;
            tdo_o               : in  std_logic;
            virtual_state_cdr : out std_logic;
            virtual_state_sdr : out std_logic;
            virtual_state_udr : out std_logic;
            ir_in             : out std_logic_vector(sld_ir_width - 1 downto 0);
            ir_out            : in  std_logic_vector(sld_ir_width - 1 downto 0)
        );
    end component;

    signal tck_i      : std_logic;
    signal tdi_i      : std_logic;
    signal tdo_o      : std_logic;
    signal capture_i  : std_logic;
    signal shift_en_i : std_logic;
    signal update_i   : std_logic;
    -- sld_virtual_jtag is selected automatically when its IR matches —
    -- there is no per-instance SEL output. Tie sel_i high so rr_rea_top's
    -- TAP-side logic always observes "selected" while the virtual JTAG
    -- node is active. The capture_i/shift/update_i strobes are themselves
    -- only asserted by the sld_node when this instance is targeted.
    signal sel_i      : std_logic := '1';
    -- sld_virtual_jtag exposes no reset; rely on iface FSM's natural init.
    signal arst_i     : std_logic := '0';

    signal ir_in_unused : std_logic_vector(0 downto 0);

begin

    u_vjtag : sld_virtual_jtag
        generic map (
            sld_auto_instance_index => "NO",
            sld_instance_index      => G_CTRL_CHAIN,
            sld_ir_width            => 1
        )
        port map (
            tck_i               => tck_i,
            tdi_i               => tdi_i,
            tdo_o               => tdo_o,
            virtual_state_cdr => capture_i,
            virtual_state_sdr => shift_en_i,
            virtual_state_udr => update_i,
            ir_in             => ir_in_unused,
            ir_out            => "0"
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
