#ifndef MODEL_HEADER
#error "compile with -DMODEL_HEADER=generated_model.hpp"
#endif

#include MODEL_HEADER

#include <cstdint>

int main() {
  pyc::gen::observation_demand_comb_module dut;
  pyc::cpp::ProbeRegistry probes;
  dut.pyc_register_probes(probes, "dut");

  const auto *ready = probes.findByPath("dut:ready_state");
  const auto *alias = probes.findByPath("dut:ready_alias");
  const auto *out = probes.findByPath("dut:out");
  if (!ready || !alias || !out)
    return 1;

  dut.a = pyc::cpp::Wire<8>(0xa5);
  dut.b = pyc::cpp::Wire<8>(0x3c);
  dut.comb();
  if (ready->readU64() != UINT64_C(0x24) || alias->readU64() != UINT64_C(0x24))
    return 2;
  if (out->readU64() != ready->readU64())
    return 3;
  return 0;
}
