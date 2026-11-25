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
| 7 | `canonicalize` | module | 应用通用 canonicalization pattern 与 PYC dialect folder；标量与可完全求值的常量在此折叠。 | 始终 |
| 8 | `cse` | module | 消除公共子表达式。 | 始终 |
| 9 | `sccp` | module | 稀疏条件常量传播与可达性化简。 | 始终 |
| 10 | `remove-dead-values` | module | 删除无用 SSA value。 | LLVM < 19；LLVM 19+ 因稳定性问题跳过 |
| 11 | `pyc-eliminate-dead-instances` | function | 删除输出不可观察的 `pyc.instance`。 | 始终 |
| 12 | `symbol-dce` | module | 清理由实例删除产生的无用模块符号。 | 始终 |
| 13 | `pyc-lower-scf-static` | function | 将静态 `scf.for`、`scf.if` 等控制流降低为静态硬件结构。 | 始终 |
| 14 | `pyc-eliminate-wires` | function | 消除可直接替换的 `pyc.wire` / `pyc.assign` 中间连接。 | 始终 |
| 15 | `pyc-eliminate-dead-state` | function | 删除对可观察行为无影响的寄存器、存储等状态；带外部可读身份的状态视为活。 | 始终 |
| 16 | `pyc-state-optimize` | function | 统一结构化状态优化（仅 C++）：死状态清理 → retiming（pass 内 canonicalize+CSE）→ 死状态清理 → 直连 delay-line/tap 成链与等价 history 共享。被删状态的 name/debug/probe/trace 身份全程挂到 survivor 的只读 alias。 | `--emit=cpp` |
| 16a | `pyc-merge-reg-copies` | function | 合并并行寄存器拷贝（state-optimize 之后）：同 clk/rst/en/init 且同数据源的重复 `pyc.reg` 只留一个，其余删除；带观测名者改只读 alias。 | 始终 |
| 17 | `pyc-comb-canonicalize` | function | PYC 组合图结构化简（mux、wire 重建），使 dialect folder 能折叠可求值的标量。 | 始终 |
| 18 | `pyc-check-comb-cycles` | module | 构建组合依赖图并拒绝组合环。 | 始终 |
| 19 | `pyc-check-clock-domains` | module | 检查跨时钟域连接、时钟/复位使用和 CDC 合法性。 | 始终 |
| 20 | `pyc-pack-i1-regs` | function | 将可合并的标量 `i1` 寄存器压缩为 packed 寄存器，并把被删寄存器的外部可读身份挂到 packed bit alias。 | 始终 |
| 21 | `pyc-state-loop-partition` | function | 分析 pass（仅 C++）：给同时钟域的标量 `pyc.reg` / `pyc.delay_line` 打 `pyc.loop_id`，供 emitter 生成每域一个更新循环。 | `--emit=cpp` 且 `--state-loop`（默认开） |
| 22 | `pyc-apply-observation-demand` | module | 状态优化重映射身份之后，把未声明的 `pyc.name` 标成懒查找（`pyc.observe_lazy` + 存储切片描述），声明过的探针（`@probe` source、`--trace-codegen-plan`、`debug_keep` / `observable` / `probe*` / `trace*`）仍急切。懒名字不钉 struct、不每拍抄，但 `findByPath` 仍可按名读。 | `--emit=cpp` 且 `--observe-named=demand`（默认） |
| 23 | `pyc-fuse-comb` | function | 将连续组合逻辑融合为 `pyc.comb` 区域，减少 emitter 调度开销；仍带稳定名字或观测属性的 op 是 fusion barrier。 | 默认启用；仅当同时设置 `--sim-mode=cpp-only` 与 `--cpp-only-preserve-ops` 时跳过 |
| 24 | `canonicalize` | module | 对前面 lowering/fusion 产生的新模式再次规范化。 | 始终 |
| 25 | `cse` | module | 再次消除公共子表达式。 | 始终 |
| 26 | `remove-dead-values` | module | 清理二次优化后的无用 SSA value。 | LLVM < 19；LLVM 19+ 跳过 |
| 27 | `pyc-eliminate-dead-instances` | function | 再次删除优化后变得不可观察的实例。 | 始终 |
| 28 | `symbol-dce` | module | 删除无用符号。 | 始终 |
| 29 | `pyc-check-flat-types` | function | 验证所有 operand/result 类型均可被目标 emitter 表示。 | 始终 |
| 30 | `pyc-check-no-dynamic` | function | 拒绝残留的 `scf.*`、`index` 等动态结构。 | 始终 |
| 31 | `pyc-check-logic-depth` | module | 计算组合逻辑深度并按 `--logic-depth` 限制拒绝超限设计。 | 始终 |
| 32 | `pyc-cpp-placement` | module | 为 C++ emit 标注 comb 方法切分与 struct/local 存储；此时仍带 `pyc.name` 的值留在结构体上，供探针和 trace 取样。 | 仅 `--emit=cpp` |
| 33 | `pyc-collect-compile-stats` | function | 写入寄存器、存储和硬件位数等编译统计属性。 | 始终 |

通过第 29–31 项 legality gate 后，`pycc` 才调用 C++ 或 Verilog emitter。
C++ 路径在统计和 emit 之前还会跑第 32 项 `pyc-cpp-placement`。

## 向量在前端展开

Vector 只存在于 Python 前端与 API 层：发射 `.pyc` 时前端已把逐 lane 运算
展开为标量 op，模块端口写成单根 packed `i{leaf*lanes}` 总线（lane 0 在
LSB）。因此 pycc 管线中没有 vector 专用 pass，`pyc-eliminate-wires` /
`pyc-eliminate-dead-state` 等标量清理直接作用于 lane 级 op。

原先的 `pyc-unroll-vector`（IR 标量化）与 `pyc-slp-pack-wires`（把同构
标量重新打包为 Vector）两条互斥路径，随 dialect 中的 `vector<>` 类型与
`pyc.v_*` op 一起删除；pycc 也不再接受 `--unroll-vector`。

## 常量传播职责

PYC dialect folder 是常量语义的唯一来源：它折叠标量算术、位逻辑与比较。
`pyc-comb-canonicalize` 不复制算术常量规则，只处理 mux / wire 等图结构。
不会跨状态、实例边界或未知操作数推导常量。向量运算在发射期已拆成逐 lane
标量 op，因此不存在 Vector 专用的折叠规则。

## 已注册但不在默认 pycc 管线中的 pass

| Pass | 基本功能 |
| --- | --- |
| `pyc-prune-ports` | 删除未使用的函数端口，并同步更新调用点。可由 `pyc-opt` 单独调用，但 `pycc` 默认管线不添加它。 |

## 相关源码

- 默认管线：`compiler/mlir/tools/pycc.cpp`
- 自定义 pass 声明：`compiler/mlir/include/pyc/Transforms/Passes.h`
- pass 实现：`compiler/mlir/lib/Transforms/`
- 可单独运行 pass 的工具：`compiler/mlir/tools/pyc-opt.cpp`
