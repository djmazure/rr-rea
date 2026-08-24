-- SPDX-FileCopyrightText: 2026 Daniel J. Mazure
-- SPDX-License-Identifier: MIT
--
-- rr_rea_axis_window_cdc_harness — SIM-ONLY unit harness that exposes the
-- rr_rea_axis_window snapshot boundary (done_i / start_ptr_i) to the testbench
-- so the CDC race the P2.5 landing reviewer named can be MODELLED and pinned.
--
-- In the real core done and start_ptr leave the capture FSM on the SAME
-- sample-clock edge and cross to the register-bus domain on SEPARATE
-- rr_rea_sync_word instances (1-bit done, multi-bit start_ptr, no gray code).
-- Two independent 2-flop synchronizers observing the same source transition
-- can present their new outputs up to one dest cycle apart — so at the exact
-- dest edge `done` first resolves high, `start_ptr` may still be the stale
-- pre-capture value. nvc gives both synchronizers identical delay, so the skew
-- is INVISIBLE in a plain full-core sim; here the testbench drives done_i and
-- start_ptr_i directly and can hold start_ptr stale for a few cycles after
-- done rises, reproducing the worst-case divergence deterministically.
--
-- The engine reads DPRAM port B one clock after mem_addr_o; this harness backs
-- that with a trivial registered memory whose data IS its address, so every
-- emitted beat reveals the physical ring address the engine walked — and thus
-- which start_ptr snapshot it actually used. G_TIMESTAMP_W=0 / a 1-word sample
-- cell makes one beat == one physical address.

library ieee;
    use ieee.std_logic_1164.all;
    use ieee.numeric_std.all;

library work;
    use work.rr_rea_pkg.all;

entity rr_rea_axis_window_cdc_harness is
    generic (
        G_SAMPLE_W    : positive := 12;
        G_DEPTH       : positive := 32;
        G_TIMESTAMP_W : natural  := 0
    );
    port (
        clk_i         : in  std_logic;
        rst_i         : in  std_logic;

        -- Snapshot boundary, driven by the testbench to model CDC skew.
        done_i        : in  std_logic;
        start_ptr_i   : in  std_logic_vector(clog2(G_DEPTH) - 1 downto 0);
        capture_len_i : in  std_logic_vector(clog2(G_DEPTH) downto 0);

        -- AXI-Stream master out
        m_tdata_o     : out std_logic_vector(31 downto 0);
        m_tvalid_o    : out std_logic;
        m_tready_i    : in  std_logic;
        m_tlast_o     : out std_logic
    );
end entity;

architecture tb of rr_rea_axis_window_cdc_harness is

    constant C_PTR_W : positive := clog2(G_DEPTH);

    signal mem_addr   : std_logic_vector(C_PTR_W - 1 downto 0);
    signal mem_rd     : std_logic;
    signal sample_dout : std_logic_vector(G_SAMPLE_W - 1 downto 0) := (others => '0');
    signal ts_dout    : std_logic_vector(max_nat(1, G_TIMESTAMP_W) - 1 downto 0) :=
        (others => '0');

begin

    -- Trivial port-B model: registered read, data = address. One clock of
    -- latency, exactly as rr_rea_dpram port B presents to the engine.
    process (clk_i)
    begin
        if rising_edge(clk_i) then
            sample_dout <= std_logic_vector(resize(unsigned(mem_addr), G_SAMPLE_W));
        end if;
    end process;

    u_dut : entity work.rr_rea_axis_window
        generic map (
            G_SAMPLE_W    => G_SAMPLE_W,
            G_DEPTH       => G_DEPTH,
            G_TIMESTAMP_W => G_TIMESTAMP_W
        )
        port map (
            clk_i         => clk_i,
            rst_i         => rst_i,
            done_i        => done_i,
            start_ptr_i   => start_ptr_i,
            capture_len_i => capture_len_i,
            mem_addr_o    => mem_addr,
            mem_rd_o      => mem_rd,
            sample_dout_i => sample_dout,
            ts_dout_i     => ts_dout,
            m_tdata_o     => m_tdata_o,
            m_tvalid_o    => m_tvalid_o,
            m_tready_i    => m_tready_i,
            m_tlast_o     => m_tlast_o
        );

end architecture;
