# pyCircuit 编程指南

pyCircuit 是一个基于 Python 的硬件描述框架，将 Python 函数编译为可综合的 RTL（通过 MLIR 中间表示）。其核心特性是**周期感知信号系统（Cycle-Aware Signal System）**：每个信号都携带逻辑时钟周期标注，编译器在组合不同流水线阶段的信号时**自动插入流水线寄存器（DFF）**。

pyCircuit 的设计哲学是**统一的信号模型**：不区分组合信号（wire）和时序信号（register）的定义语法。所有硬件元素统一定义为 **signal**，其底层实现类型（组合逻辑 / D 触发器）由编译器根据**复位值（reset）的有无**和**赋值关系的周期平衡**自动推导。

---

## 目录

1. [快速入门](#1-快速入门)
2. [设计模型：统一信号](#2-设计模型统一信号)
3. [核心数据类型](#3-核心数据类型)
4. [信号定义](#4-信号定义)
5. [信号操作](#5-信号操作)
6. [条件赋值](#6-条件赋值)
7. [周期与 `domain.next()`](#7-周期与-domainnext)
8. [反馈信号（Feedback）](#8-反馈信号feedback)
9. [内存与队列](#9-内存与队列)
10. [编译与仿真](#10-编译与仿真)
11. [完整示例：LinxISA 五级流水线 CPU](#11-完整示例linxisa-五级流水线-cpu)

---

## 1. 快速入门

### 1.1 最简示例：8 位计数器

```python
from pycircuit import CycleAwareCircuit, CycleAwareDomain, compile_cycle_aware, mux

def counter(m: CycleAwareCircuit, domain: CycleAwareDomain) -> None:
    enable = domain.signal("enable", width=1)              # 输入端口（cycle 0）

    count = domain.signal("count", width=8, reset=0)       # Q 输出（cycle 0）
    next_count = mux(enable, count + 1, count)              # 组合逻辑（cycle 0）

    domain.next()  # → cycle 1（DFF 的 D→Q 延迟）

    count.set(next_count)                                   # D 输入（cycle 1）

    m.output("count", count.sig)

circuit = compile_cycle_aware(counter, name="counter")
print(circuit.emit_mlir())
```

关键点：
- `count` 指定了 `reset=0` → 编译器推导为 D 触发器
- `count` 的 Q 输出在 cycle 0，读取和组合计算都在 cycle 0
- `domain.next()` 表示一个时钟周期过去（DFF 的 D→Q 延迟）
- `count.set(next_count)` 是条件赋值——将 D 输入连接到 flop
- `enable` 没有 `reset` → 编译器推导为组合信号（输入端口）

### 1.2 设计函数签名

每个 pyCircuit 设计都是一个 **Python 函数**，接受两个必要参数：

```python
def my_design(m: CycleAwareCircuit, domain: CycleAwareDomain) -> None:
    ...
```

| 参数 | 类型 | 说明 |
|------|------|------|
| `m` | `CycleAwareCircuit` | 电路构建器——创建信号、内存等所有硬件原语 |
| `domain` | `CycleAwareDomain` | 时钟域——封装 clk/rst、追踪当前逻辑周期 |

函数还可以接受额外的 **JIT 编译时参数**（如 `width`、`STAGES`），编译期间被静态展开：

```python
def build(m: CycleAwareCircuit, domain: CycleAwareDomain, STAGES: int = 3) -> None:
    ...

circuit = compile_cycle_aware(build, name="my_pipeline", STAGES=5)
```

### 1.3 必要导入

```python
from pycircuit import (
    CycleAwareCircuit,    # 电路构建器
    CycleAwareDomain,     # 时钟域（封装 clk + rst）
    CycleAwareSignal,     # 周期感知信号（统一类型）
    compile_cycle_aware,  # 编译入口
    mux,                  # 多路选择器
    ca_cat,               # 信号拼接
    ca_bundle,            # 结构体打包
)
```

---

## 2. 设计模型：统一信号

### 2.1 一切皆 Signal

在 pyCircuit 中，**没有独立的 wire 和 register 定义语法**。所有硬件元素统一定义为 `signal`，编译器根据以下信息**自动推导**其硬件实现：

```
signal 的硬件类型由两个信息源共同决定：

  1. 显式标记：定义时指定 reset 值 → 隐含为 D 触发器（flop）
  2. 隐式推导：编译器分析赋值依赖图 → 检测自引用环路 → 推导为 flop

  两者必须一致，矛盾则编译报错。
```

### 2.2 推导规则

```python
# ① 组合信号：无 reset，无自引用 → wire
alu_result = domain.signal("alu_result", width=64)
alu_result.set(srcl + srcr, when=op_is_add)    # 纯组合逻辑，无自引用

# ② 时序信号：有 reset，有自引用 → flop ✓ 一致
counter = domain.signal("counter", width=8, reset=0)     # Q at cycle 0
domain.next()                                              # cycle 1
counter.set(counter + 1, when=enable)                      # D at cycle 1, 自引用 → flop

# ③ 矛盾：有 reset 但无自引用 → 编译报错
temp = domain.signal("temp", width=8, reset=0)
temp.set(a + b)                                   # 纯组合 → 错误：reset 无意义

# ④ 矛盾：无 reset 但有自引用 → 编译报错
accum = domain.signal("accum", width=8)
accum.set(accum + a)                              # 自引用需要 flop，但无 reset 值
```

**编译器检查矩阵**：

| | 推导为 wire（无自引用） | 推导为 flop（有自引用/跨 cycle） |
|---|---|---|
| **无 reset** | 合法（组合逻辑） | **编译报错**：需要 reset 值 |
| **有 reset** | **编译报错**：reset 无意义 | 合法（时序逻辑） |

### 2.3 `reset` 是唯一的时序标记

`reset` 参数的存在与否是区分组合逻辑和时序逻辑的**唯一显式标记**。这是一个有意的设计折中：

- 完全隐式推导（连 reset 都不写）→ 危险：上电后 flop 的初始值不确定
- 完全显式区分（独立的 wire/reg 定义）→ 冗余：cycle 标注已携带足够的时序信息
- **`reset` 标记**（本方案）→ 最优平衡：语法统一，但时序意图明确

`reset` 逻辑上对应异步复位行为。`domain` 已封装了 clk 和 rst 信号，因此 `reset` 参数足以完整定义一个 D 触发器。

### 2.4 信号携带周期标注

每个信号都有一个 `.cycle` 属性，标注它逻辑上属于哪个时钟周期：

```python
# domain.current_cycle = 0
a = domain.signal("a", width=8)         # a.cycle = 0

domain.next()  # 推进到 cycle 1

b = domain.signal("b", width=8)         # b.cycle = 1
```

### 2.5 自动周期平衡

当**不同周期**的信号在**表达式**中组合时，框架自动插入 DFF 链对齐：

```python
# a.cycle=0, b.cycle=2, domain.current_cycle=2
result = a + b
# 编译器在 'a' 上插入 2 级 DFF，将其延迟到 cycle 2
# result.cycle = domain.current_cycle = 2
```

**平衡规则**：

| 条件 | 动作 |
|------|------|
| `signal.cycle < domain.current_cycle` | 插入 DFF 链延迟（前向平衡） |
| `signal.cycle == domain.current_cycle` | 同周期，直接使用 |
| `signal.cycle > domain.current_cycle` | 反馈信号，直接使用（不插入 DFF） |

### 2.6 常量自动匹配当前周期

常量自动标注为 `domain.current_cycle`，避免不必要的 DFF：

```python
domain.next()  # cycle = 1
c = domain.const(42, width=8)   # c.cycle = 1
```

---

## 3. 核心数据类型

### 3.1 `CycleAwareDomain` — 时钟域（信号声明的唯一入口）

`domain` 封装了 clk/rst 信号和逻辑周期计数器，是**所有信号声明的唯一入口**：

**信号声明**：

| 方法 | 说明 |
|------|------|
| `domain.signal(name, width, reset=None)` | **统一信号定义**（核心 API，见[第 4 节](#4-信号定义)） |
| `domain.const(value, width)` | 创建常量信号（cycle = 当前周期） |

**周期管理**：

| 方法/属性 | 说明 |
|-----------|------|
| `domain.next()` | 将周期计数器 +1（表示一个 DFF 延迟） |
| `domain.prev()` | 将周期计数器 -1 |
| `domain.push()` | 将当前周期压入保存栈 |
| `domain.pop()` | 从栈中恢复周期 |
| `domain.current_cycle` | 读取当前周期值（只读属性） |

### 3.2 `CycleAwareCircuit` — 电路构建器（结构性操作）

`m` 负责模块级的结构性操作（端口、连接、复合原语），**不直接创建信号**：

| 方法 | 说明 |
|------|------|
| `m.output(name, signal)` | 声明模块输出端口 |
| `m.named_wire(name, width)` | 创建占位导线（仅用于[反馈信号](#8-反馈信号feedback)） |
| `m.assign(wire, signal)` | 驱动占位导线 |
| `m.cat_signals(*signals)` | 信号拼接（MSB 在前） |
| `m.ca_byte_mem(name, domain, depth, data_width)` | 创建字节寻址内存 |
| `m.ca_queue(name, domain, width, depth)` | 创建 FIFO 队列 |
| `m.ca_bundle(**fields)` | 创建结构体（命名字段集合） |
| `m.create_domain(name)` | 创建/获取时钟域 |

### 3.3 `CycleAwareSignal` — 周期感知信号

**唯一的硬件信号类型**。每个信号携带 cycle、domain 和位宽等元数据，支持 Python 运算符重载：

| 属性/方法 | 说明 |
|-----------|------|
| `.cycle` | 信号的逻辑周期标注 |
| `.width` | 位宽 |
| `.sig` | 底层原始 `Signal`（用于 `m.output`、`m.assign`） |
| `.signed` | 是否有符号 |
| `.name` | 调试名称 |
| `.set(value, when=cond)` | **条件赋值**（等价于 `if cond: signal = value`） |

### 3.4 `CycleAwareBundle` — 结构体

命名字段的集合，支持打包/解包：

```python
from pycircuit import ca_bundle

bundle = ca_bundle(tag=tag_sig, data=data_sig, addr=addr_sig)
tag = bundle["tag"]
packed = bundle.pack()
unpacked = bundle.unpack(packed)
```

### 3.5 `CycleAwareQueue` — FIFO 队列

Ready/valid 握手协议的同步 FIFO：

```python
q = m.ca_queue("fifo", domain=domain, width=8, depth=4)
push_fire = q.push(data_in, when=in_valid)
p = q.pop(when=out_ready)    # p.valid / p.data / p.fire
```

### 3.6 `CycleAwareByteMem` — 字节寻址内存

```python
mem = m.ca_byte_mem("dmem", domain=domain, depth=4096, data_width=64)
rdata = mem.read(raddr)
mem.write(waddr, wdata, wstrb, when=write_enable)
```

---

## 4. 信号定义

### 4.1 统一定义语法

所有信号通过 `domain.signal()` 定义，`reset` 参数决定类型推导：

```python
# 组合信号（无 reset）
enable  = domain.signal("enable",  width=1)           # 输入端口 / wire
data_in = domain.signal("data_in", width=8)

# 时序信号（有 reset → 隐含为 D 触发器）
counter_r  = domain.signal("counter_r",  width=8,  reset=0)
fetch_pc_r = domain.signal("fetch_pc_r", width=64, reset=0)
halted_r   = domain.signal("halted_r",   width=1,  reset=0)
```

两者的定义语法完全一致，唯一的区别是 `reset` 参数的有无。

### 4.2 常量

```python
zero = domain.const(0, width=8)
```

常量自动标注 `cycle = domain.current_cycle`。

在运算中，整数字面量自动转换为常量：

```python
result = a + 1         # 1 自动转换为 width 匹配的常量
masked = data & 0xFF   # 0xFF 自动转换
```

### 4.3 命名约定（推荐）

由于 wire 和 flop 不再有语法区别，建议使用**命名约定**增强可读性：

```python
# 约定：_r 后缀表示时序信号（带 reset 的 flop）
counter_r   = domain.signal("counter_r",  width=8,  reset=0)
fetch_pc_r  = domain.signal("fetch_pc_r", width=64, reset=0)

# 约定：无后缀表示组合信号
next_pc     = ...    # 由表达式产生
alu_result  = ...    # 由表达式产生
```

框架可提供可选的 **lint 规则**（非强制）来检查命名一致性。

---

## 5. 信号操作

### 5.1 算术运算

```python
c = a + b           # 加法（自动位宽扩展）
c = a - b           # 减法
c = a * b           # 乘法
```

所有二元运算自动执行**周期平衡**和**位宽对齐**。

### 5.2 逻辑运算

```python
c = a & b           # 按位与
c = a | b           # 按位或
c = a ^ b           # 按位异或
c = ~a              # 按位取反
c = a << 3          # 左移常数位
c = a >> 2          # 右移常数位（有符号信号为算术右移）
```

### 5.3 比较运算

返回 1 位宽的信号：

```python
c = a.eq(b)         # a == b
c = a.ne(b)         # a != b
c = a.lt(b)         # a < b
c = a.gt(b)         # a > b
c = a.le(b)         # a <= b
c = a.ge(b)         # a >= b
```

### 5.4 多路选择器 `mux()`

```python
from pycircuit import mux

result = mux(condition, true_value, false_value)
# condition 必须为 1 位 (i1)
# 等价于 Verilog: condition ? true_value : false_value
```

`mux()` 自动对三个输入进行周期平衡。

### 5.5 位操作

```python
low8  = sig.trunc(width=8)           # 截断到低 8 位
wide  = sig.zext(width=64)           # 零扩展到 64 位
wide  = sig.sext(width=64)           # 符号扩展到 64 位
field = sig.slice(lsb=4, width=8)    # 提取 bit[11:4]
field = sig[4:12]                    # 同上（Python 切片语法）
bit   = sig[0]                       # 提取单个 bit
```

### 5.6 信号拼接

```python
from pycircuit import ca_cat

bus = ca_cat(tag, data, lo8)   # MSB 在前拼接: {tag, data, lo8}
```

### 5.7 调试命名与符号性

```python
result = (a + b).named("sum")   # 附加调试名称（出现在 VCD/MLIR 中）
s = sig.as_signed()              # 标记为有符号
u = sig.as_unsigned()            # 标记为无符号
```

---

## 6. 条件赋值

### 6.1 `.set()` 就是条件赋值

`.set()` 的语义等价于命令式编程中的赋值语句 `=`，加上可选的条件 `when`：

```python
signal.set(value)                    # 等价于  signal = value
signal.set(value, when=condition)    # 等价于  if condition: signal = value
```

**`.set()` 对所有信号类型（wire / flop）的行为完全一致**——它描述的是"将 value 赋给 signal"这个逻辑关系，而不是硬件的时序行为。时序行为由 `domain.next()` 和 `reset` 共同决定。

### 6.2 多次赋值：last-write-wins

对同一个信号多次调用 `.set()`，按命令式控制流的 **last-write-wins** 语义执行——**最后一次** `.set()` 优先级最高：

```python
pc_r = domain.signal("pc_r", width=64, reset=0)        # Q at cycle 0
next_pc_default = pc_r                                    # 保持不变
next_pc_advance = pc_r + inst_len                         # 正常推进
# ...

domain.next()  # → cycle 1

pc_r.set(next_pc_default)                                # 1. 默认保持（优先级最低）
pc_r.set(next_pc_advance, when=advance)                  # 2. 正常推进
pc_r.set(branch_target, when=branch_taken)                # 3. 分支跳转
pc_r.set(exception_entry, when=exception)                  # 4. 异常（优先级最高）
```

编译器将其展开为等价的 mux 树：

```
D = mux(exception, exception_entry,
      mux(branch_taken, branch_target,
        mux(advance, pc_r + inst_len,
          pc_r)))
```

这个 mux 树的生成规则对 wire 和 flop 完全一致：

- **wire**：`signal = mux_tree(...)` — 组合逻辑直连
- **flop**：`DFF.D = mux_tree(...)` — mux 树接入 DFF 的 D 端

### 6.3 组合信号的赋值

对于组合信号（无 `reset`），`.set()` 描述纯组合逻辑关系：

```python
alu_result = domain.signal("alu_result", width=64)   # 无 reset → wire

alu_result.set(0)                                      # 默认值（最先写，优先级最低）
alu_result.set(srcl + srcr, when=op_is_add)
alu_result.set(srcl - srcr, when=op_is_sub)
alu_result.set(srcl & srcr, when=op_is_and)
```

注意：组合信号的 `.set()` 中**不能出现自引用**（`alu_result` 不能出现在右侧），否则形成组合环路，编译报错。

### 6.4 时序信号的赋值

对于时序信号（有 `reset`），`.set()` 提供 DFF 的 D 端输入。**必须在 `domain.next()` 之后调用**，因为 DFF 的 D→Q 延迟由 `domain.next()` 表示：

```python
count_r = domain.signal("count_r", width=8, reset=0)   # Q at cycle 0
next_val = mux(enable, count_r + 1, count_r)             # 组合逻辑，cycle 0

domain.next()  # → cycle 1（DFF 延迟）

count_r.set(next_val)                                     # D at cycle 1
```

硬件视角：

```
cycle 0:  count_r (Q) ──→ +1 ──→ mux(enable) ──→ next_val
                                                      │
          domain.next()                               │  （flop 自身就是时序桥，
                                                      │   .set() 不触发额外的
cycle 1:                                count_r.set() ←   周期平衡 DFF 插入）
                                            │
                                        DFF 的 D 端
```

### 6.5 保持模式（Stall 支持）

流水线暂停时需要 flop 保持当前值。在 `.set()` 的 last-write-wins 语义下自然表达：

```python
fetch_pc_r = domain.signal("fetch_pc_r", width=64, reset=0)   # Q at cycle 0
next_pc = ...                                                    # cycle 0 的组合逻辑

domain.next()  # → cycle 1

fetch_pc_r.set(fetch_pc_r)                             # 默认：保持（优先级低）
fetch_pc_r.set(next_pc, when=~freeze)                  # 不冻结时更新（优先级高）
```

`freeze=1` 时：第二个 `.set()` 条件为假，第一个生效 → 保持当前值。
`freeze=0` 时：第二个 `.set()` 优先级更高 → 更新为 `next_pc`。

---

## 7. 周期与 `domain.next()`

### 7.1 `domain.next()` = 一个 DFF 延迟

`domain.next()` 的语义是**一个时钟周期过去**。每次调用将 `domain.current_cycle` 加 1。在硬件层面，它对应一个 DFF 的 D→Q 延迟。

这意味着：
- 每个带 `reset` 的信号（flop），其 `.set()` 必须在一个 `domain.next()` **之后**调用
- 信号定义时的 cycle = Q 端输出可用的周期
- `.set()` 调用时的 cycle = D 端输入的周期（= Q 的 cycle + 1）

```python
# 单级：计数器
count_r = domain.signal("count_r", width=8, reset=0)     # Q at cycle 0
domain.next()                                              # → cycle 1
count_r.set(count_r + 1, when=enable)                      # D at cycle 1

# 多级：三级流水线
stage0_in = domain.signal("data_in", width=16)            # cycle 0
stage1_r = domain.signal("stage1_r", width=16, reset=0)   # Q at cycle 0

domain.next()  # → cycle 1
stage1_r.set(stage0_in + 1)                                # D at cycle 1
stage2_r = domain.signal("stage2_r", width=16, reset=0)   # Q at cycle 1

domain.next()  # → cycle 2
stage2_r.set(stage1_r * 2)                                 # D at cycle 2
m.output("result", stage2_r.sig)
```

### 7.2 信号定义与 `.set()` 的分离

一个 flop 信号的定义（Q）和赋值（D）天然分属不同的 cycle。这使得代码结构清晰：

```
cycle N:     定义信号（Q 端） + 读取 Q + 组合计算
             domain.next()
cycle N+1:   .set()（D 端） + 定义下一级信号 + ...
```

### 7.3 使用 `push()`/`pop()` 预定义信号

复杂设计中，信号可能需要提前在正确的 cycle 定义（因为 Q 输出可能被多个 cycle 引用）。使用 `push()`/`pop()` 暂时跳转 cycle：

```python
def pipeline_design(m, domain):
    data_in = domain.signal("data_in", width=16)           # cycle 0

    # 预定义各级信号
    s1_r = domain.signal("s1_r", width=16, reset=0)        # Q at cycle 0

    domain.push()             # 保存 cycle 0
    domain.next()             # cycle 1
    s2_r = domain.signal("s2_r", width=16, reset=0)        # Q at cycle 1
    domain.pop()              # 恢复到 cycle 0

    # 按 cycle 顺序编写逻辑和赋值
    domain.next()             # → cycle 1
    s1_r.set(data_in + 1)    # D at cycle 1

    domain.next()             # → cycle 2
    s2_r.set(s1_r * 2)       # D at cycle 2

    m.output("out", s2_r.sig)
```

### 7.4 Python 循环展开流水线

JIT 编译时参数配合 Python `for` 循环生成多级流水线（编译期静态展开）：

```python
def build(m: CycleAwareCircuit, domain: CycleAwareDomain, STAGES: int = 3) -> None:
    a = domain.signal("a", width=16)
    b = domain.signal("b", width=16)
    bus = ca_cat(a, b)

    for i in range(STAGES):
        stage_r = domain.signal(f"stage{i}_r", width=bus.width, reset=0)  # Q at cycle i
        domain.next()                                                       # → cycle i+1
        stage_r.set(bus)                                                    # D at cycle i+1
        bus = stage_r

    m.output("result", bus.sig)

circuit = compile_cycle_aware(build, name="pipeline", STAGES=5)
```

---

## 8. 反馈信号（Feedback）

### 8.1 问题

在流水线中，后级阶段有时需要向前级发送信号（如 WB 的分支冲刷 → IF，EX 的数据前递 → ID）。按正常平衡规则，cycle 4 的信号在 cycle 0 使用会被插入 4 级 DFF——这对反馈来说是**错误的**。

### 8.2 解决方案：`named_wire` + `CycleAwareSignal`

反馈信号利用平衡规则的第三条：**`signal.cycle >= domain.current_cycle` → 直接使用**。

步骤：

1. **声明**一个占位导线（`named_wire`）
2. **包装**为 `CycleAwareSignal`，设置 `.cycle` 为**信号产生的周期**
3. **使用**：在前级阶段引用——框架看到 `signal.cycle >= current_cycle`，直接使用（不插 DFF）
4. **驱动**：在后级阶段用 `m.assign()` 将真实值连接到占位导线

```python
# 步骤 1-2：声明反馈信号（由 cycle 4 的 WB 阶段产生）
_fb_flush_w = m.named_wire("fb_flush", width=1)
fb_flush = CycleAwareSignal(
    m=m, sig=_fb_flush_w.sig, cycle=4,
    domain=domain, name="fb_flush",
)

# 步骤 3：在 IF 阶段（cycle 0）使用 —— 4 >= 0 → 反馈，直接引用
valid_if = ~fb_flush & ~fb_stop

# ... 后续阶段 ...

# 步骤 4：在 WB 阶段（cycle 4）驱动
m.assign(_fb_flush_w, actual_flush_signal.sig)
```

### 8.3 辅助函数模式

```python
def _fb(name: str, width: int, cycle: int) -> tuple:
    """创建反馈信号：返回 (占位导线, CycleAwareSignal)。"""
    w = m.named_wire(name, width=width)
    s = CycleAwareSignal(m=m, sig=w.sig, cycle=cycle, domain=domain, name=name)
    return w, s

# 控制反馈（WB → 全部前级）
_fb_flush_w, fb_flush           = _fb("fb_flush",       1,  cycle=4)
_fb_redirect_pc_w, fb_redirect_pc = _fb("fb_redirect_pc", 64, cycle=4)

# 数据前递（EX → ID）
_fb_ex_alu_w, fb_ex_alu         = _fb("fb_ex_alu",      64, cycle=2)
```

### 8.4 反馈信号的周期平衡行为

| 使用位置 | 信号 cycle | domain.current_cycle | 关系 | 行为 |
|----------|-----------|---------------------|------|------|
| IF (cycle 0) | `fb_flush` cycle=4 | 0 | 4 > 0 | **反馈**，直接使用 |
| ID (cycle 1) | `fb_ex_alu` cycle=2 | 1 | 2 > 1 | **反馈**，直接使用 |
| ID (cycle 1) | `ifid_pc_r` cycle=1 | 1 | 1 == 1 | **同周期**，直接使用 |
| EX (cycle 2) | `data_in` cycle=0 | 2 | 0 < 2 | **前向**，插入 2 级 DFF |

---

## 9. 内存与队列

### 9.1 字节寻址内存

```python
mem = m.ca_byte_mem("dmem", domain=domain, depth=4096, data_width=64)

# 组合读取（无延迟）
rdata = mem.read(raddr)

# 同步写入
mem.write(waddr, wdata, wstrb, when=write_enable)
```

`wstrb`（写字节使能）：每个 bit 控制 1 个字节。`0xFF` = 写全部 8 字节，`0x0F` = 只写低 4 字节。

### 9.2 FIFO 队列

```python
q = m.ca_queue("q", domain=domain, width=8, depth=4)

push_fire = q.push(data_in, when=in_valid)    # 入队
p = q.pop(when=out_ready)                      # 出队
# p.valid — 有数据
# p.data  — 队头数据
# p.fire  — 本周期实际出队
```

### 9.3 模块 I/O

```python
# 输入（signal 无 reset → 组合信号/端口）
data_in = domain.signal("data_in", width=8)

# 输出（.sig 获取底层原始信号）
m.output("result", some_signal.sig)
```

---

## 10. 编译与仿真

### 10.1 编译到 MLIR

```python
from pycircuit import compile_cycle_aware

circuit = compile_cycle_aware(my_design, name="my_module")
mlir_text = circuit.emit_mlir()
```

### 10.2 降级到 Verilog / C++

```bash
pyc-compile my_module.pyc --emit=cpp -o my_module_gen.hpp
pyc-compile my_module.pyc --emit=verilog -o my_module.v
```

### 10.3 C++ Testbench

```cpp
#include <pyc/cpp/pyc_tb.hpp>
#include "my_module_gen.hpp"

pyc::gen::my_module dut{};
pyc::cpp::Testbench<pyc::gen::my_module> tb(dut);

tb.addClock(dut.clk, /*halfPeriodSteps=*/1);
tb.reset(dut.rst, /*cyclesAsserted=*/2, /*cyclesDeasserted=*/1);

tb.enableVcd("trace.vcd", "top");
tb.vcdTrace(dut.clk, "clk");
tb.vcdTrace(dut.result, "result");

for (int i = 0; i < 100; i++) {
    tb.runCycles(1);
}
```

### 10.4 环境变量

| 变量 | 说明 |
|------|------|
| `PYC_VCD=1` | 启用 VCD 波形输出 |
| `PYC_TRACE_DIR=path` | 指定 VCD 输出目录 |
| `PYC_BOOT_PC=0x80000` | 指定启动 PC（CPU 设计） |

---

## 11. 完整示例：LinxISA 五级流水线 CPU

`linx_cpu_pyc` 实现了一个完整的 LinxISA 五级流水线 CPU，展示了统一信号模型在复杂设计中的运用。

### 11.1 流水线架构

```
cycle 0: IF  — 取指（从指令内存读取指令）
cycle 1: ID  — 译码、寄存器堆读取、数据前递、冒险检测
cycle 2: EX  — ALU 执行、地址计算
cycle 3: MEM — 数据内存访问（读/写）
cycle 4: WB  — 写回寄存器堆、分支解析
```

### 11.2 整体代码结构

设计函数遵循统一模式——每个 cycle 内先读取信号（Q），再做组合计算，`domain.next()` 后写入前一级信号（D）并读取当前级信号（Q）：

```python
def _linx_cpu_impl(m, domain, mem_bytes):
    # ① 在 cycle 0 定义输入信号和所有 flop 信号（用 push/next/pop）
    # ② 声明反馈信号（named_wire + CycleAwareSignal）
    # ③ 按 cycle 顺序：读 Q → 组合逻辑 → domain.next() → .set() D
    # ④ 在每级驱动反馈信号
    # ⑤ 声明输出
    ...
```

### 11.3 步骤 ①：定义所有信号

所有 flop 信号在其 **Q 输出被读取的 cycle** 定义：

```python
boot_pc = domain.signal("boot_pc", width=64)                # cycle 0, 输入

# Cycle 0: IF 级 flop
fetch_pc_r = domain.signal("fetch_pc_r", width=64, reset=0) # Q at cycle 0

domain.push()  # 保存 cycle 0

domain.next()  # → cycle 1
# IF/ID 流水线 flop（ID 阶段读取 Q）
ifid_window_r = domain.signal("ifid_window_r", width=64, reset=0)  # Q at cycle 1
ifid_pc_r     = domain.signal("ifid_pc_r",     width=64, reset=0)
valid_id_r    = domain.signal("valid_id_r",    width=1,  reset=0)

domain.next()  # → cycle 2
# ID/EX 流水线 flop（EX 阶段读取 Q）
idex_op_r     = domain.signal("idex_op_r",     width=6,  reset=OP_INVALID)
idex_regdst_r = domain.signal("idex_regdst_r", width=6,  reset=REG_INVALID)
# ...

domain.next()  # → cycle 3
# EX/MEM flop ...

domain.next()  # → cycle 4
# MEM/WB flop + 架构状态 + 寄存器堆
state_pc_r     = domain.signal("state_pc_r",     width=64, reset=0)
state_halted_r = domain.signal("state_halted_r", width=1,  reset=0)
gpr = [domain.signal(f"gpr{i}_r", width=64, reset=0) for i in range(32)]

domain.pop()   # 恢复到 cycle 0
```

### 11.4 步骤 ②：声明反馈信号

```python
def _fb(name, width, cycle):
    w = m.named_wire(name, width=width)
    s = CycleAwareSignal(m=m, sig=w.sig, cycle=cycle, domain=domain, name=name)
    return w, s

_fb_flush_w, fb_flush           = _fb("fb_flush",       1,  cycle=4)
_fb_redirect_pc_w, fb_redirect_pc = _fb("fb_redirect_pc", 64, cycle=4)
_fb_stop_w, fb_stop             = _fb("fb_stop",        1,  cycle=4)
_fb_ex_alu_w, fb_ex_alu         = _fb("fb_ex_alu",      64, cycle=2)
_fb_mem_value_w, fb_mem_value   = _fb("fb_mem_value",    64, cycle=3)
_fb_stall_id_w, fb_stall_id    = _fb("fb_stall_id",     1,  cycle=1)
_fb_freeze_if_w, fb_freeze_if  = _fb("fb_freeze_if",    1,  cycle=0)
_fb_freeze_id_w, fb_freeze_id  = _fb("fb_freeze_id",    1,  cycle=1)
# ...
```

### 11.5 步骤 ③：逐级逻辑

```python
# ======== Cycle 0: IF 阶段 — 读 Q，组合计算 ========
c = lambda v, w: domain.const(v, width=w)

# 暂停链（fb_stall_* 的 cycle > 0 → 反馈，直接使用）
cum_stall_if = fb_stall_id | fb_stall_ex | ...
m.assign(_fb_freeze_if_w, (cum_stall_if & ~fb_flush).sig)

# 读取 fetch_pc_r 的 Q 输出（cycle 0 == current → 直接读）
current_pc = mux(is_first, boot_pc, fetch_pc_r)
window = build_byte_mem(m, domain, raddr=current_pc, ...)
next_pc = mux(fb_flush, fb_redirect_pc, current_pc + inst_len)
valid_if = ~fb_stop & ~fb_flush & ~fb_freeze_if

domain.next()  # → cycle 1（DFF 延迟）

# 赋值 IF 级 flop 的 D 端
fetch_pc_r.set(fetch_pc_r)                              # 默认保持
fetch_pc_r.set(next_pc, when=~fb_stop & ~fb_freeze_if)  # 正常推进

ifid_window_r.set(ifid_window_r)
ifid_window_r.set(window, when=~fb_freeze_if)
valid_id_r.set(valid_if, when=~fb_freeze_if)

# ======== Cycle 1: ID 阶段 — 读 Q，组合计算 ========
c = lambda v, w: domain.const(v, width=w)

# 读 IF/ID flop Q — cycle 1 == current → 直接使用
ifid_window = ifid_window_r
valid_id = valid_id_r & ~fb_flush

# 寄存器堆读取 — GPR 在 cycle 4 → 反馈，直接使用
srcl_val = read_reg(m, srcl, gpr=gpr, ...)

# 数据前递
v = mux(fwd_ex_ok & srcl.eq(idex_regdst_r), fb_ex_alu, srcl_val)

# 冒险检测
stall_id = (load_use_hazard | tu_hazard) & valid_id
m.assign(_fb_stall_id_w, stall_id.sig)

domain.next()  # → cycle 2（DFF 延迟）

# 赋值 ID/EX flop 的 D 端
idex_op_r.set(mux(id_to_ex_valid, op_id, c(OP_INVALID, 6)), when=~fb_freeze_id)
idex_regdst_r.set(mux(id_to_ex_valid, regdst_id, c(REG_INVALID, 6)), when=~fb_freeze_id)
# ...

# ======== Cycle 2: EX 阶段 ========
ex_out = ex_stage_logic(m, domain, ...)
m.assign(_fb_ex_alu_w, ex_out["alu"].sig)

domain.next()  # → cycle 3
# 赋值 EX/MEM flop ...

# ======== Cycle 3: MEM 阶段 ========
mem_out = mem_stage_logic(m, ...)
m.assign(_fb_mem_value_w, mem_out["value"].sig)

domain.next()  # → cycle 4
# 赋值 MEM/WB flop ...

# ======== Cycle 4: WB 阶段 ========
wb_result = wb_stage_updates(m, ...)
m.assign(_fb_flush_w, wb_result["flush"].sig)
m.assign(_fb_redirect_pc_w, wb_result["redirect_pc"].sig)
m.assign(_fb_stop_w, stop.sig)

# 声明输出
m.output("halted", state_halted_r.sig)
m.output("pc", state_pc_r.sig)
# ...
```

### 11.6 运行和调试

```bash
bash tools/run_linx_cpu_pyc_cpp.sh                          # 全部回归测试
PYC_VCD=1 bash tools/run_linx_cpu_pyc_cpp.sh                # 带 VCD
PYC_VCD=1 PYC_TRACE_DIR=examples/generated/linx_cpu_pyc bash tools/run_linx_cpu_pyc_cpp.sh
```

---

## 附录：API 速查表

### 信号定义（统一语法）

```python
sig = domain.signal(name, width)                # 组合信号（wire）
sig = domain.signal(name, width, reset=value)   # 时序信号（flop）
```

### 条件赋值

```python
sig.set(value)                    # 无条件赋值（= value）
sig.set(value, when=condition)    # 条件赋值（if condition: = value）
```

### 常量

```python
c = domain.const(value, width)
```

### 周期管理

```python
domain.next()                      # cycle += 1（一个 DFF 延迟）
domain.prev()                      # cycle -= 1
domain.push()                      # 保存 cycle
domain.pop()                       # 恢复 cycle
```

### 反馈信号

```python
wire = m.named_wire(name, width)
sig = CycleAwareSignal(m=m, sig=wire.sig, cycle=N, domain=domain, name=name)
m.assign(wire, actual_value.sig)
```

### 编译

```python
circuit = compile_cycle_aware(fn, name=..., **jit_params)
mlir = circuit.emit_mlir()
```

### 每个 cycle 的代码模式

```
cycle N:
  1. 读取当前级 flop 信号的 Q（cycle N == current → 直接读）
  2. 组合逻辑计算
  3. domain.next()  → cycle N+1
  4. 对 cycle N 的 flop 调用 .set()（D 端赋值）
  5. 读取 cycle N+1 的 flop Q ... 重复
```

### 编译器类型推导规则

| 定义 | 赋值模式 | 推导结果 |
|------|---------|---------|
| 无 `reset`，无自引用 | `sig.set(expr)` | **wire**（组合逻辑） |
| 有 `reset`，有自引用 | `sig.set(sig + 1, when=en)` | **flop**（D 触发器） |
| 有 `reset`，无自引用 | — | **编译报错** |
| 无 `reset`，有自引用 | — | **编译报错** |
