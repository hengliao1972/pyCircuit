# DFS 归并 retiming（分叉口公共级数合并）设计规划

日期：2026-11-02 晚。状态：**已实现**（分支 `ldz/exp/fork-merge-retime`，pass `pyc-fork-merge-retime`；门禁 `fork_merge_retime_smoke.sh`、`state_retime_smoke.sh`、`make smoke` 全过）。实现与本文差异见文末「实现纪要」。提出人：用户。
本文取代 `reg_upstream_retime_plan.md` 中"选项 B 逐步上移"的后续地位；该方案已实现（`40622c6`）并在 auto_perf 上超时，证据见 `HANDOFF.md` §5/§8A。

## 1. 用户原始描述（照录）

> 对于这个有向无环图来说，可以进行 DFS，从输入节点开始向深处搜索，直到输出节点之后开始回退，在每个支路，统计每个下游分支的寄存器级数，如果有公共的级数，就合并到当前节点上游，留下额外的级数在当前分支节点的紧邻下一个节点。这样，DFS 递归、归并地实现 retiming。

形式化（对 `pyc-state-optimize` 输入的每个函数，组合 DAG = 状态元件（`pyc.reg` 的 q、端口、存储器）之间的无环组合逻辑）：

- **后序 DFS（回退时统计）**：从输入侧深搜，抵达输出/sink 后回退；回退路径上归并信息。
- **req(v)**：值 `v` 的下游分支寄存器级数多重集 = 从 `v` 沿数据流到各消费 sink 的路径寄存器个数。
- **分叉节点**：`|req(v)| > 1`（多个下游消费）的值。公共级数 `r(v) = min req(v)`。
- **公共级数上游化**：`r(v) > 0` 时，`v` 的这 `r` 拍物化为一条共享寄存器链，放在 `v` 的生产组合**上游**（即生产组合被推到链的下游，与其下游及兄弟支路的同拍组合连成一片）。
- **额外级数贴分支**：`req(v)` 中超出 `r` 的部分，物化在该支路紧邻 `v` 的下一条边上（`v` 与支路第一个节点之间）。
- **每条输入→输出路径寄存器个数不变**（拍数差是语义，§1 原则沿用）。

与旧算法的本质区别：旧选项 B 是**逐 op 局部重写 + 固定点**，寄存器每跨一步按输入数翻倍（指数）；本算法是**全局一次性计算物化点**，寄存器只在分叉口/分支边物化，同 (值, 延迟, 语义) 的消费者全部共享同一条链——寄存器只会因共享变少，组合逻辑因锥复制变多，二者此消彼长、由预算控制。

## 2. 探针数据（超时任务 `pass_ir/cpp` 真实 IR，2026-11-02 晚）

三个口径：**A** 纯下游归并（延迟只沿直连链 alias/wire/reg-next 传播，不跨组合；额外级数全贴 sink）；**B** 全上游化（组合值在延迟 d 被消费 → 操作数也需延迟 d，即每个被多拍消费的值都物化；自环按现有 pass 同款规则剔除）。自环判定：next 组合锥含自身 q。

| 函数 | 原 reg | 原 comb op | A：归并后 reg | B：全上游 reg | B：op 实例 | 旧 pass 实测 reg |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| v5_group_scoreboard | 83 | 1277 | 83 | **1992**（位宽≈原×2.3） | ×1.47 | 26026 |
| v5_shared_gm_interface | 88 | 736 | 88 | **811** | ×0.98 | 2454 |
| v5_core_pe_cluster0 | 9669 | 110556 | **7041** | 157 万 ✗ | ×63 ✗ | （未完成） |

读法：

1. **scoreboard / gm_interface 上，全上游化（B）完全可行**：寄存器只有旧 pass 产物的一半以下到 1/13，组合复制 ≤×1.5，max_delay=2。用户的"公共级数合并到分叉上游"在这类函数上成立。
2. **pe_cluster 有深 bal 链（≥64 级，直连链曾达 992 层）**：全上游化把每个中间值灌满延迟实例（23k 值 × 64 延迟），不可行。但 **A 口径净省 2628 个寄存器（−27%）**——纯归并（含 111 份 64bit 拷贝的合并）本身就是收益。
3. before 形态（现有 pass 的输入）里直连拷贝极少（scoreboard 83→83）；旧 pass 超时产出的 2.6 万拷贝是它自己逐输入复制出来的。**爆炸不是 Davinci 的形状，是旧算法的形状。**

结论：**分两期落地，预算守卫贯穿**。Phase 1 = A（纯归并，只减状态，零风险）；Phase 2 = 分叉口公共级数上游化（用户算法主体，受预算守卫；pe_cluster 深链自动退化为 Phase 1 行为）。

## 3. 算法规格（Phase 2 主体）

新 pass：`pyc-fork-merge-retime`（替换 `runHoistRegsUpstream` 的管线槽位；`HoistRegsUpstreamPass.cpp` 及其测试在实现合入时删除）。

### 3.1 延迟标注（后序 DFS）

对函数组合 DAG 反向传播事件 `(v, d)`（"v 被消费于延迟 d"）：

- 每个 `pyc.reg` R 产生 `(resolve(next_R), 1)`；每个函数输出产生 `(v, 0)`。
- `(q_R, d)`：R 无自环 → 展开 `(resolve(next_R), d+1)`；有自环（保持环）→ 终止，q_R 视为状态边界。
- `(组合值 u, d)`：`d` 记入 `D(u)`。若 `d ≥ 1` 且 u 被选为物化点（下节），则 `d` 级寄存器物化；**否则穿透**：不为本值物化，延迟需求沿 `u` 的操作数继续以 `(o_i, d)` 传播（这是"公共级数推到上游"的传播方向）。
- 常量：终止（免费）。存储器/实例/delay_line：状态边界，终止。
- `(v, d)` 事件全局去重；`d` 上限 `MAX_DELAY`（默认 8，覆盖 Davinci 级数差 ≤3 的现实），超限丢弃并计数 `pyc.stats.fork_merge_deep_drop`。

### 3.2 物化点选择与放置

- `D(v)` 收集完后：**分叉物化点** = `|D(v)| ≥ 2` 或（fanout 消费延迟不一致）的值。对物化点 `v`：
  - 公共 `r(v) = min D(v)`：`r` 级共享链物化在 `v` 的**操作数侧**（生产组合推到链下游——锥整体延迟 `r`，即 `(o_i, d−r)` 变 `(o_i, d)` 的传播在 r 处收口）；各消费延迟 `d ∈ D(v)` 从 `(v, r)` 读，**额外 `d − r` 级物化在 `v` 紧邻下游**（分支第一边）。
  - 语义合并条件：消费同一 `(v, d)` 的各 sink 链，其 `clk/rst/en/init` 签名（剥保持 mux 后）必须一致才共享；不一致按签名分裂为多条链（不合并，各物化各的）。
  - 初值：共享链第 k 级 init 由结构候选解（沿用 `solveInits` 的候选集推广到链首值），无解则该点退化为不共享（`pyc.stats.fork_merge_blocked_init`）。
- 非物化点（直线单延迟消费）：穿透，寄存器最终落在 sink 自身（等价 Phase 1 形态）。

### 3.3 预算守卫（防 pe_cluster 型放大）

函数级两个上限，任一超限即把该函数退化为 Phase 1（纯归并）：

- **位宽守卫**：物化后寄存器位宽合计 ≤ 原位宽 × `FORK_MERGE_WIDTH_BUDGET`（默认 2.0；选项 A 精神的放宽版）。
- **实例守卫**：组合 op 实例数 ≤ 原 × `FORK_MERGE_OP_BUDGET`（默认 2.0）。

探针数据标定：scoreboard（B 口径 reg 位宽 ×2.3 / op ×1.47）在预算 2.5/2.0 内可通过；pe_cluster 深链函数自动被守卫拦回 Phase 1（省 27% 状态）。

### 3.4 等价性论证

- 每条 sink 路径的拍数 = 路径上物化寄存器数（物化点沿路径分段求和不变，构造保证）。
- 逐拍等价：`(v, d)` 的链是同一值延迟 d 拍的标准实例；锥延迟 r 等价于 backward retiming 跨锥（cutset 在 `v` 的操作数集，`u@d = f(o@d)` 恒等式）；初始拍由初值结构解保证 `q(0)` 相等，归纳得逐拍相等。
- 观测身份：`pyc.name/probe/trace` 所指值保持在原拍（物化链上挂 `pyc.optimized_by="fork_merge_retime"`，锥副本剥观测属性，与旧 pass 同规则）。

### 3.5 与现有 pass 的协同

- 管线槽位不变：`dead-state → fork-merge-retime → retime-pipelines → canonicalize+CSE → dead-state → delay-chain`。
- 归并产生的同源链（`v → reg → reg` 直连）正是 `CombineDelayChainsPass` 的输入形状，成链在其后自然发生。
- CSE 负责把相同 `(值, 延迟)` 的链合到一份（pass 内也可显式查重，避免依赖顺序）。

## 4. 文件改动清单（批准后）

| 文件 | 改动 |
| --- | --- |
| `compiler/mlir/lib/Transforms/ForkMergeRetimePass.cpp` | 新文件：延迟标注、物化点选择、预算守卫、改写。 |
| `compiler/mlir/include/pyc/Transforms/Passes.h` | 声明 `createForkMergeRetimePass` / `runForkMergeRetime`。 |
| `compiler/mlir/lib/Transforms/StateOptimizePass.cpp` | `runHoistRegsUpstream` 调用点换成 `runForkMergeRetime`。 |
| `compiler/mlir/CMakeLists.txt` | 源列表换名；删除 `HoistRegsUpstreamPass.cpp`。 |
| `compiler/mlir/test/fork_merge_retime*.mlir/.sh/.cpp` | 新门禁（见 §5）；删除 `reg_upstream_hoist*` 三件套。 |
| `.github/workflows/ci.yml` | 门禁条目换名。 |
| `docs/MLIR_PASS_PIPELINE.md`、`docs/delay_line.md` | 顺序说明换名。 |

不改 dialect、不改 emitter（`CppEmitter` 的无 comb `DirtyBitset<1>` 补丁保留，那是独立修复）。

统计属性：`pyc.stats.fork_merge_shared`（合并掉的寄存器数）、`fork_merge_materialized`（物化链级数）、`fork_merge_blocked_init`、`fork_merge_budget_fallback`（函数退化为 Phase 1 的次数）、`fork_merge_deep_drop`。

## 5. 测试计划（gate-first）

FileCheck 用例（新 `.mlir` + smoke.sh）：

1. 两支路同 1 拍 + 共享分叉：合并后分叉上游一条链、两支路读同一 q，路径拍数不变。
2. 支路级数差 1：公共 1 拍上游化，额外 1 拍贴分支第一边。
3. `en` 签名不同的两个 sink：不共享，各自物化。
4. 保持环（自环）：不展开，原样。
5. 初值无解：退化不共享。
6. 深链（>MAX_DELAY）：丢弃传播，退 Phase 1。
7. 预算守卫：构造超位宽函数 → 整函数退化，输出与 Phase 1 相同。
8. `pyc.name` 观测：名字留原拍，副本无观测属性。

逐拍模型：复用 `reg_upstream_hoist_model` 思路，新模型覆盖"分叉 + 级数差 1 + 共享"，64 拍手写对照。回归：`state_retime_smoke.sh`、`make smoke`。

## 6. 风险与开放问题

1. **CSE 依赖**：共享链查重若依赖后置 CSE，中间 IR 可能瞬时偏大；pass 内显式 memo（`(值, 延迟, 签名) → 链 q`）规避。
2. **mux 保持语义**：剥保持 mux 收 en 的规则沿用旧 pass（最多 8 层）；en 非常量时签名含 en 值身份。
3. **深链初值**：bal 链合并后链首 init 直接取原链首（语义相同的拷贝链 init 相同），无需搜索。
4. **Verilog 路径**：不跑（沿用约定）。
5. **auto_perf 纪律**：用户约定同分支最多两次；本分支已用掉一次（超时）。新实现合入后只剩一次提测机会，必须本地先过全部门禁 + 在 pe_cluster 尺寸的 IR 上本地验证生成时间（可用 `pycc --emit=cpp` 对 dump 的 before IR 直接测）。

## 7. 待用户确认

1. 分期与预算默认值：Phase 1（纯归并）先行合入，还是两期一次做完？（建议：一次实现、预算守卫默认打开，Phase 1 即守卫最严的特例。）
2. 位宽预算 2.0 / op 预算 2.0 / MAX_DELAY 8 是否可接受（探针：scoreboard 需要 ≥2.3 位宽比）。
3. 旧 pass 文件直接删除（从未进任何集成分支）还是保留一个提交作历史？

## 8. 实现纪要（2026-11-02 深夜，分支 ldz/exp/fork-merge-retime）

按 §7 建议执行：一次实现、预算 2.0/2.0、MAX_DELAY 8、旧 pass 与其三件测试直接删除。

实测：

- 新门禁 `fork_merge_retime_smoke.sh` PASS：6 个 FileCheck 用例（归并 / 级数差共享前缀 / en 不同不共享 / 保持环不动 / 非常量 init 不动 / 观测名留原拍）+ 64 拍 C++ 模型对照。
- `state_retime_smoke.sh` PASS；`make smoke`（examples + 门禁 + 语义回归 + sims）PASS（本机需 python3.10+，默认 3.9 会因 `X | Y` 类型语法挂掉，与本次改动无关）。
- 真实 IR（超时任务 dump 包 module 壳，单 pass 实测）：**gm_interface 2454 → 182 reg（0.03 s，2400 sink 重写、128 条共享链、守卫未触发）**；scoreboard 预算守卫正确拦截（其全上游化位宽比实测为 ×153，远超 2.0）——退化为纯归并是设计行为；pe_cluster 的 before dump 本身无法 parse（并行 dump 的支配序缺陷），该函数级验证只能在 auto_perf 环境做。

与 §2 探针的两个偏差，接手人须知：

1. **探针 B 的「scoreboard 1992 reg 可行」结论按位宽口径不成立**：1992 对应 101403 bit，位宽比 ×153（此前 §7 写的"≥2.3"把寄存器数比当成了位宽比）。全上游化把寄存器推到锥叶（端口），cut 位宽结构性大于 sink 位宽——这正是旧 plan「整锥 598 万 bit」估计的同一现象。预算 2.0 下上游化只对窄锥函数（如 gm_interface）生效；scoreboard/pe_cluster 走纯归并路径。
2. **纯归并（A 口径）在真实 IR 上收益远超探针估计**：探针对 before 形态测得 83→83（无可合并），但那批 before dump 缺少并行拷贝形态；gm_interface after 形态实测归并 92% 状态。Davinci 的拷贝冗余要在 fuse-observe 之后的 IR 上才充分暴露。

下一步（优先级序）：① 推送 + CI 绿后，用剩余的一次 auto_perf 名额提测（对照 f70f6a6 的 12029.6/2.663）；② 若上游化收益需要覆盖 scoreboard 类函数，研究「只在分叉点局部穿透、不推到锥叶」的折中（cut 位宽守卫按锥计而非按函数计）；③ 观测 dump 支配序缺陷另开 issue（PassIRDumper 并行残留）。
