-- SPDX-FileCopyrightText: 2026 Daniel J. Mazure
-- SPDX-License-Identifier: MIT
--
-- rr_rea_axi_harness — SIM-ONLY wiring of rr_rea_axi4lite onto a REAL
-- rr_rea_top elaborated with G_REG_IFACE = "external" (RTL-P3.931).
--
-- This is a test fixture, not shippable RTL, and it exists so the AXI4-Lite
-- tests are NOT bridge-in-a-vacuum tests. The property that matters most
-- (REA-REQ-904: a read must not lag by one register) is a property of the
-- bridge TOGETHER WITH the regbank's registered read path — a bridge tested
-- against a fake register file with combinational reads would pass while
-- being wrong on silicon.
--
-- reg_wr_en_probe_o is brought out purely so a monitor can count register-bus
-- write strobes (REA-REQ-907). It is an observation point, never a control.

library ieee;
    use ieee.std_logic_1164.all;

entity rr_rea_axi_harness is
    generic (
        G_SAMPLE_W    : positive := 8;
        G_DEPTH       : positive := 512;
        G_TIMESTAMP_W : natural  := 0;
        G_NUM_CHAN    : positive := 1;
        G_TRIG_CONDS  : positive := 1;
        G_NUM_SOURCE  : positive := 1
    );
    port (
        -- AXI4-Lite slave
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
        source_o     : out std_logic_vector(G_NUM_SOURCE - 1 downto 0);

        -- TAP stubs — driven by the test to prove they are INERT (REA-REQ-901)
        arst_i     : in  std_logic;
        tck_i      : in  std_logic;
        tdi_i      : in  std_logic;
        tdo_o      : out std_logic;
        capture_i  : in  std_logic;
        shift_en_i : in  std_logic;
        update_i   : in  std_logic;
        sel_i      : in  std_logic;

        -- Observation only
        reg_wr_en_probe_o : out std_logic
    );
end entity;

architecture tb of rr_rea_axi_harness is

    signal reg_wr_en : std_logic;
    signal reg_rd_en : std_logic;
    signal reg_addr  : std_logic_vector(15 downto 0);
    signal reg_wdata : std_logic_vector(31 downto 0);
    signal reg_rdata : std_logic_vector(31 downto 0);
    signal reg_rst   : std_logic;

begin

    reg_rst <= not aresetn_i;
    reg_wr_en_probe_o <= reg_wr_en;

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
            G_REG_IFACE   => "external"
        )
        port map (
            sample_clk_i => sample_clk_i,
            sample_rst_i => sample_rst_i,
            probe_i      => probe_i,
            source_o     => source_o,
            trigger_o    => trigger_o,
            arst_i       => arst_i,
            tck_i        => tck_i,
            tdi_i        => tdi_i,
            tdo_o        => tdo_o,
            capture_i    => capture_i,
            shift_en_i   => shift_en_i,
            update_i     => update_i,
            sel_i        => sel_i,
            reg_clk_i    => aclk_i,
            reg_rst_i    => reg_rst,
            reg_wr_en_i  => reg_wr_en,
            reg_rd_en_i  => reg_rd_en,
            reg_addr_i   => reg_addr,
            reg_wdata_i  => reg_wdata,
            reg_rdata_o  => reg_rdata
        );

end architecture;
