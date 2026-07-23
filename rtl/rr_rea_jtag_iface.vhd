-- SPDX-FileCopyrightText: 2026 Daniel J. Mazure
-- SPDX-License-Identifier: MIT
--
-- rr_rea_jtag_iface — vendor-neutral BSCAN → register-bus bridge.
--
-- Mirrors fcapz's `jtag_reg_iface.v` 49-bit DR protocol so the
-- existing fcapz host SW (Analyzer / XilinxHwServerTransport) connects
-- unmodified. The protocol is the SW interface contract:
--
--   49-bit DR (LSB-first on the wire):
--     bits[31:0]  — wdata / rdata
--     bits[47:32] — addr[15:0]
--     bits[48]    — rnw (1 = write, 0 = read)
--
--   Phases driven by the vendor-specific TAP wrapper:
--     CAPTURE   — load reg_rdata into sr[31:0]
--     SHIFT     — sr <= {tdi, sr[48:1]}
--     UPDATE    — decode sr[48]: write → reg_wr_en; read → reg_rd_en
--                 (with reg_addr / reg_wdata committed from the sr)
--
-- v0.1 implements only the USER1 control chain — the host's
-- `read_block` falls back to USER1 reads when USER2 burst isn't
-- present, so dpram readout works at full speed/cycle even without
-- the burst path. USER2 burst is a v0.2 feature (rr_rea_jtag_burst).
--
-- Tested via REA-REQ-001..003. The TAP signals are driven directly
-- by the cocotb testbench, mocking the BSCAN hard macro.

library ieee;
    use ieee.std_logic_1164.all;
    use ieee.numeric_std.all;

entity rr_rea_jtag_iface is
    port (
        arst       : in  std_logic;

        -- ── TAP signals (from vendor-specific BSCAN wrapper) ─────
        tck        : in  std_logic;
        tdi        : in  std_logic;
        tdo        : out std_logic;
        capture    : in  std_logic;
        shift_en   : in  std_logic;
        update     : in  std_logic;
        sel        : in  std_logic;

        -- ── Register bus (drives rr_rea_regbank) ─────────────────
        reg_clk    : out std_logic;
        reg_rst    : out std_logic;
        reg_wr_en  : out std_logic;
        reg_rd_en  : out std_logic;
        reg_addr   : out std_logic_vector(15 downto 0);
        reg_wdata  : out std_logic_vector(31 downto 0);
        reg_rdata  : in  std_logic_vector(31 downto 0)
    );
end entity;

architecture rtl of rr_rea_jtag_iface is

    signal sr        : std_logic_vector(48 downto 0) := (others => '0');

    -- RTL-P1.96: keep vendor synthesis away from the DR shift register.
    -- Quartus Pro 26.1 (Arria 10, wide G_SAMPLE_W) restructured the ~100:1
    -- capture-mux cones feeding sr and miscompiled them (any captured value
    -- with bit0=1 read back as all-ones across the whole 49-bit DR). The
    -- registered read-data stages (rr_rea_regbank / rr_rea_top, same ticket)
    -- remove the cone; these attributes pin the DR itself so no future
    -- restructuring/merging pass can recreate the shape. Ignored by
    -- simulators and non-Quartus tools.
    attribute preserve   : boolean;
    attribute dont_merge : boolean;
    attribute keep       : boolean;
    attribute preserve of sr   : signal is true;
    attribute dont_merge of sr : signal is true;

    -- RTL-P1.96 (0.7.2): hold-safe DR shift chain. On Arria 10 the fitter
    -- placed adjacent sr bits as same-LAB direct FF-to-FF hops with ~20 ps
    -- hold slack ("met", but inside silicon variation — the root cause of
    -- the bit0/all-ones readback corruption), and router hold-fixing could
    -- not pad those direct connections even under an explicit set_min_delay.
    -- sr_shift_buf is a kept combinational copy of sr: every shift hop then
    -- goes FF -> LUT buffer -> FF, carrying a guaranteed cell+routing delay
    -- on every family. 49 LUTs in a debug core — free; the DR runs at JTAG
    -- rates so the added setup delay is irrelevant.
    signal sr_shift_buf : std_logic_vector(48 downto 0);
    attribute keep       of sr_shift_buf : signal is true;
    attribute preserve   of sr_shift_buf : signal is true;
    attribute dont_merge of sr_shift_buf : signal is true;
    signal reg_wr_en_r : std_logic := '0';
    signal reg_rd_en_r : std_logic := '0';
    signal reg_addr_r  : std_logic_vector(15 downto 0) := (others => '0');
    signal reg_wdata_r : std_logic_vector(31 downto 0) := (others => '0');

begin

    sr_shift_buf <= sr;

    -- TDO is the standard combinational DR shift-out: the bit currently at
    -- sr(0). The shift register advances ONLY on rising_edge(tck) (below), so
    -- TDO is a pure function of rising-edge state — NO falling-edge logic.
    --
    -- History (REA-T1.1): 0.7.4 registered TDO on the FALLING edge of tck as an
    -- RTL-level hold-margin hack for the Arria-10 SLD-hub readback corruption
    -- (RTL-P1.96). That hack (a) introduced dual-edge clocking — a design smell
    -- now forbidden (AGENTS.md: no falling_edge unless completely justified),
    -- and (b) did NOT hold up in silicon — REA readback stayed faulty (the
    -- 2026-07 odd-address fault). The corruption is an intra-tck HOLD-slack
    -- problem in the SLD fabric domain; the PROVEN fix is the SDC
    -- `set_clock_uncertainty` hold pad on that domain (an integration timing
    -- constraint), not an RTL edge trick. The v0.8 trust tier (CRC sweep +
    -- readback selftest) is the on-silicon instrument that validates the
    -- readback is finally correct end-to-end.
    tdo       <= sr(0);
    reg_clk   <= tck;
    reg_rst   <= arst;
    reg_wr_en <= reg_wr_en_r;
    reg_rd_en <= reg_rd_en_r;
    reg_addr  <= reg_addr_r;
    reg_wdata <= reg_wdata_r;

    process (tck, arst)
    begin
        if arst = '1' then
            sr          <= (others => '0');
            reg_wr_en_r <= '0';
            reg_rd_en_r <= '0';
            reg_addr_r  <= (others => '0');
            reg_wdata_r <= (others => '0');

        elsif rising_edge(tck) then
            -- Default: pulse signals are 1-cycle.
            reg_wr_en_r <= '0';
            reg_rd_en_r <= '0';

            if sel = '1' then
                if capture = '1' then
                    -- CAPTURE: load current rdata into low half of sr.
                    sr(31 downto 0) <= reg_rdata;
                elsif shift_en = '1' then
                    -- SHIFT: shift LSB-first toward TDO (via the kept
                    -- LUT-buffer stage — see sr_shift_buf above).
                    sr <= tdi & sr_shift_buf(48 downto 1);
                elsif update = '1' then
                    -- UPDATE: decode rnw bit and pulse the bus.
                    if sr(48) = '1' then
                        reg_addr_r  <= sr(47 downto 32);
                        reg_wdata_r <= sr(31 downto 0);
                        reg_wr_en_r <= '1';
                    else
                        reg_addr_r  <= sr(47 downto 32);
                        reg_rd_en_r <= '1';
                    end if;
                end if;
            end if;
        end if;
    end process;

end architecture;
