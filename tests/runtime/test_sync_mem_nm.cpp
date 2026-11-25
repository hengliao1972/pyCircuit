#include "pyc_sync_mem.hpp"
#include <cstdio>
int main() {
  using pyc::cpp::Wire;
  // 2R3W, 32 entries × 64b
  Wire<1> clk{0}, ren0{1}, ren1{1};
  Wire<5> ra0{0}, ra1{0}, wa0{0}, wa1{0}, wa2{0};
  Wire<1> wv0{0}, wv1{0}, wv2{0};
  Wire<64> rd0{0}, rd1{0}, wd0{0}, wd1{0}, wd2{0};
  Wire<8> st0{0}, st1{0}, st2{0};
  using M = pyc::cpp::pycSyncMemNM<5, 64, 32, 2, 3>;
  const pyc::cpp::Wire<1> *rens[] = {&ren0, &ren1};
  const pyc::cpp::Wire<5> *raddrs[] = {&ra0, &ra1};
  pyc::cpp::Wire<64> *rdatas[] = {&rd0, &rd1};
  const pyc::cpp::Wire<1> *wvs[] = {&wv0, &wv1, &wv2};
  const pyc::cpp::Wire<5> *was[] = {&wa0, &wa1, &wa2};
  const pyc::cpp::Wire<64> *wds[] = {&wd0, &wd1, &wd2};
  const pyc::cpp::Wire<8> *sts[] = {&st0, &st1, &st2};
  M m(clk, rens, raddrs, rdatas, wvs, was, wds, sts);
  // 沿1：三写同址 addr7，port0=AA port1=BB port2=CC → port2 胜；双读口同沿转发
  wv0=Wire<1>{1}; wa0=Wire<5>{7}; wd0=Wire<64>{0xAA}; st0=Wire<8>{0xFF};
  wv1=Wire<1>{1}; wa1=Wire<5>{7}; wd1=Wire<64>{0xBB}; st1=Wire<8>{0xFF};
  wv2=Wire<1>{1}; wa2=Wire<5>{7}; wd2=Wire<64>{0xCC}; st2=Wire<8>{0xFF};
  ra0=Wire<5>{7}; ra1=Wire<5>{7};
  clk=Wire<1>{1}; m.tick_sample();
  auto c = m.tick_commit();
  if (rd0.value()!=0xCC || rd1.value()!=0xCC) { printf("FAIL fwd3: %lx %lx\n",(unsigned long)rd0.value(),(unsigned long)rd1.value()); return 1; }
  if (!(c & 3)) { puts("FAIL change bits"); return 1; }
  // 沿2：port0 全字写 addr9=0xDD..DD，port1 strb 只开 byte1 写 0x11 → 0xDD..D11D（byte1 被 port1 覆盖）
  clk=Wire<1>{0}; m.tick_sample(); m.tick_commit();
  wv0=Wire<1>{1}; wa0=Wire<5>{9}; wd0=Wire<64>{0xDDDDDDDDDDDDDDDDull}; st0=Wire<8>{0xFF};
  wv1=Wire<1>{1}; wa1=Wire<5>{9}; wd1=Wire<64>{0x1111111111111111ull}; st1=Wire<8>{0x02};
  wv2=Wire<1>{0};
  ra0=Wire<5>{9}; ra1=Wire<5>{9};
  clk=Wire<1>{1}; m.tick_sample(); m.tick_commit();
  if (rd0.value()!=0xDDDDDDDDDDDD11DDull) { printf("FAIL strb: %lx\n",(unsigned long)rd0.value()); return 1; }
  // 沿3：双读口读不同地址
  clk=Wire<1>{0}; m.tick_sample(); m.tick_commit();
  wv0=Wire<1>{0}; wv1=Wire<1>{0};
  ra0=Wire<5>{7}; ra1=Wire<5>{9};
  clk=Wire<1>{1}; m.tick_sample(); m.tick_commit();
  if (rd0.value()!=0xCC || rd1.value()!=0xDDDDDDDDDDDD11DDull) { printf("FAIL rd2: %lx %lx\n",(unsigned long)rd0.value(),(unsigned long)rd1.value()); return 1; }
  // read_mode=old: read-during-write must return the PRE-write data.
  using O = pyc::cpp::pycSyncMemNM<5, 64, 32, 1, 1, true>;
  Wire<1> oren{1}, owv{0}; Wire<5> ora{0}, owa{0};
  Wire<64> ord{0}, owd{0}; Wire<8> ost{0};
  const pyc::cpp::Wire<1> *orens[] = {&oren};
  const pyc::cpp::Wire<5> *oraddrs[] = {&ora};
  pyc::cpp::Wire<64> *ordatas[] = {&ord};
  const pyc::cpp::Wire<1> *owvs[] = {&owv};
  const pyc::cpp::Wire<5> *owaddrs[] = {&owa};
  const pyc::cpp::Wire<64> *owdatas[] = {&owd};
  const pyc::cpp::Wire<8> *owstrbs[] = {&ost};
  O om(clk, orens, oraddrs, ordatas, owvs, owaddrs, owdatas, owstrbs);
  clk=Wire<1>{0}; om.tick_sample(); om.tick_commit();
  owv=Wire<1>{1}; owa=Wire<5>{3}; owd=Wire<64>{0x55}; ost=Wire<8>{0xFF}; ora=Wire<5>{3};
  clk=Wire<1>{1}; om.tick_sample(); om.tick_commit();
  if (ord.value()!=0) { printf("FAIL old-rdw: read %lx, want pre-write 0\n",(unsigned long)ord.value()); return 1; }
  clk=Wire<1>{0}; om.tick_sample(); om.tick_commit(); ora=Wire<5>{3};
  clk=Wire<1>{1}; om.tick_sample(); om.tick_commit();
  if (ord.value()!=0x55) { printf("FAIL old-next: read %lx, want 0x55\n",(unsigned long)ord.value()); return 1; }
  puts("SYNC_MEM_NM_TEST_OK");
  return 0;
}
