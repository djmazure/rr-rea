-- SPDX-FileCopyrightText: 2026 Daniel J. Mazure
-- SPDX-License-Identifier: MIT
--
-- rr_rea_dr_guard_discriminator_top — reference top for the REA-P2.20
-- discriminator image (REA-REQ-968/969; RTL-P2.901).
--
-- An Intel/Altera debug image with NO design logic: three rr_rea_intel cores
-- on one bitstream, read on one boot over one cable, so the only variable
-- between core A and core B is the guarded DR.
--
--   core A  G_SAMPLE_W = 256, G_DR_GUARD = false   positive control
--   core B  G_SAMPLE_W = 256, G_DR_GUARD = true    treatment
--   core C  G_SAMPLE_W = 12,  G_DR_GUARD = false   width control
--
-- The pre-registered verdict table lives in REA-P2.20. A reads with the
-- default 49-bit host framing, B with the guard flag (RTL-P2.1527), and the
-- sweep is `rr ila readback-map` (RTL-P2.1528).
--
-- Probe content does not matter to the experiment (it reads registers, not
-- captures), but it must be live so no core is pruned: a free-running
-- counter, replicated across the probe width, XORed with a 32-bit LFSR.
-- Reset is a power-on counter on clk_i, since the image has no reset pin.

library ieee;
    use ieee.std_logic_1164.all;
    use ieee.numeric_std.all;

entity rr_rea_dr_guard_discriminator_top is
    generic (
        G_IDX_A : positive := 1;  -- sld_instance_index of each core
        G_IDX_B : positive := 2;
        G_IDX_C : positive := 3
    );
    port (
        clk_i     : in  std_logic;  -- any free-running board oscillator
        trigger_o : out std_logic   -- keeps every core observable (no pruning)
    );
end entity;

architecture rtl of rr_rea_dr_guard_discriminator_top is

    constant C_WIDE_W   : positive := 256;
    constant C_NARROW_W : positive := 12;

    signal por_cnt_r : unsigned(7 downto 0)  := (others => '0');
    signal rst_r     : std_logic             := '1';
    signal count_r   : unsigned(31 downto 0) := (others => '0');
    signal lfsr_r    : std_logic_vector(31 downto 0) := x"ACE1_2026";
    signal probe_w   : std_logic_vector(C_WIDE_W - 1 downto 0);
    signal trig_a    : std_logic;
    signal trig_b    : std_logic;
    signal trig_c    : std_logic;

begin

    -- Power-on reset: held for 255 clk_i cycles after configuration.
    p_por : process (clk_i)
    begin
        if rising_edge(clk_i) then
            if por_cnt_r /= x"FF" then
                por_cnt_r <= por_cnt_r + 1;
                rst_r     <= '1';
            else
                rst_r <= '0';
            end if;
        end if;
    end process;

    -- Live probe content: counter and Galois LFSR (taps 32,22,2,1).
    p_stim : process (clk_i)
    begin
        if rising_edge(clk_i) then
            count_r <= count_r + 1;
            if lfsr_r(0) = '1' then
                lfsr_r <= ('0' & lfsr_r(31 downto 1)) xor x"80200003";
            else
                lfsr_r <= '0' & lfsr_r(31 downto 1);
            end if;
        end if;
    end process;

    g_probe : for i in 0 to C_WIDE_W / 32 - 1 generate
        probe_w(32 * i + 31 downto 32 * i) <=
            std_logic_vector(count_r) xor lfsr_r;
    end generate;

    u_core_a : entity work.rr_rea_intel
        generic map (
            G_SAMPLE_W   => C_WIDE_W,
            G_DEPTH      => 1024,
            G_CTRL_CHAIN => G_IDX_A,
            G_DR_GUARD   => false
        )
        port map (
            sample_clk_i => clk_i,
            sample_rst_i => rst_r,
            probe_i      => probe_w,
            source_o     => open,
            trigger_o    => trig_a
        );

    u_core_b : entity work.rr_rea_intel
        generic map (
            G_SAMPLE_W   => C_WIDE_W,
            G_DEPTH      => 1024,
            G_CTRL_CHAIN => G_IDX_B,
            G_DR_GUARD   => true
        )
        port map (
            sample_clk_i => clk_i,
            sample_rst_i => rst_r,
            probe_i      => probe_w,
            source_o     => open,
            trigger_o    => trig_b
        );

    u_core_c : entity work.rr_rea_intel
        generic map (
            G_SAMPLE_W   => C_NARROW_W,
            G_DEPTH      => 1024,
            G_CTRL_CHAIN => G_IDX_C,
            G_DR_GUARD   => false
        )
        port map (
            sample_clk_i => clk_i,
            sample_rst_i => rst_r,
            probe_i      => probe_w(C_NARROW_W - 1 downto 0),
            source_o     => open,
            trigger_o    => trig_c
        );

    trigger_o <= trig_a or trig_b or trig_c;

end architecture;
