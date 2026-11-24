// Verifies the shared component protocol: every simulated leaf derives from
// ComponentBase, reports changes as a change_mask_t, and exposes the same
// two-phase update shape used by generated models.
#include "pyc_byte_mem.hpp"
#include "pyc_cdc_sync.hpp"
#include "pyc_component.hpp"
#include "pyc_primitives.hpp"
#include "pyc_sync_mem.hpp"
#include "pyc_vec.hpp"

#include <cassert>
#include <type_traits>

using namespace pyc::cpp;

namespace {

using mask_t = ComponentBase::change_mask_t;

// Every leaf is a component and reports its changes through the same type.
static_assert(std::is_base_of_v<ComponentBase, pyc_reg<8>>);
static_assert(std::is_base_of_v<ComponentBase, pyc_vec_reg<Vec<Wire<4>, 2>>>);
static_assert(std::is_base_of_v<ComponentBase, pyc_delay_line<8, 2>>);
static_assert(std::is_base_of_v<ComponentBase, pyc_vec_delay_line<Vec<Wire<4>, 2>, 2>>);
static_assert(std::is_base_of_v<ComponentBase, pyc_cdc_sync<8, 2>>);
static_assert(std::is_base_of_v<ComponentBase, pyc_fifo<8, 2>>);
static_assert(std::is_base_of_v<ComponentBase, pyc_byte_mem<4, 8, 16>>);
static_assert(std::is_base_of_v<ComponentBase, pyc_sync_mem_nm<4, 8, 16, 1, 1>>);

static_assert(std::is_same_v<decltype(std::declval<pyc_reg<8> &>().tick_commit()), mask_t>);
static_assert(std::is_same_v<decltype(std::declval<pyc_delay_line<8, 2> &>().tick_commit()), mask_t>);
static_assert(std::is_same_v<decltype(std::declval<pyc_cdc_sync<8, 2> &>().tick_commit()), mask_t>);
static_assert(std::is_same_v<decltype(std::declval<pyc_fifo<8, 2> &>().tick_commit()), mask_t>);
static_assert(std::is_same_v<decltype(std::declval<pyc_byte_mem<4, 8, 16> &>().tick_commit()), mask_t>);
static_assert(std::is_same_v<decltype(std::declval<pyc_sync_mem_nm<4, 8, 16, 1, 1> &>().tick_commit()), mask_t>);

// The canonical entry point accepts a caller-supplied clock level, so a driver
// can hoist the edge test without changing component code.
static_assert(std::is_same_v<decltype(std::declval<pyc_reg<8> &>().tick_compute(true)), void>);
static_assert(std::is_same_v<decltype(std::declval<pyc_fifo<8, 2> &>().tick_compute(true)), void>);

void checkReg() {
  Wire<1> clk(0), rst(0), en(1);
  Wire<8> d(0x12), init(0x7), q(0);
  pyc_reg<8> reg(clk, rst, en, d, init, q);

  clk = Wire<1>(1);
  reg.tick_compute(true);
  assert(reg.tick_commit() == ComponentBase::kChanged);
  assert(q.value() == 0x12u);

  // Repeating the same level is not an edge and must not report a change.
  clk = Wire<1>(0);
  reg.negedge_update();
  clk = Wire<1>(1);
  reg.tick_compute(true);
  assert(reg.tick_commit() == mask_t{0});

  // Reset takes precedence over enable.
  rst = Wire<1>(1);
  clk = Wire<1>(0);
  reg.negedge_update();
  clk = Wire<1>(1);
  reg.tick_compute(true);
  assert(reg.tick_commit() == ComponentBase::kChanged);
  assert(q.value() == 0x7u);
}

void checkFifo() {
  Wire<1> clk(0), rst(0), in_valid(0), in_ready(0), out_valid(0), out_ready(0);
  Wire<8> in_data(0), out_data(0);
  pyc_fifo<8, 4> fifo(clk, rst, in_valid, in_ready, in_data, out_valid, out_ready, out_data);

  in_valid = Wire<1>(1);
  in_data = Wire<8>(0x5a);
  clk = Wire<1>(1);
  fifo.tick_compute(true);
  const mask_t pushed = fifo.tick_commit();
  fifo.eval();
  assert((pushed & pyc_fifo<8, 4>::kOutValidChanged) != 0);
  assert(out_valid.toBool());
}

void checkByteMem() {
  Wire<1> clk(0);
  Wire<4> raddr(0), waddr(0);
  Wire<8> rdata(0), wdata(0xab);
  Wire<1> wvalid(1);
  Wire<1> wstrb(1);
  pyc_byte_mem<4, 8, 16> mem(clk, raddr, rdata, wvalid, waddr, wdata, wstrb);

  clk = Wire<1>(1);
  mem.tick_compute(true);
  (void)mem.tick_commit();
  mem.eval();
  assert(rdata.value() == 0xabu);
}

void checkCdc() {
  Wire<1> clk(0), rst(0);
  Wire<8> in(0x3c), out(0);
  pyc_cdc_sync<8, 1> cdc(clk, rst, in, out);

  clk = Wire<1>(1);
  cdc.tick_compute(true);
  assert(cdc.tick_commit() == ComponentBase::kChanged);
  assert(out.value() == 0x3cu);
}

} // namespace

int main() {
  checkReg();
  checkFifo();
  checkByteMem();
  checkCdc();
  return 0;
}
