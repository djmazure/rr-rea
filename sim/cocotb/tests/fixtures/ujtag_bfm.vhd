-- SPDX-FileCopyrightText: 2026 Daniel J. Mazure
-- SPDX-License-Identifier: MIT
--
-- ujtag_bfm.vhd — Behavioral simulation model / BFM for Microchip's UJTAG primitive.
--
-- Implements the IEEE 1149.1 TAP state machine and Microchip UJTAG interface signals:
--   - 16-state standard TAP controller clocked on TCK, reset by TRSTB='0'.
--   - Instruction register: 8 bits, shifts TDI on Shift-IR, updates UIREG on Update-IR.
--   - Data register controls: UDRCAP, UDRSH, UDRUPD, UTDI, UTDO.
--   - URSTB: active-low reset asserted during Test-Logic-Reset.

library ieee;
    use ieee.std_logic_1164.all;
    use ieee.numeric_std.all;

entity UJTAG is
    port (
        UTDO   : in  std_logic;
        UDRCAP : out std_logic;
        UDRSH  : out std_logic;
        UDRUPD : out std_logic;
        UIREG  : out std_logic_vector(7 downto 0);
        URSTB  : out std_logic;
        UTDI   : out std_logic;
        TCK    : in  std_logic := '0';
        TRSTB  : in  std_logic := '1';
        TDI    : in  std_logic := '0';
        TDO    : out std_logic;
        TMS    : in  std_logic := '0';
        UDRCK  : out std_logic
    );
end entity UJTAG;

architecture bfm of UJTAG is

    type tap_state_t is (
        Test_Logic_Reset,
        Run_Test_Idle,
        Select_DR_Scan,
        Capture_DR,
        Shift_DR,
        Exit1_DR,
        Pause_DR,
        Exit2_DR,
        Update_DR,
        Select_IR_Scan,
        Capture_IR,
        Shift_IR,
        Exit1_IR,
        Pause_IR,
        Exit2_IR,
        Update_IR
    );

    signal current_state : tap_state_t := Test_Logic_Reset;
    signal ir_shift_reg  : std_logic_vector(7 downto 0) := x"01";
    signal ir_reg        : std_logic_vector(7 downto 0) := x"01";

begin

    UDRCK <= TCK;
    UTDI  <= TDI;

    -- TAP State Machine transitions on TCK rising edge (asynchronous TRSTB)
    process (TCK, TRSTB)
    begin
        if TRSTB = '0' then
            current_state <= Test_Logic_Reset;
            ir_shift_reg  <= x"01";
            ir_reg        <= x"01";
        elsif rising_edge(TCK) then
            case current_state is
                when Test_Logic_Reset =>
                    if TMS = '0' then
                        current_state <= Run_Test_Idle;
                    end if;

                when Run_Test_Idle =>
                    if TMS = '1' then
                        current_state <= Select_DR_Scan;
                    end if;

                when Select_DR_Scan =>
                    if TMS = '1' then
                        current_state <= Select_IR_Scan;
                    else
                        current_state <= Capture_DR;
                    end if;

                when Capture_DR =>
                    if TMS = '1' then
                        current_state <= Exit1_DR;
                    else
                        current_state <= Shift_DR;
                    end if;

                when Shift_DR =>
                    if TMS = '1' then
                        current_state <= Exit1_DR;
                    end if;

                when Exit1_DR =>
                    if TMS = '1' then
                        current_state <= Update_DR;
                    else
                        current_state <= Pause_DR;
                    end if;

                when Pause_DR =>
                    if TMS = '1' then
                        current_state <= Exit2_DR;
                    end if;

                when Exit2_DR =>
                    if TMS = '1' then
                        current_state <= Update_DR;
                    else
                        current_state <= Shift_DR;
                    end if;

                when Update_DR =>
                    if TMS = '1' then
                        current_state <= Select_DR_Scan;
                    else
                        current_state <= Run_Test_Idle;
                    end if;

                when Select_IR_Scan =>
                    if TMS = '1' then
                        current_state <= Test_Logic_Reset;
                    else
                        current_state <= Capture_IR;
                    end if;

                when Capture_IR =>
                    ir_shift_reg <= x"01";  -- IEEE 1149.1 requires '01' on Capture-IR
                    if TMS = '1' then
                        current_state <= Exit1_IR;
                    else
                        current_state <= Shift_IR;
                    end if;

                when Shift_IR =>
                    ir_shift_reg <= TDI & ir_shift_reg(7 downto 1);
                    if TMS = '1' then
                        current_state <= Exit1_IR;
                    end if;

                when Exit1_IR =>
                    if TMS = '1' then
                        current_state <= Update_IR;
                    else
                        current_state <= Pause_IR;
                    end if;

                when Pause_IR =>
                    if TMS = '1' then
                        current_state <= Exit2_IR;
                    end if;

                when Exit2_IR =>
                    if TMS = '1' then
                        current_state <= Update_IR;
                    else
                        current_state <= Shift_IR;
                    end if;

                when Update_IR =>
                    ir_reg <= ir_shift_reg;
                    if TMS = '1' then
                        current_state <= Select_DR_Scan;
                    else
                        current_state <= Run_Test_Idle;
                    end if;
            end case;
        end if;
    end process;

    -- UJTAG signal outputs
    UIREG  <= ir_reg;
    URSTB  <= '0' when (current_state = Test_Logic_Reset or TRSTB = '0') else '1';
    UDRCAP <= '1' when current_state = Capture_DR else '0';
    UDRSH  <= '1' when current_state = Shift_DR else '0';
    UDRUPD <= '1' when current_state = Update_DR else '0';

    -- TDO multiplexer: output on falling edge of TCK per IEEE 1149.1
    process (TCK, TRSTB)
    begin
        if TRSTB = '0' then
            TDO <= '0';
        elsif falling_edge(TCK) then
            if current_state = Shift_IR then
                TDO <= ir_shift_reg(0);
            elsif current_state = Shift_DR then
                TDO <= UTDO;
            else
                TDO <= '0';
            end if;
        end if;
    end process;

end architecture bfm;
