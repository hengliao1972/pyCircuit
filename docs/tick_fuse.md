# Tick-fuse on state loops（compute/commit 合并）

在 state loop（见 L2）之上，把「下一值是上一拍快照」的寄存器从两相协议（`compute_rise` 采样 →
`commit` 发布）合并成单相 `RegLoop::direct_commit()`：

- 分类（`CppEmitter.cpp`）：reg 的 `next`/`rst`/`en` 沿值图都不依赖本模块任何状态输出（reg/delay 的 q 与 tap、
  mem/fifo/cdc 输出、实例结果）才算 fused；comb 边界、顶层端口及其上的纯运算是稳定快照。
  仅当该模块全部 reg/delay 同一时钟时启用。
- 拆分：fused 与 phased 的 lane 分属不同 loop（`pyc.loop_id` key 末位区分），fused loop 不再调用
  `compute_rise/negedge/compute_generic/commit`。
- 时机：`direct_commit()` 在 `tick_compute` 末尾、所有其他元件（mem/fifo/实例/phased loop）都采样完之后，
  且只在本次调用见到上升沿时执行；返回逐 lane 脏位图，沿用同一套 `emitMetadataCombFanout` 唤醒。
- 写意图探针：`direct_commit()` 仍写 `pending_/qNext_`，`tick_commit` 里 `retire()` 清 pending，
  `lane_pending/lane_qnext` 的语义与两相一致（`tick()` 之后、`commit()` 之前可见）。
- 相对 `ldz/feat/tick-fuse` 原实现的取舍：原版按单个 reg 的 `direct_commit` 直写，且只检查 `next`；这里
  同时检查 `rst`/`en`，并把写入点后移到所有采样之后。未带 word-mask 唤醒（d57feb20 已有）与
  `--cpp-fuse-negedge`（默认关）。
- 门禁：`tests/runtime/test_state_loop.cpp`（fused lane 与两相 reg 的 q 与脏位图逐拍相等），其余沿用
  `comb_dirty_scheduler_smoke.sh`、`state_loop_smoke.sh` 与 34 trace 对拍。
