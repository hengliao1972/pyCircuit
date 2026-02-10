#include <array>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <optional>
#include <string>

#include <pyc/cpp/pyc_konata.hpp>
#include <pyc/cpp/pyc_tb.hpp>

#include "janus_tmu_pyc_gen.hpp"

using pyc::cpp::Testbench;
using pyc::cpp::Wire;

namespace {

constexpr int kNodes = 8;
constexpr int kAddrBits = 20;
constexpr int kTagBits = 8;
constexpr int kWords = 32;

using DataWord = Wire<64>;
using DataLine = std::array<DataWord, kWords>;

struct NodePorts {
  Wire<1> *req_valid = nullptr;
  Wire<1> *req_write = nullptr;
  Wire<kAddrBits> *req_addr = nullptr;
  Wire<kTagBits> *req_tag = nullptr;
  std::array<DataWord *, kWords> req_data{};
  Wire<1> *req_ready = nullptr;
  Wire<1> *resp_ready = nullptr;
  Wire<1> *resp_valid = nullptr;
  Wire<kTagBits> *resp_tag = nullptr;
  std::array<DataWord *, kWords> resp_data{};
  Wire<1> *resp_is_write = nullptr;
};

static bool envFlag(const char *name) {
  const char *v = std::getenv(name);
  if (!v)
    return false;
  return !(v[0] == '0' && v[1] == '\0');
}

static std::uint32_t makeAddr(std::uint32_t index, std::uint32_t pipe, std::uint32_t offset = 0) {
  return (index << 11) | (pipe << 8) | (offset & 0xFFu);
}

static DataLine makeData(std::uint32_t seed) {
  DataLine out{};
  for (unsigned i = 0; i < kWords; i++) {
    std::uint64_t word = (static_cast<std::uint64_t>(seed) << 32) | i;
    out[i] = DataWord(word);
  }
  return out;
}

static void zeroReq(NodePorts &n) {
  *n.req_valid = Wire<1>(0);
  *n.req_write = Wire<1>(0);
  *n.req_addr = Wire<kAddrBits>(0);
  *n.req_tag = Wire<kTagBits>(0);
  for (auto *w : n.req_data)
    *w = DataWord(0);
}

static void setRespReady(NodePorts &n, bool ready) { *n.resp_ready = Wire<1>(ready ? 1u : 0u); }

static void sendReq(Testbench<pyc::gen::janus_tmu_pyc> &tb,
                    NodePorts &n,
                    std::uint64_t &cycle,
                    int node_id,
                    bool write,
                    std::uint32_t addr,
                    std::uint8_t tag,
                    const DataLine &data,
                    std::ofstream &trace) {
  *n.req_write = Wire<1>(write ? 1u : 0u);
  *n.req_addr = Wire<kAddrBits>(addr);
  *n.req_tag = Wire<kTagBits>(tag);
  for (unsigned i = 0; i < kWords; i++)
    *n.req_data[i] = data[i];
  *n.req_valid = Wire<1>(1);
  while (true) {
    tb.runCycles(1);
    cycle++;
    if (n.req_ready->toBool()) {
      trace << cycle << ",accept"
            << "," << node_id << "," << unsigned(tag) << "," << (write ? 1 : 0) << ",0x" << std::hex << addr
            << std::dec << ",0x"
            << std::hex << data[0].value() << std::dec << "\n";
      break;
    }
  }
  *n.req_valid = Wire<1>(0);
}

static void waitResp(Testbench<pyc::gen::janus_tmu_pyc> &tb,
                     NodePorts &n,
                     std::uint64_t &cycle,
                     int node_id,
                     std::uint8_t tag,
                     bool expect_write,
                     const DataLine &expect_data,
                     std::ofstream &trace) {
  for (std::uint64_t i = 0; i < 2000; i++) {
    tb.runCycles(1);
    cycle++;
    if (!n.resp_valid->toBool())
      continue;
    if (n.resp_tag->value() != tag) {
      std::cerr << "FAIL: tag mismatch. got=" << std::hex << n.resp_tag->value() << " exp=" << unsigned(tag) << std::dec
                << "\n";
      std::exit(1);
    }
    if (n.resp_is_write->toBool() != expect_write) {
      std::cerr << "FAIL: resp_is_write mismatch\n";
      std::exit(1);
    }
    for (unsigned i = 0; i < kWords; i++) {
      if (n.resp_data[i]->value() != expect_data[i].value()) {
        std::cerr << "FAIL: resp_data mismatch\n";
        std::exit(1);
      }
    }
    trace << cycle << ",resp"
          << "," << node_id << "," << unsigned(tag) << "," << (expect_write ? 1 : 0) << ",0x" << std::hex
          << n.resp_data[0]->value()
          << std::dec << "\n";
    return;
  }
  std::cerr << "FAIL: timeout waiting for response tag=0x" << std::hex << unsigned(tag) << std::dec << "\n";
  std::exit(1);
}

} // namespace

int main() {
  pyc::gen::janus_tmu_pyc dut{};
  Testbench<pyc::gen::janus_tmu_pyc> tb(dut);

  const bool trace_log = envFlag("PYC_TRACE");
  const bool trace_vcd = envFlag("PYC_VCD");

  std::filesystem::path out_dir{};
  if (trace_log || trace_vcd) {
    const char *trace_dir_env = std::getenv("PYC_TRACE_DIR");
    out_dir = trace_dir_env ? std::filesystem::path(trace_dir_env) : std::filesystem::path("janus/generated/janus_tmu_pyc");
    std::filesystem::create_directories(out_dir);
  }

  if (trace_log) {
    tb.enableLog((out_dir / "tb_janus_tmu_pyc_cpp.log").string());
  }

  if (trace_vcd) {
    tb.enableVcd((out_dir / "tb_janus_tmu_pyc_cpp.vcd").string(), /*top=*/"tb_janus_tmu_pyc_cpp");
    tb.vcdTrace(dut.clk, "clk");
    tb.vcdTrace(dut.rst, "rst");
    tb.vcdTrace(dut.n0_req_valid, "n0_req_valid");
    tb.vcdTrace(dut.n0_req_ready, "n0_req_ready");
    tb.vcdTrace(dut.n0_resp_valid, "n0_resp_valid");
    tb.vcdTrace(dut.n0_resp_is_write, "n0_resp_is_write");
    tb.vcdTrace(dut.n0_resp_tag, "n0_resp_tag");
    tb.vcdTrace(dut.n0_req_data_w0, "n0_req_data_w0");
    tb.vcdTrace(dut.n0_resp_data_w0, "n0_resp_data_w0");
    tb.vcdTrace(dut.dbg_req_cw_v0, "dbg_req_cw_v0");
    tb.vcdTrace(dut.dbg_req_cc_v0, "dbg_req_cc_v0");
    tb.vcdTrace(dut.dbg_rsp_cw_v0, "dbg_rsp_cw_v0");
    tb.vcdTrace(dut.dbg_rsp_cc_v0, "dbg_rsp_cc_v0");
    tb.vcdTrace(dut.dbg_req_cw_v1, "dbg_req_cw_v1");
    tb.vcdTrace(dut.dbg_req_cc_v1, "dbg_req_cc_v1");
    tb.vcdTrace(dut.dbg_rsp_cw_v1, "dbg_rsp_cw_v1");
    tb.vcdTrace(dut.dbg_rsp_cc_v1, "dbg_rsp_cc_v1");
    tb.vcdTrace(dut.dbg_req_cw_v2, "dbg_req_cw_v2");
    tb.vcdTrace(dut.dbg_req_cc_v2, "dbg_req_cc_v2");
    tb.vcdTrace(dut.dbg_rsp_cw_v2, "dbg_rsp_cw_v2");
    tb.vcdTrace(dut.dbg_rsp_cc_v2, "dbg_rsp_cc_v2");
    tb.vcdTrace(dut.dbg_req_cw_v3, "dbg_req_cw_v3");
    tb.vcdTrace(dut.dbg_req_cc_v3, "dbg_req_cc_v3");
    tb.vcdTrace(dut.dbg_rsp_cw_v3, "dbg_rsp_cw_v3");
    tb.vcdTrace(dut.dbg_rsp_cc_v3, "dbg_rsp_cc_v3");
    tb.vcdTrace(dut.dbg_req_cw_v4, "dbg_req_cw_v4");
    tb.vcdTrace(dut.dbg_req_cc_v4, "dbg_req_cc_v4");
    tb.vcdTrace(dut.dbg_rsp_cw_v4, "dbg_rsp_cw_v4");
    tb.vcdTrace(dut.dbg_rsp_cc_v4, "dbg_rsp_cc_v4");
    tb.vcdTrace(dut.dbg_req_cw_v5, "dbg_req_cw_v5");
    tb.vcdTrace(dut.dbg_req_cc_v5, "dbg_req_cc_v5");
    tb.vcdTrace(dut.dbg_rsp_cw_v5, "dbg_rsp_cw_v5");
    tb.vcdTrace(dut.dbg_rsp_cc_v5, "dbg_rsp_cc_v5");
    tb.vcdTrace(dut.dbg_req_cw_v6, "dbg_req_cw_v6");
    tb.vcdTrace(dut.dbg_req_cc_v6, "dbg_req_cc_v6");
    tb.vcdTrace(dut.dbg_rsp_cw_v6, "dbg_rsp_cw_v6");
    tb.vcdTrace(dut.dbg_rsp_cc_v6, "dbg_rsp_cc_v6");
    tb.vcdTrace(dut.dbg_req_cw_v7, "dbg_req_cw_v7");
    tb.vcdTrace(dut.dbg_req_cc_v7, "dbg_req_cc_v7");
    tb.vcdTrace(dut.dbg_rsp_cw_v7, "dbg_rsp_cw_v7");
    tb.vcdTrace(dut.dbg_rsp_cc_v7, "dbg_rsp_cc_v7");
  }

  tb.addClock(dut.clk, /*halfPeriodSteps=*/1);
  tb.reset(dut.rst, /*cyclesAsserted=*/2, /*cyclesDeasserted=*/1);

  std::ofstream trace;
  if (trace_log) {
    trace.open(out_dir / "tmu_trace.csv", std::ios::out | std::ios::trunc);
    trace << "cycle,event,node,tag,write,addr_or_word0,data_word0\n";
  }

  std::array<NodePorts, kNodes> nodes = {{
      {&dut.n0_req_valid, &dut.n0_req_write, &dut.n0_req_addr, &dut.n0_req_tag,
       {&dut.n0_req_data_w0, &dut.n0_req_data_w1, &dut.n0_req_data_w2, &dut.n0_req_data_w3, &dut.n0_req_data_w4, &dut.n0_req_data_w5, &dut.n0_req_data_w6, &dut.n0_req_data_w7, &dut.n0_req_data_w8, &dut.n0_req_data_w9, &dut.n0_req_data_w10, &dut.n0_req_data_w11, &dut.n0_req_data_w12, &dut.n0_req_data_w13, &dut.n0_req_data_w14, &dut.n0_req_data_w15, &dut.n0_req_data_w16, &dut.n0_req_data_w17, &dut.n0_req_data_w18, &dut.n0_req_data_w19, &dut.n0_req_data_w20, &dut.n0_req_data_w21, &dut.n0_req_data_w22, &dut.n0_req_data_w23, &dut.n0_req_data_w24, &dut.n0_req_data_w25, &dut.n0_req_data_w26, &dut.n0_req_data_w27, &dut.n0_req_data_w28, &dut.n0_req_data_w29, &dut.n0_req_data_w30, &dut.n0_req_data_w31}, &dut.n0_req_ready, &dut.n0_resp_ready, &dut.n0_resp_valid, &dut.n0_resp_tag,
       {&dut.n0_resp_data_w0, &dut.n0_resp_data_w1, &dut.n0_resp_data_w2, &dut.n0_resp_data_w3, &dut.n0_resp_data_w4, &dut.n0_resp_data_w5, &dut.n0_resp_data_w6, &dut.n0_resp_data_w7, &dut.n0_resp_data_w8, &dut.n0_resp_data_w9, &dut.n0_resp_data_w10, &dut.n0_resp_data_w11, &dut.n0_resp_data_w12, &dut.n0_resp_data_w13, &dut.n0_resp_data_w14, &dut.n0_resp_data_w15, &dut.n0_resp_data_w16, &dut.n0_resp_data_w17, &dut.n0_resp_data_w18, &dut.n0_resp_data_w19, &dut.n0_resp_data_w20, &dut.n0_resp_data_w21, &dut.n0_resp_data_w22, &dut.n0_resp_data_w23, &dut.n0_resp_data_w24, &dut.n0_resp_data_w25, &dut.n0_resp_data_w26, &dut.n0_resp_data_w27, &dut.n0_resp_data_w28, &dut.n0_resp_data_w29, &dut.n0_resp_data_w30, &dut.n0_resp_data_w31}, &dut.n0_resp_is_write},
      {&dut.n1_req_valid, &dut.n1_req_write, &dut.n1_req_addr, &dut.n1_req_tag,
       {&dut.n1_req_data_w0, &dut.n1_req_data_w1, &dut.n1_req_data_w2, &dut.n1_req_data_w3, &dut.n1_req_data_w4, &dut.n1_req_data_w5, &dut.n1_req_data_w6, &dut.n1_req_data_w7, &dut.n1_req_data_w8, &dut.n1_req_data_w9, &dut.n1_req_data_w10, &dut.n1_req_data_w11, &dut.n1_req_data_w12, &dut.n1_req_data_w13, &dut.n1_req_data_w14, &dut.n1_req_data_w15, &dut.n1_req_data_w16, &dut.n1_req_data_w17, &dut.n1_req_data_w18, &dut.n1_req_data_w19, &dut.n1_req_data_w20, &dut.n1_req_data_w21, &dut.n1_req_data_w22, &dut.n1_req_data_w23, &dut.n1_req_data_w24, &dut.n1_req_data_w25, &dut.n1_req_data_w26, &dut.n1_req_data_w27, &dut.n1_req_data_w28, &dut.n1_req_data_w29, &dut.n1_req_data_w30, &dut.n1_req_data_w31}, &dut.n1_req_ready, &dut.n1_resp_ready, &dut.n1_resp_valid, &dut.n1_resp_tag,
       {&dut.n1_resp_data_w0, &dut.n1_resp_data_w1, &dut.n1_resp_data_w2, &dut.n1_resp_data_w3, &dut.n1_resp_data_w4, &dut.n1_resp_data_w5, &dut.n1_resp_data_w6, &dut.n1_resp_data_w7, &dut.n1_resp_data_w8, &dut.n1_resp_data_w9, &dut.n1_resp_data_w10, &dut.n1_resp_data_w11, &dut.n1_resp_data_w12, &dut.n1_resp_data_w13, &dut.n1_resp_data_w14, &dut.n1_resp_data_w15, &dut.n1_resp_data_w16, &dut.n1_resp_data_w17, &dut.n1_resp_data_w18, &dut.n1_resp_data_w19, &dut.n1_resp_data_w20, &dut.n1_resp_data_w21, &dut.n1_resp_data_w22, &dut.n1_resp_data_w23, &dut.n1_resp_data_w24, &dut.n1_resp_data_w25, &dut.n1_resp_data_w26, &dut.n1_resp_data_w27, &dut.n1_resp_data_w28, &dut.n1_resp_data_w29, &dut.n1_resp_data_w30, &dut.n1_resp_data_w31}, &dut.n1_resp_is_write},
      {&dut.n2_req_valid, &dut.n2_req_write, &dut.n2_req_addr, &dut.n2_req_tag,
       {&dut.n2_req_data_w0, &dut.n2_req_data_w1, &dut.n2_req_data_w2, &dut.n2_req_data_w3, &dut.n2_req_data_w4, &dut.n2_req_data_w5, &dut.n2_req_data_w6, &dut.n2_req_data_w7, &dut.n2_req_data_w8, &dut.n2_req_data_w9, &dut.n2_req_data_w10, &dut.n2_req_data_w11, &dut.n2_req_data_w12, &dut.n2_req_data_w13, &dut.n2_req_data_w14, &dut.n2_req_data_w15, &dut.n2_req_data_w16, &dut.n2_req_data_w17, &dut.n2_req_data_w18, &dut.n2_req_data_w19, &dut.n2_req_data_w20, &dut.n2_req_data_w21, &dut.n2_req_data_w22, &dut.n2_req_data_w23, &dut.n2_req_data_w24, &dut.n2_req_data_w25, &dut.n2_req_data_w26, &dut.n2_req_data_w27, &dut.n2_req_data_w28, &dut.n2_req_data_w29, &dut.n2_req_data_w30, &dut.n2_req_data_w31}, &dut.n2_req_ready, &dut.n2_resp_ready, &dut.n2_resp_valid, &dut.n2_resp_tag,
       {&dut.n2_resp_data_w0, &dut.n2_resp_data_w1, &dut.n2_resp_data_w2, &dut.n2_resp_data_w3, &dut.n2_resp_data_w4, &dut.n2_resp_data_w5, &dut.n2_resp_data_w6, &dut.n2_resp_data_w7, &dut.n2_resp_data_w8, &dut.n2_resp_data_w9, &dut.n2_resp_data_w10, &dut.n2_resp_data_w11, &dut.n2_resp_data_w12, &dut.n2_resp_data_w13, &dut.n2_resp_data_w14, &dut.n2_resp_data_w15, &dut.n2_resp_data_w16, &dut.n2_resp_data_w17, &dut.n2_resp_data_w18, &dut.n2_resp_data_w19, &dut.n2_resp_data_w20, &dut.n2_resp_data_w21, &dut.n2_resp_data_w22, &dut.n2_resp_data_w23, &dut.n2_resp_data_w24, &dut.n2_resp_data_w25, &dut.n2_resp_data_w26, &dut.n2_resp_data_w27, &dut.n2_resp_data_w28, &dut.n2_resp_data_w29, &dut.n2_resp_data_w30, &dut.n2_resp_data_w31}, &dut.n2_resp_is_write},
      {&dut.n3_req_valid, &dut.n3_req_write, &dut.n3_req_addr, &dut.n3_req_tag,
       {&dut.n3_req_data_w0, &dut.n3_req_data_w1, &dut.n3_req_data_w2, &dut.n3_req_data_w3, &dut.n3_req_data_w4, &dut.n3_req_data_w5, &dut.n3_req_data_w6, &dut.n3_req_data_w7, &dut.n3_req_data_w8, &dut.n3_req_data_w9, &dut.n3_req_data_w10, &dut.n3_req_data_w11, &dut.n3_req_data_w12, &dut.n3_req_data_w13, &dut.n3_req_data_w14, &dut.n3_req_data_w15, &dut.n3_req_data_w16, &dut.n3_req_data_w17, &dut.n3_req_data_w18, &dut.n3_req_data_w19, &dut.n3_req_data_w20, &dut.n3_req_data_w21, &dut.n3_req_data_w22, &dut.n3_req_data_w23, &dut.n3_req_data_w24, &dut.n3_req_data_w25, &dut.n3_req_data_w26, &dut.n3_req_data_w27, &dut.n3_req_data_w28, &dut.n3_req_data_w29, &dut.n3_req_data_w30, &dut.n3_req_data_w31}, &dut.n3_req_ready, &dut.n3_resp_ready, &dut.n3_resp_valid, &dut.n3_resp_tag,
       {&dut.n3_resp_data_w0, &dut.n3_resp_data_w1, &dut.n3_resp_data_w2, &dut.n3_resp_data_w3, &dut.n3_resp_data_w4, &dut.n3_resp_data_w5, &dut.n3_resp_data_w6, &dut.n3_resp_data_w7, &dut.n3_resp_data_w8, &dut.n3_resp_data_w9, &dut.n3_resp_data_w10, &dut.n3_resp_data_w11, &dut.n3_resp_data_w12, &dut.n3_resp_data_w13, &dut.n3_resp_data_w14, &dut.n3_resp_data_w15, &dut.n3_resp_data_w16, &dut.n3_resp_data_w17, &dut.n3_resp_data_w18, &dut.n3_resp_data_w19, &dut.n3_resp_data_w20, &dut.n3_resp_data_w21, &dut.n3_resp_data_w22, &dut.n3_resp_data_w23, &dut.n3_resp_data_w24, &dut.n3_resp_data_w25, &dut.n3_resp_data_w26, &dut.n3_resp_data_w27, &dut.n3_resp_data_w28, &dut.n3_resp_data_w29, &dut.n3_resp_data_w30, &dut.n3_resp_data_w31}, &dut.n3_resp_is_write},
      {&dut.n4_req_valid, &dut.n4_req_write, &dut.n4_req_addr, &dut.n4_req_tag,
       {&dut.n4_req_data_w0, &dut.n4_req_data_w1, &dut.n4_req_data_w2, &dut.n4_req_data_w3, &dut.n4_req_data_w4, &dut.n4_req_data_w5, &dut.n4_req_data_w6, &dut.n4_req_data_w7, &dut.n4_req_data_w8, &dut.n4_req_data_w9, &dut.n4_req_data_w10, &dut.n4_req_data_w11, &dut.n4_req_data_w12, &dut.n4_req_data_w13, &dut.n4_req_data_w14, &dut.n4_req_data_w15, &dut.n4_req_data_w16, &dut.n4_req_data_w17, &dut.n4_req_data_w18, &dut.n4_req_data_w19, &dut.n4_req_data_w20, &dut.n4_req_data_w21, &dut.n4_req_data_w22, &dut.n4_req_data_w23, &dut.n4_req_data_w24, &dut.n4_req_data_w25, &dut.n4_req_data_w26, &dut.n4_req_data_w27, &dut.n4_req_data_w28, &dut.n4_req_data_w29, &dut.n4_req_data_w30, &dut.n4_req_data_w31}, &dut.n4_req_ready, &dut.n4_resp_ready, &dut.n4_resp_valid, &dut.n4_resp_tag,
       {&dut.n4_resp_data_w0, &dut.n4_resp_data_w1, &dut.n4_resp_data_w2, &dut.n4_resp_data_w3, &dut.n4_resp_data_w4, &dut.n4_resp_data_w5, &dut.n4_resp_data_w6, &dut.n4_resp_data_w7, &dut.n4_resp_data_w8, &dut.n4_resp_data_w9, &dut.n4_resp_data_w10, &dut.n4_resp_data_w11, &dut.n4_resp_data_w12, &dut.n4_resp_data_w13, &dut.n4_resp_data_w14, &dut.n4_resp_data_w15, &dut.n4_resp_data_w16, &dut.n4_resp_data_w17, &dut.n4_resp_data_w18, &dut.n4_resp_data_w19, &dut.n4_resp_data_w20, &dut.n4_resp_data_w21, &dut.n4_resp_data_w22, &dut.n4_resp_data_w23, &dut.n4_resp_data_w24, &dut.n4_resp_data_w25, &dut.n4_resp_data_w26, &dut.n4_resp_data_w27, &dut.n4_resp_data_w28, &dut.n4_resp_data_w29, &dut.n4_resp_data_w30, &dut.n4_resp_data_w31}, &dut.n4_resp_is_write},
      {&dut.n5_req_valid, &dut.n5_req_write, &dut.n5_req_addr, &dut.n5_req_tag,
       {&dut.n5_req_data_w0, &dut.n5_req_data_w1, &dut.n5_req_data_w2, &dut.n5_req_data_w3, &dut.n5_req_data_w4, &dut.n5_req_data_w5, &dut.n5_req_data_w6, &dut.n5_req_data_w7, &dut.n5_req_data_w8, &dut.n5_req_data_w9, &dut.n5_req_data_w10, &dut.n5_req_data_w11, &dut.n5_req_data_w12, &dut.n5_req_data_w13, &dut.n5_req_data_w14, &dut.n5_req_data_w15, &dut.n5_req_data_w16, &dut.n5_req_data_w17, &dut.n5_req_data_w18, &dut.n5_req_data_w19, &dut.n5_req_data_w20, &dut.n5_req_data_w21, &dut.n5_req_data_w22, &dut.n5_req_data_w23, &dut.n5_req_data_w24, &dut.n5_req_data_w25, &dut.n5_req_data_w26, &dut.n5_req_data_w27, &dut.n5_req_data_w28, &dut.n5_req_data_w29, &dut.n5_req_data_w30, &dut.n5_req_data_w31}, &dut.n5_req_ready, &dut.n5_resp_ready, &dut.n5_resp_valid, &dut.n5_resp_tag,
       {&dut.n5_resp_data_w0, &dut.n5_resp_data_w1, &dut.n5_resp_data_w2, &dut.n5_resp_data_w3, &dut.n5_resp_data_w4, &dut.n5_resp_data_w5, &dut.n5_resp_data_w6, &dut.n5_resp_data_w7, &dut.n5_resp_data_w8, &dut.n5_resp_data_w9, &dut.n5_resp_data_w10, &dut.n5_resp_data_w11, &dut.n5_resp_data_w12, &dut.n5_resp_data_w13, &dut.n5_resp_data_w14, &dut.n5_resp_data_w15, &dut.n5_resp_data_w16, &dut.n5_resp_data_w17, &dut.n5_resp_data_w18, &dut.n5_resp_data_w19, &dut.n5_resp_data_w20, &dut.n5_resp_data_w21, &dut.n5_resp_data_w22, &dut.n5_resp_data_w23, &dut.n5_resp_data_w24, &dut.n5_resp_data_w25, &dut.n5_resp_data_w26, &dut.n5_resp_data_w27, &dut.n5_resp_data_w28, &dut.n5_resp_data_w29, &dut.n5_resp_data_w30, &dut.n5_resp_data_w31}, &dut.n5_resp_is_write},
      {&dut.n6_req_valid, &dut.n6_req_write, &dut.n6_req_addr, &dut.n6_req_tag,
       {&dut.n6_req_data_w0, &dut.n6_req_data_w1, &dut.n6_req_data_w2, &dut.n6_req_data_w3, &dut.n6_req_data_w4, &dut.n6_req_data_w5, &dut.n6_req_data_w6, &dut.n6_req_data_w7, &dut.n6_req_data_w8, &dut.n6_req_data_w9, &dut.n6_req_data_w10, &dut.n6_req_data_w11, &dut.n6_req_data_w12, &dut.n6_req_data_w13, &dut.n6_req_data_w14, &dut.n6_req_data_w15, &dut.n6_req_data_w16, &dut.n6_req_data_w17, &dut.n6_req_data_w18, &dut.n6_req_data_w19, &dut.n6_req_data_w20, &dut.n6_req_data_w21, &dut.n6_req_data_w22, &dut.n6_req_data_w23, &dut.n6_req_data_w24, &dut.n6_req_data_w25, &dut.n6_req_data_w26, &dut.n6_req_data_w27, &dut.n6_req_data_w28, &dut.n6_req_data_w29, &dut.n6_req_data_w30, &dut.n6_req_data_w31}, &dut.n6_req_ready, &dut.n6_resp_ready, &dut.n6_resp_valid, &dut.n6_resp_tag,
       {&dut.n6_resp_data_w0, &dut.n6_resp_data_w1, &dut.n6_resp_data_w2, &dut.n6_resp_data_w3, &dut.n6_resp_data_w4, &dut.n6_resp_data_w5, &dut.n6_resp_data_w6, &dut.n6_resp_data_w7, &dut.n6_resp_data_w8, &dut.n6_resp_data_w9, &dut.n6_resp_data_w10, &dut.n6_resp_data_w11, &dut.n6_resp_data_w12, &dut.n6_resp_data_w13, &dut.n6_resp_data_w14, &dut.n6_resp_data_w15, &dut.n6_resp_data_w16, &dut.n6_resp_data_w17, &dut.n6_resp_data_w18, &dut.n6_resp_data_w19, &dut.n6_resp_data_w20, &dut.n6_resp_data_w21, &dut.n6_resp_data_w22, &dut.n6_resp_data_w23, &dut.n6_resp_data_w24, &dut.n6_resp_data_w25, &dut.n6_resp_data_w26, &dut.n6_resp_data_w27, &dut.n6_resp_data_w28, &dut.n6_resp_data_w29, &dut.n6_resp_data_w30, &dut.n6_resp_data_w31}, &dut.n6_resp_is_write},
      {&dut.n7_req_valid, &dut.n7_req_write, &dut.n7_req_addr, &dut.n7_req_tag,
       {&dut.n7_req_data_w0, &dut.n7_req_data_w1, &dut.n7_req_data_w2, &dut.n7_req_data_w3, &dut.n7_req_data_w4, &dut.n7_req_data_w5, &dut.n7_req_data_w6, &dut.n7_req_data_w7, &dut.n7_req_data_w8, &dut.n7_req_data_w9, &dut.n7_req_data_w10, &dut.n7_req_data_w11, &dut.n7_req_data_w12, &dut.n7_req_data_w13, &dut.n7_req_data_w14, &dut.n7_req_data_w15, &dut.n7_req_data_w16, &dut.n7_req_data_w17, &dut.n7_req_data_w18, &dut.n7_req_data_w19, &dut.n7_req_data_w20, &dut.n7_req_data_w21, &dut.n7_req_data_w22, &dut.n7_req_data_w23, &dut.n7_req_data_w24, &dut.n7_req_data_w25, &dut.n7_req_data_w26, &dut.n7_req_data_w27, &dut.n7_req_data_w28, &dut.n7_req_data_w29, &dut.n7_req_data_w30, &dut.n7_req_data_w31}, &dut.n7_req_ready, &dut.n7_resp_ready, &dut.n7_resp_valid, &dut.n7_resp_tag,
       {&dut.n7_resp_data_w0, &dut.n7_resp_data_w1, &dut.n7_resp_data_w2, &dut.n7_resp_data_w3, &dut.n7_resp_data_w4, &dut.n7_resp_data_w5, &dut.n7_resp_data_w6, &dut.n7_resp_data_w7, &dut.n7_resp_data_w8, &dut.n7_resp_data_w9, &dut.n7_resp_data_w10, &dut.n7_resp_data_w11, &dut.n7_resp_data_w12, &dut.n7_resp_data_w13, &dut.n7_resp_data_w14, &dut.n7_resp_data_w15, &dut.n7_resp_data_w16, &dut.n7_resp_data_w17, &dut.n7_resp_data_w18, &dut.n7_resp_data_w19, &dut.n7_resp_data_w20, &dut.n7_resp_data_w21, &dut.n7_resp_data_w22, &dut.n7_resp_data_w23, &dut.n7_resp_data_w24, &dut.n7_resp_data_w25, &dut.n7_resp_data_w26, &dut.n7_resp_data_w27, &dut.n7_resp_data_w28, &dut.n7_resp_data_w29, &dut.n7_resp_data_w30, &dut.n7_resp_data_w31}, &dut.n7_resp_is_write},
  }};

  for (auto &n : nodes) {
    zeroReq(n);
    setRespReady(n, true);
  }

  std::uint64_t cycle = 0;

  for (int n = 0; n < kNodes; n++) {
    const auto addr = makeAddr(static_cast<std::uint32_t>(n), static_cast<std::uint32_t>(n));
    const auto data = makeData(static_cast<std::uint32_t>(n + 1));
    const std::uint8_t tag_w = static_cast<std::uint8_t>(n);
    const std::uint8_t tag_r = static_cast<std::uint8_t>(0x80 | n);

    sendReq(tb, nodes[n], cycle, n, true, addr, tag_w, data, trace);
    waitResp(tb, nodes[n], cycle, n, tag_w, true, data, trace);

    sendReq(tb, nodes[n], cycle, n, false, addr, tag_r, DataLine{}, trace);
    waitResp(tb, nodes[n], cycle, n, tag_r, false, data, trace);
  }

  // Cross-node: node0 writes to pipe2, then reads it back.
  {
    const auto addr = makeAddr(5, 2);
    const auto data = makeData(0xAA);
    sendReq(tb, nodes[0], cycle, 0, true, addr, 0x55, data, trace);
    waitResp(tb, nodes[0], cycle, 0, 0x55, true, data, trace);
    sendReq(tb, nodes[0], cycle, 0, false, addr, 0x56, DataLine{}, trace);
    waitResp(tb, nodes[0], cycle, 0, 0x56, false, data, trace);
  }

  // Ring traffic: each node accesses a non-local pipe to exercise ring flow.
  for (int n = 0; n < kNodes; n++) {
    const int dst_pipe = (n + 2) % kNodes;
    const auto addr = makeAddr(16 + n, static_cast<std::uint32_t>(dst_pipe));
    const auto data = makeData(0x100 + n);
    const std::uint8_t tag_w = static_cast<std::uint8_t>(0x20 + n);
    const std::uint8_t tag_r = static_cast<std::uint8_t>(0xA0 + n);

    sendReq(tb, nodes[n], cycle, n, true, addr, tag_w, data, trace);
    waitResp(tb, nodes[n], cycle, n, tag_w, true, data, trace);
    sendReq(tb, nodes[n], cycle, n, false, addr, tag_r, DataLine{}, trace);
    waitResp(tb, nodes[n], cycle, n, tag_r, false, data, trace);
  }

  std::cout << "PASS: TMU tests\n";
  return 0;
}
