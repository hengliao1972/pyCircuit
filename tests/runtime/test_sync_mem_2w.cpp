#include "pyc_sync_mem.hpp"
#include <cstdio>
int main() {
  using pyc::cpp::Wire;
  Wire<1> clk{0}, ren{1}, wv0{0}, wv1{0};
  Wire<5> raddr{0}, wa0{0}, wa1{0};
  Wire<64> rdata{0}, wd0{0}, wd1{0};
  Wire<8> st0{0}, st1{0};
  using M = pyc::cpp::pyc_sync_mem_2w<5,64,32>;
  M m(clk,ren,raddr,rdata,wv0,wa0,wd0,st0,wv1,wa1,wd1,st1);
  // 沿1：同拍同址 addr3：port0=0xAAAAAAAAAAAAAAAA port1=0xBBBBBBBBBBBBBBBB → port1 胜
  wv0=Wire<1>{1}; wa0=Wire<5>{3}; wd0=Wire<64>{0xAAAAAAAAAAAAAAAAull}; st0=Wire<8>{0xFF};
  wv1=Wire<1>{1}; wa1=Wire<5>{3}; wd1=Wire<64>{0xBBBBBBBBBBBBBBBBull}; st1=Wire<8>{0xFF};
  raddr=Wire<5>{3};
  clk=Wire<1>{1}; m.tick_compute();
  if (auto c=m.tick_commit(); !(c & M::kReadDataChanged)) { puts("FAIL no-change-flag"); return 1; }
  if (rdata.value()!=0xBBBBBBBBBBBBBBBBull) { printf("FAIL conflict fwd: %lx\n",(unsigned long)rdata.value()); return 1; }
  clk=Wire<1>{0}; m.tick_compute(); m.tick_commit();
  clk=Wire<1>{1}; wv0=Wire<1>{0}; wv1=Wire<1>{0};
  m.tick_compute(); m.tick_commit();
  if (rdata.value()!=0xBBBBBBBBBBBBBBBBull) { printf("FAIL conflict: %lx\n",(unsigned long)rdata.value()); return 1; }
  // 沿2：port0 全字 0xCC..CC 到 addr5；port1 strb 只开 byte0 写 0x11 → 0xCCCCCCCCCCCCCC11
  clk=Wire<1>{0}; m.tick_compute(); m.tick_commit();
  wv0=Wire<1>{1}; wa0=Wire<5>{5}; wd0=Wire<64>{0xCCCCCCCCCCCCCCCCull}; st0=Wire<8>{0xFF};
  wv1=Wire<1>{1}; wa1=Wire<5>{5}; wd1=Wire<64>{0x1111111111111111ull}; st1=Wire<8>{0x01}; raddr=Wire<5>{5};
  clk=Wire<1>{1}; m.tick_compute(); m.tick_commit();  // write-forward：同沿读到 0xCCCCCCCCCCCCCC11
  wv0=Wire<1>{0}; wv1=Wire<1>{0};
  clk=Wire<1>{0}; m.tick_compute(); m.tick_commit();
  clk=Wire<1>{1}; m.tick_compute(); m.tick_commit();
  if (rdata.value()!=0xCCCCCCCCCCCCCC11ull) { printf("FAIL strb: %lx\n",(unsigned long)rdata.value()); return 1; }
  puts("SYNC_MEM_2W_TEST_OK");
  return 0;
}
