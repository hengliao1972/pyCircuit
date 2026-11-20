# 寄存器上移：交给下一个 agent 的交接

日期：2026-11-02。工作目录：`pyCircuit-reg-upstream/`。分支 `ldz/exp/reg-upstream`，tip `40622c6178ab6c52fbcb5594114bebf668eeb88d`（`feat(state): hoist registers one comb op upstream`）。远端已推送。Linux CI 与 macOS CI 都是 success（[Linux](https://github.com/CISArch/pyCircuit/actions/runs/41386578416)、[macOS](https://github.com/CISArch/pyCircuit/actions/runs/41386578578)）。

本文是探索交接，不是新的设计批准。已批准并落地的方案在 `docs/reg_upstream_retime_plan.md`。

## 1. 用户期望

用户要的不是把仿真立刻跑快，而是给后面的 comb 划分腾空间：

- 两条支路打拍数不同时，**不能**收成一个寄存器。拍数差是语义，消掉会错。
- 把多出来的那几拍尽量挪到支路上游（靠近分叉、靠近输入）。
- 被越过的组合逻辑落到寄存器下游，和汇合点、以及另一条支路同一拍的组合逻辑连成一片。
- 每条从输入到输出的路径上，寄存器个数不变。
- 只在 C++ 路径做。Verilog 网表保持原样。
- 并行的 `cycle_balance` 拷贝合并**不是**这次的范围。

示意：

```
现在:   分叉 ─ f ─ R ─ g ─ 汇合
        分叉 ─ h ───── 汇合

期望:   分叉 ─ R ─ f ─ g ─ 汇合
        分叉 ─ h ───── 汇合
```

`f`、`g`、`h` 和汇合点之间不再有寄存器。长支路仍然多 1 拍。

更早的讨论里还有两件相关、但没在本分支做的事：

1. 两条支路拍数相同，可以把分叉后的多份寄存器收成一份（情况 1）。Davinci 上大头是 frontend `clk.next()` 插入的并行 `cycle_balance` 拷贝，现有 pass 只折串行链，不合并并行拷贝。
2. 一边有寄存器、一边没有：差值消不掉，只能把那一拍挪到分叉口、让延迟支路共享。这就是上面的上移，不是合并。

## 2. 已拍板的实现策略（选项 B）

审查时在两个策略里选了 **B**：

- 一次只跨过生产 `next` 的**那一个**组合操作，然后重复做到不能再挪。
- 该操作有多个非常量输入时，**每个输入一个新寄存器**。位数允许变多。
- 不一次跨过整棵锥。整棵锥一起跨会在每个叶子上放寄存器。事前估计会把约 8.9 万 bit 变成约 598 万 bit。
- 初值必须能用少量结构候选解出来（全 0、把旧初值放到某一侧、全 1、一元取反、mux 的两种选择）。解不出就跳过，不搜索 `2^位宽`。
- 输出侧（剥掉保持 mux、alias、单驱动 wire 之后）没有组合逻辑用户的寄存器不动，留给现有 delay-line 成链。
- 数据锥依赖自己的 `q`（反馈）不动。保持 mux `mux(cond, data, q)` 先收成 enable，再挪 data。
- 带 `pyc.name` 等观测身份的原操作留在原拍；克隆到寄存器下游的副本去掉这些属性。

当时的另一选项 A 是「新位宽之和不超过原寄存器才挪」。没有实现。A 在 Davinci 上第一轮大约只有几百个寄存器会动，但不会把状态位数拉高。

## 3. 代码改了什么

基线是八层实验栈终点 `ldz/exp/dirty-consume` @ `f70f6a6`。不要在 `pyCircuit-dirty-consume/` 里改。

`pyc-state-optimize`（仅 `--emit=cpp`）的顺序现在是：

1. `runEliminateDeadState`
2. **`runHoistRegsUpstream`（新）**
3. `runRetimePipelines`（原样，没有放宽匹配）
4. canonicalize + CSE
5. `runEliminateDeadState`
6. `runDelayChainFormAndShare`

新 pass 名：`pyc-hoist-regs-upstream`。统计写在函数上：

- `pyc.stats.hoist_regs_upstream`：成功上移次数（同一寄存器跨多步算多次）
- `pyc.stats.hoist_ops_moved`：挪到下游的组合操作数
- `pyc.stats.hoist_blocked_init`：初值无解而放弃的次数

没有 `hoist_blocked_fanin`。选项 B 不因为位数变多而放弃。新寄存器带 `pyc.optimized_by = "hoist_regs_upstream"`。若原寄存器有 `pyc.generated`，会抄到新寄存器上。

| 文件 | 作用 |
| --- | --- |
| `compiler/mlir/lib/Transforms/HoistRegsUpstreamPass.cpp` | 匹配、初值求解、改写 |
| `compiler/mlir/include/pyc/Transforms/Passes.h` | `createHoistRegsUpstreamPass`、`runHoistRegsUpstream` |
| `compiler/mlir/lib/Transforms/StateOptimizePass.cpp` | 在 retime 之前调用一次 |
| `compiler/mlir/CMakeLists.txt` | 把新 cpp 编进 `pyc_transforms` |
| `compiler/mlir/lib/Emit/CppEmitter.cpp` | 没有 comb 时，融合唤醒仍要一个 `DirtyBitset` 参数，补局部 `DirtyBitset<1>`。见第 6 节 |
| `compiler/mlir/test/reg_upstream_hoist.mlir` | FileCheck |
| `compiler/mlir/test/reg_upstream_hoist_smoke.sh` | FileCheck + 逐拍模型 |
| `compiler/mlir/test/reg_upstream_hoist_model.mlir` | `q' = a & b`，`out = q ^ a` |
| `compiler/mlir/test/reg_upstream_hoist_model.cpp` | 64 拍手写对照 |
| `.github/workflows/ci.yml` | G1 里紧挨 `state_retime_smoke.sh` |
| `docs/MLIR_PASS_PIPELINE.md`、`docs/delay_line.md` | 管线顺序多了上移这一步 |
| `docs/reg_upstream_retime_plan.md` | 已批准的方案和验证记录 |

工作区根 `README.md`、`AGENTS.md` 已加上 `pyCircuit-reg-upstream/`。那两个文件不在本 git 仓库里。

两输入 `and` 的改写形状：

```
改写前:  %n = pyc.and %a, %b
         %q = pyc.reg ... %n, %zero
         %u = pyc.xor %q, %a

改写后:  %ra = pyc.reg ... %a, %zero {pyc.optimized_by = "hoist_regs_upstream"}
         %rb = pyc.reg ... %b, %zero {pyc.optimized_by = "hoist_regs_upstream"}
         %n2 = pyc.and %ra, %rb
         %u  = pyc.xor %n2, %a
```

`mux` 的选择端为 0 时取第三个操作数（`b`），非 0 取第二个（`a`）。这和 `RetimePipelinesPass::evaluateConeOp` 一致。保持 mux 的判定按这个来。

### 3.1 pass 内部算法（`HoistRegsUpstreamPass.cpp`，代码级）

性质：不是求解器，也不是图上全局 retiming（Leiserson–Saxe 一类），是**局部语法模式重写 + 固定点迭代**。对每个 `pyc.reg` 反复尝试「跨过紧邻的一个组合门」，直到无寄存器可动或轮数用尽。

单次尝试（`hoistOne`）的步骤：

1. 门禁：`q` 是标量整数；不在本轮 `initBlocked` 集合里。
2. 剥保持 mux（`peelHold`）：沿 `next` 穿 alias / 单驱动 wire，剥最多 8 层 `mux(cond, data, q)` / `mux(cond, q, data)`，收成使能条件列表 + 数据锥根。
3. 匹配：锥根必须是与 reg 同一个 block 的白名单组合操作（22 种：add/sub/mul/and/or/xor/not/mux/eq/ult/slt/trunc/zext/sext/extract/shli/lshri/ashri/shl/lshr/ashr/concat）。
4. 值得性：剥掉 hold mux 之后，`q` 仍要有组合用户；没有就留给 delay-line 成链。
5. 收变量：锥根的非常量操作数（去重）就是各获得一个新寄存器的输入。任一操作数不是标量整数，或其组合锥依赖本寄存器自己的 `q`（反馈）→ 放弃整个 reg。
6. 初值求解（`solveInits`）：要求 `op(init_x1, init_x2, …) == 旧 init`。只试少量结构候选：全 0；每个变量轮流取旧 init / 1 / 全 1（其余变量为 0）；全 1；单变量时取 `~旧init`；mux 特例（sel=1 配 a 臂=旧init，sel=0 配 b 臂=旧init）。每个候选用 `evaluateOp`（与 `RetimePipelinesPass::evaluateConeOp` 同一套常量折叠语义）验证。无解 → `blocked_init` +1 并记入 `initBlocked`，之后不再重试这个 reg。
7. 重写：插入点取 reg / 锥根 / 各操作数 / 各使能条件里最靠后的那个之后；剥出的使能条件 AND 进原 `en`（反相用 `not`）；每个变量新建一个 `pyc.reg`（同 clk/rst，`next` = 该操作数，init = 解），标 `pyc.optimized_by="hoist_regs_upstream"`，继承 `pyc.generated`；克隆锥根（操作数换成新寄存器的 `q`），克隆上剥掉观测身份属性（`pyc.name` / `pyc.debug_keep` / `pyc.observable` / `pyc.probe*` / `pyc.trace*`）；原 `q` 的全部使用替换成克隆结果；删原 reg；清掉只剩 hold-mux 的独占 wire assign；锥根若已无用户且无外部观测身份则删；`sinkUsersBelow` 把下游用户沉到克隆之后。

正确性是 forward retiming 的标准归纳：`q(t) = op(qi(t))`，`q(0) = op(init_i)` 由第 6 步保证；新旧寄存器同 clk/rst，hold 语义经 mux 剥离并入同一个 `en`，所以逐拍等价，每条路径的寄存器个数不变。

驱动循环（`hoistUpstream`）：轮数上限 = 函数内操作总数（只防死循环，不限制增长）；每轮 walk 收集全部 `RegOp` 逐一尝试；一轮零成功即停。多输入门每跨一步寄存器数 ×2~×3，新寄存器的 `next` 往往还是组合门，下一轮继续拆——固定点呈指数增长，这是超时的算法根源。

## 4. 已经证明能工作的部分

本机，2026-11-02：

- `compiler/mlir/test/reg_upstream_hoist_smoke.sh` PASS。覆盖：两输入 `and`、单输入 `xor`（初值从 0 解出 1）、`not` 挪到分叉侧、两层 `not` 固定点、wire 后的保持 mux、带 `pyc.name = "sum"` 的 `add` 原操作留在原拍、自环不动、`and` 初值 2 无解不动、直连 `reg → reg` 不动。
- C++ 模型 64 拍与手写模型一致。生成统计：`regs=2 (16 bits)`，也就是一个 8 bit `and` 拆成两个 8 bit 寄存器。
- `state_retime_smoke.sh` PASS。`check_state_retime_models.py` 613 拍 checksum `4607132513909987476`，与改前一致。
- `make smoke` 通过（examples + sims）。
- GitHub CI Linux / macOS 全绿。

构建：

```bash
cd /home/lidongzhe/pycircuit-workspace/pyCircuit-reg-upstream
LLVM_DIR=/opt/llvm19/lib/cmake/llvm MLIR_DIR=/opt/llvm19/lib/cmake/mlir make tools
FILECHECK=/usr/lib64/llvm19/bin/FileCheck \
  PYC_OPT=.pycircuit_out/toolchain/build/bin/pyc-opt \
  PYCC=.pycircuit_out/toolchain/build/bin/pycc \
  bash compiler/mlir/test/reg_upstream_hoist_smoke.sh
```

`pyc-opt` 会把源码里的 `%clk` 收成 `%arg0`。FileCheck 按收名后的 IR 写。

## 5. 性能：没有数字，原因已经定位

auto_perf 任务 `task-20261102T040633971932Z-ldz_exp_reg-upstream-40622c61`（工作区 `auto_perf_logs/`，也就是本分支唯一一次性能任务）：

- status：`timeout`
- error：`davinci_top align timed out after 7200s`
- `pycc` 编译 23.2 秒
- 矩阵 7199.1 秒，死在第一条 trace `examples_advanced_gemm_eltwise.pto.trace` 的 `cpp-generate`
- 其余 33 条：`not run: matrix timed out`
- cycle/s、P/V、MATCH：都没有

对照：`ldz/exp/dirty-consume` @ `f70f6a6` 的任务 `task-20261101T072932427879Z` 是 pass，整组矩阵 410 秒，**12029.6 cycle/s，P/V 2.663**，34/34 MATCH。

IR dump 在同一任务的 `pass_ir/cpp/`（668MB，生成被杀时停在这里）。已经写完的 `state-optimize` 里，`pyc.stats.hoist_regs_upstream` 合计 **63576**。

`v5_group_scoreboard`（`0093_before_0054_state-optimize__L13.mlir` → `0171_after_0029_state-optimize__L6.mlir`）：

| | 上移前 | 上移后 |
| --- | ---: | ---: |
| 寄存器 | 83 | 26026 |
| `and` | 297 | 12145 |
| `not` | 96 | 12442 |
| `mux` | 323 | 5209 |
| `extract` | 32 | 8608 |
| 带 `hoist_regs_upstream` 的寄存器 | 0 | 25945 |

机制：多输入门每跨一步，1 个寄存器变成 2 个或 3 个，克隆出的门留在下游。新寄存器的 `next` 往往还是 `and` / `or` / `mux`，下一轮继续拆。二叉树每深一层大约翻倍。它会停，但停下来时状态已经很大。

scoreboard 里 `%103` 的 `next` 根是 `mux`，两层之内就有 `or`、多个 `and`、`eq`、`not`。这不是「一路信号晚一拍」。

`v5_core_pe_cluster1` 上移前 9669 个寄存器（`0157_before_0085_state-optimize__L11.mlir`，8.1MB）。目录里**没有**它的 `after state-optimize`。`pycc` 死在这个函数的上移里。按 scoreboard 大约 300 倍估计，这里会拆出上百万个寄存器。同一提交再跑 auto_perf 还会超时。

## 6. 顺手修过的一个发射器洞

`designs/examples/xz_value_model_smoke` 的前端 IR 就是 `reg.next = in_a`，没有组合操作，上移 pass 不会动它。融合唤醒 `FusedLaneWake::apply` 仍要一个 `DirtyBitset`。模块没有 comb 时，成员 `_pyc_comb_dirty` 不会声明，C++ 编不过。`CppEmitter.cpp` 在 `combs.empty()` 时补了局部 `pyc::cpp::DirtyBitset<1> _pyc_comb_dirty{}`，fused 路径和 phased commit 路径都补了。这是 `make smoke` 语义回归能过的原因。不要把它理解成上移 pass 的一部分。

## 7. 下一个 agent 不要做的事

- 不要对 `40622c6` 再提交 auto_perf。用户约定测试最多跑两次；这一次已经用掉，而且必超时。
- 不要在 `pyCircuit-dirty-consume/` 或其他功能目录里改上移。本功能只在 `pyCircuit-reg-upstream/`。
- 不要一次跨过整棵锥。
- 不要为了减寄存器去改拍数差。差值是不变量。
- 不要改 `RetimePipelinesPass` 的匹配来「顺便」做这件事。现有 retiming 是另一件事：单源流水线收成 `delay_line`，以及 common-delay sinking（组合逻辑拉到 `next` 一侧）。Davinci 上 `retime_regions_rewritten = 0`。它不穿 wire，也要求级间锥只有一个前级寄存器。
- 代码改动保持最小。先改文档里的策略并让用户看过，再改 pass。本仓库 `AGENTS.md` 要求 gate-first：语义在 MLIR pass 里，不在 emitter 里补行为。

## 8. 值得继续的方向

按「先能跑完 Davinci，再谈 comb 是否连上」排序：

1. **限制位数不增加（回到选项 A）**，或者给每个函数的新寄存器个数 / 上移次数一个上限。目标是 `pycc` 能在分钟级生成 Davinci，auto_perf 能跑完 34 条。验收仍然是现有 FileCheck 里「两输入 `and` 会拆」要改成「两输入同宽 `and` 不拆」，并加一条「窄输入、宽输出可以拆」。
2. **只挪单输入，或者新位宽之和 ≤ 原位宽。** 事前在 dirty-consume 的 IR 上粗算过：输出侧紧挨组合逻辑的约 12581 个寄存器里，单输入 380 个（4709 bit），多输入但不增加位数 145 个（16089 → 16039 bit），会变多的 12056 个（约 53.8 万 → 89.8 万 bit，这还只是跨一层，不是固定点）。固定点才是这次超时的原因。
3. 若 A 能跑完但 comb 几乎没变大，再考虑有上限的 B：例如一个函数新寄存器数不得超过原来的 2 倍，超过就停。
4. 另开题目、不要混进本 pass：同一 `clk/rst/en/init`、`next` 语义相同的并行 `cycle_balance` 合成一个寄存器或一条 `delay_line`。dirty-consume 上两个 PE cluster 里，`br_exec_e0_sequence_id` 一类 64 bit 信号有过 111 份拷贝。那是减状态，不是把 comb 做大。

分析脚本不在仓库里。只读统计用过 `/tmp/retime_fork_analyze.py` 和 `/tmp/retime_fork_report.json`，输入是 dirty-consume 那次 auto_perf 的 `pass_ir/cpp/*before*state-optimize*.mlir`。机器重启后 `/tmp` 可能没了。本分支这次任务的 `pass_ir/cpp/` 还在，可直接对比 scoreboard 的 before/after。

## 8A. 逐函数复核（2026-11-02 晚，同任务 `pass_ir/cpp/`）

func pass 并行执行，dump 文件序号交错；**按函数名配对**（after 文件内第一个 `@` 名即该函数），不是按 pass 实例号：

| 函数 | 上移前 reg | 上移后 reg | 存活 hoist reg | `hoist_regs_upstream` 动作数 | `blocked_init` |
| --- | ---: | ---: | ---: | ---: | ---: |
| `v5_group_scoreboard` | 83 | 26026 | 25944 | 51719 | 24 |
| `v5_shared_gm_interface` | 88 | 2454 | 2401 | 7199 | 0 |
| `v5_shared_mte_dma` | 87 | 1313 | 1232 | 2742 | 1 |
| `v5_gmov` | 148 | 1763 | 1637 | 1887 | 30 |
| `v5_stgbuf_lifecycle_coordinator` | 28 | 31（另有 8 条 delay_line） | 15 | 22 | 0 |
| `v5_stgbufb` | 267 | 273 | 7 | 7 | 0 |
| `v5_superscalar_v5_core` | 497 | 497 | 0 | 0 | 0 |
| `v5_shared_resources` | 114 | 114 | 0 | 0 | 0 |
| `v5_core_pe_cluster0` | 9669 | **无 after dump** | — | — | — |
| `v5_core_pe_cluster1` | 9669 | **无 after dump** | — | — | — |

动作数合计 63576，与 §5 的合计一致。定性结论：

- scoreboard 的 25944 个存活 hoist 寄存器**全部**带 `pyc.generated="cycle_balance"`；位宽合计 662 → 101403 bit（×153），宽度分布 1 bit×9824、2 bit×8584、3 bit×6674、64 bit×838。上移在 Davinci 上吃的正是 frontend `clk.next()` 插入的并行拷贝（§1 情况 1 的形状）。
- scoreboard 存活的 82 个非 hoist 寄存器，`next` 全是 wire——「输出侧无组合用户」按设计留原拍，符合预期。
- `v5_superscalar_v5_core`（497 reg）与 `v5_shared_resources`（114 reg）**一次都没动**：它们的寄存器输出侧没有组合用户（或锥根不匹配），直接走 delay-line。爆炸入口高度集中在 `cycle_balance` 形状。
- 两个 PE cluster（各 9669 reg）都只有 before、没有 after dump——`state-optimize` 在两个函数上都没跑完，不只是 pe_cluster1。`execution.log` 证实进程死在第一条 trace 的 `cpp-generate`（pycc IR 管线）内，7200 s SIGTERM。
- 复核脚本 `/tmp/hoist_analyze.py`（机器重启即失，逻辑见本表，可按 §3.1 重写）。

§8 的方向 1/2 由此有了更硬的依据：按函数限制动作数或新寄存器数即可按住爆炸——scoreboard 单函数 51719 次动作就是失控样本；反过来 `cycle_balance` 集中度 100% 也说明「并行拷贝合并」（§8 第 4 条）才是治本的路。

## 8B. 算法替换（2026-11-02 深夜已实现，分支 `ldz/exp/fork-merge-retime`）

用户提出用 **DFS 归并 retiming** 替换选项 B 的逐步上移：后序 DFS 回退时统计分叉节点各下游支路的寄存器级数，公共级数（min）合并到分叉上游成一条共享链，额外级数贴在分叉紧邻下游的支路边。设计与实现纪见 **`docs/reg_upstream_dfs_merge_plan.md`**（§8 实现纪要含实测与两个探针纠偏）。

实现要点：pass `pyc-fork-merge-retime`（替换 `runHoistRegsUpstream` 槽位，旧 pass 文件与测试已删）；延迟事件 (值, 延迟, 语义序列) 传播 + 惰性 liftCone（链级身份 =(输入值, 级语义) 天然前缀共享）；预算守卫（位宽×2.0 / 克隆×2.0 / MAX_DELAY 8）超限整函数退化纯归并。**gm_interface 真实 IR 实测 2454→182 reg（0.03s）**；scoreboard 因全上游化位宽比 ×153 被守卫正确拦截退化为归并；门禁/`state_retime_smoke`/`make smoke` 全过。

## 8C. auto_perf 提测记录（2026-11-03 上午，分支两次名额已用完）

| 提交 | 任务 | 结果 | 根因 |
| --- | --- | --- | --- |
| `0cfcd5b` | local:ab257892 | partial（34/34 C++ 侧失败，8 分钟） | `pyc.reg` verifier：链 init 类型用了级语义来源寄存器的位宽，跨宽度穿透（zext/concat/trunc/extract 改变事件值宽度）后 next/init/q 不同型 |
| `59f19bb` | local:447f1d03 | partial（同上） | 共享 handle（okMemo 跨 sink 复用）物化在依赖之后、位置晚于其它 sink 的既有用户，RAUW 后 `operand does not dominate this use` |

两次都在 **cpp-generate 阶段**（pycc IR 管线）失败，不是超时——对比旧实现 7200s 被杀，编译时间问题已解决。修复链：`59f19bb`（init 宽度失配即放弃整链，保守回退，+cross_width 门禁用例）→ `fae0932`（RAUW 后 sinkUsersBelow 把用户锥沉到 handle 之下，恢复旧 pass 同款做法）。本地门禁/state_retime/gm_interface（2454→182）全绿。**`fae0932` 尚未提测**——第三次需用户确认（约定每分支最多两次已用完）。

## 9. 名词

- **支路**：分叉点到汇合点的一条路径。拍数差是这条路径上的寄存器个数差。
- **多个输入**：寄存器正前方那一个门的非常量操作数，不是支路条数。`and %a, %b` 就是两个输入。只给 `a` 打拍、让 `b` 留在后面，会把「旧 a 与旧 b」算成「旧 a 与新 b」。
- **向上游**：寄存器改去采样更早的值，组合操作克隆到 `q` 的用户侧。
- **保持 mux**：`en` 端口是常量 1，保持写在 `next = mux(cond, data, q)` 里。Davinci 大量寄存器是这种形状。
- **cycle_balance**：frontend `clk.next()` 插入的 `_v5_bal_*`，属性 `pyc.generated = "cycle_balance"`。它的私有名字不算外部观测身份。
