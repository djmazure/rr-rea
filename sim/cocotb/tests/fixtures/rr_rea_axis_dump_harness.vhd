-- SPDX-FileCopyrightText: 2026 Daniel J. Mazure
-- SPDX-License-Identifier: MIT
--
-- rr_rea_axis_dump_harness — SIM-ONLY wiring for the REA-P2.5 AXI-Stream
-- window-dump test. A REAL rr_rea_top elaborated with G_REG_IFACE="external"
-- AND G_AXIS_WINDOW=true, its control door driven by rr_rea_axi4lite (the
-- host's register path) and its m_axis_* window-dump master brought straight
-- out for the testbench to collect.
--
-- This is deliberately NOT a burst-engine-in-a-vacuum test: the same physical
-- capture is read two ways from ONE core — walked cell-by-cell over the
-- AXI4-Lite register door (the DATA_BASE oracle rea_window_blob.py defines),
-- and streamed as one AXIS burst by the engine — and the two SHALL be
-- byte-identical (a green dump that never compared against DATA_BASE is not
-- done, docs/DUMP_PATH_STRATEGY.md).
--
-- The AXIS engine runs in the register-bus clock domain, which under the
-- "external" bridge is aclk_i — so the whole test lives on one clock for the
-- AXI4-Lite control door and the AXIS burst alike.

library ieee;
    use ieee.std_logic_1164.all;

entity rr_rea_axis_dump_harness is
    generic (
        G_SAMPLE_W    : positive := 40;
        G_DEPTH       : positive := 32;
        G_TIMESTAMP_W : natural  := 16;
        G_NUM_CHAN    : positive := 1;
        G_TRIG_CONDS  : positive := 1;
        G_NUM_SOURCE  : positive := 1
    );
    port (
        -- AXI4-Lite control door
        aclk_i    : in  std_logic;
        aresetn_i : in  std_logic;
        awaddr_i  : in  std_logic_vector(15 downto 0);
        awvalid_i : in  std_logic;
        awready_o : out std_logic;
        wdata_i   : in  std_logic_vector(31 downto 0);
        wstrb_i   : in  std_logic_vector(3 downto 0);
        wvalid_i  : in  std_logic;
        wready_o  : out std_logic;
        bresp_o   : out std_logic_vector(1 downto 0);
        bvalid_o  : out std_logic;
        bready_i  : in  std_logic;
        araddr_i  : in  std_logic_vector(15 downto 0);
        arvalid_i : in  std_logic;
        arready_o : out std_logic;
        rdata_o   : out std_logic_vector(31 downto 0);
        rresp_o   : out std_logic_vector(1 downto 0);
        rvalid_o  : out std_logic;
        rready_i  : in  std_logic;

        -- REA sample domain
        sample_clk_i : in  std_logic;
        sample_rst_i : in  std_logic;
        probe_i      : in  std_logic_vector(G_SAMPLE_W - 1 downto 0);
        trigger_o    : out std_logic;

        -- AXI-Stream window-dump master (aclk_i domain)
        m_axis_tdata_o  : out std_logic_vector(31 downto 0);
        m_axis_tvalid_o : out std_logic;
        m_axis_tready_i : in  std_logic;
        m_axis_tlast_o  : out std_logic
    );
end entity;

architecture tb of rr_rea_axis_dump_harness is

    signal reg_wr_en : std_logic;
    signal reg_rd_en : std_logic;
    signal reg_addr  : std_logic_vector(15 downto 0);
    signal reg_wdata : std_logic_vector(31 downto 0);
    signal reg_rdata : std_logic_vector(31 downto 0);
    signal reg_rst   : std_logic;
    signal source_x  : std_logic_vector(G_NUM_SOURCE - 1 downto 0);
    signal tdo_x     : std_logic;

begin

    reg_rst <= not aresetn_i;

    u_axi : entity work.rr_rea_axi4lite
        generic map (G_ADDR_W => 16)
        port map (
            aclk_i    => aclk_i,
            aresetn_i => aresetn_i,
            awaddr_i  => awaddr_i,
            awvalid_i => awvalid_i,
            awready_o => awready_o,
            wdata_i   => wdata_i,
            wstrb_i   => wstrb_i,
            wvalid_i  => wvalid_i,
            wready_o  => wready_o,
            bresp_o   => bresp_o,
            bvalid_o  => bvalid_o,
            bready_i  => bready_i,
            araddr_i  => araddr_i,
            arvalid_i => arvalid_i,
            arready_o => arready_o,
            rdata_o   => rdata_o,
            rresp_o   => rresp_o,
            rvalid_o  => rvalid_o,
            rready_i  => rready_i,
            reg_wr_en_o => reg_wr_en,
            reg_rd_en_o => reg_rd_en,
            reg_addr_o  => reg_addr,
            reg_wdata_o => reg_wdata,
            reg_rdata_i => reg_rdata
        );

    u_rea : entity work.rr_rea_top
        generic map (
            G_SAMPLE_W    => G_SAMPLE_W,
            G_DEPTH       => G_DEPTH,
            G_TIMESTAMP_W => G_TIMESTAMP_W,
            G_NUM_CHAN    => G_NUM_CHAN,
            G_TRIG_CONDS  => G_TRIG_CONDS,
            G_NUM_SOURCE  => G_NUM_SOURCE,
            G_REG_IFACE   => "external",
            G_AXIS_WINDOW => true
        )
        port map (
            sample_clk_i => sample_clk_i,
            sample_rst_i => sample_rst_i,
            probe_i      => probe_i,
            source_o     => source_x,
            trigger_o    => trigger_o,
            arst_i       => '0',
            tck_i        => '0',
            tdi_i        => '0',
            tdo_o        => tdo_x,
            capture_i    => '0',
            shift_en_i   => '0',
            update_i     => '0',
            sel_i        => '0',
            reg_clk_i    => aclk_i,
            reg_rst_i    => reg_rst,
            reg_wr_en_i  => reg_wr_en,
            reg_rd_en_i  => reg_rd_en,
            reg_addr_i   => reg_addr,
            reg_wdata_i  => reg_wdata,
            reg_rdata_o  => reg_rdata,
            m_axis_tdata_o  => m_axis_tdata_o,
            m_axis_tvalid_o => m_axis_tvalid_o,
            m_axis_tready_i => m_axis_tready_i,
            m_axis_tlast_o  => m_axis_tlast_o
        );

end architecture;
