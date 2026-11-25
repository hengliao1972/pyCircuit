# PyCircuit V6 软件架构（Software Architecture）

**版本：6.0**

本文档描述 PyCircuit 工具链的内部架构：Python 前端、MLIR `pyc` 方言与 pass 流水线、Verilog / C++ 双发射器、C++ 仿真运行时、测试调度（inline / sidecar）以及构建系统。目标读者：工具链开发者、需要理解生成产物的设计者。

**配套文档**：语言定义 → `v6_PyCircuit_Specification.md`；使用教程 → `v6_PyCircuit_Tutorial.md`。

---

## 目录

1. [总体数据流](#1-总体数据流)
2. [仓库布局](#2-仓库布局)
3. [Python 前端](#3-python-前端)
4. [pyc MLIR 方言](#4-pyc-mlir-方言)
5. [pycc 编译器与 Pass 流水线](#5-pycc-编译器与-pass-流水线)
6. [Verilog 发射器（RTL 综合输出）](#6-verilog-发射器rtl-综合输出)
7. [C++ 发射器与仿真模型](#7-c-发射器与仿真模型)
8. [C++ 仿真运行时](#8-c-仿真运行时)
9. [测试调度：inline 与 sidecar](#9-测试调度inline-与-sidecar)
10. [pycircuit CLI 构建编排](#10-pycircuit-cli-构建编排)
11. [构建系统与发布](#11-构建系统与发布)
12. [质量门禁与可观测性](#12-质量门禁与可观测性)

---

## 1. 总体数据流

```
┌────────────────────────  Python 前端  ────────────────────────┐
│  设计函数 (CycleAwareCircuit)                                 │
│  测试台   (@testbench + Tb / CycleAwareTb)                    │
│        │ compile_cycle_aware(eager/JIT)  或  @module JIT      │
│        ▼                                                      │
│  MLIR 文本（pyc 方言，.pyc 文件；TB 序列化为 pyc.tb.payload） │
└───────────────┬───────────────────────────────────────────────┘
                ▼
┌──────────────────────────  pycc  ─────────────────────────────┐
│  合法性检查 → 内联/展平 → 优化(canonicalize/cse/sccp)         │
│  → SCF 静态展开 → 死代码/死状态消除                            │
│  → comb 规范化（向量已在前端展开为标量 lane）                  │
│  → 组合环/时钟域/类型/逻辑深度检查 → comb 融合 → 统计         │
└───────┬──────────────────────────────────┬────────────────────┘
        ▼                                  ▼
  Verilog 发射器                     C++ 发射器
  ├ 每模块 .v（--hierarchical）      ├ pyc::gen::<Module> 结构体
  ├ 或单文件 / --flatten            ├ eval/tick/commit/step 相位
  ├ pyc_* 原语库                    ├ ProbeRegistry 注册
  └ manifest + yosys 脚本           └ 编译清单 + 分片
        │                                  │
        ▼                                  ▼
  综合 (Yosys/Vivado…)             CMake/Clang 编译 → 仿真器
                                     │  ├ inline schedule（编入 C++）
                                     │  └ sidecar 容器（运行时加载）
                                     ▼
                              运行：drive/expect、VCD、trace、stats
```

关键设计决策：

- **单一 IR，双发射**：Verilog 与 C++ 从**同一份**经过完整 pass 流水线的 MLIR 生成，保证仿真模型与综合网表语义一致。
- **测试旁路**：`@testbench` 程序不做硬件 lowering，作为模块属性 `pyc.tb.payload`（JSON，含 `cpp_text` / `sv_text`）直通发射，或外置为 sidecar。
- **周期精确仿真**：C++ 模型实现 comb → tick → commit 相位语义，与 Verilog 的时钟沿行为逐拍一致，`--target both` 可与 Verilator 交叉验证。

---

## 2. 仓库布局

```
pyCircuit/
├── compiler/
│   ├── frontend/pycircuit/        # Python 前端包（pip 包 pycircuit）
│   │   ├── dsl.py                 # 底层 MLIR 构图（Module/Signal）
│   │   ├── hw.py                  # Circuit/Wire[DT]/Reg[DT] 硬件对象模型
│   │   ├── data.py                # Data 类型层级（Bits/Vector/Clock/Reset）
│   │   ├── v5.py                  # CycleAware* 周期感知层（V6 主路径）
│   │   ├── design.py              # @module/@function/@const/@testbench
│   │   ├── jit.py                 # AST JIT 追踪编译
│   │   ├── connectors.py          # Connector/Bundle 跨模块连接
│   │   ├── schedule_ir.py         # 测试调度 JSON IR
│   │   ├── sidecar_sections.py    # SIDECAAR 二进制容器编解码
│   │   └── cli.py                 # pycircuit emit/build/sidecar
│   └── mlir/                      # C++ 编译器（LLVM/MLIR）
│       ├── include/pyc/Dialect/PYC/PYCOps.td    # 方言定义
│       ├── lib/Dialect/PYC/       # op 实现
│       ├── lib/Transforms/        # 全部 pass
│       ├── lib/Emit/              # VerilogEmitter / CppEmitter
│       └── tools/                 # pycc / pyc-opt
├── runtime/cpp/                   # C++ 仿真运行时头文件库
├── designs/                       # 示例与设计（examples/BypassUnit/IssueQueue…）
├── tests/                         # pytest（vec 算子框架、sidecar 单测等）
├── flows/scripts/                 # pyc build、run_examples 等脚本
├── docs/
└── Makefile / CMake / pyproject.toml
```

---

## 3. Python 前端

前端分三层，自底向上：

### 3.1 dsl.py — MLIR 构图层

`Module` / `Signal`：直接拼装 `pyc` 方言 op 的文本发射器。提供全部标量 op 的构造方法（`add`、`mux`、`extract`、`concat`…）以及 `emit_mlir()`；`Vector` 值在发射期展开为逐 lane 标量 op。上层所有 API 最终都落到这里。

### 3.2 hw.py — 硬件对象模型

- **`Circuit`**（继承 `dsl.Module`）：端口（`input`/`output`/`const`，支持 `shape=` 向量端口）、可赋值 wire、寄存器（`out`/`reg_wire`/`backedge_reg`）、原语（`fifo`/`byte_mem`/`sync_mem`/`sync_mem_dp`/`async_fifo`/`cdc_sync`/`rv_queue`）、层次实例化（`instance`/`array`）、命名作用域（`scope`）。
- **`Wire[DT]`**：统一信号句柄，泛型参数 `DT` 绑定 `Data` 类型层级（`Bits` / `Vector` / `Clock` / `Reset`，定义在 `data.py`）。标量是 `Wire[Bits]`、向量是 `Wire[Vector[...]]`，共享全部运算符（`+ - * & \| ^ ~ == != < >` 等），逐 lane 并行、标量自动广播。向量形态额外提供归约（`reduce_or`/`reduce_and`/`reduce_sum`）、`broadcast`、`priority_mux`、`cat`、`v[i]`。禁止作 Python bool。
- **`Reg`**：q/next/en 三元组，`set(value, when=...)` 驱动。

### 3.3 v5.py — 周期感知层（V6 设计主路径）

在 `Circuit`/`Wire` 之上实现语言规范定义的模型：

- `CycleAwareDomain` 维护逻辑周期计数器（`next`/`prev`/`push`/`pop` 栈）；
- `CycleAwareSignal` 用 `(wire, cycle)` 对表示信号，运算前通过 `delay_to()` 自动补 `pyc.reg` 链对齐操作数（**自动周期平衡**的实现点）；
- `ForwardSignal` 记录声明周期与赋值周期，差值决定生成几级 `pyc.reg` 反馈；
- `domain.call()`：
  - **扁平模式**：push → 执行子函数（同一张图上内联构图）→ pop；
  - **层次化模式**：把子函数以 `inputs=None` 独立编译为 `func.func` 注册进 `Design`，父模块发射 `pyc.instance`，并用记录的输出 cycle 元数据把 instance 结果重新包装为 CAS 返回。

### 3.4 两条编译入口

| 入口 | 机制 | 适用 |
|------|------|------|
| `compile_cycle_aware(fn, eager=True, ...)` | **直接执行** fn，Python 控制流即元编程 | V6 主路径 |
| `compile_cycle_aware(fn)`（JIT）/ `compile(fn)` + `@module` | **AST 解析**不执行；`if`(i1)→`scf.if`、静态 `for` 展开、`@module` 边界→`pyc.instance`、`@function` 内联、`@const` 编译期求值 | V4 风格 / CLI `emit`/`build` 路径 |

前端在输出的 MLIR 上打契约属性（`pyc.frontend.contract="pycircuit"`、`pyc.kind`、结构度量 attrs），后端第一个 pass 即校验。

---

## 4. pyc MLIR 方言

类型：`iN`、`!pyc.clock`、`!pyc.reset`。

> 前端 Python 层的 `Vector` 类型不会出现在 `.pyc` 里：发射时逐 lane 展开为
> 标量 op，模块端口合并为单根 packed `i{leaf*lanes}` 总线。

### Op 一览（按类别）

**组合 / 算术**（Pure；标量）：

| Op | 语义 |
|----|------|
| `pyc.constant` | 整数常量（attr `value`） |
| `pyc.add / sub / mul` | 加减乘（截断） |
| `pyc.udiv / urem / sdiv / srem` | 除法 / 取余 |
| `pyc.and / or / xor / not` | 位逻辑 |
| `pyc.mux` | 2:1 选择 |
| `pyc.eq / ult / slt` | 比较（结果为 i1） |
| `pyc.trunc / zext / sext` | 宽度变换 |
| `pyc.extract` | 位切片（attr `lsb`；仅标量） |
| `pyc.shli / lshri / ashri` | 立即数移位 |
| `pyc.shl / lshr / ashr` | 动态移位 |
| `pyc.concat` | MSB-first 拼接 |
| `pyc.alias` | 命名别名（调试名传播） |
| `pyc.reset_active` | 复位有效高视图（`!pyc.reset` → i1） |

**Wire / 状态 / 存储 / CDC**：

| Op | 语义 |
|----|------|
| `pyc.wire` / `pyc.assign` | 组合线占位 / 驱动 |
| `pyc.reg` | `(clk, rst, en, next, init) → q` 时钟寄存器 |
| `pyc.fifo` | 单时钟 ready/valid FIFO（attr `depth`） |
| `pyc.byte_mem` | 异步读同步写字节存储 |
| `pyc.sync_mem_nm` | 同步 N 读 × M 写（attr `depth`/`read_mode`；1R1W 与 2R1W 为其特例） |
| `pyc.async_fifo` | 双时钟 FIFO |
| `pyc.cdc_sync` | 多级同步器（attr `stages`） |

**结构**：

| Op | 语义 |
|----|------|
| `pyc.instance` | 实例化 `func.func`（attr `callee`、可选 `name`） |
| `pyc.comb` + `pyc.yield` | 融合组合区域（IsolatedFromAbove），发射期展开 |

> 向量没有专用 op：`m.vec(...)`、`broadcast`、`reduce_*`、`priority_mux` 等
> 前端 API 在发射期展开为上述标量 op（入口 `pyc.extract`、出口
> `pyc.concat`，模块端口为 packed `i{leaf*lanes}`），因此 dialect 中不再有
> `pyc.v_*`。

**验证**：`pyc.assert`（仿真断言；Verilog 侧包在 `` `ifndef SYNTHESIS`` 中，C++ 侧 abort）。测试台本身**没有** op——走模块属性旁路（§9）。

---

## 5. pycc 编译器与 Pass 流水线

`pycc` 是唯一的后端驱动器：读入 `.pyc` MLIR → 固定流水线 → 按 `--emit` 发射。

### 5.1 精确 pass 顺序（verilog / cpp 共用）

```
 1. pyc-check-frontend-contract      前端契约与必填属性校验
 2. pyc-inline-functions             内联 @function 实例
 3. [--flatten] pyc-flatten-instances 全部实例内联
 4. pyc-check-hierarchy-discipline   层次纪律（函数体量/重复层次）
 5. symbol-dce
 6. [inline 允许时] mlir inline      通用内联器
 7. canonicalize → cse → sccp
 8. pyc-eliminate-dead-instances → symbol-dce
 9. pyc-lower-scf-static             scf.for 展开、scf.if → mux
10. pyc-eliminate-wires              trivial wire/assign 消除
11. pyc-eliminate-dead-state         不可观测 reg/fifo/mem 删除
12. pyc-comb-canonicalize            mux/布尔折叠
13. pyc-check-comb-cycles            组合环检测
14. pyc-check-clock-domains          跨域必须经 CDC 原语
15. pyc-pack-i1-regs                 相邻 i1 寄存器打包
16. [fuse 允许时] pyc-fuse-comb      连续组合 op → pyc.comb 区域
17. canonicalize → cse
18. pyc-eliminate-dead-instances → symbol-dce
19. pyc-check-flat-types             仅 iN / clock / reset
20. pyc-check-no-dynamic             禁止残留 SCF / index
21. pyc-check-logic-depth            深度 ≤ --logic-depth；写 WNS/TNS
22. pyc-collect-compile-stats        reg/mem 计数与位数统计
──  层次符号集校验 → 打印 stats → 发射  ──
```

fuse 条件：`--sim-mode=cpp-only` 且 `--cpp-only-preserve-ops` 时关闭（保留 op 粒度供细粒度调度）。

### 5.2 主要 CLI 标志

| 标志 | 默认 | 说明 |
|------|------|------|
| `--emit` | `verilog` | `verilog` \| `cpp` \| `none` |
| `-o` / `--out-dir` | stdout / — | 单文件 / 按模块拆分（+`manifest.json`） |
| `--hierarchical` | off | 保留层次；隐含 `hierarchy-policy=strict` + 关内联器 |
| `--flatten` | off | 全部实例内联为单顶层（与上互斥） |
| `--logic-depth` | `32` | 组合深度上限 |
| `--target` | `default` | `fpga` 时 Verilog 加 `` `define PYC_TARGET_FPGA`` |
| `--include-primitives` | `true` | 单文件是否内嵌原语 `include` |
| `--sim-mode` / `--cpp-only-preserve-ops` | `default` / off | C++-only 模式与 op 粒度保留 |
| `--inline-policy` | `default` | `off` \| `threshold:N` |
| `--hierarchy-policy` | `strict` | `strict`（符号集不得变）\| `instantiate`（警告） |
| `--build-profile` | `release` | `dev-fast` 降低 canonicalize 预算、减小 C++ 分片阈值 |
| `--cpp-split` / `--cpp-shard-threshold-*` | `module` / 120k 行 / 4 MiB | 大模块 C++ 分片 |
| `--probe-plan` / `--probe-manifest` | — | DFX probe 注入与清单 |
| `--profile-json` / `--profile-pass-timing` | — | 编译 profile |

环境变量：`PYC_PRIMITIVES_DIR`（原语库路径）、`PYC_TOOLCHAIN_ROOT`。

### 5.3 向量处理策略

- **前端展开**：Vector 只是 Python 前端 / API 层的类型；发射 `.pyc` 时每个逐 lane 运算变成一条标量 op，`reduce_*` 按 `mode` 生成链式或树形标量 op，向量状态拆成共享 clk/rst/en 的 per-lane `pyc.reg`。
- **端口 packed**：模块边界的向量端口写成单根 packed `i{leaf*lanes}` 总线（lane 0 在 LSB），入口 `pyc.extract` 拆 lane、出口 `pyc.concat` 打包；实例边界同样只传这根总线。
- **pycc 无向量 pass**：dialect 中已无 `vector<>` 类型与 `pyc.v_*` op，管线里没有 unroll / SLP 阶段，emitter 只处理标量 `Wire<W>`。

### 5.4 逻辑深度模型

`pyc-check-logic-depth` 的代价模型：常量 / alias / wire = 0；一般 op = 1；寄存器与存储为路径切点；跨 `pyc.instance` 用 `CombDepGraph` 缓存的深度摘要传播。归约在前端已展开为 `⌈log₂ lanes⌉` 层树或 `lanes−1` 层链的标量 op，按标量 op 自然计深。超限直接编译失败，同时把 `pyc.logic_depth.max/wns/tns` 写回 IR 供统计输出。

### 5.5 pyc-opt

调试工具：标准 `MlirOptMain`，注册全部 pyc pass + 上游 pass，无固定流水线，用于单 pass 复现与 FileCheck 测试。

---

## 6. Verilog 发射器（RTL 综合输出）

文件：`compiler/mlir/lib/Emit/VerilogEmitter.cpp`。

### 模块结构

每个 `func.func` → 一个 `module <symName>`：

```verilog
module fetch ( input clk, input rst, ... );
  // 内部 wire 声明（按名排序，注释来源 op / pyc.name）
  // --- Combinational (netlist)   ← 拓扑序 assign（pyc.comb 内联展开）
  // --- Instances                 ← 子模块例化（层次模式）
  // --- Sequential primitives     ← pyc_reg / pyc_fifo / pyc_sync_mem ...
  //     输出端口连线
endmodule
```

### 原语库

时序 op 不展开为 always 块，而是例化参数化原语（随工具链提供 `.v` 源，或 `--out-dir` 时拼出 `pyc_primitives.v`）：

| IR | 原语 |
|----|------|
| `pyc.reg` | `pyc_reg #(.WIDTH)` |
| `pyc.fifo` | `pyc_fifo #(.WIDTH,.DEPTH)` |
| `pyc.byte_mem` / `sync_mem_nm` | `pyc_byte_mem` / `pyc_sync_mem_nm #(.ADDR_WIDTH,.DATA_WIDTH,.DEPTH,.R_PORTS,.W_PORTS,.READ_MODE)` |
| `pyc.async_fifo` / `cdc_sync` | `pyc_async_fifo` / `pyc_cdc_sync #(.WIDTH,.STAGES)` |

### 向量端口

模块边界的向量端口是**单根 packed bus**（`[N*W-1:0]`，Yosys 友好）：前端发射 IR 时已把它写成 `i{N*W}`，模块体内逐 lane `pyc.extract` / `pyc.concat` 进出，不再有 unpacked 数组 wire 或 `__flat` 桥；层次实例的端口同样只传这根总线。

### 输出形态

| 方式 | 产物 |
|------|------|
| `--out-dir` | 每模块 `<name>.v` + `pyc_primitives.v` + `manifest.json` + `yosys_synth.ys` |
| `-o file.v` | 单文件（可选 `` `include`` 原语） |
| `--flatten` | pass 阶段已内联，只剩顶层一个 module |

`pyc.assert` 发射为 `` `ifndef SYNTHESIS`` 保护的 `$fatal`，不影响综合。

---

## 7. C++ 发射器与仿真模型

文件：`compiler/mlir/lib/Emit/CppEmitter.cpp`；生成代码依赖 `runtime/cpp/` 头文件库。

### 生成结构

每个模块 → `namespace pyc::gen { struct <ModuleName> {...}; }`：

- 端口与内部 wire 成员：`pyc::cpp::Wire<W>`（即 `Bits<W>`）；向量端口就是一根宽 `Wire<W>`；
- 子模块成员（层次模式）与时序原语对象（`pyc_reg`、`pyc_sync_mem`…）；
- 构造函数注册 ProbeRegistry（递归含子模块）并初始化运行时控制。

### 求值相位（与硬件语义对应）

| 函数 | 语义 |
|------|------|
| `eval_comb_pass()` | 拓扑序执行一遍组合逻辑（assign / 融合的 `eval_comb_N()` 分块 / assert） |
| `eval()` | **固定点迭代**：组合 ↔ 子模块/原语交替求值直至收敛（输入指纹缓存避免重复算），最后写输出端口 |
| `tick_compute()` / `tick_commit()` | 两阶段时钟沿：先算所有寄存器/FIFO/mem 的 next（含子模块），再统一提交——保证无竞态 |
| `step()` | `comb → tick → commit → comb`，即一个完整时钟周期 |

### 规模控制

大模块（davinci 级）单文件会失控，发射器按 `--cpp-split=module` + 行数/字节/AST 节点阈值把 eval/tick/comb 分片为多个 `.cpp`，并输出 `cpp_compile_manifest.json`（源列表、include 路径、`libpyc4_runtime.a`、确定性哈希）供上层并行编译。

---

## 8. C++ 仿真运行时

头文件库 `runtime/cpp/`（无独立编译单元，除 `pyc_runtime.cpp`）：

| 头文件 | 内容 |
|--------|------|
| `pyc_bits.hpp` | `Bits<Width>`：`uint64_t` 字数组表示任意宽度；全套算术/比较/移位/宽度变换自由函数；ARM NEON 加速路径 |
| `pyc_tb.hpp` | `Testbench<Dut>`：时钟管理（`addClock`/`runCycles`/`runCyclesAuto`）、复位、步进语义 `comb → 时钟翻转 → tick → transfer → comb → VCD` |
| `pyc_vcd.hpp` | `VcdWriter`：差分 VCD dump，支持窗口（`setVcdWindow`） |
| `pyc_trace_bin.hpp` | `PycTraceBinWriter`：comb/tick/commit 三相二进制采样 |
| `pyc_linxtrace.hpp` / `pyc_konata.hpp` | 处理器流水线可视化 trace |
| `pyc_probe_registry.hpp` | `ProbeRegistry`：以 xxHash64(规范路径) 为 id 的信号探针表；`findByPath/findByGlob/findByKind`；长实例路径压缩 |
| `pyc_tb_sidecar.hpp` / `pyc_tb_sidecar_runtime.hpp` | Sidecar 容器加载与 runner 调度结构（§9） |
| `pyc_sim.hpp` / `pyc_sync_mem.hpp` / `pyc_clock.hpp` / `pyc_ops.hpp` | 原语模型与公共设施 |

运行时环境变量：`PYC_SIM_STATS`（打印实例/缓存命中统计）、`PYC_SIM_STATS_PATH`、`PYC_SIM_FAST`。

---

## 9. 测试调度：inline 与 sidecar

`@testbench` 程序（drive/expect/finish/print 事件序列）有两条下发路径：

### 9.1 inline（默认）

前端把 TB 程序序列化为模块属性 `pyc.tb.payload`（JSON，内含 `cpp_text` 与 `sv_text`）。`pycc` 检测到该属性后**跳过全部硬件 pass**，把文本直通写出——C++ 测试主程序 / SV 测试台由前端生成、后端透传。事件被编入 C++ 源码。

**问题**：数万周期的激励让生成的 C++ 巨大，编译时间失控，且改一个激励值就要全量重编。

### 9.2 sidecar

`pycircuit build --tb-schedule-mode=sidecar` 时：

1. 前端把 drive/expect 事件转为 **Schedule IR**（JSON，schema `pycircuit.schedule_ir` v1.0.0：`ports`/`events`/`frames`/`patterns`/`timebase`/`stats`）；周期性 backpressure（如 ready 口固定占空比起伏）自动识别压缩为 `periodic_drive` pattern；
2. `schedule_ir_to_sidecar_bytes()` 序列化为二进制 **SIDECAR 容器**（魔数 `SIDECAR\n`），五个 section：

   | Section | 内容 |
   |---------|------|
   | `string_table` | 字符串池 |
   | `port_table` | 端口名 / 宽度 / 方向 |
   | `event_table` | 离散 drive/expect 事件 |
   | `frame_table` | 按周期分帧的驱动字（`port_mask` + words） |
   | `pattern_table` | 周期性模式（periodic_drive） |

3. 生成的 C++ runner 是**稳定的**（不随激励内容变化）：启动时 `loadSidecarSchedule()` → `convertSidecarToRunnerSchedule()` → 按 cycle 应用 drive frame、pre/post expect 事件。

改激励只需重新生成 sidecar 文件，仿真器二进制无需重编。

检查工具：`pycircuit sidecar inspect FILE [--strict]`（解码打印）、`pycircuit sidecar verify FILE`（结构校验，如拒绝未知 port id）。单测见 `tests/test_sidecar_sections.py`。

---

## 10. pycircuit CLI 构建编排

`pycircuit`（入口 `pycircuit.cli:main`）三个子命令：

### `pycircuit emit`

```
pycircuit emit DESIGN.py -o OUT.pyc [--param k=v]... [--module-graph-out ...]
```

执行前端 JIT（`@module build`）→ 写出 MLIR；可选输出模块依赖图（DOT）。

### `pycircuit build`

```
pycircuit build TB.py --out-dir DIR
    [--param k=v]... [--jobs N] [--profile {dev,release}]
    [--target {cpp,verilator,both}] [--logic-depth N]
    [--tb-schedule-mode {inline,sidecar}] [--trace-config JSON]
    [--run-verilator] [--run-arg ...]
```

端到端编排：

```
前端 emit（可能多个 .pyc）
  → 并行调用 pycc（--emit=cpp，dev profile 映射到 --build-profile=dev-fast）
  → 读 cpp_compile_manifest.json → CMake/Ninja 编译仿真器 → 运行
  → [--target verilator|both] 另发 Verilog → Verilator 构建 →（--run-verilator）运行比对
  → [--tb-schedule-mode=sidecar] 生成 .sidecar 文件 + 稳定 runner
```

### `pycircuit sidecar`

`inspect` / `verify`，见 §9.2。

---

## 11. 构建系统与发布

### 工具链构建（pycc）

```bash
# 推荐脚本（内部即下述 Makefile 目标）
bash flows/scripts/pyc build

# 或手动
make configure      # CMake+Ninja；需 LLVM_DIR/MLIR_DIR 或 LLVM_CONFIG
make tools          # 构建 pycc + libpyc4_runtime（可选 pyc-opt）
make install        # 装入 .pycircuit_out/toolchain/install
```

CMake 目标：TableGen（`PYCOps.td` → `.inc`）→ `pyc_dialect` → `pyc_transforms` → `pycc`（链接双发射器，include `runtime/`）；`pyc-opt` 为可选目标（需 `MLIRRegisterAllPasses`）。C++17，依赖已安装的 LLVM/MLIR。

### Python 包与入口点

- 根 `pyproject.toml`：包名 `pycircuit-hisi`，import 名 `pycircuit`，脚本 `pycircuit = pycircuit.cli:main`。
- 平台 wheel（`packaging/wheel/`）额外捆绑预编译工具链，并提供 `pycc` / `pyc-opt` 控制台入口；源码 editable 安装不含 `pycc`，需本地构建。
- 定位顺序：`PYC_TOOLCHAIN_ROOT` 环境变量 → wheel 内置工具链。

### 测试入口

```bash
make smoke                                    # 示例 + 仿真冒烟
make vec-smoke                                # 向量算子回归
PYTHONPATH=compiler/frontend pytest tests/vec -m vec     # 向量框架
PYTHONPATH=compiler/frontend pytest tests/test_sidecar_sections.py
```

`tests/vec/` 是生成式测试框架：`cases.py` 定义算子用例（vv/vs/sv 形态 × add/sub/mul/logic/cmp/div/shift/reduce），`generate.py` 产出 DUT+TB，`oracle.py` 提供位精确参考模型，`runner.py` 走完整 `pycircuit build` 并可选 Verilator 对比。

---

## 12. 质量门禁与可观测性

### 编译期门禁（全部为硬错误）

| 检查 | Pass | 抓什么 |
|------|------|--------|
| 前端契约 | `pyc-check-frontend-contract` | 非 pycircuit 前端产物 / 缺属性 |
| 层次纪律 | `pyc-check-hierarchy-discipline` | 过大 `@function`、裸循环复制层次 |
| 组合环 | `pyc-check-comb-cycles` | 无寄存器切断的反馈 |
| 时钟域 | `pyc-check-clock-domains` | 未经 CDC 原语的跨域信号 |
| 类型扁平 | `pyc-check-flat-types` | 发射前残留非法类型 |
| 无动态结构 | `pyc-check-no-dynamic` | 残留 SCF / index |
| 逻辑深度 | `pyc-check-logic-depth` | 组合路径超 `--logic-depth` |
| 层次策略 | pycc post-check | `strict` 下模块符号集被优化改变 |

### 统计与 profile

- `pyc-collect-compile-stats` 写 `pyc.stats.reg_count/reg_bits/mem_count/mem_bits`；
- pycc 结束打印并写 JSON（单文件 `<out>.stats.json`；out-dir `compile_stats.json`）：

  ```
  stats: regs=… (… bits), mems=… (… bits), max_depth=…/LIMIT, WNS=…, TNS=…, fuse_comb=on|off
  ```

- `--profile-json` + `--profile-pass-timing`：每 pass 时间/内存 profile；
- 运行期：`PYC_SIM_STATS=1` 输出实例求值与缓存命中统计；ProbeRegistry + `--probe-manifest` 支持 DFX 信号观测点清单。

---

**Copyright © 2024-2026 Liao Heng / PyCircuit Contributors. All rights reserved.**
