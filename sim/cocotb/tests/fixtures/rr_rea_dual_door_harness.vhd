-- SPDX-FileCopyrightText: 2026 Daniel J. Mazure
-- SPDX-License-Identifier: MIT
--
-- rr_rea_dual_door_harness — SIM-ONLY fixture for the dump-path transport
-- contract (REA-P2.4, REA-REQ-909..913, docs/DUMP_PATH_STRATEGY.md).
--
-- TWO independent rr_rea_top cores, fed by the SAME sample clock, reset and
-- probe, each with exactly ONE register-bus master:
--
--   u_jtag  G_REG_IFACE = "jtag"      driven through the TAP  (transport `jtag`)
--   u_ext   G_REG_IFACE = "external"  driven by rr_rea_axi4lite (`axi_lite`)
--
-- The point is to read the SAME frozen register map through BOTH doors and
-- prove it is the protocol — VERSION / STATUS / DATA_WORD_SEL / DATA_BASE sit
-- at the identical offsets and yield identical values — and to build the
-- window blob (plane-major, ceil(SAMPLE_W/32) LE words, CAPTURE_LEN cells)
-- from each door and prove the two blobs are byte-identical.
--
-- This is NOT a dual-master core. Two cores, one master each. Two live
-- masters on ONE register bus stays forbidden (REA-REQ-901) until the
-- arbiter ticket (REA-P3.4). To prove the jtag core has no second door, its
-- reg_*_i slave ports are brought out so the test can drive them and show
-- they are inert (REA-REQ-902).

library ieee;
    use ieee.std_logic_1164.all;

entity rr_rea_dual_door_harness is
    generic (
        G_SAMPLE_W    : positive := 40;
        G_DEPTH       : positive := 32;
        G_TIMESTAMP_W : natural  := 16;
        G_NUM_CHAN    : positive := 1;
        G_TRIG_CONDS  : positive := 1;
        G_NUM_SOURCE  : positive := 1
    );
    port (
        -- ── Shared sample domain ─────────────────────────────────
        sample_clk_i : in  std_logic;
        sample_rst_i : in  std_logic;
        probe_i      : in  std_logic_vector(G_SAMPLE_W - 1 downto 0);
        trigger_jtag_o : out std_logic;
        trigger_ext_o  : out std_logic;

        -- ── Door 1: JTAG TAP of u_jtag ───────────────────────────
        arst_i     : in  std_logic;
        tck_i      : in  std_logic;
        tdi_i      : in  std_logic;
        tdo_o      : out std_logic;
        capture_i  : in  std_logic;
        shift_en_i : in  std_logic;
        update_i   : in  std_logic;
        sel_i      : in  std_logic;

        -- u_jtag's external slave ports — MUST be inert (REA-REQ-902)
        j_reg_clk_i   : in  std_logic;
        j_reg_wr_en_i : in  std_logic;
        j_reg_rd_en_i : in  std_logic;
        j_reg_addr_i  : in  std_logic_vector(15 downto 0);
        j_reg_wdata_i : in  std_logic_vector(31 downto 0);
        j_reg_rdata_o : out std_logic_vector(31 downto 0);

        -- ── Door 2: AXI4-Lite of u_ext ───────────────────────────
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
        rready_i  : in  std_logic
    );
end entity;

architecture tb of rr_rea_dual_door_harness is

    signal e_reg_wr_en : std_logic;
    signal e_reg_rd_en : std_logic;
    signal e_reg_addr  : std_logic_vector(15 downto 0);
    signal e_reg_wdata : std_logic_vector(31 downto 0);
    signal e_reg_rdata : std_logic_vector(31 downto 0);
    signal e_reg_rst   : std_logic;

    signal source_jtag : std_logic_vector(G_NUM_SOURCE - 1 downto 0);
    signal source_ext  : std_logic_vector(G_NUM_SOURCE - 1 downto 0);
    signal tdo_ext     : std_logic;

begin

    e_reg_rst <= not aresetn_i;

    -- ── Door 1: the JTAG core ────────────────────────────────────
    u_jtag : entity work.rr_rea_top
        generic map (
            G_SAMPLE_W    => G_SAMPLE_W,
            G_DEPTH       => G_DEPTH,
            G_TIMESTAMP_W => G_TIMESTAMP_W,
            G_NUM_CHAN    => G_NUM_CHAN,
            G_TRIG_CONDS  => G_TRIG_CONDS,
            G_NUM_SOURCE  => G_NUM_SOURCE,
            G_REG_IFACE   => "jtag"
        )
        port map (
            sample_clk_i => sample_clk_i,
            sample_rst_i => sample_rst_i,
            probe_i      => probe_i,
            source_o     => source_jtag,
            trigger_o    => trigger_jtag_o,
            arst_i       => arst_i,
            tck_i        => tck_i,
            tdi_i        => tdi_i,
            tdo_o        => tdo_o,
            capture_i    => capture_i,
            shift_en_i   => shift_en_i,
            update_i     => update_i,
            sel_i        => sel_i,
            -- Deliberately WIRED (not left at default) so the test can drive
            -- them and prove a jtag core has no second door (REA-REQ-902).
            reg_clk_i    => j_reg_clk_i,
            reg_rst_i    => '0',
            reg_wr_en_i  => j_reg_wr_en_i,
            reg_rd_en_i  => j_reg_rd_en_i,
            reg_addr_i   => j_reg_addr_i,
            reg_wdata_i  => j_reg_wdata_i,
            reg_rdata_o  => j_reg_rdata_o
        );

    -- ── Door 2: the external core behind rr_rea_axi4lite ─────────
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
            reg_wr_en_o => e_reg_wr_en,
            reg_rd_en_o => e_reg_rd_en,
            reg_addr_o  => e_reg_addr,
            reg_wdata_o => e_reg_wdata,
            reg_rdata_i => e_reg_rdata
        );

    u_ext : entity work.rr_rea_top
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
            source_o     => source_ext,
            trigger_o    => trigger_ext_o,
            arst_i       => '0',
            tck_i        => '0',
            tdi_i        => '0',
            tdo_o        => tdo_ext,
            capture_i    => '0',
            shift_en_i   => '0',
            update_i     => '0',
            sel_i        => '0',
            reg_clk_i    => aclk_i,
            reg_rst_i    => e_reg_rst,
            reg_wr_en_i  => e_reg_wr_en,
            reg_rd_en_i  => e_reg_rd_en,
            reg_addr_i   => e_reg_addr,
            reg_wdata_i  => e_reg_wdata,
            reg_rdata_o  => e_reg_rdata
        );

end architecture;
