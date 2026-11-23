# Delay-line 与观测需求（L1）

本文说明 `pyc.delay_line` / `pyc.delay_tap`、结构化链折叠、retiming、观测身份重映射、
observation demand 与 trace codegen plan。范围只含 L1：**不含**等价寄存器合并、
state-lane 打包、bank/loop 化（分别属于后续 stack-2/3/4）。

## 1. 为什么需要 delay-line

frontend 为平衡周期插入的 `_v5_bal_*` 串行寄存器链，在 C++ model 里每级都是一个
`pyc_reg`：每拍每级各做一次采样/提交/唤醒。把 N 级纯串行链折成一个 N 深度的环形历史，
一拍只推进一个指针，旁路读取中间级用 `pyc.delay_tap`。

## 2. 语义

- `pyc.delay_line %clk, %rst, %en, %next, %init {depth = N}`：`q` 是第 N 拍前的 `next`；
  复位后全部历史等于 `init`。**不是 FIFO**：没有 valid/ready，`en` 为 0 时整条线冻结。
- `pyc.delay_tap %line {depth = k}`（1 ≤ k < N）：只读视图，读第 k 级，不新增状态。
- 一个 delay_line 不等于一个寄存器：`CollectCompileStatsPass` 按逻辑级数计
  `reg_count/reg_bits`，因此 RTL 状态量口径不变。
- verifier（`PYCOps.cpp`）拒绝非正深度、tap 深度越界、tap 源不是 delay_line。

## 3. `pyc-state-optimize` 流程（仅 `--emit=cpp`）

`pyc-eliminate-dead-state` → `pyc-retime-pipelines` → canonicalize+CSE（pass 内）→
`pyc-eliminate-dead-state` → `pyc-combine-delay-chains`（成链 + 等价 history 共享）。

- **成链**：`consumer.next` 只经透明 alias 到达前驱 `q`，且 clk/rst/en/init 等价；
  中间级允许只读 fanout，改写成 `delay_tap`。
- **共享**：完全相同（输入、深度、init、控制）的 delay_line 合并为一个。
- **retiming**：单源 computed pipeline 与“共同 delay 下沉”。收益来自**跨拍组合融合**
  （消费者 cone 下沉到 `source.next`，与生产者在同一个调度 comb 区域内共享子表达式），
  不是位宽差。只改写白名单纯组合算子、无反馈、init 可由 APInt 求值的区域；无预算开关。
- Verilog 保持优化前网表：pass 只在 C++ emit 路径加入；Verilog 遇到 delay_line/tap
  报 “not supported by the Verilog backend”。

## 4. 观测身份

被删/被替换的状态不能让外部读取（probe、VCD、`dut.read`）失效：

- `hasExternalObservationIdentity`：`pyc.name`（cycle_balance 自动名除外）、
  `pyc.debug_keep`、`observable`、`probe*`、`trace*` 属性之一。
- 带外部身份的状态在 dead-state 清理中视为活；被改写时，身份物化到 survivor 的只读
  `pyc.alias`（`materializeObservationAlias`）。
- frontend 给编译器插入的平衡状态打 `pyc.generated = "cycle_balance"`，其私有名
  不当作可观测身份（不再靠 `_v5_bal_*` 字符串猜）。

## 5. Observation demand

delay-line 化之后，大量保留的名字会造成额外的 probe 注册与每拍抄写开销。
`pyc-apply-observation-demand`（`--observe-named=demand`，默认）把**未被需求**的
`pyc.name` 标成懒查找：

- 需求集合：`@probe` 的 source、trace codegen plan 选中的字段、带
  `debug_keep/observable/probe*/trace*` 的 IR 属性。
- 其余名字写 `pyc.observe_lazy`，并在存储上挂 `pyc.lazy_probe_slices` /
  `pyc.lazy_probe_wires`；emitter 用 `addRegLazySlice/addRegLazyTap/addWire` 注册，
  `findByPath` 仍可按名读，但不钉 struct 成员、不每拍抄。
- `--observe-named=all`：所有非 cycle_balance 名字保持急切（调试用）。

## 6. Trace codegen plan

`compiler/frontend/pycircuit/cli.py` 从 trace plan 与 probe manifest 推导
`trace_codegen_plan.json`（模块 → 被选中的内部字段），通过 `--trace-codegen-plan`
传给 pycc：placement 把这些名字钉在 struct，使 VCD 采样地址稳定；字段不存在时报错。
C++ 缓存 key 包含 trace 字段与 probe alias 路径，避免旧头文件被误用。

## 7. C++ 后端表示

- `pyc_delay_line<W,N>`：环形历史；`tick_commit()` 返回“尾部是否变化”，
  `has_pending()` 表示本拍是否推进。tail 与 tap 消费者**分别唤醒**：tail 只在值变化时，
  tap 在每次有效推进时（所有级整体移位）。
- probe/VCD 把 delay tail、tap 与 slice 通过 `StateStorageSource` 解析到同一个存储。
- 每个时钟域只做一次沿检测（`fastEdge`），所有 reg/delay_line 共享。

## 8. 验证

| 内容 | 入口 |
|------|------|
| ops/verifier | `compiler/mlir/test/delay_line_verify_smoke.sh` |
| 运行时提交语义 | `tests/runtime/run_delay_line_commit.sh` |
| 链折叠 | `compiler/mlir/test/state_delay_chain_smoke.sh` |
| retime（IR） | `compiler/mlir/test/state_retime_smoke.sh` |
| demand / Verilog 诊断 / 软件模型对拍 | `compiler/mlir/test/state_codegen_smoke.sh` |
| trace plan / cache key | `tests/test_trace_codegen_plan.py` |
| VCD alias（需 Verilator） | `tests/test_trace_config_vcd_alias.py` |

## 9. 不做的事

任意图上的全局 retiming、非纯算子/带反馈区域的下沉、delay_line 的 Verilog 降级、
等价寄存器合并与 lane 打包（见后续分层）。
