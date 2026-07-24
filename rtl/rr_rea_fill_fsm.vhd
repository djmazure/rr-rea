-- SPDX-FileCopyrightText: 2026 Daniel J. Mazure
-- SPDX-License-Identifier: MIT
--
-- rr_rea_fill_fsm — v0.8 selftest LFSR fill state machine (REA-P3.2 extraction).
--
-- The selftest fill walks the sample plane writing a deterministic LFSR pattern
-- so the readback path can be validated word-exact + by CRC before any real
-- capture is trusted (REA-P2.3, REQ-850..854). Extracted verbatim out of
-- rr_rea_top so its control contract is FORMALLY PROVABLE with FREE control
-- inputs (arm/reset/fill_request/armed/triggered/sweep_busy) — on the full top
-- those sit ~40-60 cycles behind the BSCAN protocol, so any feasible-depth proof
-- is vacuous (REA-P3.2). Behaviour is byte-identical to the in-top process.
--
-- Contract (see requirements.yml):
--   REQ-850 : accepted fill writes the exact LFSR sequence (taps 32,22,2,1).
--   REQ-851 : seed 0 substitutes the default 0x52454108.
--   REQ-852 : a fill while armed / mid-capture / DURING A SWEEP (REA-T1.2) is
--             REFUSED (sticky selftest_refused), not queued.
--   REQ-853 : an accepted fill sets selftest_mode before the first write and
--             bumps CAPTURE_EPOCH (fill_accept pulse); mode clears on arm/reset.

library ieee;
    use ieee.std_logic_1164.all;
    use ieee.numeric_std.all;
library work;
    use work.rr_rea_pkg.all;

entity rr_rea_fill_fsm is
    generic (
        G_SAMPLE_W : positive := 12;
        G_DEPTH    : positive := 4096
    );
    port (
        sample_clk_i       : in  std_logic;
        sample_rst_i       : in  std_logic;
        -- Control stimuli (sample_clk_i domain; free inputs for formal).
        arm_pulse_i        : in  std_logic;
        reset_pulse_i      : in  std_logic;
        fill_request_i     : in  std_logic;
        armed_i            : in  std_logic;
        triggered_i        : in  std_logic;
        sweep_busy_i       : in  std_logic;
        selftest_seed_i    : in  std_logic_vector(31 downto 0);
        -- Outputs.
        fill_we_o          : out std_logic;
        fill_busy_o        : out std_logic;
        fill_din_o         : out std_logic_vector(G_SAMPLE_W - 1 downto 0);
        fill_addr_o        : out std_logic_vector(clog2(G_DEPTH) - 1 downto 0);
        fill_done_o        : out std_logic;   -- 1-cycle pulse: last cell landed
        fill_accept_o      : out std_logic;   -- 1-cycle pulse: fill accepted
        selftest_mode_o    : out std_logic;
        selftest_refused_o : out std_logic
    );
end entity;

architecture rtl of rr_rea_fill_fsm is

    constant C_PTR_W : positive := clog2(G_DEPTH);
    constant C_PAGES : positive := (G_SAMPLE_W + 31) / 32;

    type fill_state_t is (F_IDLE, F_STAGE, F_WRITE);
    signal fill_state_r   : fill_state_t := F_IDLE;
    signal lfsr_r         : std_logic_vector(31 downto 0) := C_SELFTEST_SEED_DEFAULT;
    signal fill_stage_r   : std_logic_vector(G_SAMPLE_W - 1 downto 0) := (others => '0');
    signal fill_addr_r    : unsigned(C_PTR_W - 1 downto 0) := (others => '0');
    signal fill_page_r    : natural range 0 to C_PAGES - 1 := 0;
    signal selftest_mode_r    : std_logic := '0';
    signal selftest_refused_r : std_logic := '0';
    signal fill_done      : std_logic := '0';
    signal fill_accept    : std_logic := '0';

begin

    fill_busy_o   <= '1' when fill_state_r /= F_IDLE else '0';
    fill_din_o    <= fill_stage_r;
    fill_we_o     <= '1' when fill_state_r = F_WRITE else '0';
    fill_addr_o   <= std_logic_vector(fill_addr_r);
    fill_done_o   <= fill_done;
    fill_accept_o <= fill_accept;
    selftest_mode_o    <= selftest_mode_r;
    selftest_refused_o <= selftest_refused_r;

    -- Fill FSM (REQ-850..854). On an accepted request, walk the sample plane
    -- writing a deterministic LFSR pattern — C_PAGES stage cycles assemble one
    -- cell, then one write cycle. Refused (sticky) while armed / mid-capture /
    -- during a sweep (REQ-852, REA-T1.2). selftest_mode set before the first
    -- write, cleared on arm/reset (REQ-853). The timestamp plane is never
    -- written (REQ-854, gated in rr_rea_top's generate). fill_done triggers the
    -- sweep so the pattern is validated.
    process (sample_clk_i, sample_rst_i)
        variable b : std_logic;
    begin
        if sample_rst_i = '1' then
            fill_state_r       <= F_IDLE;
            lfsr_r             <= C_SELFTEST_SEED_DEFAULT;
            fill_addr_r        <= (others => '0');
            fill_page_r        <= 0;
            fill_stage_r       <= (others => '0');
            selftest_mode_r    <= '0';
            selftest_refused_r <= '0';
            fill_done          <= '0';
            fill_accept        <= '0';
        elsif rising_edge(sample_clk_i) then
            fill_done   <= '0';
            fill_accept <= '0';
            if arm_pulse_i = '1' or reset_pulse_i = '1' then
                selftest_mode_r    <= '0';   -- REQ-853
                selftest_refused_r <= '0';   -- cleared on arm/reset (REQ-852)
            end if;

            case fill_state_r is
                when F_IDLE =>
                    if fill_request_i = '1' then
                        -- REA-T1.2: also refuse while a CRC sweep is active. A
                        -- fill-triggered validation sweep runs with armed=0, so
                        -- without this guard a fill accepted mid-sweep would win
                        -- the port-A arbiter and hijack the sweep's read address
                        -- (dpram_addr_a <= fill_addr_r when fill_busy), corrupting
                        -- the CRC over the wrong cells. Sticky, per REQ-852.
                        if armed_i = '1' or triggered_i = '1'
                           or sweep_busy_i = '1' then
                            selftest_refused_r <= '1';               -- REQ-852
                        else
                            selftest_refused_r <= '0';
                            selftest_mode_r <= '1';                  -- before 1st write
                            fill_accept  <= '1';                     -- bumps epoch (REQ-853)
                            if selftest_seed_i = x"00000000" then    -- REQ-851
                                lfsr_r <= C_SELFTEST_SEED_DEFAULT;
                            else
                                lfsr_r <= selftest_seed_i;
                            end if;
                            fill_addr_r  <= (others => '0');
                            fill_page_r  <= 0;
                            fill_state_r <= F_STAGE;
                        end if;
                    end if;

                when F_STAGE =>
                    -- Stage the current LFSR word (pre-step) into the cell's
                    -- current 32-bit page, clipped to the final partial page.
                    for k in 0 to C_PAGES - 1 loop
                        if fill_page_r = k then
                            if 32 * k + 31 <= G_SAMPLE_W - 1 then
                                fill_stage_r(32 * k + 31 downto 32 * k) <= lfsr_r;
                            else
                                fill_stage_r(G_SAMPLE_W - 1 downto 32 * k) <=
                                    lfsr_r(G_SAMPLE_W - 1 - 32 * k downto 0);
                            end if;
                        end if;
                    end loop;
                    b := lfsr_r(31) xor lfsr_r(21) xor lfsr_r(1) xor lfsr_r(0);
                    lfsr_r <= b & lfsr_r(31 downto 1);
                    if fill_page_r = C_PAGES - 1 then
                        fill_state_r <= F_WRITE;
                    else
                        fill_page_r <= fill_page_r + 1;
                    end if;

                when F_WRITE =>
                    -- fill_we is combinational (above), stable this whole state.
                    if fill_addr_r = to_unsigned(G_DEPTH - 1, C_PTR_W) then
                        fill_done    <= '1';
                        fill_state_r <= F_IDLE;
                    else
                        fill_addr_r  <= fill_addr_r + 1;
                        fill_page_r  <= 0;
                        fill_state_r <= F_STAGE;
                    end if;
            end case;
        end if;
    end process;

end architecture;
