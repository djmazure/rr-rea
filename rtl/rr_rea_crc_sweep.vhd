-- SPDX-FileCopyrightText: 2026 Daniel J. Mazure
-- SPDX-License-Identifier: MIT
--
-- CRC-32 memory sweep engine.
-- Requirements: REA-REQ-801, REA-REQ-805.

library ieee;
    use ieee.std_logic_1164.all;
    use ieee.numeric_std.all;

library work;
    use work.rr_rea_pkg.all;

entity rr_rea_crc_sweep is
  generic (
    G_SAMPLE_W : positive;
    G_DEPTH    : positive
  );
  port (
    sample_clk : in  std_logic;
    sample_rst : in  std_logic;
    start      : in  std_logic;
    mem_dout   : in  std_logic_vector(G_SAMPLE_W - 1 downto 0);
    mem_addr   : out std_logic_vector(clog2(G_DEPTH) - 1 downto 0);
    mem_rd_en  : out std_logic;
    busy       : out std_logic;
    crc_done   : out std_logic;
    crc_out    : out std_logic_vector(31 downto 0)
  );
end entity;

architecture rtl of rr_rea_crc_sweep is

  constant C_BYTE_BITS      : positive := 8;
  constant C_BYTES_PER_PAGE : positive := 4;
  constant C_PAGE_BITS      : positive := 32;

  pure function f_page(
    data : std_logic_vector;
    page : natural
  ) return std_logic_vector is
    variable result_v : std_logic_vector(
      C_PAGE_BITS - 1 downto 0
    ) := (others => '0');
    variable source_v : natural;
  begin
    for bit_v in 0 to C_PAGE_BITS - 1 loop
      source_v := (page * C_PAGE_BITS) + bit_v;
      if source_v < data'length then
        result_v(bit_v) := data(data'low + source_v);
      end if;
    end loop;
    return result_v;
  end function;

  pure function f_crc_byte(
    crc  : std_logic_vector(C_PAGE_BITS - 1 downto 0);
    data : std_logic_vector(C_BYTE_BITS - 1 downto 0)
  ) return std_logic_vector is
    variable result_v : std_logic_vector(C_PAGE_BITS - 1 downto 0);
  begin
    result_v := crc xor std_logic_vector(
      resize(unsigned(data), C_PAGE_BITS)
    );
    for bit_v in 0 to C_BYTE_BITS - 1 loop
      if result_v(0) = '1' then
        result_v := ('0' & result_v(31 downto 1)) xor x"EDB88320";
      else
        result_v := '0' & result_v(31 downto 1);
      end if;
    end loop;
    return result_v;
  end function;

  constant C_ADDR_W : positive := clog2(G_DEPTH);
  constant C_NPAGES : positive := (G_SAMPLE_W + C_PAGE_BITS - 1) / 32;

  type state_t is (
    IDLE,
    ISSUE_READ,
    WAIT_DATA,
    READ_CAPTURE,
    PROCESS_BYTE
  );

  signal state_r     : state_t := IDLE;
  signal addr_r      : natural range 0 to G_DEPTH - 1 := 0;
  signal page_r      : natural range 0 to C_NPAGES - 1 := 0;
  signal byte_r      : natural range 0 to C_BYTES_PER_PAGE - 1 := 0;
  signal cell_data_r : std_logic_vector(G_SAMPLE_W - 1 downto 0) := (others => '0');
  signal crc_r       : std_logic_vector(31 downto 0) := (others => '0');
  signal mem_rd_en_r : std_logic := '0';
  signal busy_r      : std_logic := '0';
  signal crc_done_r  : std_logic := '0';
  signal crc_out_r   : std_logic_vector(31 downto 0) := (others => '0');

begin

  mem_addr  <= std_logic_vector(to_unsigned(addr_r, C_ADDR_W));
  mem_rd_en <= mem_rd_en_r;
  busy      <= busy_r;
  crc_done  <= crc_done_r;
  crc_out   <= crc_out_r;

  process (sample_clk, sample_rst)
    variable page_v : std_logic_vector(C_PAGE_BITS - 1 downto 0);
    variable crc_v  : std_logic_vector(C_PAGE_BITS - 1 downto 0);
  begin
    if sample_rst = '1' then
      state_r     <= IDLE;
      addr_r      <= 0;
      page_r      <= 0;
      byte_r      <= 0;
      cell_data_r <= (others => '0');
      crc_r       <= (others => '0');
      mem_rd_en_r <= '0';
      busy_r      <= '0';
      crc_done_r  <= '0';
      crc_out_r   <= (others => '0');
    elsif rising_edge(sample_clk) then
      mem_rd_en_r <= '0';
      crc_done_r  <= '0';

      case state_r is
        when IDLE =>
          if start = '1' then
            addr_r <= 0;
            page_r <= 0;
            byte_r <= 0;
            crc_r  <= x"FFFFFFFF";
            busy_r <= '1';
            state_r <= ISSUE_READ;
          end if;

        when ISSUE_READ =>
          mem_rd_en_r <= '1';
          state_r <= WAIT_DATA;

        when WAIT_DATA =>
          state_r <= READ_CAPTURE;

        when READ_CAPTURE =>
          cell_data_r <= mem_dout;
          page_r <= 0;
          byte_r <= 0;
          state_r <= PROCESS_BYTE;

        when PROCESS_BYTE =>
          page_v := f_page(cell_data_r, page_r);
          crc_v := f_crc_byte(
            crc_r,
            page_v(
              (byte_r * C_BYTE_BITS) + C_BYTE_BITS - 1
              downto byte_r * C_BYTE_BITS
            )
          );
          crc_r <= crc_v;

          if byte_r < C_BYTES_PER_PAGE - 1 then
            byte_r <= byte_r + 1;
          elsif page_r < C_NPAGES - 1 then
            byte_r <= 0;
            page_r <= page_r + 1;
          elsif addr_r < G_DEPTH - 1 then
            addr_r <= addr_r + 1;
            byte_r <= 0;
            page_r <= 0;
            state_r <= ISSUE_READ;
          else
            crc_out_r <= crc_v xor x"FFFFFFFF";
            crc_done_r <= '1';
            busy_r <= '0';
            state_r <= IDLE;
          end if;

        when others =>
          state_r <= IDLE;
      end case;
    end if;
  end process;

end architecture;
