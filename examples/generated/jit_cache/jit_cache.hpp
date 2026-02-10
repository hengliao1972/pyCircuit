// pyCircuit C++ emission (prototype)
#include <cstdlib>
#include <iostream>
#include <pyc/cpp/pyc_sim.hpp>

namespace pyc::gen {

struct JitCache {
  pyc::cpp::Wire<1> sys_clk{};
  pyc::cpp::Wire<1> sys_rst{};
  pyc::cpp::Wire<1> req_valid{};
  pyc::cpp::Wire<32> req_addr{};
  pyc::cpp::Wire<1> rsp_ready{};
  pyc::cpp::Wire<1> req_ready{};
  pyc::cpp::Wire<1> rsp_valid{};
  pyc::cpp::Wire<1> rsp_hit{};
  pyc::cpp::Wire<32> rsp_rdata{};

  pyc::cpp::Wire<32> cache__addr__jit_cache__L196{};
  pyc::cpp::Wire<32> cache__data__s0__w0{};
  pyc::cpp::Wire<32> cache__data__s0__w0__next{};
  pyc::cpp::Wire<32> cache__data__s0__w1{};
  pyc::cpp::Wire<32> cache__data__s0__w1__next{};
  pyc::cpp::Wire<32> cache__data__s1__w0{};
  pyc::cpp::Wire<32> cache__data__s1__w0__next{};
  pyc::cpp::Wire<32> cache__data__s1__w1{};
  pyc::cpp::Wire<32> cache__data__s1__w1__next{};
  pyc::cpp::Wire<32> cache__data__s2__w0{};
  pyc::cpp::Wire<32> cache__data__s2__w0__next{};
  pyc::cpp::Wire<32> cache__data__s2__w1{};
  pyc::cpp::Wire<32> cache__data__s2__w1__next{};
  pyc::cpp::Wire<32> cache__data__s3__w0{};
  pyc::cpp::Wire<32> cache__data__s3__w0__next{};
  pyc::cpp::Wire<32> cache__data__s3__w1{};
  pyc::cpp::Wire<32> cache__data__s3__w1__next{};
  pyc::cpp::Wire<32> cache__data__s4__w0{};
  pyc::cpp::Wire<32> cache__data__s4__w0__next{};
  pyc::cpp::Wire<32> cache__data__s4__w1{};
  pyc::cpp::Wire<32> cache__data__s4__w1__next{};
  pyc::cpp::Wire<32> cache__data__s5__w0{};
  pyc::cpp::Wire<32> cache__data__s5__w0__next{};
  pyc::cpp::Wire<32> cache__data__s5__w1{};
  pyc::cpp::Wire<32> cache__data__s5__w1__next{};
  pyc::cpp::Wire<32> cache__data__s6__w0{};
  pyc::cpp::Wire<32> cache__data__s6__w0__next{};
  pyc::cpp::Wire<32> cache__data__s6__w1{};
  pyc::cpp::Wire<32> cache__data__s6__w1__next{};
  pyc::cpp::Wire<32> cache__data__s7__w0{};
  pyc::cpp::Wire<32> cache__data__s7__w0__next{};
  pyc::cpp::Wire<32> cache__data__s7__w1{};
  pyc::cpp::Wire<32> cache__data__s7__w1__next{};
  pyc::cpp::Wire<1> cache__hit__jit_cache__L202{};
  pyc::cpp::Wire<32> cache__hit_data__jit_cache__L203{};
  pyc::cpp::Wire<32> cache__mem_rdata__jit_cache__L206{};
  pyc::cpp::Wire<1> cache__miss__jit_cache__L218{};
  pyc::cpp::Wire<32> cache__rdata__jit_cache__L232{};
  pyc::cpp::Wire<1> cache__repl_way__jit_cache__L219{};
  pyc::cpp::Wire<1> cache__req_fire__jit_cache__L194{};
  pyc::cpp::Wire<32> cache__req_q__in_data{};
  pyc::cpp::Wire<1> cache__req_q__in_valid{};
  pyc::cpp::Wire<1> cache__req_q__out_ready{};
  pyc::cpp::Wire<1> cache__rr__s0{};
  pyc::cpp::Wire<1> cache__rr__s0__next{};
  pyc::cpp::Wire<1> cache__rr__s1{};
  pyc::cpp::Wire<1> cache__rr__s1__next{};
  pyc::cpp::Wire<1> cache__rr__s2{};
  pyc::cpp::Wire<1> cache__rr__s2__next{};
  pyc::cpp::Wire<1> cache__rr__s3{};
  pyc::cpp::Wire<1> cache__rr__s3__next{};
  pyc::cpp::Wire<1> cache__rr__s4{};
  pyc::cpp::Wire<1> cache__rr__s4__next{};
  pyc::cpp::Wire<1> cache__rr__s5{};
  pyc::cpp::Wire<1> cache__rr__s5__next{};
  pyc::cpp::Wire<1> cache__rr__s6{};
  pyc::cpp::Wire<1> cache__rr__s6__next{};
  pyc::cpp::Wire<1> cache__rr__s7{};
  pyc::cpp::Wire<1> cache__rr__s7__next{};
  pyc::cpp::Wire<1> cache__rsp_hit__jit_cache__L183{};
  pyc::cpp::Wire<33> cache__rsp_pkt__jit_cache__L233{};
  pyc::cpp::Wire<33> cache__rsp_q__in_data{};
  pyc::cpp::Wire<1> cache__rsp_q__in_valid{};
  pyc::cpp::Wire<1> cache__rsp_q__out_ready{};
  pyc::cpp::Wire<32> cache__rsp_rdata__jit_cache__L184{};
  pyc::cpp::Wire<3> cache__set_idx__jit_cache__L197{};
  pyc::cpp::Wire<27> cache__tag__jit_cache__L198{};
  pyc::cpp::Wire<27> cache__tag__s0__w0{};
  pyc::cpp::Wire<27> cache__tag__s0__w0__next{};
  pyc::cpp::Wire<27> cache__tag__s0__w1{};
  pyc::cpp::Wire<27> cache__tag__s0__w1__next{};
  pyc::cpp::Wire<27> cache__tag__s1__w0{};
  pyc::cpp::Wire<27> cache__tag__s1__w0__next{};
  pyc::cpp::Wire<27> cache__tag__s1__w1{};
  pyc::cpp::Wire<27> cache__tag__s1__w1__next{};
  pyc::cpp::Wire<27> cache__tag__s2__w0{};
  pyc::cpp::Wire<27> cache__tag__s2__w0__next{};
  pyc::cpp::Wire<27> cache__tag__s2__w1{};
  pyc::cpp::Wire<27> cache__tag__s2__w1__next{};
  pyc::cpp::Wire<27> cache__tag__s3__w0{};
  pyc::cpp::Wire<27> cache__tag__s3__w0__next{};
  pyc::cpp::Wire<27> cache__tag__s3__w1{};
  pyc::cpp::Wire<27> cache__tag__s3__w1__next{};
  pyc::cpp::Wire<27> cache__tag__s4__w0{};
  pyc::cpp::Wire<27> cache__tag__s4__w0__next{};
  pyc::cpp::Wire<27> cache__tag__s4__w1{};
  pyc::cpp::Wire<27> cache__tag__s4__w1__next{};
  pyc::cpp::Wire<27> cache__tag__s5__w0{};
  pyc::cpp::Wire<27> cache__tag__s5__w0__next{};
  pyc::cpp::Wire<27> cache__tag__s5__w1{};
  pyc::cpp::Wire<27> cache__tag__s5__w1__next{};
  pyc::cpp::Wire<27> cache__tag__s6__w0{};
  pyc::cpp::Wire<27> cache__tag__s6__w0__next{};
  pyc::cpp::Wire<27> cache__tag__s6__w1{};
  pyc::cpp::Wire<27> cache__tag__s6__w1__next{};
  pyc::cpp::Wire<27> cache__tag__s7__w0{};
  pyc::cpp::Wire<27> cache__tag__s7__w0__next{};
  pyc::cpp::Wire<27> cache__tag__s7__w1{};
  pyc::cpp::Wire<27> cache__tag__s7__w1__next{};
  pyc::cpp::Wire<1> cache__valid__s0__w0{};
  pyc::cpp::Wire<1> cache__valid__s0__w0__next{};
  pyc::cpp::Wire<1> cache__valid__s0__w1{};
  pyc::cpp::Wire<1> cache__valid__s0__w1__next{};
  pyc::cpp::Wire<1> cache__valid__s1__w0{};
  pyc::cpp::Wire<1> cache__valid__s1__w0__next{};
  pyc::cpp::Wire<1> cache__valid__s1__w1{};
  pyc::cpp::Wire<1> cache__valid__s1__w1__next{};
  pyc::cpp::Wire<1> cache__valid__s2__w0{};
  pyc::cpp::Wire<1> cache__valid__s2__w0__next{};
  pyc::cpp::Wire<1> cache__valid__s2__w1{};
  pyc::cpp::Wire<1> cache__valid__s2__w1__next{};
  pyc::cpp::Wire<1> cache__valid__s3__w0{};
  pyc::cpp::Wire<1> cache__valid__s3__w0__next{};
  pyc::cpp::Wire<1> cache__valid__s3__w1{};
  pyc::cpp::Wire<1> cache__valid__s3__w1__next{};
  pyc::cpp::Wire<1> cache__valid__s4__w0{};
  pyc::cpp::Wire<1> cache__valid__s4__w0__next{};
  pyc::cpp::Wire<1> cache__valid__s4__w1{};
  pyc::cpp::Wire<1> cache__valid__s4__w1__next{};
  pyc::cpp::Wire<1> cache__valid__s5__w0{};
  pyc::cpp::Wire<1> cache__valid__s5__w0__next{};
  pyc::cpp::Wire<1> cache__valid__s5__w1{};
  pyc::cpp::Wire<1> cache__valid__s5__w1__next{};
  pyc::cpp::Wire<1> cache__valid__s6__w0{};
  pyc::cpp::Wire<1> cache__valid__s6__w0__next{};
  pyc::cpp::Wire<1> cache__valid__s6__w1{};
  pyc::cpp::Wire<1> cache__valid__s6__w1__next{};
  pyc::cpp::Wire<1> cache__valid__s7__w0{};
  pyc::cpp::Wire<1> cache__valid__s7__w0__next{};
  pyc::cpp::Wire<1> cache__valid__s7__w1{};
  pyc::cpp::Wire<1> cache__valid__s7__w1__next{};
  pyc::cpp::Wire<32> cache__waddr0__jit_cache__L173{};
  pyc::cpp::Wire<32> cache__wdata0__jit_cache__L174{};
  pyc::cpp::Wire<4> cache__wstrb0__jit_cache__L175{};
  pyc::cpp::Wire<1> cache__wvalid0__jit_cache__L172{};
  pyc::cpp::Wire<1> pyc_add_217{};
  pyc::cpp::Wire<1> pyc_add_249{};
  pyc::cpp::Wire<1> pyc_add_269{};
  pyc::cpp::Wire<1> pyc_add_289{};
  pyc::cpp::Wire<1> pyc_add_309{};
  pyc::cpp::Wire<1> pyc_add_329{};
  pyc::cpp::Wire<1> pyc_add_349{};
  pyc::cpp::Wire<1> pyc_add_369{};
  pyc::cpp::Wire<1> pyc_and_113{};
  pyc::cpp::Wire<1> pyc_and_117{};
  pyc::cpp::Wire<1> pyc_and_149{};
  pyc::cpp::Wire<1> pyc_and_151{};
  pyc::cpp::Wire<1> pyc_and_176{};
  pyc::cpp::Wire<1> pyc_and_194{};
  pyc::cpp::Wire<1> pyc_and_208{};
  pyc::cpp::Wire<1> pyc_and_216{};
  pyc::cpp::Wire<1> pyc_and_220{};
  pyc::cpp::Wire<1> pyc_and_233{};
  pyc::cpp::Wire<1> pyc_and_241{};
  pyc::cpp::Wire<1> pyc_and_248{};
  pyc::cpp::Wire<1> pyc_and_252{};
  pyc::cpp::Wire<1> pyc_and_256{};
  pyc::cpp::Wire<1> pyc_and_262{};
  pyc::cpp::Wire<1> pyc_and_268{};
  pyc::cpp::Wire<1> pyc_and_272{};
  pyc::cpp::Wire<1> pyc_and_276{};
  pyc::cpp::Wire<1> pyc_and_282{};
  pyc::cpp::Wire<1> pyc_and_288{};
  pyc::cpp::Wire<1> pyc_and_292{};
  pyc::cpp::Wire<1> pyc_and_296{};
  pyc::cpp::Wire<1> pyc_and_302{};
  pyc::cpp::Wire<1> pyc_and_308{};
  pyc::cpp::Wire<1> pyc_and_312{};
  pyc::cpp::Wire<1> pyc_and_316{};
  pyc::cpp::Wire<1> pyc_and_322{};
  pyc::cpp::Wire<1> pyc_and_328{};
  pyc::cpp::Wire<1> pyc_and_332{};
  pyc::cpp::Wire<1> pyc_and_336{};
  pyc::cpp::Wire<1> pyc_and_342{};
  pyc::cpp::Wire<1> pyc_and_348{};
  pyc::cpp::Wire<1> pyc_and_352{};
  pyc::cpp::Wire<1> pyc_and_356{};
  pyc::cpp::Wire<1> pyc_and_362{};
  pyc::cpp::Wire<1> pyc_and_368{};
  pyc::cpp::Wire<1> pyc_and_372{};
  pyc::cpp::Wire<1> pyc_and_376{};
  pyc::cpp::Wire<1> pyc_and_382{};
  pyc::cpp::Wire<32> pyc_byte_mem_192{};
  pyc::cpp::Wire<3> pyc_comb_16{};
  pyc::cpp::Wire<3> pyc_comb_17{};
  pyc::cpp::Wire<1> pyc_comb_179{};
  pyc::cpp::Wire<3> pyc_comb_18{};
  pyc::cpp::Wire<32> pyc_comb_180{};
  pyc::cpp::Wire<27> pyc_comb_181{};
  pyc::cpp::Wire<1> pyc_comb_182{};
  pyc::cpp::Wire<1> pyc_comb_183{};
  pyc::cpp::Wire<1> pyc_comb_184{};
  pyc::cpp::Wire<1> pyc_comb_185{};
  pyc::cpp::Wire<1> pyc_comb_186{};
  pyc::cpp::Wire<1> pyc_comb_187{};
  pyc::cpp::Wire<1> pyc_comb_188{};
  pyc::cpp::Wire<1> pyc_comb_189{};
  pyc::cpp::Wire<3> pyc_comb_19{};
  pyc::cpp::Wire<1> pyc_comb_190{};
  pyc::cpp::Wire<32> pyc_comb_191{};
  pyc::cpp::Wire<32> pyc_comb_196{};
  pyc::cpp::Wire<1> pyc_comb_197{};
  pyc::cpp::Wire<8> pyc_comb_198{};
  pyc::cpp::Wire<3> pyc_comb_20{};
  pyc::cpp::Wire<3> pyc_comb_21{};
  pyc::cpp::Wire<3> pyc_comb_22{};
  pyc::cpp::Wire<1> pyc_comb_222{};
  pyc::cpp::Wire<1> pyc_comb_223{};
  pyc::cpp::Wire<1> pyc_comb_224{};
  pyc::cpp::Wire<1> pyc_comb_225{};
  pyc::cpp::Wire<1> pyc_comb_226{};
  pyc::cpp::Wire<1> pyc_comb_227{};
  pyc::cpp::Wire<1> pyc_comb_228{};
  pyc::cpp::Wire<1> pyc_comb_229{};
  pyc::cpp::Wire<3> pyc_comb_23{};
  pyc::cpp::Wire<1> pyc_comb_230{};
  pyc::cpp::Wire<1> pyc_comb_231{};
  pyc::cpp::Wire<1> pyc_comb_235{};
  pyc::cpp::Wire<1> pyc_comb_236{};
  pyc::cpp::Wire<1> pyc_comb_237{};
  pyc::cpp::Wire<27> pyc_comb_24{};
  pyc::cpp::Wire<1> pyc_comb_243{};
  pyc::cpp::Wire<1> pyc_comb_244{};
  pyc::cpp::Wire<1> pyc_comb_245{};
  pyc::cpp::Wire<1> pyc_comb_25{};
  pyc::cpp::Wire<1> pyc_comb_254{};
  pyc::cpp::Wire<1> pyc_comb_255{};
  pyc::cpp::Wire<1> pyc_comb_258{};
  pyc::cpp::Wire<1> pyc_comb_259{};
  pyc::cpp::Wire<32> pyc_comb_26{};
  pyc::cpp::Wire<1> pyc_comb_264{};
  pyc::cpp::Wire<1> pyc_comb_265{};
  pyc::cpp::Wire<1> pyc_comb_27{};
  pyc::cpp::Wire<1> pyc_comb_274{};
  pyc::cpp::Wire<1> pyc_comb_275{};
  pyc::cpp::Wire<1> pyc_comb_278{};
  pyc::cpp::Wire<1> pyc_comb_279{};
  pyc::cpp::Wire<16> pyc_comb_28{};
  pyc::cpp::Wire<1> pyc_comb_284{};
  pyc::cpp::Wire<1> pyc_comb_285{};
  pyc::cpp::Wire<8> pyc_comb_29{};
  pyc::cpp::Wire<1> pyc_comb_294{};
  pyc::cpp::Wire<1> pyc_comb_295{};
  pyc::cpp::Wire<1> pyc_comb_298{};
  pyc::cpp::Wire<1> pyc_comb_299{};
  pyc::cpp::Wire<1> pyc_comb_30{};
  pyc::cpp::Wire<1> pyc_comb_304{};
  pyc::cpp::Wire<1> pyc_comb_305{};
  pyc::cpp::Wire<32> pyc_comb_31{};
  pyc::cpp::Wire<1> pyc_comb_314{};
  pyc::cpp::Wire<1> pyc_comb_315{};
  pyc::cpp::Wire<1> pyc_comb_318{};
  pyc::cpp::Wire<1> pyc_comb_319{};
  pyc::cpp::Wire<1> pyc_comb_32{};
  pyc::cpp::Wire<1> pyc_comb_324{};
  pyc::cpp::Wire<1> pyc_comb_325{};
  pyc::cpp::Wire<1> pyc_comb_33{};
  pyc::cpp::Wire<1> pyc_comb_334{};
  pyc::cpp::Wire<1> pyc_comb_335{};
  pyc::cpp::Wire<1> pyc_comb_338{};
  pyc::cpp::Wire<1> pyc_comb_339{};
  pyc::cpp::Wire<32> pyc_comb_34{};
  pyc::cpp::Wire<1> pyc_comb_344{};
  pyc::cpp::Wire<1> pyc_comb_345{};
  pyc::cpp::Wire<32> pyc_comb_35{};
  pyc::cpp::Wire<1> pyc_comb_354{};
  pyc::cpp::Wire<1> pyc_comb_355{};
  pyc::cpp::Wire<1> pyc_comb_358{};
  pyc::cpp::Wire<1> pyc_comb_359{};
  pyc::cpp::Wire<4> pyc_comb_36{};
  pyc::cpp::Wire<1> pyc_comb_364{};
  pyc::cpp::Wire<1> pyc_comb_365{};
  pyc::cpp::Wire<1> pyc_comb_374{};
  pyc::cpp::Wire<1> pyc_comb_375{};
  pyc::cpp::Wire<1> pyc_comb_378{};
  pyc::cpp::Wire<1> pyc_comb_379{};
  pyc::cpp::Wire<1> pyc_comb_384{};
  pyc::cpp::Wire<1> pyc_comb_385{};
  pyc::cpp::Wire<33> pyc_comb_390{};
  pyc::cpp::Wire<1> pyc_comb_45{};
  pyc::cpp::Wire<32> pyc_comb_46{};
  pyc::cpp::Wire<1> pyc_comb_65{};
  pyc::cpp::Wire<1> pyc_comb_66{};
  pyc::cpp::Wire<1> pyc_comb_67{};
  pyc::cpp::Wire<1> pyc_comb_68{};
  pyc::cpp::Wire<1> pyc_comb_69{};
  pyc::cpp::Wire<1> pyc_comb_70{};
  pyc::cpp::Wire<1> pyc_comb_71{};
  pyc::cpp::Wire<1> pyc_comb_72{};
  pyc::cpp::Wire<1> pyc_comb_73{};
  pyc::cpp::Wire<1> pyc_comb_74{};
  pyc::cpp::Wire<1> pyc_comb_75{};
  pyc::cpp::Wire<1> pyc_comb_76{};
  pyc::cpp::Wire<1> pyc_comb_77{};
  pyc::cpp::Wire<1> pyc_comb_78{};
  pyc::cpp::Wire<1> pyc_comb_79{};
  pyc::cpp::Wire<1> pyc_comb_80{};
  pyc::cpp::Wire<8> pyc_concat_195{};
  pyc::cpp::Wire<33> pyc_concat_389{};
  pyc::cpp::Wire<16> pyc_concat_47{};
  pyc::cpp::Wire<3> pyc_constant_1{};
  pyc::cpp::Wire<1> pyc_constant_10{};
  pyc::cpp::Wire<4> pyc_constant_11{};
  pyc::cpp::Wire<32> pyc_constant_12{};
  pyc::cpp::Wire<1> pyc_constant_13{};
  pyc::cpp::Wire<16> pyc_constant_14{};
  pyc::cpp::Wire<8> pyc_constant_15{};
  pyc::cpp::Wire<3> pyc_constant_2{};
  pyc::cpp::Wire<3> pyc_constant_3{};
  pyc::cpp::Wire<3> pyc_constant_4{};
  pyc::cpp::Wire<3> pyc_constant_5{};
  pyc::cpp::Wire<3> pyc_constant_6{};
  pyc::cpp::Wire<3> pyc_constant_7{};
  pyc::cpp::Wire<3> pyc_constant_8{};
  pyc::cpp::Wire<27> pyc_constant_9{};
  pyc::cpp::Wire<1> pyc_eq_116{};
  pyc::cpp::Wire<1> pyc_eq_118{};
  pyc::cpp::Wire<1> pyc_eq_120{};
  pyc::cpp::Wire<1> pyc_eq_122{};
  pyc::cpp::Wire<1> pyc_eq_124{};
  pyc::cpp::Wire<1> pyc_eq_126{};
  pyc::cpp::Wire<1> pyc_eq_128{};
  pyc::cpp::Wire<1> pyc_eq_130{};
  pyc::cpp::Wire<1> pyc_eq_148{};
  pyc::cpp::Wire<1> pyc_eq_175{};
  pyc::cpp::Wire<1> pyc_eq_218{};
  pyc::cpp::Wire<1> pyc_eq_232{};
  pyc::cpp::Wire<1> pyc_eq_240{};
  pyc::cpp::Wire<1> pyc_eq_250{};
  pyc::cpp::Wire<1> pyc_eq_270{};
  pyc::cpp::Wire<1> pyc_eq_290{};
  pyc::cpp::Wire<1> pyc_eq_310{};
  pyc::cpp::Wire<1> pyc_eq_330{};
  pyc::cpp::Wire<1> pyc_eq_350{};
  pyc::cpp::Wire<1> pyc_eq_370{};
  pyc::cpp::Wire<3> pyc_extract_114{};
  pyc::cpp::Wire<27> pyc_extract_115{};
  pyc::cpp::Wire<1> pyc_extract_200{};
  pyc::cpp::Wire<1> pyc_extract_201{};
  pyc::cpp::Wire<1> pyc_extract_202{};
  pyc::cpp::Wire<1> pyc_extract_203{};
  pyc::cpp::Wire<1> pyc_extract_204{};
  pyc::cpp::Wire<1> pyc_extract_205{};
  pyc::cpp::Wire<1> pyc_extract_206{};
  pyc::cpp::Wire<1> pyc_extract_207{};
  pyc::cpp::Wire<1> pyc_extract_43{};
  pyc::cpp::Wire<32> pyc_extract_44{};
  pyc::cpp::Wire<1> pyc_extract_49{};
  pyc::cpp::Wire<1> pyc_extract_50{};
  pyc::cpp::Wire<1> pyc_extract_51{};
  pyc::cpp::Wire<1> pyc_extract_52{};
  pyc::cpp::Wire<1> pyc_extract_53{};
  pyc::cpp::Wire<1> pyc_extract_54{};
  pyc::cpp::Wire<1> pyc_extract_55{};
  pyc::cpp::Wire<1> pyc_extract_56{};
  pyc::cpp::Wire<1> pyc_extract_57{};
  pyc::cpp::Wire<1> pyc_extract_58{};
  pyc::cpp::Wire<1> pyc_extract_59{};
  pyc::cpp::Wire<1> pyc_extract_60{};
  pyc::cpp::Wire<1> pyc_extract_61{};
  pyc::cpp::Wire<1> pyc_extract_62{};
  pyc::cpp::Wire<1> pyc_extract_63{};
  pyc::cpp::Wire<1> pyc_extract_64{};
  pyc::cpp::Wire<1> pyc_fifo_37{};
  pyc::cpp::Wire<1> pyc_fifo_38{};
  pyc::cpp::Wire<32> pyc_fifo_39{};
  pyc::cpp::Wire<1> pyc_fifo_40{};
  pyc::cpp::Wire<1> pyc_fifo_41{};
  pyc::cpp::Wire<33> pyc_fifo_42{};
  pyc::cpp::Wire<1> pyc_mux_119{};
  pyc::cpp::Wire<1> pyc_mux_121{};
  pyc::cpp::Wire<1> pyc_mux_123{};
  pyc::cpp::Wire<1> pyc_mux_125{};
  pyc::cpp::Wire<1> pyc_mux_127{};
  pyc::cpp::Wire<1> pyc_mux_129{};
  pyc::cpp::Wire<1> pyc_mux_131{};
  pyc::cpp::Wire<27> pyc_mux_132{};
  pyc::cpp::Wire<27> pyc_mux_133{};
  pyc::cpp::Wire<27> pyc_mux_134{};
  pyc::cpp::Wire<27> pyc_mux_135{};
  pyc::cpp::Wire<27> pyc_mux_136{};
  pyc::cpp::Wire<27> pyc_mux_137{};
  pyc::cpp::Wire<27> pyc_mux_138{};
  pyc::cpp::Wire<27> pyc_mux_139{};
  pyc::cpp::Wire<32> pyc_mux_140{};
  pyc::cpp::Wire<32> pyc_mux_141{};
  pyc::cpp::Wire<32> pyc_mux_142{};
  pyc::cpp::Wire<32> pyc_mux_143{};
  pyc::cpp::Wire<32> pyc_mux_144{};
  pyc::cpp::Wire<32> pyc_mux_145{};
  pyc::cpp::Wire<32> pyc_mux_146{};
  pyc::cpp::Wire<32> pyc_mux_147{};
  pyc::cpp::Wire<32> pyc_mux_150{};
  pyc::cpp::Wire<1> pyc_mux_152{};
  pyc::cpp::Wire<1> pyc_mux_153{};
  pyc::cpp::Wire<1> pyc_mux_154{};
  pyc::cpp::Wire<1> pyc_mux_155{};
  pyc::cpp::Wire<1> pyc_mux_156{};
  pyc::cpp::Wire<1> pyc_mux_157{};
  pyc::cpp::Wire<1> pyc_mux_158{};
  pyc::cpp::Wire<27> pyc_mux_159{};
  pyc::cpp::Wire<27> pyc_mux_160{};
  pyc::cpp::Wire<27> pyc_mux_161{};
  pyc::cpp::Wire<27> pyc_mux_162{};
  pyc::cpp::Wire<27> pyc_mux_163{};
  pyc::cpp::Wire<27> pyc_mux_164{};
  pyc::cpp::Wire<27> pyc_mux_165{};
  pyc::cpp::Wire<27> pyc_mux_166{};
  pyc::cpp::Wire<32> pyc_mux_167{};
  pyc::cpp::Wire<32> pyc_mux_168{};
  pyc::cpp::Wire<32> pyc_mux_169{};
  pyc::cpp::Wire<32> pyc_mux_170{};
  pyc::cpp::Wire<32> pyc_mux_171{};
  pyc::cpp::Wire<32> pyc_mux_172{};
  pyc::cpp::Wire<32> pyc_mux_173{};
  pyc::cpp::Wire<32> pyc_mux_174{};
  pyc::cpp::Wire<32> pyc_mux_178{};
  pyc::cpp::Wire<1> pyc_mux_209{};
  pyc::cpp::Wire<1> pyc_mux_210{};
  pyc::cpp::Wire<1> pyc_mux_211{};
  pyc::cpp::Wire<1> pyc_mux_212{};
  pyc::cpp::Wire<1> pyc_mux_213{};
  pyc::cpp::Wire<1> pyc_mux_214{};
  pyc::cpp::Wire<1> pyc_mux_215{};
  pyc::cpp::Wire<1> pyc_mux_221{};
  pyc::cpp::Wire<27> pyc_mux_238{};
  pyc::cpp::Wire<32> pyc_mux_239{};
  pyc::cpp::Wire<27> pyc_mux_246{};
  pyc::cpp::Wire<32> pyc_mux_247{};
  pyc::cpp::Wire<1> pyc_mux_253{};
  pyc::cpp::Wire<27> pyc_mux_260{};
  pyc::cpp::Wire<32> pyc_mux_261{};
  pyc::cpp::Wire<27> pyc_mux_266{};
  pyc::cpp::Wire<32> pyc_mux_267{};
  pyc::cpp::Wire<1> pyc_mux_273{};
  pyc::cpp::Wire<27> pyc_mux_280{};
  pyc::cpp::Wire<32> pyc_mux_281{};
  pyc::cpp::Wire<27> pyc_mux_286{};
  pyc::cpp::Wire<32> pyc_mux_287{};
  pyc::cpp::Wire<1> pyc_mux_293{};
  pyc::cpp::Wire<27> pyc_mux_300{};
  pyc::cpp::Wire<32> pyc_mux_301{};
  pyc::cpp::Wire<27> pyc_mux_306{};
  pyc::cpp::Wire<32> pyc_mux_307{};
  pyc::cpp::Wire<1> pyc_mux_313{};
  pyc::cpp::Wire<27> pyc_mux_320{};
  pyc::cpp::Wire<32> pyc_mux_321{};
  pyc::cpp::Wire<27> pyc_mux_326{};
  pyc::cpp::Wire<32> pyc_mux_327{};
  pyc::cpp::Wire<1> pyc_mux_333{};
  pyc::cpp::Wire<27> pyc_mux_340{};
  pyc::cpp::Wire<32> pyc_mux_341{};
  pyc::cpp::Wire<27> pyc_mux_346{};
  pyc::cpp::Wire<32> pyc_mux_347{};
  pyc::cpp::Wire<1> pyc_mux_353{};
  pyc::cpp::Wire<27> pyc_mux_360{};
  pyc::cpp::Wire<32> pyc_mux_361{};
  pyc::cpp::Wire<27> pyc_mux_366{};
  pyc::cpp::Wire<32> pyc_mux_367{};
  pyc::cpp::Wire<1> pyc_mux_373{};
  pyc::cpp::Wire<27> pyc_mux_380{};
  pyc::cpp::Wire<32> pyc_mux_381{};
  pyc::cpp::Wire<27> pyc_mux_386{};
  pyc::cpp::Wire<32> pyc_mux_387{};
  pyc::cpp::Wire<32> pyc_mux_388{};
  pyc::cpp::Wire<1> pyc_not_193{};
  pyc::cpp::Wire<1> pyc_not_219{};
  pyc::cpp::Wire<1> pyc_not_251{};
  pyc::cpp::Wire<1> pyc_not_271{};
  pyc::cpp::Wire<1> pyc_not_291{};
  pyc::cpp::Wire<1> pyc_not_311{};
  pyc::cpp::Wire<1> pyc_not_331{};
  pyc::cpp::Wire<1> pyc_not_351{};
  pyc::cpp::Wire<1> pyc_not_371{};
  pyc::cpp::Wire<1> pyc_or_177{};
  pyc::cpp::Wire<1> pyc_or_234{};
  pyc::cpp::Wire<1> pyc_or_242{};
  pyc::cpp::Wire<1> pyc_or_257{};
  pyc::cpp::Wire<1> pyc_or_263{};
  pyc::cpp::Wire<1> pyc_or_277{};
  pyc::cpp::Wire<1> pyc_or_283{};
  pyc::cpp::Wire<1> pyc_or_297{};
  pyc::cpp::Wire<1> pyc_or_303{};
  pyc::cpp::Wire<1> pyc_or_317{};
  pyc::cpp::Wire<1> pyc_or_323{};
  pyc::cpp::Wire<1> pyc_or_337{};
  pyc::cpp::Wire<1> pyc_or_343{};
  pyc::cpp::Wire<1> pyc_or_357{};
  pyc::cpp::Wire<1> pyc_or_363{};
  pyc::cpp::Wire<1> pyc_or_377{};
  pyc::cpp::Wire<1> pyc_or_383{};
  pyc::cpp::Wire<32> pyc_reg_100{};
  pyc::cpp::Wire<32> pyc_reg_101{};
  pyc::cpp::Wire<32> pyc_reg_102{};
  pyc::cpp::Wire<32> pyc_reg_103{};
  pyc::cpp::Wire<32> pyc_reg_104{};
  pyc::cpp::Wire<32> pyc_reg_105{};
  pyc::cpp::Wire<32> pyc_reg_106{};
  pyc::cpp::Wire<32> pyc_reg_107{};
  pyc::cpp::Wire<32> pyc_reg_108{};
  pyc::cpp::Wire<32> pyc_reg_109{};
  pyc::cpp::Wire<32> pyc_reg_110{};
  pyc::cpp::Wire<32> pyc_reg_111{};
  pyc::cpp::Wire<32> pyc_reg_112{};
  pyc::cpp::Wire<8> pyc_reg_199{};
  pyc::cpp::Wire<16> pyc_reg_48{};
  pyc::cpp::Wire<27> pyc_reg_81{};
  pyc::cpp::Wire<27> pyc_reg_82{};
  pyc::cpp::Wire<27> pyc_reg_83{};
  pyc::cpp::Wire<27> pyc_reg_84{};
  pyc::cpp::Wire<27> pyc_reg_85{};
  pyc::cpp::Wire<27> pyc_reg_86{};
  pyc::cpp::Wire<27> pyc_reg_87{};
  pyc::cpp::Wire<27> pyc_reg_88{};
  pyc::cpp::Wire<27> pyc_reg_89{};
  pyc::cpp::Wire<27> pyc_reg_90{};
  pyc::cpp::Wire<27> pyc_reg_91{};
  pyc::cpp::Wire<27> pyc_reg_92{};
  pyc::cpp::Wire<27> pyc_reg_93{};
  pyc::cpp::Wire<27> pyc_reg_94{};
  pyc::cpp::Wire<27> pyc_reg_95{};
  pyc::cpp::Wire<27> pyc_reg_96{};
  pyc::cpp::Wire<32> pyc_reg_97{};
  pyc::cpp::Wire<32> pyc_reg_98{};
  pyc::cpp::Wire<32> pyc_reg_99{};
  pyc::cpp::Wire<32> req_addr__jit_cache__L167{};
  pyc::cpp::Wire<1> req_valid__jit_cache__L166{};
  pyc::cpp::Wire<1> rsp_ready__jit_cache__L168{};

  pyc::cpp::pyc_reg<32> pyc_reg_100_inst;
  pyc::cpp::pyc_reg<32> pyc_reg_101_inst;
  pyc::cpp::pyc_reg<32> pyc_reg_102_inst;
  pyc::cpp::pyc_reg<32> pyc_reg_103_inst;
  pyc::cpp::pyc_reg<32> pyc_reg_104_inst;
  pyc::cpp::pyc_reg<32> pyc_reg_105_inst;
  pyc::cpp::pyc_reg<32> pyc_reg_106_inst;
  pyc::cpp::pyc_reg<32> pyc_reg_107_inst;
  pyc::cpp::pyc_reg<32> pyc_reg_108_inst;
  pyc::cpp::pyc_reg<32> pyc_reg_109_inst;
  pyc::cpp::pyc_reg<32> pyc_reg_110_inst;
  pyc::cpp::pyc_reg<32> pyc_reg_111_inst;
  pyc::cpp::pyc_reg<32> pyc_reg_112_inst;
  pyc::cpp::pyc_reg<8> pyc_reg_199_inst;
  pyc::cpp::pyc_reg<16> pyc_reg_48_inst;
  pyc::cpp::pyc_reg<27> pyc_reg_81_inst;
  pyc::cpp::pyc_reg<27> pyc_reg_82_inst;
  pyc::cpp::pyc_reg<27> pyc_reg_83_inst;
  pyc::cpp::pyc_reg<27> pyc_reg_84_inst;
  pyc::cpp::pyc_reg<27> pyc_reg_85_inst;
  pyc::cpp::pyc_reg<27> pyc_reg_86_inst;
  pyc::cpp::pyc_reg<27> pyc_reg_87_inst;
  pyc::cpp::pyc_reg<27> pyc_reg_88_inst;
  pyc::cpp::pyc_reg<27> pyc_reg_89_inst;
  pyc::cpp::pyc_reg<27> pyc_reg_90_inst;
  pyc::cpp::pyc_reg<27> pyc_reg_91_inst;
  pyc::cpp::pyc_reg<27> pyc_reg_92_inst;
  pyc::cpp::pyc_reg<27> pyc_reg_93_inst;
  pyc::cpp::pyc_reg<27> pyc_reg_94_inst;
  pyc::cpp::pyc_reg<27> pyc_reg_95_inst;
  pyc::cpp::pyc_reg<27> pyc_reg_96_inst;
  pyc::cpp::pyc_reg<32> pyc_reg_97_inst;
  pyc::cpp::pyc_reg<32> pyc_reg_98_inst;
  pyc::cpp::pyc_reg<32> pyc_reg_99_inst;
  pyc::cpp::pyc_fifo<32, 2> pyc_fifo_37_inst;
  pyc::cpp::pyc_fifo<33, 2> pyc_fifo_40_inst;
  pyc::cpp::pyc_byte_mem<32, 32, 4096> main_mem;

  JitCache() :
      pyc_reg_100_inst(sys_clk, sys_rst, pyc_comb_25, cache__data__s1__w1__next, pyc_comb_26, pyc_reg_100),
      pyc_reg_101_inst(sys_clk, sys_rst, pyc_comb_25, cache__data__s2__w0__next, pyc_comb_26, pyc_reg_101),
      pyc_reg_102_inst(sys_clk, sys_rst, pyc_comb_25, cache__data__s2__w1__next, pyc_comb_26, pyc_reg_102),
      pyc_reg_103_inst(sys_clk, sys_rst, pyc_comb_25, cache__data__s3__w0__next, pyc_comb_26, pyc_reg_103),
      pyc_reg_104_inst(sys_clk, sys_rst, pyc_comb_25, cache__data__s3__w1__next, pyc_comb_26, pyc_reg_104),
      pyc_reg_105_inst(sys_clk, sys_rst, pyc_comb_25, cache__data__s4__w0__next, pyc_comb_26, pyc_reg_105),
      pyc_reg_106_inst(sys_clk, sys_rst, pyc_comb_25, cache__data__s4__w1__next, pyc_comb_26, pyc_reg_106),
      pyc_reg_107_inst(sys_clk, sys_rst, pyc_comb_25, cache__data__s5__w0__next, pyc_comb_26, pyc_reg_107),
      pyc_reg_108_inst(sys_clk, sys_rst, pyc_comb_25, cache__data__s5__w1__next, pyc_comb_26, pyc_reg_108),
      pyc_reg_109_inst(sys_clk, sys_rst, pyc_comb_25, cache__data__s6__w0__next, pyc_comb_26, pyc_reg_109),
      pyc_reg_110_inst(sys_clk, sys_rst, pyc_comb_25, cache__data__s6__w1__next, pyc_comb_26, pyc_reg_110),
      pyc_reg_111_inst(sys_clk, sys_rst, pyc_comb_25, cache__data__s7__w0__next, pyc_comb_26, pyc_reg_111),
      pyc_reg_112_inst(sys_clk, sys_rst, pyc_comb_25, cache__data__s7__w1__next, pyc_comb_26, pyc_reg_112),
      pyc_reg_199_inst(sys_clk, sys_rst, pyc_comb_25, pyc_comb_198, pyc_comb_29, pyc_reg_199),
      pyc_reg_48_inst(sys_clk, sys_rst, pyc_comb_25, pyc_concat_47, pyc_comb_28, pyc_reg_48),
      pyc_reg_81_inst(sys_clk, sys_rst, pyc_comb_25, cache__tag__s0__w0__next, pyc_comb_24, pyc_reg_81),
      pyc_reg_82_inst(sys_clk, sys_rst, pyc_comb_25, cache__tag__s0__w1__next, pyc_comb_24, pyc_reg_82),
      pyc_reg_83_inst(sys_clk, sys_rst, pyc_comb_25, cache__tag__s1__w0__next, pyc_comb_24, pyc_reg_83),
      pyc_reg_84_inst(sys_clk, sys_rst, pyc_comb_25, cache__tag__s1__w1__next, pyc_comb_24, pyc_reg_84),
      pyc_reg_85_inst(sys_clk, sys_rst, pyc_comb_25, cache__tag__s2__w0__next, pyc_comb_24, pyc_reg_85),
      pyc_reg_86_inst(sys_clk, sys_rst, pyc_comb_25, cache__tag__s2__w1__next, pyc_comb_24, pyc_reg_86),
      pyc_reg_87_inst(sys_clk, sys_rst, pyc_comb_25, cache__tag__s3__w0__next, pyc_comb_24, pyc_reg_87),
      pyc_reg_88_inst(sys_clk, sys_rst, pyc_comb_25, cache__tag__s3__w1__next, pyc_comb_24, pyc_reg_88),
      pyc_reg_89_inst(sys_clk, sys_rst, pyc_comb_25, cache__tag__s4__w0__next, pyc_comb_24, pyc_reg_89),
      pyc_reg_90_inst(sys_clk, sys_rst, pyc_comb_25, cache__tag__s4__w1__next, pyc_comb_24, pyc_reg_90),
      pyc_reg_91_inst(sys_clk, sys_rst, pyc_comb_25, cache__tag__s5__w0__next, pyc_comb_24, pyc_reg_91),
      pyc_reg_92_inst(sys_clk, sys_rst, pyc_comb_25, cache__tag__s5__w1__next, pyc_comb_24, pyc_reg_92),
      pyc_reg_93_inst(sys_clk, sys_rst, pyc_comb_25, cache__tag__s6__w0__next, pyc_comb_24, pyc_reg_93),
      pyc_reg_94_inst(sys_clk, sys_rst, pyc_comb_25, cache__tag__s6__w1__next, pyc_comb_24, pyc_reg_94),
      pyc_reg_95_inst(sys_clk, sys_rst, pyc_comb_25, cache__tag__s7__w0__next, pyc_comb_24, pyc_reg_95),
      pyc_reg_96_inst(sys_clk, sys_rst, pyc_comb_25, cache__tag__s7__w1__next, pyc_comb_24, pyc_reg_96),
      pyc_reg_97_inst(sys_clk, sys_rst, pyc_comb_25, cache__data__s0__w0__next, pyc_comb_26, pyc_reg_97),
      pyc_reg_98_inst(sys_clk, sys_rst, pyc_comb_25, cache__data__s0__w1__next, pyc_comb_26, pyc_reg_98),
      pyc_reg_99_inst(sys_clk, sys_rst, pyc_comb_25, cache__data__s1__w0__next, pyc_comb_26, pyc_reg_99),
      pyc_fifo_37_inst(sys_clk, sys_rst, pyc_comb_30, pyc_fifo_37, pyc_comb_31, pyc_fifo_38, cache__req_q__out_ready, pyc_fifo_39),
      pyc_fifo_40_inst(sys_clk, sys_rst, cache__rsp_q__in_valid, pyc_fifo_40, cache__rsp_q__in_data, pyc_fifo_41, pyc_comb_32, pyc_fifo_42),
      main_mem(sys_clk, sys_rst, pyc_comb_180, pyc_byte_mem_192, pyc_comb_33, pyc_comb_34, pyc_comb_35, pyc_comb_36) {
    eval();
  }

  inline void eval_comb_0() {
    pyc_constant_1 = pyc::cpp::Wire<3>({0x7ull});
    pyc_constant_2 = pyc::cpp::Wire<3>({0x6ull});
    pyc_constant_3 = pyc::cpp::Wire<3>({0x5ull});
    pyc_constant_4 = pyc::cpp::Wire<3>({0x4ull});
    pyc_constant_5 = pyc::cpp::Wire<3>({0x3ull});
    pyc_constant_6 = pyc::cpp::Wire<3>({0x2ull});
    pyc_constant_7 = pyc::cpp::Wire<3>({0x1ull});
    pyc_constant_8 = pyc::cpp::Wire<3>({0x0ull});
    pyc_constant_9 = pyc::cpp::Wire<27>({0x0ull});
    pyc_constant_10 = pyc::cpp::Wire<1>({0x1ull});
    pyc_constant_11 = pyc::cpp::Wire<4>({0x0ull});
    pyc_constant_12 = pyc::cpp::Wire<32>({0x0ull});
    pyc_constant_13 = pyc::cpp::Wire<1>({0x0ull});
    pyc_constant_14 = pyc::cpp::Wire<16>({0x0ull});
    pyc_constant_15 = pyc::cpp::Wire<8>({0x0ull});
    req_valid__jit_cache__L166 = req_valid;
    cache__req_q__in_valid = req_valid__jit_cache__L166;
    req_addr__jit_cache__L167 = req_addr;
    cache__req_q__in_data = req_addr__jit_cache__L167;
    rsp_ready__jit_cache__L168 = rsp_ready;
    cache__rsp_q__out_ready = rsp_ready__jit_cache__L168;
    cache__wvalid0__jit_cache__L172 = pyc_constant_13;
    cache__waddr0__jit_cache__L173 = pyc_constant_12;
    cache__wdata0__jit_cache__L174 = pyc_constant_12;
    cache__wstrb0__jit_cache__L175 = pyc_constant_11;
    pyc_comb_16 = pyc_constant_1;
    pyc_comb_17 = pyc_constant_2;
    pyc_comb_18 = pyc_constant_3;
    pyc_comb_19 = pyc_constant_4;
    pyc_comb_20 = pyc_constant_5;
    pyc_comb_21 = pyc_constant_6;
    pyc_comb_22 = pyc_constant_7;
    pyc_comb_23 = pyc_constant_8;
    pyc_comb_24 = pyc_constant_9;
    pyc_comb_25 = pyc_constant_10;
    pyc_comb_26 = pyc_constant_12;
    pyc_comb_27 = pyc_constant_13;
    pyc_comb_28 = pyc_constant_14;
    pyc_comb_29 = pyc_constant_15;
    pyc_comb_30 = cache__req_q__in_valid;
    pyc_comb_31 = cache__req_q__in_data;
    pyc_comb_32 = cache__rsp_q__out_ready;
    pyc_comb_33 = cache__wvalid0__jit_cache__L172;
    pyc_comb_34 = cache__waddr0__jit_cache__L173;
    pyc_comb_35 = cache__wdata0__jit_cache__L174;
    pyc_comb_36 = cache__wstrb0__jit_cache__L175;
  }

  inline void eval_comb_1() {
    pyc_and_113 = (pyc_fifo_38 & pyc_fifo_40);
    cache__req_fire__jit_cache__L194 = pyc_and_113;
    cache__addr__jit_cache__L196 = pyc_fifo_39;
    pyc_extract_114 = pyc::cpp::extract<3, 32>(cache__addr__jit_cache__L196, 2u);
    cache__set_idx__jit_cache__L197 = pyc_extract_114;
    pyc_extract_115 = pyc::cpp::extract<27, 32>(cache__addr__jit_cache__L196, 5u);
    cache__tag__jit_cache__L198 = pyc_extract_115;
    pyc_eq_116 = pyc::cpp::Wire<1>((cache__set_idx__jit_cache__L197 == pyc_comb_23) ? 1u : 0u);
    pyc_and_117 = (pyc_eq_116 & pyc_comb_65);
    pyc_eq_118 = pyc::cpp::Wire<1>((cache__set_idx__jit_cache__L197 == pyc_comb_22) ? 1u : 0u);
    pyc_mux_119 = (pyc_eq_118.toBool() ? pyc_comb_67 : pyc_and_117);
    pyc_eq_120 = pyc::cpp::Wire<1>((cache__set_idx__jit_cache__L197 == pyc_comb_21) ? 1u : 0u);
    pyc_mux_121 = (pyc_eq_120.toBool() ? pyc_comb_69 : pyc_mux_119);
    pyc_eq_122 = pyc::cpp::Wire<1>((cache__set_idx__jit_cache__L197 == pyc_comb_20) ? 1u : 0u);
    pyc_mux_123 = (pyc_eq_122.toBool() ? pyc_comb_71 : pyc_mux_121);
    pyc_eq_124 = pyc::cpp::Wire<1>((cache__set_idx__jit_cache__L197 == pyc_comb_19) ? 1u : 0u);
    pyc_mux_125 = (pyc_eq_124.toBool() ? pyc_comb_73 : pyc_mux_123);
    pyc_eq_126 = pyc::cpp::Wire<1>((cache__set_idx__jit_cache__L197 == pyc_comb_18) ? 1u : 0u);
    pyc_mux_127 = (pyc_eq_126.toBool() ? pyc_comb_75 : pyc_mux_125);
    pyc_eq_128 = pyc::cpp::Wire<1>((cache__set_idx__jit_cache__L197 == pyc_comb_17) ? 1u : 0u);
    pyc_mux_129 = (pyc_eq_128.toBool() ? pyc_comb_77 : pyc_mux_127);
    pyc_eq_130 = pyc::cpp::Wire<1>((cache__set_idx__jit_cache__L197 == pyc_comb_16) ? 1u : 0u);
    pyc_mux_131 = (pyc_eq_130.toBool() ? pyc_comb_79 : pyc_mux_129);
    pyc_mux_132 = (pyc_eq_116.toBool() ? cache__tag__s0__w0 : pyc_comb_24);
    pyc_mux_133 = (pyc_eq_118.toBool() ? cache__tag__s1__w0 : pyc_mux_132);
    pyc_mux_134 = (pyc_eq_120.toBool() ? cache__tag__s2__w0 : pyc_mux_133);
    pyc_mux_135 = (pyc_eq_122.toBool() ? cache__tag__s3__w0 : pyc_mux_134);
    pyc_mux_136 = (pyc_eq_124.toBool() ? cache__tag__s4__w0 : pyc_mux_135);
    pyc_mux_137 = (pyc_eq_126.toBool() ? cache__tag__s5__w0 : pyc_mux_136);
    pyc_mux_138 = (pyc_eq_128.toBool() ? cache__tag__s6__w0 : pyc_mux_137);
    pyc_mux_139 = (pyc_eq_130.toBool() ? cache__tag__s7__w0 : pyc_mux_138);
    pyc_mux_140 = (pyc_eq_116.toBool() ? cache__data__s0__w0 : pyc_comb_26);
    pyc_mux_141 = (pyc_eq_118.toBool() ? cache__data__s1__w0 : pyc_mux_140);
    pyc_mux_142 = (pyc_eq_120.toBool() ? cache__data__s2__w0 : pyc_mux_141);
    pyc_mux_143 = (pyc_eq_122.toBool() ? cache__data__s3__w0 : pyc_mux_142);
    pyc_mux_144 = (pyc_eq_124.toBool() ? cache__data__s4__w0 : pyc_mux_143);
    pyc_mux_145 = (pyc_eq_126.toBool() ? cache__data__s5__w0 : pyc_mux_144);
    pyc_mux_146 = (pyc_eq_128.toBool() ? cache__data__s6__w0 : pyc_mux_145);
    pyc_mux_147 = (pyc_eq_130.toBool() ? cache__data__s7__w0 : pyc_mux_146);
    pyc_eq_148 = pyc::cpp::Wire<1>((pyc_mux_139 == cache__tag__jit_cache__L198) ? 1u : 0u);
    pyc_and_149 = (pyc_mux_131 & pyc_eq_148);
    pyc_mux_150 = (pyc_and_149.toBool() ? pyc_mux_147 : pyc_comb_26);
    pyc_and_151 = (pyc_eq_116 & pyc_comb_66);
    pyc_mux_152 = (pyc_eq_118.toBool() ? pyc_comb_68 : pyc_and_151);
    pyc_mux_153 = (pyc_eq_120.toBool() ? pyc_comb_70 : pyc_mux_152);
    pyc_mux_154 = (pyc_eq_122.toBool() ? pyc_comb_72 : pyc_mux_153);
    pyc_mux_155 = (pyc_eq_124.toBool() ? pyc_comb_74 : pyc_mux_154);
    pyc_mux_156 = (pyc_eq_126.toBool() ? pyc_comb_76 : pyc_mux_155);
    pyc_mux_157 = (pyc_eq_128.toBool() ? pyc_comb_78 : pyc_mux_156);
    pyc_mux_158 = (pyc_eq_130.toBool() ? pyc_comb_80 : pyc_mux_157);
    pyc_mux_159 = (pyc_eq_116.toBool() ? cache__tag__s0__w1 : pyc_comb_24);
    pyc_mux_160 = (pyc_eq_118.toBool() ? cache__tag__s1__w1 : pyc_mux_159);
    pyc_mux_161 = (pyc_eq_120.toBool() ? cache__tag__s2__w1 : pyc_mux_160);
    pyc_mux_162 = (pyc_eq_122.toBool() ? cache__tag__s3__w1 : pyc_mux_161);
    pyc_mux_163 = (pyc_eq_124.toBool() ? cache__tag__s4__w1 : pyc_mux_162);
    pyc_mux_164 = (pyc_eq_126.toBool() ? cache__tag__s5__w1 : pyc_mux_163);
    pyc_mux_165 = (pyc_eq_128.toBool() ? cache__tag__s6__w1 : pyc_mux_164);
    pyc_mux_166 = (pyc_eq_130.toBool() ? cache__tag__s7__w1 : pyc_mux_165);
    pyc_mux_167 = (pyc_eq_116.toBool() ? cache__data__s0__w1 : pyc_comb_26);
    pyc_mux_168 = (pyc_eq_118.toBool() ? cache__data__s1__w1 : pyc_mux_167);
    pyc_mux_169 = (pyc_eq_120.toBool() ? cache__data__s2__w1 : pyc_mux_168);
    pyc_mux_170 = (pyc_eq_122.toBool() ? cache__data__s3__w1 : pyc_mux_169);
    pyc_mux_171 = (pyc_eq_124.toBool() ? cache__data__s4__w1 : pyc_mux_170);
    pyc_mux_172 = (pyc_eq_126.toBool() ? cache__data__s5__w1 : pyc_mux_171);
    pyc_mux_173 = (pyc_eq_128.toBool() ? cache__data__s6__w1 : pyc_mux_172);
    pyc_mux_174 = (pyc_eq_130.toBool() ? cache__data__s7__w1 : pyc_mux_173);
    pyc_eq_175 = pyc::cpp::Wire<1>((pyc_mux_166 == cache__tag__jit_cache__L198) ? 1u : 0u);
    pyc_and_176 = (pyc_mux_158 & pyc_eq_175);
    pyc_or_177 = (pyc_and_149 | pyc_and_176);
    pyc_mux_178 = (pyc_and_176.toBool() ? pyc_mux_174 : pyc_mux_150);
    cache__hit__jit_cache__L202 = pyc_or_177;
    cache__hit_data__jit_cache__L203 = pyc_mux_178;
    pyc_comb_179 = cache__req_fire__jit_cache__L194;
    pyc_comb_180 = cache__addr__jit_cache__L196;
    pyc_comb_181 = cache__tag__jit_cache__L198;
    pyc_comb_182 = pyc_eq_116;
    pyc_comb_183 = pyc_eq_118;
    pyc_comb_184 = pyc_eq_120;
    pyc_comb_185 = pyc_eq_122;
    pyc_comb_186 = pyc_eq_124;
    pyc_comb_187 = pyc_eq_126;
    pyc_comb_188 = pyc_eq_128;
    pyc_comb_189 = pyc_eq_130;
    pyc_comb_190 = cache__hit__jit_cache__L202;
    pyc_comb_191 = cache__hit_data__jit_cache__L203;
  }

  inline void eval_comb_2() {
    cache__mem_rdata__jit_cache__L206 = pyc_byte_mem_192;
    pyc_not_193 = (~pyc_comb_190);
    pyc_and_194 = (pyc_comb_179 & pyc_not_193);
    cache__miss__jit_cache__L218 = pyc_and_194;
    pyc_concat_195 = pyc::cpp::concat(cache__rr__s7__next, cache__rr__s6__next, cache__rr__s5__next, cache__rr__s4__next, cache__rr__s3__next, cache__rr__s2__next, cache__rr__s1__next, cache__rr__s0__next);
    pyc_comb_196 = cache__mem_rdata__jit_cache__L206;
    pyc_comb_197 = cache__miss__jit_cache__L218;
    pyc_comb_198 = pyc_concat_195;
  }

  inline void eval_comb_3() {
    pyc_extract_200 = pyc::cpp::extract<1, 8>(pyc_reg_199, 0u);
    pyc_extract_201 = pyc::cpp::extract<1, 8>(pyc_reg_199, 1u);
    pyc_extract_202 = pyc::cpp::extract<1, 8>(pyc_reg_199, 2u);
    pyc_extract_203 = pyc::cpp::extract<1, 8>(pyc_reg_199, 3u);
    pyc_extract_204 = pyc::cpp::extract<1, 8>(pyc_reg_199, 4u);
    pyc_extract_205 = pyc::cpp::extract<1, 8>(pyc_reg_199, 5u);
    pyc_extract_206 = pyc::cpp::extract<1, 8>(pyc_reg_199, 6u);
    pyc_extract_207 = pyc::cpp::extract<1, 8>(pyc_reg_199, 7u);
    cache__rr__s0 = pyc_extract_200;
    cache__rr__s1 = pyc_extract_201;
    cache__rr__s2 = pyc_extract_202;
    cache__rr__s3 = pyc_extract_203;
    cache__rr__s4 = pyc_extract_204;
    cache__rr__s5 = pyc_extract_205;
    cache__rr__s6 = pyc_extract_206;
    cache__rr__s7 = pyc_extract_207;
    pyc_and_208 = (pyc_comb_182 & cache__rr__s0);
    pyc_mux_209 = (pyc_comb_183.toBool() ? cache__rr__s1 : pyc_and_208);
    pyc_mux_210 = (pyc_comb_184.toBool() ? cache__rr__s2 : pyc_mux_209);
    pyc_mux_211 = (pyc_comb_185.toBool() ? cache__rr__s3 : pyc_mux_210);
    pyc_mux_212 = (pyc_comb_186.toBool() ? cache__rr__s4 : pyc_mux_211);
    pyc_mux_213 = (pyc_comb_187.toBool() ? cache__rr__s5 : pyc_mux_212);
    pyc_mux_214 = (pyc_comb_188.toBool() ? cache__rr__s6 : pyc_mux_213);
    pyc_mux_215 = (pyc_comb_189.toBool() ? cache__rr__s7 : pyc_mux_214);
    cache__repl_way__jit_cache__L219 = pyc_mux_215;
    pyc_and_216 = (pyc_comb_197 & pyc_comb_182);
    pyc_add_217 = (cache__rr__s0 + pyc_comb_25);
    pyc_eq_218 = pyc::cpp::Wire<1>((cache__rr__s0 == pyc_comb_25) ? 1u : 0u);
    pyc_not_219 = (~pyc_eq_218);
    pyc_and_220 = (pyc_not_219 & pyc_add_217);
    pyc_mux_221 = (pyc_and_216.toBool() ? pyc_and_220 : cache__rr__s0);
    pyc_comb_222 = cache__rr__s1;
    pyc_comb_223 = cache__rr__s2;
    pyc_comb_224 = cache__rr__s3;
    pyc_comb_225 = cache__rr__s4;
    pyc_comb_226 = cache__rr__s5;
    pyc_comb_227 = cache__rr__s6;
    pyc_comb_228 = cache__rr__s7;
    pyc_comb_229 = cache__repl_way__jit_cache__L219;
    pyc_comb_230 = pyc_and_216;
    pyc_comb_231 = pyc_mux_221;
  }

  inline void eval_comb_4() {
    pyc_eq_232 = pyc::cpp::Wire<1>((pyc_comb_229 == pyc_comb_27) ? 1u : 0u);
    pyc_and_233 = (pyc_comb_230 & pyc_eq_232);
    pyc_or_234 = (pyc_and_233 | pyc_comb_65);
    pyc_comb_235 = pyc_eq_232;
    pyc_comb_236 = pyc_and_233;
    pyc_comb_237 = pyc_or_234;
  }

  inline void eval_comb_5() {
    pyc_eq_240 = pyc::cpp::Wire<1>((pyc_comb_229 == pyc_comb_25) ? 1u : 0u);
    pyc_and_241 = (pyc_comb_230 & pyc_eq_240);
    pyc_or_242 = (pyc_and_241 | pyc_comb_66);
    pyc_comb_243 = pyc_eq_240;
    pyc_comb_244 = pyc_and_241;
    pyc_comb_245 = pyc_or_242;
  }

  inline void eval_comb_6() {
    pyc_and_248 = (pyc_comb_197 & pyc_comb_183);
    pyc_add_249 = (pyc_comb_222 + pyc_comb_25);
    pyc_eq_250 = pyc::cpp::Wire<1>((pyc_comb_222 == pyc_comb_25) ? 1u : 0u);
    pyc_not_251 = (~pyc_eq_250);
    pyc_and_252 = (pyc_not_251 & pyc_add_249);
    pyc_mux_253 = (pyc_and_248.toBool() ? pyc_and_252 : pyc_comb_222);
    pyc_comb_254 = pyc_and_248;
    pyc_comb_255 = pyc_mux_253;
  }

  inline void eval_comb_7() {
    pyc_and_256 = (pyc_comb_254 & pyc_comb_235);
    pyc_or_257 = (pyc_and_256 | pyc_comb_67);
    pyc_comb_258 = pyc_and_256;
    pyc_comb_259 = pyc_or_257;
  }

  inline void eval_comb_8() {
    pyc_and_262 = (pyc_comb_254 & pyc_comb_243);
    pyc_or_263 = (pyc_and_262 | pyc_comb_68);
    pyc_comb_264 = pyc_and_262;
    pyc_comb_265 = pyc_or_263;
  }

  inline void eval_comb_9() {
    pyc_and_268 = (pyc_comb_197 & pyc_comb_184);
    pyc_add_269 = (pyc_comb_223 + pyc_comb_25);
    pyc_eq_270 = pyc::cpp::Wire<1>((pyc_comb_223 == pyc_comb_25) ? 1u : 0u);
    pyc_not_271 = (~pyc_eq_270);
    pyc_and_272 = (pyc_not_271 & pyc_add_269);
    pyc_mux_273 = (pyc_and_268.toBool() ? pyc_and_272 : pyc_comb_223);
    pyc_comb_274 = pyc_and_268;
    pyc_comb_275 = pyc_mux_273;
  }

  inline void eval_comb_10() {
    pyc_and_276 = (pyc_comb_274 & pyc_comb_235);
    pyc_or_277 = (pyc_and_276 | pyc_comb_69);
    pyc_comb_278 = pyc_and_276;
    pyc_comb_279 = pyc_or_277;
  }

  inline void eval_comb_11() {
    pyc_and_282 = (pyc_comb_274 & pyc_comb_243);
    pyc_or_283 = (pyc_and_282 | pyc_comb_70);
    pyc_comb_284 = pyc_and_282;
    pyc_comb_285 = pyc_or_283;
  }

  inline void eval_comb_12() {
    pyc_and_288 = (pyc_comb_197 & pyc_comb_185);
    pyc_add_289 = (pyc_comb_224 + pyc_comb_25);
    pyc_eq_290 = pyc::cpp::Wire<1>((pyc_comb_224 == pyc_comb_25) ? 1u : 0u);
    pyc_not_291 = (~pyc_eq_290);
    pyc_and_292 = (pyc_not_291 & pyc_add_289);
    pyc_mux_293 = (pyc_and_288.toBool() ? pyc_and_292 : pyc_comb_224);
    pyc_comb_294 = pyc_and_288;
    pyc_comb_295 = pyc_mux_293;
  }

  inline void eval_comb_13() {
    pyc_and_296 = (pyc_comb_294 & pyc_comb_235);
    pyc_or_297 = (pyc_and_296 | pyc_comb_71);
    pyc_comb_298 = pyc_and_296;
    pyc_comb_299 = pyc_or_297;
  }

  inline void eval_comb_14() {
    pyc_and_302 = (pyc_comb_294 & pyc_comb_243);
    pyc_or_303 = (pyc_and_302 | pyc_comb_72);
    pyc_comb_304 = pyc_and_302;
    pyc_comb_305 = pyc_or_303;
  }

  inline void eval_comb_15() {
    pyc_and_308 = (pyc_comb_197 & pyc_comb_186);
    pyc_add_309 = (pyc_comb_225 + pyc_comb_25);
    pyc_eq_310 = pyc::cpp::Wire<1>((pyc_comb_225 == pyc_comb_25) ? 1u : 0u);
    pyc_not_311 = (~pyc_eq_310);
    pyc_and_312 = (pyc_not_311 & pyc_add_309);
    pyc_mux_313 = (pyc_and_308.toBool() ? pyc_and_312 : pyc_comb_225);
    pyc_comb_314 = pyc_and_308;
    pyc_comb_315 = pyc_mux_313;
  }

  inline void eval_comb_16() {
    pyc_and_316 = (pyc_comb_314 & pyc_comb_235);
    pyc_or_317 = (pyc_and_316 | pyc_comb_73);
    pyc_comb_318 = pyc_and_316;
    pyc_comb_319 = pyc_or_317;
  }

  inline void eval_comb_17() {
    pyc_and_322 = (pyc_comb_314 & pyc_comb_243);
    pyc_or_323 = (pyc_and_322 | pyc_comb_74);
    pyc_comb_324 = pyc_and_322;
    pyc_comb_325 = pyc_or_323;
  }

  inline void eval_comb_18() {
    pyc_and_328 = (pyc_comb_197 & pyc_comb_187);
    pyc_add_329 = (pyc_comb_226 + pyc_comb_25);
    pyc_eq_330 = pyc::cpp::Wire<1>((pyc_comb_226 == pyc_comb_25) ? 1u : 0u);
    pyc_not_331 = (~pyc_eq_330);
    pyc_and_332 = (pyc_not_331 & pyc_add_329);
    pyc_mux_333 = (pyc_and_328.toBool() ? pyc_and_332 : pyc_comb_226);
    pyc_comb_334 = pyc_and_328;
    pyc_comb_335 = pyc_mux_333;
  }

  inline void eval_comb_19() {
    pyc_and_336 = (pyc_comb_334 & pyc_comb_235);
    pyc_or_337 = (pyc_and_336 | pyc_comb_75);
    pyc_comb_338 = pyc_and_336;
    pyc_comb_339 = pyc_or_337;
  }

  inline void eval_comb_20() {
    pyc_and_342 = (pyc_comb_334 & pyc_comb_243);
    pyc_or_343 = (pyc_and_342 | pyc_comb_76);
    pyc_comb_344 = pyc_and_342;
    pyc_comb_345 = pyc_or_343;
  }

  inline void eval_comb_21() {
    pyc_and_348 = (pyc_comb_197 & pyc_comb_188);
    pyc_add_349 = (pyc_comb_227 + pyc_comb_25);
    pyc_eq_350 = pyc::cpp::Wire<1>((pyc_comb_227 == pyc_comb_25) ? 1u : 0u);
    pyc_not_351 = (~pyc_eq_350);
    pyc_and_352 = (pyc_not_351 & pyc_add_349);
    pyc_mux_353 = (pyc_and_348.toBool() ? pyc_and_352 : pyc_comb_227);
    pyc_comb_354 = pyc_and_348;
    pyc_comb_355 = pyc_mux_353;
  }

  inline void eval_comb_22() {
    pyc_and_356 = (pyc_comb_354 & pyc_comb_235);
    pyc_or_357 = (pyc_and_356 | pyc_comb_77);
    pyc_comb_358 = pyc_and_356;
    pyc_comb_359 = pyc_or_357;
  }

  inline void eval_comb_23() {
    pyc_and_362 = (pyc_comb_354 & pyc_comb_243);
    pyc_or_363 = (pyc_and_362 | pyc_comb_78);
    pyc_comb_364 = pyc_and_362;
    pyc_comb_365 = pyc_or_363;
  }

  inline void eval_comb_24() {
    pyc_and_368 = (pyc_comb_197 & pyc_comb_189);
    pyc_add_369 = (pyc_comb_228 + pyc_comb_25);
    pyc_eq_370 = pyc::cpp::Wire<1>((pyc_comb_228 == pyc_comb_25) ? 1u : 0u);
    pyc_not_371 = (~pyc_eq_370);
    pyc_and_372 = (pyc_not_371 & pyc_add_369);
    pyc_mux_373 = (pyc_and_368.toBool() ? pyc_and_372 : pyc_comb_228);
    pyc_comb_374 = pyc_and_368;
    pyc_comb_375 = pyc_mux_373;
  }

  inline void eval_comb_25() {
    pyc_and_376 = (pyc_comb_374 & pyc_comb_235);
    pyc_or_377 = (pyc_and_376 | pyc_comb_79);
    pyc_comb_378 = pyc_and_376;
    pyc_comb_379 = pyc_or_377;
  }

  inline void eval_comb_26() {
    pyc_and_382 = (pyc_comb_374 & pyc_comb_243);
    pyc_or_383 = (pyc_and_382 | pyc_comb_80);
    pyc_comb_384 = pyc_and_382;
    pyc_comb_385 = pyc_or_383;
  }

  inline void eval_comb_27() {
    pyc_mux_388 = (pyc_comb_190.toBool() ? pyc_comb_191 : pyc_comb_196);
    cache__rdata__jit_cache__L232 = pyc_mux_388;
    pyc_concat_389 = pyc::cpp::concat(pyc_comb_190, cache__rdata__jit_cache__L232);
    cache__rsp_pkt__jit_cache__L233 = pyc_concat_389;
    pyc_comb_390 = cache__rsp_pkt__jit_cache__L233;
  }

  inline void eval_comb_28() {
    pyc_extract_43 = pyc::cpp::extract<1, 33>(pyc_fifo_42, 32u);
    cache__rsp_hit__jit_cache__L183 = pyc_extract_43;
    pyc_extract_44 = pyc::cpp::extract<32, 33>(pyc_fifo_42, 0u);
    cache__rsp_rdata__jit_cache__L184 = pyc_extract_44;
    pyc_comb_45 = cache__rsp_hit__jit_cache__L183;
    pyc_comb_46 = cache__rsp_rdata__jit_cache__L184;
  }

  inline void eval_comb_29() {
    pyc_extract_49 = pyc::cpp::extract<1, 16>(pyc_reg_48, 0u);
    pyc_extract_50 = pyc::cpp::extract<1, 16>(pyc_reg_48, 1u);
    pyc_extract_51 = pyc::cpp::extract<1, 16>(pyc_reg_48, 2u);
    pyc_extract_52 = pyc::cpp::extract<1, 16>(pyc_reg_48, 3u);
    pyc_extract_53 = pyc::cpp::extract<1, 16>(pyc_reg_48, 4u);
    pyc_extract_54 = pyc::cpp::extract<1, 16>(pyc_reg_48, 5u);
    pyc_extract_55 = pyc::cpp::extract<1, 16>(pyc_reg_48, 6u);
    pyc_extract_56 = pyc::cpp::extract<1, 16>(pyc_reg_48, 7u);
    pyc_extract_57 = pyc::cpp::extract<1, 16>(pyc_reg_48, 8u);
    pyc_extract_58 = pyc::cpp::extract<1, 16>(pyc_reg_48, 9u);
    pyc_extract_59 = pyc::cpp::extract<1, 16>(pyc_reg_48, 10u);
    pyc_extract_60 = pyc::cpp::extract<1, 16>(pyc_reg_48, 11u);
    pyc_extract_61 = pyc::cpp::extract<1, 16>(pyc_reg_48, 12u);
    pyc_extract_62 = pyc::cpp::extract<1, 16>(pyc_reg_48, 13u);
    pyc_extract_63 = pyc::cpp::extract<1, 16>(pyc_reg_48, 14u);
    pyc_extract_64 = pyc::cpp::extract<1, 16>(pyc_reg_48, 15u);
    cache__valid__s0__w0 = pyc_extract_49;
    cache__valid__s0__w1 = pyc_extract_50;
    cache__valid__s1__w0 = pyc_extract_51;
    cache__valid__s1__w1 = pyc_extract_52;
    cache__valid__s2__w0 = pyc_extract_53;
    cache__valid__s2__w1 = pyc_extract_54;
    cache__valid__s3__w0 = pyc_extract_55;
    cache__valid__s3__w1 = pyc_extract_56;
    cache__valid__s4__w0 = pyc_extract_57;
    cache__valid__s4__w1 = pyc_extract_58;
    cache__valid__s5__w0 = pyc_extract_59;
    cache__valid__s5__w1 = pyc_extract_60;
    cache__valid__s6__w0 = pyc_extract_61;
    cache__valid__s6__w1 = pyc_extract_62;
    cache__valid__s7__w0 = pyc_extract_63;
    cache__valid__s7__w1 = pyc_extract_64;
    pyc_comb_65 = cache__valid__s0__w0;
    pyc_comb_66 = cache__valid__s0__w1;
    pyc_comb_67 = cache__valid__s1__w0;
    pyc_comb_68 = cache__valid__s1__w1;
    pyc_comb_69 = cache__valid__s2__w0;
    pyc_comb_70 = cache__valid__s2__w1;
    pyc_comb_71 = cache__valid__s3__w0;
    pyc_comb_72 = cache__valid__s3__w1;
    pyc_comb_73 = cache__valid__s4__w0;
    pyc_comb_74 = cache__valid__s4__w1;
    pyc_comb_75 = cache__valid__s5__w0;
    pyc_comb_76 = cache__valid__s5__w1;
    pyc_comb_77 = cache__valid__s6__w0;
    pyc_comb_78 = cache__valid__s6__w1;
    pyc_comb_79 = cache__valid__s7__w0;
    pyc_comb_80 = cache__valid__s7__w1;
  }

  inline void eval_comb_pass() {
    eval_comb_0();
    eval_comb_28();
    pyc_concat_47 = pyc::cpp::concat(cache__valid__s7__w1__next, cache__valid__s7__w0__next, cache__valid__s6__w1__next, cache__valid__s6__w0__next, cache__valid__s5__w1__next, cache__valid__s5__w0__next, cache__valid__s4__w1__next, cache__valid__s4__w0__next, cache__valid__s3__w1__next, cache__valid__s3__w0__next, cache__valid__s2__w1__next, cache__valid__s2__w0__next, cache__valid__s1__w1__next, cache__valid__s1__w0__next, cache__valid__s0__w1__next, cache__valid__s0__w0__next);
    eval_comb_29();
    cache__tag__s0__w0 = pyc_reg_81;
    cache__tag__s0__w1 = pyc_reg_82;
    cache__tag__s1__w0 = pyc_reg_83;
    cache__tag__s1__w1 = pyc_reg_84;
    cache__tag__s2__w0 = pyc_reg_85;
    cache__tag__s2__w1 = pyc_reg_86;
    cache__tag__s3__w0 = pyc_reg_87;
    cache__tag__s3__w1 = pyc_reg_88;
    cache__tag__s4__w0 = pyc_reg_89;
    cache__tag__s4__w1 = pyc_reg_90;
    cache__tag__s5__w0 = pyc_reg_91;
    cache__tag__s5__w1 = pyc_reg_92;
    cache__tag__s6__w0 = pyc_reg_93;
    cache__tag__s6__w1 = pyc_reg_94;
    cache__tag__s7__w0 = pyc_reg_95;
    cache__tag__s7__w1 = pyc_reg_96;
    cache__data__s0__w0 = pyc_reg_97;
    cache__data__s0__w1 = pyc_reg_98;
    cache__data__s1__w0 = pyc_reg_99;
    cache__data__s1__w1 = pyc_reg_100;
    cache__data__s2__w0 = pyc_reg_101;
    cache__data__s2__w1 = pyc_reg_102;
    cache__data__s3__w0 = pyc_reg_103;
    cache__data__s3__w1 = pyc_reg_104;
    cache__data__s4__w0 = pyc_reg_105;
    cache__data__s4__w1 = pyc_reg_106;
    cache__data__s5__w0 = pyc_reg_107;
    cache__data__s5__w1 = pyc_reg_108;
    cache__data__s6__w0 = pyc_reg_109;
    cache__data__s6__w1 = pyc_reg_110;
    cache__data__s7__w0 = pyc_reg_111;
    cache__data__s7__w1 = pyc_reg_112;
    eval_comb_1();
    eval_comb_2();
    eval_comb_3();
    cache__rr__s0__next = pyc_comb_231;
    eval_comb_4();
    cache__valid__s0__w0__next = pyc_comb_237;
    pyc_mux_238 = (pyc_comb_236.toBool() ? pyc_comb_181 : cache__tag__s0__w0);
    cache__tag__s0__w0__next = pyc_mux_238;
    pyc_mux_239 = (pyc_comb_236.toBool() ? pyc_comb_196 : cache__data__s0__w0);
    cache__data__s0__w0__next = pyc_mux_239;
    eval_comb_5();
    cache__valid__s0__w1__next = pyc_comb_245;
    pyc_mux_246 = (pyc_comb_244.toBool() ? pyc_comb_181 : cache__tag__s0__w1);
    cache__tag__s0__w1__next = pyc_mux_246;
    pyc_mux_247 = (pyc_comb_244.toBool() ? pyc_comb_196 : cache__data__s0__w1);
    cache__data__s0__w1__next = pyc_mux_247;
    eval_comb_6();
    cache__rr__s1__next = pyc_comb_255;
    eval_comb_7();
    cache__valid__s1__w0__next = pyc_comb_259;
    pyc_mux_260 = (pyc_comb_258.toBool() ? pyc_comb_181 : cache__tag__s1__w0);
    cache__tag__s1__w0__next = pyc_mux_260;
    pyc_mux_261 = (pyc_comb_258.toBool() ? pyc_comb_196 : cache__data__s1__w0);
    cache__data__s1__w0__next = pyc_mux_261;
    eval_comb_8();
    cache__valid__s1__w1__next = pyc_comb_265;
    pyc_mux_266 = (pyc_comb_264.toBool() ? pyc_comb_181 : cache__tag__s1__w1);
    cache__tag__s1__w1__next = pyc_mux_266;
    pyc_mux_267 = (pyc_comb_264.toBool() ? pyc_comb_196 : cache__data__s1__w1);
    cache__data__s1__w1__next = pyc_mux_267;
    eval_comb_9();
    cache__rr__s2__next = pyc_comb_275;
    eval_comb_10();
    cache__valid__s2__w0__next = pyc_comb_279;
    pyc_mux_280 = (pyc_comb_278.toBool() ? pyc_comb_181 : cache__tag__s2__w0);
    cache__tag__s2__w0__next = pyc_mux_280;
    pyc_mux_281 = (pyc_comb_278.toBool() ? pyc_comb_196 : cache__data__s2__w0);
    cache__data__s2__w0__next = pyc_mux_281;
    eval_comb_11();
    cache__valid__s2__w1__next = pyc_comb_285;
    pyc_mux_286 = (pyc_comb_284.toBool() ? pyc_comb_181 : cache__tag__s2__w1);
    cache__tag__s2__w1__next = pyc_mux_286;
    pyc_mux_287 = (pyc_comb_284.toBool() ? pyc_comb_196 : cache__data__s2__w1);
    cache__data__s2__w1__next = pyc_mux_287;
    eval_comb_12();
    cache__rr__s3__next = pyc_comb_295;
    eval_comb_13();
    cache__valid__s3__w0__next = pyc_comb_299;
    pyc_mux_300 = (pyc_comb_298.toBool() ? pyc_comb_181 : cache__tag__s3__w0);
    cache__tag__s3__w0__next = pyc_mux_300;
    pyc_mux_301 = (pyc_comb_298.toBool() ? pyc_comb_196 : cache__data__s3__w0);
    cache__data__s3__w0__next = pyc_mux_301;
    eval_comb_14();
    cache__valid__s3__w1__next = pyc_comb_305;
    pyc_mux_306 = (pyc_comb_304.toBool() ? pyc_comb_181 : cache__tag__s3__w1);
    cache__tag__s3__w1__next = pyc_mux_306;
    pyc_mux_307 = (pyc_comb_304.toBool() ? pyc_comb_196 : cache__data__s3__w1);
    cache__data__s3__w1__next = pyc_mux_307;
    eval_comb_15();
    cache__rr__s4__next = pyc_comb_315;
    eval_comb_16();
    cache__valid__s4__w0__next = pyc_comb_319;
    pyc_mux_320 = (pyc_comb_318.toBool() ? pyc_comb_181 : cache__tag__s4__w0);
    cache__tag__s4__w0__next = pyc_mux_320;
    pyc_mux_321 = (pyc_comb_318.toBool() ? pyc_comb_196 : cache__data__s4__w0);
    cache__data__s4__w0__next = pyc_mux_321;
    eval_comb_17();
    cache__valid__s4__w1__next = pyc_comb_325;
    pyc_mux_326 = (pyc_comb_324.toBool() ? pyc_comb_181 : cache__tag__s4__w1);
    cache__tag__s4__w1__next = pyc_mux_326;
    pyc_mux_327 = (pyc_comb_324.toBool() ? pyc_comb_196 : cache__data__s4__w1);
    cache__data__s4__w1__next = pyc_mux_327;
    eval_comb_18();
    cache__rr__s5__next = pyc_comb_335;
    eval_comb_19();
    cache__valid__s5__w0__next = pyc_comb_339;
    pyc_mux_340 = (pyc_comb_338.toBool() ? pyc_comb_181 : cache__tag__s5__w0);
    cache__tag__s5__w0__next = pyc_mux_340;
    pyc_mux_341 = (pyc_comb_338.toBool() ? pyc_comb_196 : cache__data__s5__w0);
    cache__data__s5__w0__next = pyc_mux_341;
    eval_comb_20();
    cache__valid__s5__w1__next = pyc_comb_345;
    pyc_mux_346 = (pyc_comb_344.toBool() ? pyc_comb_181 : cache__tag__s5__w1);
    cache__tag__s5__w1__next = pyc_mux_346;
    pyc_mux_347 = (pyc_comb_344.toBool() ? pyc_comb_196 : cache__data__s5__w1);
    cache__data__s5__w1__next = pyc_mux_347;
    eval_comb_21();
    cache__rr__s6__next = pyc_comb_355;
    eval_comb_22();
    cache__valid__s6__w0__next = pyc_comb_359;
    pyc_mux_360 = (pyc_comb_358.toBool() ? pyc_comb_181 : cache__tag__s6__w0);
    cache__tag__s6__w0__next = pyc_mux_360;
    pyc_mux_361 = (pyc_comb_358.toBool() ? pyc_comb_196 : cache__data__s6__w0);
    cache__data__s6__w0__next = pyc_mux_361;
    eval_comb_23();
    cache__valid__s6__w1__next = pyc_comb_365;
    pyc_mux_366 = (pyc_comb_364.toBool() ? pyc_comb_181 : cache__tag__s6__w1);
    cache__tag__s6__w1__next = pyc_mux_366;
    pyc_mux_367 = (pyc_comb_364.toBool() ? pyc_comb_196 : cache__data__s6__w1);
    cache__data__s6__w1__next = pyc_mux_367;
    eval_comb_24();
    cache__rr__s7__next = pyc_comb_375;
    eval_comb_25();
    cache__valid__s7__w0__next = pyc_comb_379;
    pyc_mux_380 = (pyc_comb_378.toBool() ? pyc_comb_181 : cache__tag__s7__w0);
    cache__tag__s7__w0__next = pyc_mux_380;
    pyc_mux_381 = (pyc_comb_378.toBool() ? pyc_comb_196 : cache__data__s7__w0);
    cache__data__s7__w0__next = pyc_mux_381;
    eval_comb_26();
    cache__valid__s7__w1__next = pyc_comb_385;
    pyc_mux_386 = (pyc_comb_384.toBool() ? pyc_comb_181 : cache__tag__s7__w1);
    cache__tag__s7__w1__next = pyc_mux_386;
    pyc_mux_387 = (pyc_comb_384.toBool() ? pyc_comb_196 : cache__data__s7__w1);
    cache__data__s7__w1__next = pyc_mux_387;
    eval_comb_27();
    cache__req_q__out_ready = pyc_fifo_40;
    cache__rsp_q__in_valid = pyc_comb_179;
    cache__rsp_q__in_data = pyc_comb_390;
  }

  void eval() {
    eval_comb_pass();
    for (unsigned _i = 0; _i < 3u; ++_i) {
      pyc_fifo_37_inst.eval();
      pyc_fifo_40_inst.eval();
      main_mem.eval();
      eval_comb_pass();
    }
    req_ready = pyc_fifo_37;
    rsp_valid = pyc_fifo_41;
    rsp_hit = pyc_comb_45;
    rsp_rdata = pyc_comb_46;
  }

  void tick_compute() {
    // Local sequential primitives.
    pyc_reg_100_inst.tick_compute();
    pyc_reg_101_inst.tick_compute();
    pyc_reg_102_inst.tick_compute();
    pyc_reg_103_inst.tick_compute();
    pyc_reg_104_inst.tick_compute();
    pyc_reg_105_inst.tick_compute();
    pyc_reg_106_inst.tick_compute();
    pyc_reg_107_inst.tick_compute();
    pyc_reg_108_inst.tick_compute();
    pyc_reg_109_inst.tick_compute();
    pyc_reg_110_inst.tick_compute();
    pyc_reg_111_inst.tick_compute();
    pyc_reg_112_inst.tick_compute();
    pyc_reg_199_inst.tick_compute();
    pyc_reg_48_inst.tick_compute();
    pyc_reg_81_inst.tick_compute();
    pyc_reg_82_inst.tick_compute();
    pyc_reg_83_inst.tick_compute();
    pyc_reg_84_inst.tick_compute();
    pyc_reg_85_inst.tick_compute();
    pyc_reg_86_inst.tick_compute();
    pyc_reg_87_inst.tick_compute();
    pyc_reg_88_inst.tick_compute();
    pyc_reg_89_inst.tick_compute();
    pyc_reg_90_inst.tick_compute();
    pyc_reg_91_inst.tick_compute();
    pyc_reg_92_inst.tick_compute();
    pyc_reg_93_inst.tick_compute();
    pyc_reg_94_inst.tick_compute();
    pyc_reg_95_inst.tick_compute();
    pyc_reg_96_inst.tick_compute();
    pyc_reg_97_inst.tick_compute();
    pyc_reg_98_inst.tick_compute();
    pyc_reg_99_inst.tick_compute();
    pyc_fifo_37_inst.tick_compute();
    pyc_fifo_40_inst.tick_compute();
    main_mem.tick_compute();
  }

  void tick_commit() {
    // Local sequential primitives.
    pyc_reg_100_inst.tick_commit();
    pyc_reg_101_inst.tick_commit();
    pyc_reg_102_inst.tick_commit();
    pyc_reg_103_inst.tick_commit();
    pyc_reg_104_inst.tick_commit();
    pyc_reg_105_inst.tick_commit();
    pyc_reg_106_inst.tick_commit();
    pyc_reg_107_inst.tick_commit();
    pyc_reg_108_inst.tick_commit();
    pyc_reg_109_inst.tick_commit();
    pyc_reg_110_inst.tick_commit();
    pyc_reg_111_inst.tick_commit();
    pyc_reg_112_inst.tick_commit();
    pyc_reg_199_inst.tick_commit();
    pyc_reg_48_inst.tick_commit();
    pyc_reg_81_inst.tick_commit();
    pyc_reg_82_inst.tick_commit();
    pyc_reg_83_inst.tick_commit();
    pyc_reg_84_inst.tick_commit();
    pyc_reg_85_inst.tick_commit();
    pyc_reg_86_inst.tick_commit();
    pyc_reg_87_inst.tick_commit();
    pyc_reg_88_inst.tick_commit();
    pyc_reg_89_inst.tick_commit();
    pyc_reg_90_inst.tick_commit();
    pyc_reg_91_inst.tick_commit();
    pyc_reg_92_inst.tick_commit();
    pyc_reg_93_inst.tick_commit();
    pyc_reg_94_inst.tick_commit();
    pyc_reg_95_inst.tick_commit();
    pyc_reg_96_inst.tick_commit();
    pyc_reg_97_inst.tick_commit();
    pyc_reg_98_inst.tick_commit();
    pyc_reg_99_inst.tick_commit();
    pyc_fifo_37_inst.tick_commit();
    pyc_fifo_40_inst.tick_commit();
    main_mem.tick_commit();
  }

  void tick() {
    tick_compute();
    tick_commit();
  }
};

} // namespace pyc::gen
