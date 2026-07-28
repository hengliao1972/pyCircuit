# Agentic Optimizer 适配 PyCircuit —— 差距分析与 TODO

**状态：** 待决策（v0.1）
**来源：** 对照 `agentic_circuit_optimizer.md` 与当前 V6 框架（`docs/v6_PyCircuit_Specification.md`、`docs/v6_PyCircuit_Software_Architecture.md`、`compiler/frontend/pycircuit/`、`runtime/cpp/`）
**用途：** 逐条列出「智能体自优化循环」对框架提出的能力要求、当前现状、缺口与建议动作，供决策如何开展工作。勾选框仅表示"是否已具备/是否已完成"，不代表优先级。

---

## 0. 结论速览

智能体循环把 PyCircuit 当作"变异载体 + 传感器"。当前框架的**变异载体**能力（周期感知抽象、自动周期平衡、切分点数据化的骨架）已基本就位，但**传感器/裁判**能力（提交追踪、锁步对拍、停顿归因、真实 PPA 回归与归因链）几乎全部缺失。文档 §7 明确"在写第一行流水线代码之前，裁判必须先就位"——因此缺口的重心在**验证与度量基础设施**，而非语言本身。

差距分为四类：
- **A. 变异载体**（切分点数据化、参数记录）：小改，已有骨架。
- **B. 等价门 / 裁判**（提交追踪、锁步对拍、反例最小化）：**基本从零**，是阻塞项。
- **C. 度量传感器**（性能计数器/停顿归因、STA/功耗、归因链）：**基本从零**。
- **D. 规范与文档对齐**（NDF 条款联动、文档版本漂移）：中等，偏流程。

---

## A. 变异载体（PyCircuit 语言层）—— 小改，已有骨架

### A1. 切分点数据化的参数记录（`pyc.params`）
- 文档要求：§2.4.3 "改常量即重新 JIT"，且"取值向量记录在模块的 `pyc.params` 属性中，天然就是决策记录"。
- 现状：
  - JIT/层次化路径经 `CompiledModule` 正确发射 `pyc.params`（`design.py`，含 `params_json`）。
  - ~~**eager 路径缺陷**：`compile_cycle_aware(..., eager=True)` 在 `v5.py:448` 把 `pyc.params` 硬写成 `"{}"`——`jit_params` 没有被记录。~~ **已修复。**
- [x] **TODO A1（已完成）**：eager 路径现把 `jit_params` 经 `_eager_params_json()`（复用 `design.canonical_params_json`）规范化后写入 `pyc.params`，与 JIT 路径一致。
  - 层次化 eager：始终记录（含默认 `{}`）；扁平 eager：非空参数时记录（空参数保持无属性、emit 干净）。
  - 非 JSON 安全的 kwargs（如携带 CAS 的 `inputs` dict）best-effort 丢弃，不会导致编译崩溃。
  - 实测（`datapath` 例）：`pyc.params` 正确反映 `cut_after_*` 向量，且 `pyc.reg` 数精确等于置位常量个数（0/1/3），同时验证了 §2.4.3 "静态性契约"。

### A2. 切分点数据化的"静态性契约"验证
- 文档要求：§2.4.3 "生成 MLIR 中自动平衡插入的流水寄存器（`pyc.reg`）数量精确等于置位常量的个数"。
- 现状：`compile_cycle_aware(fn, **jit_params)` 已能转发布尔常量、`if cut_after_*: domain.next()` 已可用。
- [x] **TODO A2（已完成）**：新增门禁 `tests/test_cut_point_static_contract.py`。
  - 3 个预设候选切割点，`2^3=8` 种取值组合 × 扁平/层次化两种 eager 子模式逐一 JIT。
  - 断言①（静态性契约）：发射 MLIR 中 `pyc.reg` 数**精确等于**置位常量个数（0..3）。
  - 断言②（决策记录）：`pyc.params` 解码后**精确等于**特化向量（含 False 项）。
  - 附 `test_empty_flat_eager_omits_params`：无 kwargs 的扁平 eager 不发 `pyc.params`，emit 保持干净。

### A3. 命名流水级（stage name，非仅 cycle 整数）
- 文档要求：§5.3/§5.4 归因链以**流水级名**为键（"路径端点必须映射回 PyCircuit 源码行与流水级名"）；停顿记账、STA 分组、面积归因都要按流水级名聚合。
- 现状（改动前）：信号只携带整数 `.cycle`，**没有** stage 名称的概念。
- [x] **TODO A3（前端已完成，后端待续）**：引入可选的流水级命名。
  - `domain.next(stage="decode")`：推进并命名进入的周期；`domain.name_stage("fetch")`：命名当前周期（可在首个 `next()` 前命名初始级）。
  - `sig.stage_name` / `fwd.stage_name`（委托）/ `domain.stage_name_of(cycle)` / `domain.stage_names`：按 cycle 解析 stage 名。
  - 冲突检测：同一 cycle 赋不同名 → `ValueError`；同名幂等；空名拒绝。
  - 发射：cycle→名 映射作为模块属性 `pyc.stage_names`（字符串编码，扁平/层次化 eager 均发射；无命名则不发、emit 干净）。
  - **纯元数据契约**：命名不改变发射硬件（`pyc.reg` 数与未命名版本一致，有门禁验证）。
  - 门禁 `tests/test_stage_naming.py`（10 项，含信号解析、模块属性、reg 属性传播、metadata-only、冲突/空名、快照），本地全通过；A2 门禁无回归。
- [x] **TODO A3-be（全链路打通，前后端均已实测）**：把 stage 名一路下沉到 op / Verilog / C++。
  - **前端（已实测）**：`pyc.reg` op 携带可丢弃属性 `pyc.stage`（走 `attr-dict`，无需改 `.td`；同 `pyc.name`/tier 模式）。
    - `dsl.Module.reg(..., stage=)` 发 `{pyc.stage = "..."}`；`hw.Circuit.out(..., stage_attr=)` 与 `reg_wire(..., stage_attr=)` 透传（**不改信号名**，与既有 `stage=` 名前缀参数解耦）。
    - v5 `delay_to`（每个自动平衡寄存器按其输出周期 `from_cycle+k` 打 stage）、`_state`/`cycle`（按声明周期打 stage）自动附加。
    - 零副作用契约：未命名 stage 时发射**字节级不变**（counter 等既有设计实测无 `pyc.stage`）。
  - **后端（已实测）**：`pycc` 两处改动均编译通过并端到端验证。
    - `VerilogEmitter.cpp`：reg 实例前发 `(* pyc_stage = "..." *)` 综合属性（STA/面积可按级归组）——实测 Verilog 产物含 `(* pyc_stage = "decode" *)` / `(* pyc_stage = "execute" *)`。
    - `CppEmitter.cpp`：reg 分配处发 `// pyc_stage: ...` DFX 注释（C++ 功能无关，仅按级聚合统计）——实测生成头文件含 `// pyc_stage: decode` / `// pyc_stage: execute`。
    - 验证方式：命名流水设计 → 前端发 MLIR（带 `pyc.stage`/`pyc.stage_names`）→ `pycc --emit=verilog|cpp` → 确认 stage 属性下沉。
    - 回归门禁：`tests/test_stage_backend_emission.py`（设计固化在 `tests/designs/stage_demo.py`），跑 `pycc` 断言 Verilog `(* pyc_stage=... *)` 与 C++ `// pyc_stage:`。
    - 已知限制：`pyc-pack-i1-regs` 会重建 i1 寄存器，可能丢失其 `pyc.stage`（多位流水寄存器不受影响）；死代码消除会连同其 `pyc.stage` 一并移除（实测 fetch 级 pc 反馈寄存器因输出不依赖而被 DCE，属预期）。

### A4. 空间/时间并行语义
- 文档要求：§2.5 空间并行默认、时间并行靠 `next()` 声明。
- 现状：框架语义已完全符合（实例化即并行，`domain.next()` 是唯一时间原语）。**无需改动**。
- [x] 已具备（仅需在文档中对齐表述）。

---

## B. 等价门 / 裁判（基本从零，阻塞项）

### B1. 提交追踪接口（retire/commit trace，RVFI 等价物）
- 文档要求：§3.4 第1级 + §5.1 + §7 阶段0 —— "PyCircuit 应在设计中内建『提交追踪接口』作为一条 NDF must 条款……它是整个优化循环的传感器，不是可选的调试功能"。每条退休指令发出 retire PC、写回寄存器号与值、访存地址与数据、异常号。
- 现状：仅有 `pyc_linxtrace.hpp` / `pyc_konata.hpp`（流水线可视化 trace）、`pyc_trace_bin.hpp`（comb/tick/commit 采样）、`ProbeRegistry`（信号探针）。**没有**结构化的指令提交/退休记录接口（RVFI 风格）。
- [x] **TODO B1（前端 + 门禁 + 运行时全链路打通，schema-agnostic）**：把"提交追踪接口"做成一等构件（决策 0142/0146）。**框架只提供通用机制，具体字段词表/必填集/门控组由声明方以数据下沉**——PyCircuit 核心不认识任何具体 CPU/ISA 的 schema 名或字段名。
  - **前端**：`CycleAwareCircuit.commit_interface({field: signal}, schema=..., stage=..., required=[...], groups={name:{valid,members}})`——每字段暴露为规范可观测输出端口 `commit_<field>`，记录 func 字符串属性 `pyc.commit_iface`（canonical JSON `{schema, stage?, fields, required?, groups?}`，与 `pyc.stage_names` 同套机制）。`schema` 默认中性 `pyc-commit-v1`；`required`/`groups` 为声明方提供的数据。前端只做结构记录，不做语义校验（gate-first）。
  - **门禁（`CheckFrontendContractPass`，PYC1000–1009）**：通用**结构**校验——JSON 合法且为对象（1000/1001）、`schema` 非空（1002）、`fields` 非空（1003）、字段端口存在于 `result_names`（1005，防悬空）、端口不重复（1007）；**数据驱动语义**——遍历属性内 `required` 校验必填字段（1004，框架自身不预设任何字段）；通用有效性门控引擎遍历 `groups`，规则「任一 member 出现→该组 `valid` 必须出现」（1006，决策 0146）；`required`/`groups` 格式校验（1008/1009）。**门禁不含任何具体字段名或 schema 名的硬编码。**
  - **运行时（`runtime/cpp/pyc_commit_trace.hpp`）**：`PycCommitTraceWriter` 以 `std::function` getter 绑定字段（字段名来自设计自身 schema），`sample(cycle)` 按 `valid` 频闪逐条采集退休指令，`writeJsonl()` 产出 commit-bundle 行（含 `cycle`/`stage`，未知字段可扩展），另有紧凑二进制形式；默认 schema 中性，任意兼容差分器可消费。
  - **CPU schema 归属 contrib**：`contrib/linx/flows/tools/commit_schema.py` 定义 `LC_COMMIT_BUNDLE_V2` profile（schema + required + groups），Linx 设计以 `m.commit_interface(fields, **LC_COMMIT_BUNDLE_V2)` 使用；实测产物被 `linx_trace_diff.py` 接受（`ok: traces match`）。
  - **后端自动挂载（`CppEmitter.cpp`）**：`--emit=cpp` 直接从 `pyc.commit_iface` 契约把提交采集器织入生成模型——生成 struct 内建 `PycCommitTraceWriter` 成员、按 `fields` 映射自动 `bind("field", []{ return commit_<port>.value(); })`、`step()` 末尾按 `valid` 频闪自动 `sample(cycle)`、析构时落盘。运行开关为环境变量 `PYC_COMMIT_TRACE=<out.jsonl>`（未设置则零开销、不采集）。testbench **零绑定**：仅驱动输入并 `step()`，仿真即产出提交流。含 commit 接口的模块 preamble 自动 `#include <cpp/pyc_commit_trace.hpp>`。实测自动流与手绑流逐字节一致。
  - **回归门禁（分层）**：`tests/test_commit_interface.py`（9 例）——通用门控正/负例用测试内中性 profile（PYC1006/PYC1004，不依赖 Linx）；`test_commit_trace_from_design_sim` 手绑采集器观测设计端口的真实闭环；`test_commit_trace_auto_mounted_by_cpp_emitter` 验证 CppEmitter 自动挂载（testbench 无任何 trace 接线、`PYC_COMMIT_TRACE` 驱动，产物与手绑逐字节一致）；与 Linx 生态互通的正例（`tests/designs/commit_demo.py` 引用 contrib profile）跑 `pycc` 通过；`pycc` 定位/契约补章共享于 `tests/_toolchain.py`。
  - **本轮未做**：B2 黄金模型接入另开。

### B2. 锁步协同仿真框架（vs ASL/ISA 黄金模型）
- 文档要求：§3.4 第1级"循环的日常主力"——同一指令流喂给 ASL 参考模型单步执行，逐条比对提交记录；首个失配点即最小反例。
- [x] **TODO B2（锁步 co-sim harness 全链路打通，schema-agnostic）**：以**依赖倒置**搭建裁判——harness 只依赖抽象黄金模型协议，占位参考模型先顶位，真 ASL/ISA 解释器实现同协议即插即用（harness 零改动）。**按提交序对齐**（第 k 条退休指令），天然吸收气泡/多周期/顺序退休，无需周期记账。
  - **抽象协议（`compiler/frontend/pycircuit/cosim/lockstep.py`）**：`GoldenModel` 协议（`reset()` / `step(instr)->commit|None` / `run(program)`）；`Instr` 为共享指令流一条（`pc/insn/retire/operands`）。
  - **提交流读取**：`load_commit_jsonl` / `parse_commit_jsonl` 直接吃 B1 的 commit-bundle JSONL（`start` 头 + 每退休指令一行），得 `CommitTrace`。
  - **锁步比对器 `LockstepComparator`**：比对规则全部来自 `CommitProfile`（B1 的 `required`/`groups` 数据），**无任何 ISA 词表硬编码**；组成员字段遵守**有效性门控**（决策 0146，组 strobe 为 0 时该组成员为 don't-care）；行 strobe `valid` 只决定行存在性、不作字段比。产出结构化 `LockstepReport`（`status`/`matched`/`total`/`first_mismatch{index,cycle,pc,field,dut,golden}`），首个失配即最小反例，可直接喂智能体（衔接 B4）。
  - **占位黄金模型（`compiler/frontend/pycircuit/cosim/reference.py`）**：`ReferenceModel` 是真正的执行器（维护架构寄存器文件 + 可插语义 `immediate_writeback`/`addi`），从共享指令流独立推导参考提交记录——非重放 DUT 流；真 ASL 桥接归 `contrib/` 另接。
  - **可观测 demo（`tests/designs/run_cosim_lockstep.py`）**：占位黄金模型对拍 B1 真实产出 `commit.auto.jsonl`，实测 `MATCH (2/2)`。
  - **回归门禁（`tests/test_cosim_lockstep.py`，6 例）**：正例全绿；门控 don't-care 正例（strobe 为 0 时组成员差异被忽略）；负例精确定位首个失配（index/field/cycle/pc/双值）；缺失/多余提交的形状失配；`run_lockstep` 便捷入口。

### B3. 双后端锁步对拍的观测点强化
- 文档要求：§3.4 第4级 —— 同一 `.pyc` 发 C++ 与 Verilog，逐周期比较全部输出与观测点，隔离"编译器 bug"与"设计 bug"。
- 现状：`pycircuit build --target both` 已能构建双后端并各自对 testbench `expect` 自检；C++ TB 产出 `.pyctrace` 二相观测轨迹（TICK-OBS/XFER-OBS，决策 0113/0140）。缺"逐周期全端口+观测点的自动 diff 报告 + 失配定位"。
- [x] **TODO B3（结构化 diff 报告全链路打通，后端中性）**：以与 B2 相同的**解耦**思路交付裁判——differ 只依赖一个后端中性的观测轨迹交换格式，任一后端 testbench 吐该格式即可对拍，不绑定具体工具链。
  - **观测轨迹模型（`compiler/frontend/pycircuit/crosscheck/obstrace.py`）**：`ObsTrace` 以 `(cycle, phase, signal)` 为键的观测集合；`phase` 采用二相纪律 `comb`/`tick`/`commit`（`xfer` 为 `commit` 别名，对齐决策 0113/0140），并**支持 X/invalid**（复位/未产出）。JSONL 交换格式（`start` 头 + 每观测一行，X 记 `{"x":true}`）+ 读/写（`parse/load/dump/write_obs_jsonl`、`trace_from_records`）。
  - **结构化 differ（`compiler/frontend/pycircuit/crosscheck/diff.py`）**：`BackendDiffer` 按规范序（先周期、再相位 comb<tick<commit、再信号名）对齐两后端轨迹，定位**首个失配**并产出可喂智能体的 `DiffReport`——`first_divergence{cycle,phase,signal,kind,a,b}` + 逐信号计数 + 形状/X 处理。失配类型：`value`（值不同）/`x`（X 与具体值，多为复位纪律漂移）/`missing_a|missing_b`（一侧多/缺周期或端口）。支持按信号/相位过滤范围。`cross_check(a,b)` 便捷入口。
  - **可观测 demo（`tests/designs/run_crosscheck_demo.py`）**：模拟同源双后端在 cycle 2 的 ALU 结果漂移，打印 `FIRST DRIFT: cycle 2 / TICK-OBS / top.alu_result: cpp=42 vs verilog=41 (value)`。
  - **回归门禁（`tests/test_crosscheck_diff.py`，7 例）**：MATCH；值失配的规范序首个定位（周期/相位/信号/双值）；X 处理（X==X 匹配、X-vs-值报 `x`）；形状缺失（missing_b）；信号/相位过滤；JSONL round-trip + `cross_check`；`xfer` 别名归一。
  - **本轮未做（后续集成）**：在 C++ TB（`.pyctrace` → obstrace 适配）与 SV TB 中真正发射该观测 JSONL，并在 `pycircuit build --target both` 增 `--cross-check` 自动跑 differ（依赖 Verilator 环境）。differ 与格式已就绪，待两后端接上生产端即闭环。

### B4. 反例最小化（delta-debugging 指令流）
- 文档要求：§5.1 "反例最小化"——自动缩短触发序列，给智能体"这 7 条指令在第 3 条提交时 x5 值不符"，而非 2GB 波形。
- [x] **TODO B4（指令流 delta-debugging 全链路打通，衔接 B2）**：等价门失败后自动把激励裁剪到最小失配序列。以与 B1/B2/B3 相同的**解耦**思路交付。
  - **通用引擎（`compiler/frontend/pycircuit/cosim/mincex.py`）**：`ddmin(items, oracle)` 是后端无关的 Zeller delta-debugging，输出 1-minimal 子序列，仅依赖不透明谓词 `oracle(subseq)->bool`（True=仍复现目标失配），对指令/锁步一无所知（可裁剪任意序列）。
  - **锁步接线 `minimize_lockstep(program, golden, dut, profile)`**：把引擎接到 B2 裁判——对每个候选子序列重跑 golden/DUT 模型、按提交序比对；并以**失配签名锁定**（`FailureSignature{kind, field, pc}`，用犯错指令的 pc 而非位置索引）确保最小化不漂移到另一个 bug。产出 `MinimizeResult`（`original_len`/`minimal_len`/`oracle_calls`/`signature`/最小序列 + 复现的 `first_mismatch`），`to_dict()` 直接喂智能体。
  - **可观测 demo（`tests/designs/run_mincex_demo.py`）**：12 条指令（bug 藏于 index 7）经 7 次 oracle 调用最小化到 **1 条**，输出 `MINIMAL REPRO: commit #0 at pc=0x11C: wb_data golden=263 vs dut=262 (field)`。
  - **回归门禁（`tests/test_mincex_ddmin.py`，6 例）**：通用引擎 1-minimal 且保序（单触发/双触发/不复现原样返回）；锁步最小化到单条犯错指令且 first-mismatch 字段/pc 正确；签名保持；无 bug 时 `reproduced=False` 且原样返回。

### B5. 形式化 / 性质检查接口（针对性）
- 文档要求：§3.4 第3级 —— Burch–Dill 冲刷法、SEC、控制不变量 SVA/model-checking（精确异常、无提交丢失、无双重提交、scoreboard 一致）。
- 现状：无形式化接入；`pyc.assert` 仅仿真断言。
- [ ] **TODO B5**（后期）：预留控制骨架的 SVA/性质导出与外部 model checker 接入；不追求全覆盖。

---

## C. 度量传感器（基本从零）

### C1. 性能计数器 + 按原因分解的停顿直方图（内环，最有价值）
- 文档要求：§5.2 —— CPI/IPC、总周期；**每个气泡记账到唯一原因**（load-use 互锁、分支误预测、结构冒险、缓存缺失、前端断流）；事件计数器（误预测率、命中率、旁路利用率）。且"性能计数器本身应作为设计的一部分写进 PyCircuit……综合时可裁剪"。
- 现状：`PYC_SIM_STATS` 只输出仿真器内部统计（实例求值/缓存命中），**不是**设计级性能计数器；无停顿归因、无 CPI。
- [ ] **TODO C1a**：提供"可综合时裁剪"的性能计数器构件（设计内嵌，release 综合可剥离）。
- [ ] **TODO C1b**：停顿归因基础设施——每气泡唯一归因 + 直方图导出（依赖 B1 提交流与 A3 stage 名）。

### C2. 时序 / 面积（外环）：真实综合 + STA 集成
- 文档要求：§5.3 —— fmax/关键路径 slack 来自综合+STA（Yosys+OpenSTA）；**路径端点必须映射回 PyCircuit 源码行与流水级名**；各流水级逻辑深度分布；面积按 `@module` 边界 1:1 归因。
- 现状：
  - 只有**编译期逻辑深度启发式**（`pyc-check-logic-depth` 写 `pyc.logic_depth.max/wns/tns`），**不是**真实 STA。
  - 仓库内**没有** OpenSTA/SAIF 任何集成（`grep` 仅命中本 agentic 文档）。
  - strict hierarchy 已保证模块名 1:1 保留（面积按模块归因有基础）。
  - `--out-dir` 已生成 `yosys_synth.ys` 脚本骨架。
- [ ] **TODO C2a**：接 Yosys + OpenSTA 真实综合/STA 流程，产出 fmax/slack/面积报告。
- [ ] **TODO C2b**：STA 路径端点 → PyCircuit 源码行 + 流水级名 的回映射（依赖 A3 与 C4 provenance）。
- [ ] **TODO C2c**：各流水级逻辑深度分布报告（与 `docs/cycle_balance_improvement.md` 方向互印证）。

### C3. 功耗度量（SAIF/VCD 回注）
- 文档要求：§5.3 —— 动态功耗须用真实开关活动（SAIF/VCD 回注综合网表），否则预测器/缓存类结论全错。
- 现状：有 `pyc_vcd.hpp`（差分 VCD dump）；**没有** SAIF 导出、**没有**功耗流程集成。
- [ ] **TODO C3**：SAIF 导出 + 功耗分析流程接入（先 VCD→SAIF，再回注）。

### C4. 归因链 provenance（源码行 ↔ 模块 ↔ stage ↔ 网表单元/路径 ↔ PPA）
- 文档要求：§5.4 —— 一条贯通因果链，含 NDF 条款 ID ↔ 源码行 ↔ 流水级 ↔ 网表路径 ↔ PPA。
- 现状：strict hierarchy 保模块名与可追踪命名；`.cycle` 元数据免费。但**缺**：源码行号 provenance、stage 名（见 A3）、NDF 条款 ID 标注、贯穿到 STA 报告的键。
- [ ] **TODO C4**：在 MLIR/Verilog 发射中贯穿"源码行 + stage 名 + 可选 NDF 条款 ID"的 provenance 元数据，作为 STA/面积/功耗报告的统一键。

### C5. PPA 代理模型（预筛，可选）
- 文档要求：§5.3 末 —— 训练便宜的 PPA 代理模型以结构特征预测综合结果做提案预筛；接受判定仍以真实综合为准。
- 现状：无（`pyc-collect-compile-stats` 提供 reg/mem/depth 结构特征，可作为特征来源）。
- [ ] **TODO C5**（后期，可选）：基于结构特征的 PPA 代理模型。

---

## D. 规范与文档对齐 / 流程

### D1. NDF 条款联动
- 文档要求：§2.1/§3.5/§5.4/§7 —— 提交追踪、约束（目标频率/IPC/面积/功耗）、决策记录均以 NDF 条款 ID 为锚；等价判据不随优化演化。
- 现状：框架无 NDF 条款标注钩子。
- [ ] **TODO D1**：为设计/信号提供可选的 NDF 条款 ID 标注通道（元数据），供归因链与覆盖率工具消费。

### D2. 文档版本漂移
- 现状：`agentic_circuit_optimizer.md` 关联文档引用 `PyCircuit_V5_Spec.md`、`pycircuit_implementation_method.md`、`PIPELINE.md` 及路径 `../pycircuit2/pyCircuit/docs/`；当前主规范已是 V6。
- [ ] **TODO D2**：更新 agentic 文档的关联文档引用到 V6（`v6_PyCircuit_Specification.md` 等），核对"自动周期平衡""TICK-OBS/XFER-OBS"等术语与 V6 一致。

### D3. 度量基础设施的 must 级保护（防奖励黑客）
- 文档要求：§6.3 —— 度量基础设施、基准集、等价门配置的修改必须走人审规范提交。
- 现状：无此约束机制。
- [ ] **TODO D3**（流程）：把 B1/C1 等传感器与基准集配置纳入 must 级门禁/人审。

---

## 建议的开展顺序（对齐文档 §7 路线，仅供决策参考）

1. **阶段0（裁判先行，阻塞一切）**：B1 提交追踪接口 → B2 锁步对拍框架 → A3 stage 名（B/C 的公共依赖）。
2. **阶段1（最小可行链 + 归因链贯通）**：C4 provenance → C2a 真实 STA → C1 性能计数器/停顿归因 → B3 双后端 diff。
3. **阶段2（手动循环校准）**：A1/A2 切分点数据化落地 + 决策记录模板；B4 反例最小化。
4. **阶段3+（智能体接管）**：C3 功耗、C5 代理模型、B5 形式化、D1/D3 规范联动。

---

## 附：已具备、无需改动的能力

- [x] 自动周期平衡（前馈汇合插 DFF）——§2.4.1 的核心前提（`v5.py` `delay_to`）。
- [x] `domain.next()` 作为唯一时间声明原语——§2.5。
- [x] 层次化发射 + strict hierarchy（模块名 1:1，面积归因基础）——§5.3/§5.4。
- [x] 双后端（C++ / Verilog）同源发射 + `--target both` 交叉验证——§3.4 第4级基础。
- [x] `pyc.params` 记录机制（JIT/层次化路径已有；eager 路径待补 = A1）——§2.4.3。
- [x] 编译期结构统计 `pyc-collect-compile-stats`（reg/mem/depth，可作 C5 特征）。
