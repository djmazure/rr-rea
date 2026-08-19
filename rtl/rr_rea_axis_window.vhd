-- SPDX-FileCopyrightText: 2026 Daniel J. Mazure
-- SPDX-License-Identifier: MIT
--
-- rr_rea_axis_window — AXI-Stream window-dump engine (REA-P2.5).
--
-- The first burst "truck" for the capture window after STATUS.done
-- (docs/DUMP_PATH_STRATEGY.md, SPEC.md "Dump-path transports", REA-REQ-911/912/
-- 913/914). It is NOT a second analyser and NOT a second register-bus master:
-- the host keeps arming/configuring/polling over its one register door
-- (G_REG_IFACE). This engine only READS — it taps DPRAM port B directly and a
-- snapshot of the capture pointers — and emits the exact window blob
-- rea_window_blob.py defines, so a consumer needs no host-side rotation.
--
-- Blob order (frozen, REA-REQ-911/912): plane-major (whole sample plane, then
-- the timestamp plane iff it is elaborated), CAPTURE_LEN cells per plane, cell
-- i = physical DATA_BASE cell (START_PTR + i) mod DEPTH, each cell emitted as
-- ceil(plane_w/32) little-endian 32-bit beats (word k = plane bits [32k+31:32k],
-- final partial word zero-padded — byte-for-byte a DATA_BASE read with
-- DATA_WORD_SEL = k). `tlast` marks the last beat of the last plane.
--
-- Domain: this engine runs in the register-bus clock domain (reg_clk_o in
-- rr_rea_top; the PS AXI clock under G_REG_IFACE = "external"), the same clock
-- that reads DPRAM port B. One DPRAM read per cell; the ceil(plane_w/32) beats
-- of that cell are then paged out of a latched cell register, so the blob is
-- byte-identical to the DATA_BASE walk regardless of read cadence. Full AXIS
-- back-pressure: tdata/tlast hold while tvalid and not tready.

library ieee;
    use ieee.std_logic_1164.all;
    use ieee.numeric_std.all;

library work;
    use work.rr_rea_pkg.all;

entity rr_rea_axis_window is
    generic (
        G_SAMPLE_W    : positive := 12;
        G_DEPTH       : positive := 4096;
        G_TIMESTAMP_W : natural  := 0
    );
    port (
        clk_i : in std_logic;
        rst_i : in std_logic;

        -- ── Capture snapshot (register-bus domain) ───────────────────
        -- done_i is the STATUS.done level; a rising edge (a fresh capture
        -- completing) launches exactly one burst. start_ptr_i / capture_len_i
        -- are the ring pointers, stable once done — latched at burst start so a
        -- later re-arm cannot tear the in-flight blob.
        done_i        : in std_logic;
        start_ptr_i   : in std_logic_vector(clog2(G_DEPTH) - 1 downto 0);
        capture_len_i : in std_logic_vector(clog2(G_DEPTH) downto 0);

        -- ── DPRAM port-B read tap ────────────────────────────────────
        -- mem_rd_o is high only while the engine owns port B (rr_rea_top muxes
        -- the read address to mem_addr_o then). sample_dout_i / ts_dout_i are
        -- port B's registered outputs, valid one clk after mem_addr_o.
        mem_addr_o    : out std_logic_vector(clog2(G_DEPTH) - 1 downto 0);
        mem_rd_o      : out std_logic;
        sample_dout_i : in  std_logic_vector(G_SAMPLE_W - 1 downto 0);
        ts_dout_i     : in  std_logic_vector(max_nat(1, G_TIMESTAMP_W) - 1 downto 0);

        -- ── AXI-Stream master (32-bit) ───────────────────────────────
        m_tdata_o  : out std_logic_vector(C_DATA_WORD_W - 1 downto 0);
        m_tvalid_o : out std_logic;
        m_tready_i : in  std_logic;
        m_tlast_o  : out std_logic
    );
end entity;

architecture rtl of rr_rea_axis_window is

    constant C_PTR_W : positive := clog2(G_DEPTH);
    -- CAPTURE_LEN counts up to DEPTH, so it needs one bit more than a pointer.
    constant C_LEN_W : positive := C_PTR_W + 1;

    -- ceil(plane_w/32) beats per cell, per plane. trig_words() is the shared
    -- ceil-div-by-32 (rr_rea_pkg) — same paging as DATA_WORD_SEL; kept here so
    -- this design unit carries no divide of its own.
    constant C_SAMPLE_WORDS : positive := trig_words(G_SAMPLE_W);
    constant C_HAS_TS       : boolean  := G_TIMESTAMP_W > 0;
    constant C_TS_W         : positive := max_nat(1, G_TIMESTAMP_W);
    constant C_TS_WORDS     : positive := trig_words(C_TS_W);
    constant C_MAX_WORDS    : positive := max_nat(C_SAMPLE_WORDS, C_TS_WORDS);
    -- A cell register wide enough for either plane's cell.
    constant C_CELL_W       : positive := max_nat(G_SAMPLE_W, C_TS_W);

    type state_t is (S_IDLE, S_SETUP, S_FETCH, S_STREAM);
    signal state_r : state_t := S_IDLE;

    -- 0 = sample plane, 1 = timestamp plane.
    signal plane_r : std_logic := '0';
    signal idx_r   : unsigned(C_LEN_W - 1 downto 0) := (others => '0');
    signal word_r  : integer range 0 to C_MAX_WORDS - 1 := 0;
    -- Cell SHIFT register: word k of the cell is always the low 32 bits, and an
    -- accepted beat shifts the next word down by a constant 32. This keeps the
    -- paging out of the datapath as a fixed shift — no word_r*32 runtime shift
    -- amount (which would infer a barrel shifter / multiply).
    signal cell_sr : unsigned(C_CELL_W - 1 downto 0) := (others => '0');

    signal start_ptr_r   : unsigned(C_PTR_W - 1 downto 0) := (others => '0');
    signal capture_len_r : unsigned(C_LEN_W - 1 downto 0) := (others => '0');
    signal done_prev_r   : std_logic := '0';

    -- Beats-per-cell for the plane currently being emitted.
    signal words_in_plane : positive;
    -- Combinational physical ring address of the current cell.
    signal phys_addr      : unsigned(C_PTR_W - 1 downto 0);
    -- This beat is the final beat of the final plane.
    signal last_beat      : boolean;

begin

    -- words this plane: sample plane vs (elaborated) timestamp plane.
    words_in_plane <= C_SAMPLE_WORDS when plane_r = '0' else C_TS_WORDS;

    -- phys = (START_PTR + idx) mod DEPTH. START_PTR < DEPTH and idx < DEPTH, so
    -- the sum is < 2*DEPTH and a single conditional subtract normalises it —
    -- correct for any DEPTH, not only powers of two.
    process (start_ptr_r, idx_r)
        variable sum_v : unsigned(C_LEN_W downto 0);
    begin
        sum_v := resize(start_ptr_r, C_LEN_W + 1) + resize(idx_r, C_LEN_W + 1);
        if sum_v >= to_unsigned(G_DEPTH, C_LEN_W + 1) then
            sum_v := sum_v - to_unsigned(G_DEPTH, C_LEN_W + 1);
        end if;
        phys_addr <= resize(sum_v, C_PTR_W);
    end process;

    -- The last beat is the last plane's last cell's last word. The sample plane
    -- is the last plane only when no timestamp plane is elaborated.
    last_beat <= ((not C_HAS_TS) or plane_r = '1')
                 and (idx_r = capture_len_r - 1)
                 and (word_r = words_in_plane - 1);

    mem_addr_o <= std_logic_vector(phys_addr);
    mem_rd_o   <= '1' when state_r = S_SETUP or state_r = S_FETCH else '0';

    m_tvalid_o <= '1' when state_r = S_STREAM else '0';
    m_tlast_o  <= '1' when state_r = S_STREAM and last_beat else '0';
    -- The current word is always the low 32 bits of the shift register,
    -- zero-padded by the resize when the cell is narrower than a word.
    m_tdata_o  <= std_logic_vector(resize(cell_sr, C_DATA_WORD_W));

    process (clk_i)
    begin
        if rising_edge(clk_i) then
            if rst_i = '1' then
                state_r     <= S_IDLE;
                plane_r     <= '0';
                idx_r       <= (others => '0');
                word_r      <= 0;
                cell_sr     <= (others => '0');
                done_prev_r <= '0';
            else
                done_prev_r <= done_i;

                case state_r is
                    when S_IDLE =>
                        -- Launch one burst on the rising edge of done, provided
                        -- the window is non-empty.
                        if done_i = '1' and done_prev_r = '0'
                           and unsigned(capture_len_i) /= 0 then
                            start_ptr_r   <= unsigned(start_ptr_i);
                            capture_len_r <= unsigned(capture_len_i);
                            plane_r       <= '0';
                            idx_r         <= (others => '0');
                            word_r        <= 0;
                            state_r       <= S_SETUP;
                        end if;

                    when S_SETUP =>
                        -- Address is driven combinationally; port B latches it
                        -- on this edge, data is valid next cycle.
                        state_r <= S_FETCH;

                    when S_FETCH =>
                        if plane_r = '0' then
                            cell_sr <= resize(unsigned(sample_dout_i), C_CELL_W);
                        else
                            cell_sr <= resize(unsigned(ts_dout_i), C_CELL_W);
                        end if;
                        word_r  <= 0;
                        state_r <= S_STREAM;

                    when S_STREAM =>
                        if m_tready_i = '1' then
                            if word_r = words_in_plane - 1 then
                                -- Cell complete.
                                if idx_r = capture_len_r - 1 then
                                    -- Plane complete.
                                    if plane_r = '0' and C_HAS_TS then
                                        plane_r <= '1';
                                        idx_r   <= (others => '0');
                                        state_r <= S_SETUP;
                                    else
                                        state_r <= S_IDLE;
                                    end if;
                                else
                                    idx_r   <= idx_r + 1;
                                    state_r <= S_SETUP;
                                end if;
                            else
                                -- Advance to the next word of THIS cell: shift
                                -- the next 32 bits down (constant shift).
                                word_r  <= word_r + 1;
                                cell_sr <= shift_right(cell_sr, C_DATA_WORD_W);
                            end if;
                        end if;
                end case;
            end if;
        end if;
    end process;

end architecture;
