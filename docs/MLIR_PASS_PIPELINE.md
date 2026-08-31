# pycc MLIR Pass 执行管线

本文记录 `pycc` 默认后端管线中 pass 的实际执行顺序及其基本职责。
顺序以 `compiler/mlir/tools/pycc.cpp` 中构造的 `PassManager` 为准。

## 适用范围

- 输入：前端生成的 `.pyc`（PYC dialect MLIR）。
- 输出：通过所有 legality gate 后，交给 C++ 或 Verilog emitter。
- `func.func`：标记为“函数内”的 pass 仅作用于每个函数体。
- `module`：未标记为函数内的 pass 作用于整个 MLIR module。
- 用 `--dump-pass-ir` 对照时，嵌套 pass 前后还会出现 MLIR 内部的
  `OpToOpPassAdaptor` 包装器；它们不是语义 pass，本表不列出。

## 默认执行顺序

| 顺序 | Pass | 作用域 | 基本功能 | 条件 |
| --- | --- | --- | --- | --- |
| 1 | `pyc-check-frontend-contract` | module | 校验前端契约属性、模块元数据与输入 IR 是否属于受支持的 pyCircuit 前端。 | 始终 |
| 2 | `pyc-inline-functions` | module | 内联标记为 `pyc.kind="function"` 的辅助函数调用。 | 始终 |
| 3 | `pyc-flatten-instances` | module | 将 `pyc.instance` 层级扁平化。 | 仅 `--flatten` |
| 4 | `pyc-check-hierarchy-discipline` | module | 检查模块层级、内联策略与 hierarchy policy 是否一致。 | 始终 |
| 5 | `symbol-dce` | module | 删除不可达或未引用的符号。 | 始终 |
| 6 | MLIR inliner | module | 依照 inline policy 内联可内联的函数/调用。 | inline policy 启用时 |
| 7 | `canonicalize` | module | 应用通用 canonicalization pattern 与 PYC dialect folder；标量及可完全求值的 Vec lane 常量在此折叠。 | 始终 |
| 8 | `cse` | module | 消除公共子表达式。 | 始终 |
| 9 | `sccp` | module | 稀疏条件常量传播与可达性化简。 | 始终 |
| 10 | `remove-dead-values` | module | 删除无用 SSA value。 | LLVM < 19；LLVM 19+ 因稳定性问题跳过 |
| 11 | `pyc-eliminate-dead-instances` | function | 删除输出不可观察的 `pyc.instance`。 | 始终 |
| 12 | `symbol-dce` | module | 清理由实例删除产生的无用模块符号。 | 始终 |
| 13 | `pyc-lower-scf-static` | function | 将静态 `scf.for`、`scf.if` 等控制流降低为静态硬件结构。 | 始终 |
| 14 | `pyc-unroll-vector` | function | 将 Vector 计算、连接和状态展开为标量 lane 操作。 | `--unroll-vector` |
| 15 | `pyc-eliminate-wires` | function | 消除可直接替换的 `pyc.wire` / `pyc.assign` 中间连接。 | 始终 |
| 16 | `pyc-eliminate-dead-state` | function | 删除对可观察行为无影响的寄存器、存储等状态。 | 始终 |
| 17 | `pyc-slp-pack-wires` | function | 将可识别的同构标量 lane 重新打包成 Vector 操作。 | 未启用 `--unroll-vector` |
| 18 | `pyc-comb-canonicalize` | function | PYC 组合图结构化简（mux、wire、get/create 重建）并将结构化 Vec 拆成 lane 级运算，使 dialect folder 能折叠可知 lane。 | 始终 |
| 19 | `pyc-check-comb-cycles` | module | 构建组合依赖图并拒绝组合环。 | 始终 |
| 20 | `pyc-check-clock-domains` | module | 检查跨时钟域连接、时钟/复位使用和 CDC 合法性。 | 始终 |
| 21 | `pyc-pack-i1-regs` | function | 将可合并的标量 `i1` 寄存器压缩为 packed 寄存器。 | 始终 |
| 22 | `pyc-fuse-comb` | function | 将连续组合逻辑融合为 `pyc.comb` 区域，减少 emitter 调度开销。 | 默认启用；仅当同时设置 `--sim-mode=cpp-only` 与 `--cpp-only-preserve-ops` 时跳过 |
| 23 | `canonicalize` | module | 对前面 lowering/fusion 产生的新模式再次规范化。 | 始终 |
| 24 | `cse` | module | 再次消除公共子表达式。 | 始终 |
| 25 | `remove-dead-values` | module | 清理二次优化后的无用 SSA value。 | LLVM < 19；LLVM 19+ 跳过 |
| 26 | `pyc-eliminate-dead-instances` | function | 再次删除优化后变得不可观察的实例。 | 始终 |
| 27 | `symbol-dce` | module | 删除无用符号。 | 始终 |
| 28 | `pyc-check-flat-types` | function | 验证所有 operand/result 类型均可被目标 emitter 表示。 | 始终 |
| 29 | `pyc-check-no-dynamic` | function | 拒绝残留的 `scf.*`、`index` 等动态结构。 | 始终 |
| 30 | `pyc-check-logic-depth` | module | 计算组合逻辑深度并按 `--logic-depth` 限制拒绝超限设计。 | 始终 |
| 31 | `pyc-collect-compile-stats` | function | 写入寄存器、存储和硬件位数等编译统计属性。 | 始终 |

通过第 28–30 项 legality gate 后，`pycc` 才调用 C++ 或 Verilog emitter。

## Vector 分支

第 14–17 项是 Vector 处理相关步骤。`pyc-eliminate-wires`（15）与
`pyc-eliminate-dead-state`（16）**始终执行**；互斥的只有 unroll（14）与 SLP（17）：

```text
--unroll-vector
    └─ 14 pyc-unroll-vector → 15 eliminate-wires → 16 eliminate-dead-state
                                                    （跳过 17 slp-pack-wires）

默认（未开 --unroll-vector）
    └─ （跳过 14）→ 15 eliminate-wires → 16 eliminate-dead-state → 17 slp-pack-wires
```

`pyc-unroll-vector` 与 `pyc-slp-pack-wires` 不会同时执行。启用 unroll 时，
展开产生的标量 lane 继续经过 wire 与死状态清理；默认路径则在清理后执行
SLP 打包。两条路径随后汇入 `pyc-comb-canonicalize`、各类 legality gate
和 emitter。

## Vec 常量传播职责

PYC dialect folder 是常量语义的唯一来源：它折叠标量运算、全维归约，以及
`v_get(v_create(...))` / rank-1 `v_broadcast` 的直接读取。
`pyc-comb-canonicalize` 不复制算术常量规则；它只处理图结构，必要时把
`v_create`/broadcast 组成的 Vec 运算拆为 lane 运算。随后 greedy folder
将已知 lane 化为 `pyc.constant`，并以 `pyc.v_create` 重新组成部分或全常量
结果。不会跨状态、实例边界或未知 lane 推导常量。

## 已注册但不在默认 pycc 管线中的 pass

| Pass | 基本功能 |
| --- | --- |
| `pyc-prune-ports` | 删除未使用的函数端口，并同步更新调用点。可由 `pyc-opt` 单独调用，但 `pycc` 默认管线不添加它。 |

## 相关源码

- 默认管线：`compiler/mlir/tools/pycc.cpp`
- 自定义 pass 声明：`compiler/mlir/include/pyc/Transforms/Passes.h`
- pass 实现：`compiler/mlir/lib/Transforms/`
- 可单独运行 pass 的工具：`compiler/mlir/tools/pyc-opt.cpp`

