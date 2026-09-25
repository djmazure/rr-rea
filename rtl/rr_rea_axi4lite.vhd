-- SPDX-FileCopyrightText: 2026 Daniel J. Mazure
-- SPDX-License-Identifier: MIT
--
-- rr_rea_axi4lite — AXI4-Lite slave → rr_rea register-bus bridge (RTL-P3.931).
--
-- WHY THIS EXISTS. rr_rea's readback used to be JTAG-only, so a design that
-- already has a control bus — and no spare BSCAN user chain — could not use it
-- and hand-rolled a twin instead (rr-idp's gen_dbg_cap, whose CDC is
-- unsynchronised: IDP-P2.11). The fix is NOT a second capture core.
-- rr_rea_jtag_iface already converts the TAP into a plain register bus that
-- rr_rea_regbank speaks, so this is simply the OTHER bridge onto that same bus:
-- the capture FSM, comparator array, CDC and v0.8 trust core are untouched and
-- bit-identical either way.
--
-- Contract: REA-REQ-903..907 (requirements.yml).
--
-- THE TRAP THIS IS BUILT AROUND (REA-REQ-904). rr_rea_regbank's rd_data_o is
-- REGISTERED — the RTL-P1.96 read-path pipelining that removed the wide
-- combinational mux cone Quartus miscompiled. A bridge that drives the address
-- and returns rdata in the SAME cycle therefore returns the PREVIOUS register's
-- value. That is the "every cell lags by one" class which has burned real
-- bring-up time on two vendors, and it looks like working silicon until you
-- read two registers in a row. And the DATA_BASE window is DEEPER still: the
-- capture BRAM's synchronous port-B read plus the RTL-P1.96 registered paging
-- mux put dpram_rdata TWO edges behind the address, so a bridge that waits
-- only the regbank's one edge reads every capture cell as the cell addressed
-- BEFORE the read began (REA-P2.4 found 1.1.0 returning physical cell 0 for
-- the whole window over AXI while the JTAG door read it correctly). This
-- bridge therefore presents the address and samples TWO edges later, which
-- is why the read path is a state machine and not a wire.
--
-- Addressing: AXI byte address, word-aligned. The low 2 bits are ignored (a
-- 32-bit register file); the rr_rea register map is byte-addressed on 4-byte
-- boundaries, so the AXI address has its low 2 bits masked to "00" onto
-- reg_addr_o (REA-P2.6). An AXI write to 0x04 hits CTRL exactly as a JTAG
-- write to 0x04 does, and an AXI narrow read at +1..+3 returns the full
-- register word on the correct byte lanes of rdata_o.
--
-- Responses are always OKAY. There is deliberately no decode error: the regbank
-- reads unmapped addresses as zero and drops unmapped writes, and a debug bus
-- that SLVERRs on an unknown offset turns a harmless probe into a bus fault.

library ieee;
    use ieee.std_logic_1164.all;
    use ieee.numeric_std.all;

entity rr_rea_axi4lite is
    generic (
        -- AXI address width. 16 covers the whole rr_rea register map
        -- (addresses are 16-bit on the internal bus).
        G_ADDR_W : positive := 16
    );
    port (
        -- ── AXI4-Lite slave (all synchronous to aclk_i) ──────────────
        aclk_i    : in  std_logic;
        aresetn_i : in  std_logic;

        awaddr_i  : in  std_logic_vector(G_ADDR_W - 1 downto 0);
        awvalid_i : in  std_logic;
        awready_o : out std_logic;

        wdata_i   : in  std_logic_vector(31 downto 0);
        wstrb_i   : in  std_logic_vector(3 downto 0);
        wvalid_i  : in  std_logic;
        wready_o  : out std_logic;

        bresp_o   : out std_logic_vector(1 downto 0);
        bvalid_o  : out std_logic;
        bready_i  : in  std_logic;

        araddr_i  : in  std_logic_vector(G_ADDR_W - 1 downto 0);
        arvalid_i : in  std_logic;
        arready_o : out std_logic;

        rdata_o   : out std_logic_vector(31 downto 0);
        rresp_o   : out std_logic_vector(1 downto 0);
        rvalid_o  : out std_logic;
        rready_i  : in  std_logic;

        -- ── rr_rea register bus (master side) ────────────────────────
        reg_wr_en_o : out std_logic;
        reg_rd_en_o : out std_logic;
        reg_addr_o  : out std_logic_vector(15 downto 0);
        reg_wdata_o : out std_logic_vector(31 downto 0);
        reg_rdata_i : in  std_logic_vector(31 downto 0)
    );
end entity;

architecture rtl of rr_rea_axi4lite is

    constant C_RESP_OKAY : std_logic_vector(1 downto 0) := "00";

    -- Write channel. aw and w are INDEPENDENT (REA-REQ-905): a master may
    -- present either first, or both together, and several interconnects
    -- present w first under backpressure. Each side is latched as it arrives
    -- and the register-bus write fires only when both have been.
    type t_wr_state is (W_IDLE, W_APPLY, W_RESP);
    signal wr_state_r   : t_wr_state := W_IDLE;
    signal aw_seen_r    : std_logic := '0';
    signal w_seen_r     : std_logic := '0';
    signal aw_addr_r  : std_logic_vector(G_ADDR_W - 1 downto 0) := (others => '0');
    signal w_data_r   : std_logic_vector(31 downto 0) := (others => '0');
    signal w_strb_r   : std_logic_vector(3 downto 0) := (others => '0');

    -- Read channel. R_ADDR presents the address; R_WAIT burns the regbank's
    -- registered-read cycle; R_WAIT2 burns the DATA window's second one
    -- (BRAM read + registered paging mux, REA-P2.4); R_RESP holds rvalid
    -- until rready (REA-REQ-904/906).
    type t_rd_state is (R_IDLE, R_ADDR, R_WAIT, R_WAIT2, R_RESP);
    signal rd_state_r   : t_rd_state := R_IDLE;
    signal ar_addr_r  : std_logic_vector(G_ADDR_W - 1 downto 0) := (others => '0');
    signal rdata_r    : std_logic_vector(31 downto 0) := (others => '0');

    -- ONE DRIVER PER SIGNAL. The address and write-data lines are driven
    -- COMBINATIONALLY from the two channel state machines rather than
    -- registered inside both — assigning one resolved signal from two
    -- processes makes every differing bit resolve to 'X', and since address
    -- 0x00 is all zeros it is the ONLY address that survives. That is the
    -- "address 0 works, every other address is wrong" signature this IP has
    -- already been burned by twice (RTL-T1.18), reproduced here in simulation
    -- by the REA-REQ-904 sequence test.
    signal reg_wr_en_r : std_logic := '0';
    signal reg_rd_en_r : std_logic := '0';

    -- Zero-extend / truncate an AXI address onto the 16-bit register bus,
    -- masking the low 2 bits to enforce word alignment (REA-P2.6).
    function to_reg_addr (a : std_logic_vector) return std_logic_vector is
        variable v : std_logic_vector(15 downto 0) := (others => '0');
    begin
        if a'length >= 16 then
            v := a(15 downto 0);
        else
            v(a'length - 1 downto 0) := a;
        end if;
        v(1 downto 0) := "00";
        return v;
    end function;

begin

    -- Write address/data are accepted only while idle, so a second
    -- transaction cannot overtake one in flight (REA-REQ-907).
    awready_o <= '1' when (wr_state_r = W_IDLE and aw_seen_r = '0') else '0';
    wready_o  <= '1' when (wr_state_r = W_IDLE and w_seen_r  = '0') else '0';
    arready_o <= '1' when rd_state_r = R_IDLE else '0';

    bresp_o <= C_RESP_OKAY;
    rresp_o <= C_RESP_OKAY;
    rdata_o <= rdata_r;

    -- The read channel owns the address while it is presenting one; the write
    -- channel owns it otherwise. The two never overlap: a read presents in
    -- R_ADDR/R_WAIT and a write applies in W_APPLY, and the arbitration below
    -- gives the read priority so a concurrent write waits one extra cycle
    -- rather than corrupting the read's address.
    reg_addr_o <= to_reg_addr(ar_addr_r)
                  when (rd_state_r = R_ADDR or rd_state_r = R_WAIT
                        or rd_state_r = R_WAIT2)
                  else to_reg_addr(aw_addr_r);
    reg_wdata_o <= w_data_r;
    reg_wr_en_o <= reg_wr_en_r;
    reg_rd_en_o <= reg_rd_en_r;

    bvalid_o <= '1' when wr_state_r = W_RESP else '0';
    rvalid_o <= '1' when rd_state_r = R_RESP else '0';

    -- ── Write channel ────────────────────────────────────────────────
    p_write : process (aclk_i) is
    begin
        if rising_edge(aclk_i) then
            -- Single-cycle strobe by construction (REA-REQ-903/907): it is
            -- driven high in exactly one state transition and cleared here
            -- every other cycle, so it can never stretch or double-pulse.
            reg_wr_en_r <= '0';

            if aresetn_i = '0' then
                wr_state_r  <= W_IDLE;
                aw_seen_r   <= '0';
                w_seen_r    <= '0';
                aw_addr_r   <= (others => '0');
                w_data_r    <= (others => '0');
                w_strb_r    <= (others => '0');
            else
                if wr_state_r = W_IDLE then
                    if awvalid_i = '1' and aw_seen_r = '0' then
                        aw_addr_r <= awaddr_i;
                        aw_seen_r <= '1';
                    end if;
                    if wvalid_i = '1' and w_seen_r = '0' then
                        w_data_r <= wdata_i;
                        w_strb_r <= wstrb_i;
                        w_seen_r <= '1';
                    end if;
                    -- Both halves in hand — including the same-cycle case,
                    -- which the two ifs above have just latched.
                    if (aw_seen_r = '1' or awvalid_i = '1')
                       and (w_seen_r = '1' or wvalid_i = '1') then
                        wr_state_r <= W_APPLY;
                    end if;

                elsif wr_state_r = W_APPLY then
                    -- wstrb is deliberately not honoured per-byte: every
                    -- rr_rea register is a whole 32-bit word (several are
                    -- toggle or side-effect registers where a partial write
                    -- has no meaning), so a sub-word write would be a silent
                    -- half-action. A master writing a full word — which the
                    -- host library always does — is unaffected; anything else
                    -- is dropped rather than half-applied.
                    -- Hold off while the read channel owns the address bus,
                    -- so the write strobe never lands against a read address.
                    if rd_state_r = R_ADDR or rd_state_r = R_WAIT
                       or rd_state_r = R_WAIT2 then
                        null;  -- retry next cycle; stays in W_APPLY
                    else
                        if w_strb_r = "1111" then
                            reg_wr_en_r <= '1';
                        end if;
                        aw_seen_r  <= '0';
                        w_seen_r   <= '0';
                        wr_state_r <= W_RESP;
                    end if;

                else  -- W_RESP: hold bvalid until the master takes it
                    if bready_i = '1' then
                        wr_state_r <= W_IDLE;
                    end if;
                end if;
            end if;
        end if;
    end process;

    -- ── Read channel ─────────────────────────────────────────────────
    p_read : process (aclk_i) is
    begin
        if rising_edge(aclk_i) then
            if aresetn_i = '0' then
                rd_state_r    <= R_IDLE;
                ar_addr_r   <= (others => '0');
                rdata_r     <= (others => '0');
                reg_rd_en_r <= '0';
            else
                case rd_state_r is
                    when R_IDLE =>
                        reg_rd_en_r <= '0';
                        if arvalid_i = '1' then
                            ar_addr_r <= araddr_i;
                            rd_state_r  <= R_ADDR;
                        end if;

                    when R_ADDR =>
                        -- The address is presented combinationally (see the
                        -- concurrent assignment above) for R_ADDR and R_WAIT,
                        -- so the regbank samples it on this edge.
                        reg_rd_en_r <= '1';
                        rd_state_r    <= R_WAIT;

                    when R_WAIT =>
                        -- REA-REQ-904: the regbank's rd_data_o is REGISTERED,
                        -- so it is valid on the cycle AFTER the address was
                        -- presented. Sampling in R_ADDR would return the
                        -- previous register — the one-cell-lag defect.
                        reg_rd_en_r <= '0';
                        rd_state_r    <= R_WAIT2;

                    when R_WAIT2 =>
                        -- REA-P2.4: the DATA_BASE window is one edge deeper
                        -- than the regbank (BRAM sync read, then the
                        -- RTL-P1.96 registered paging mux). Sampling in
                        -- R_WAIT read every capture cell as the cell
                        -- addressed BEFORE this read. Waiting one more edge
                        -- costs one aclk per read and is correct for both.
                        rdata_r     <= reg_rdata_i;
                        rd_state_r    <= R_RESP;

                    when others =>  -- R_RESP: hold rvalid until rready
                        if rready_i = '1' then
                            rd_state_r <= R_IDLE;
                        end if;
                end case;
            end if;
        end if;
    end process;

end architecture;
