# Janus TMU (Tile Management Unit) 微架构规格书

> 版本: 1.0
> 日期: 2026-02-10
> 实现代码: `janus/pyc/janus/tmu/janus_tmu_pyc.py`

---

## 1. 概述

### 1.1 TMU 在 Janus 中的定位

Janus 是一个 AI 执行单元，由以下五个核心模块组成：

| 模块 | 全称 | 功能 |
|------|------|------|
| **BCC** | Block Control Core | 标量控制核，负责指令调度与流程控制 |
| **TMU** | Tile Management Unit | Tile 寄存器文件管理单元，通过 Ring 互联提供高带宽数据访问 |
| **VectorCore** | 向量执行核 | 执行向量运算（load/store 通过 TMU 访问 TileReg） |
| **Cube** | 矩阵乘计算单元 | 基于 Systolic Array 的矩阵乘法引擎 |
| **TMA** | Tile Memory Access | 负责 TileReg 与外部 DDR 之间的数据搬运 |

TMU 是 Janus 的**片上数据枢纽**，管理一块名为 **TileReg** 的可配置 SRAM 缓冲区（默认 1MB），通过 **8 站点双向 Ring 互联网络**为各个计算核提供高带宽、低延迟的数据读写服务。

### 1.2 设计目标

- **峰值带宽**: 256B x 8 / cycle = 2048B/cycle
- **低延迟**: 本地访问（node 访问自身 pipe）仅需 4 cycle
- **确定性路由**: 静态最短路径路由，无动态路由
- **无活锁/饿死**: 通过 Tag 机制和 Round-Robin 仲裁保证公平性
- **可配置容量**: TileReg 大小可通过参数配置（默认 1MB）

---

## 2. 顶层架构

### 2.1 系统框图

```
                    ┌─────────────────────────────────────────────┐
                    │                   TMU                       │
                    │                                             │
  Vector port0 ──── │── node0 ──── pipe0 (128KB SRAM)            │
  Cube   port0 ──── │── node1 ──── pipe1 (128KB SRAM)            │
  Vector port1 ──── │── node2 ──── pipe2 (128KB SRAM)            │
  Cube   port1 ──── │── node3 ──── pipe3 (128KB SRAM)            │
  Vector port2 ──── │── node4 ──── pipe4 (128KB SRAM)            │
  TMA    port0 ──── │── node5 ──── pipe5 (128KB SRAM)            │
  BCC/CSU      ──── │── node6 ──── pipe6 (128KB SRAM)            │
  TMA    port1 ──── │── node7 ──── pipe7 (128KB SRAM)            │
                    │                                             │
                    │     Ring Interconnect (CW/CC)               │
                    └─────────────────────────────────────────────┘
```

### 2.2 Node-Pipe 映射关系

| Pipe | Node | 外部连接 | 用途 |
|------|------|----------|------|
| pipe0 | node0 | Vector port0 | Vector 内部 load 指令的访问通道 |
| pipe1 | node1 | Cube port0 | Cube 的读数据通道 |
| pipe2 | node2 | Vector port1 | Vector 内部 load 指令的访问通道 |
| pipe3 | node3 | Cube port1 | Cube 的写数据通道 |
| pipe4 | node4 | Vector port2 | Vector 内部 store 指令的访问通道 |
| pipe5 | node5 | TMA port0 | TMA 读数据通道（TStore: TileReg -> DDR） |
| pipe6 | node6 | BCC/CSU | 预留给 BCC 命令/响应或 CSU |
| pipe7 | node7 | TMA port1 | TMA 写数据通道（TLoad: DDR -> TileReg） |

### 2.3 每个 CS (Station) 的能力

- 每个 CS 支持挂载**最多 3 个节点**（当前实现每个 CS 挂载 1 个节点）
- 每个 CS 支持**同拍上下 Ring**（请求 Ring 和响应 Ring 完全独立并行）
- 每个 CS 可同时向 CW 和 CC 两个方向各发出/接收一个 flit

---

## 3. Ring 互联网络

### 3.1 拓扑结构

Ring 采用**双向环形拓扑**，8 个 station 按以下物理顺序连接：

```
RING_ORDER = [0, 1, 3, 5, 7, 6, 4, 2]
```

即 node 之间的连接关系为：

```
node0 <-> node1 <-> node3 <-> node5 <-> node7 <-> node6 <-> node4 <-> node2 <-> node0
```

用环形图表示：

```
            node0
           /     \
        node2     node1
        |             |
        node4     node3
        |             |
        node6     node5
           \     /
            node7
```

### 3.2 双向车道

Ring 支持两个方向的数据流动：

| 方向 | 缩写 | 含义 |
|------|------|------|
| Clockwise | CW | 顺时针方向：沿 RING_ORDER 正序流动 (0→1→3→5→7→6→4→2→0) |
| Counter-Clockwise | CC | 逆时针方向：沿 RING_ORDER 逆序流动 (0→2→4→6→7→5→3→1→0) |

### 3.3 独立 Ring 通道

TMU 内部包含**四条独立的 Ring 通道**：

| Ring 通道 | 方向 | 用途 |
|-----------|------|------|
| req_cw | CW | 请求 Ring 顺时针通道 |
| req_cc | CC | 请求 Ring 逆时针通道 |
| rsp_cw | CW | 响应 Ring 顺时针通道 |
| rsp_cc | CC | 响应 Ring 逆时针通道 |

请求 Ring 和响应 Ring 完全解耦，可并行工作。

### 3.4 路由策略

采用**静态最短路径路由**，在编译时预计算每对 (src, dst) 的最优方向：

```python
CW_PREF[src][dst] = 1  # 如果 CW 方向跳数 <= CC 方向跳数
CW_PREF[src][dst] = 0  # 如果 CC 方向跳数更短
```

**路由规则**：
- 不允许动态路由
- 当 CW 和 CC 距离相等时，优先选择 CW
- 路由方向在请求注入 Ring 时确定，传输过程中不改变

### 3.5 Ring 跳数表

基于 RING_ORDER = [0, 1, 3, 5, 7, 6, 4, 2]，各 node 之间的 Ring 跳数（最短路径）：

| src\dst | n0 | n1 | n2 | n3 | n4 | n5 | n6 | n7 |
|---------|----|----|----|----|----|----|----|----|
| **n0** | 0 | 1 | 1 | 2 | 2 | 3 | 3 | 4 |
| **n1** | 1 | 0 | 2 | 1 | 3 | 2 | 4 | 3 |
| **n2** | 1 | 2 | 0 | 3 | 1 | 4 | 2 | 3 |
| **n3** | 2 | 1 | 3 | 0 | 4 | 1 | 3 | 2 |
| **n4** | 2 | 3 | 1 | 4 | 0 | 3 | 1 | 2 |
| **n5** | 3 | 2 | 4 | 1 | 3 | 0 | 2 | 1 |
| **n6** | 3 | 4 | 2 | 3 | 1 | 2 | 0 | 1 |
| **n7** | 4 | 3 | 3 | 2 | 2 | 1 | 1 | 0 |

---

## 4. Flit 格式

### 4.1 数据粒度

Ring 上传输的数据粒度为 **256 Bytes**（一个 cacheline），由 32 个 64-bit word 组成：

```
Flit Data = 32 x 64-bit words = 256 Bytes
```

### 4.2 请求 Flit Meta 格式

请求 flit 的 meta 信息打包在一个 64-bit 字段中：

```
[63                                    REQ_ADDR_LSB] [REQ_TAG_LSB] [REQ_DST_LSB] [REQ_SRC_LSB] [0]
|<------------- addr (20b) ---------->|<- tag (8b) ->|<- dst (3b) ->|<- src (3b) ->|<- write (1b) ->|
```

| 字段 | 位宽 | LSB | 含义 |
|------|------|-----|------|
| write | 1 | 0 | 读/写标志（1=写，0=读） |
| src | 3 (node_bits) | 1 | 源节点编号 |
| dst | 3 (node_bits) | 4 | 目的节点编号（= pipe 编号） |
| tag | 8 | 7 | 请求标签，用于匹配响应 |
| addr | 20 (addr_bits) | 15 | 字节地址 |

### 4.3 响应 Flit Meta 格式

```
[63                    RSP_TAG_LSB] [RSP_DST_LSB] [RSP_SRC_LSB] [0]
|<-------- tag (8b) -------->|<- dst (3b) ->|<- src (3b) ->|<- write (1b) ->|
```

| 字段 | 位宽 | LSB | 含义 |
|------|------|-----|------|
| write | 1 | 0 | 原始请求的读/写标志 |
| src | 3 | 1 | 响应源（= pipe 编号） |
| dst | 3 | 4 | 响应目的（= 原始请求的 src） |
| tag | 8 | 7 | 原始请求的 tag，原样返回 |

---

## 5. TileReg 存储结构

### 5.1 容量与划分

TileReg 是 TMU 管理的片上 SRAM 缓冲区：

- **默认总容量**: 1MB (1,048,576 Bytes)，可通过 `tile_bytes` 参数配置
- **划分方式**: 均分为 8 个 **pipe**，每个 pipe 对应一块独立 SRAM
- **每 pipe 容量**: tile_bytes / 8 = 128KB（默认配置下）
- **每 pipe 行数**: pipe_bytes / 256 = 512 行（默认配置下）
- **每行大小**: 256 Bytes = 32 x 64-bit words

```
TileReg (1MB)
├── pipe0: 128KB SRAM (512 lines x 256B)  ── node0
├── pipe1: 128KB SRAM (512 lines x 256B)  ── node1
├── pipe2: 128KB SRAM (512 lines x 256B)  ── node2
├── pipe3: 128KB SRAM (512 lines x 256B)  ── node3
├── pipe4: 128KB SRAM (512 lines x 256B)  ── node4
├── pipe5: 128KB SRAM (512 lines x 256B)  ── node5
├── pipe6: 128KB SRAM (512 lines x 256B)  ── node6
└── pipe7: 128KB SRAM (512 lines x 256B)  ── node7
```

每个 pipe 内部由 32 个独立的 `byte_mem` 实例组成（每个 word 一个），支持单周期读写。

### 5.2 地址编码

以 1MB 容量为例，使用 20-bit 字节地址：

```
地址格式: [19:11] [10:8] [7:0]
           index   pipe   offset
           9-bit   3-bit  8-bit
```

| 字段 | 位域 | 位宽 | 含义 |
|------|------|------|------|
| offset | [7:0] | 8 | 256B cacheline 内部的字节偏移 |
| pipe | [10:8] | 3 | 目标 pipe 编号（0~7），决定数据存储在哪个 SRAM |
| index | [19:11] | 9 | cacheline 在对应 pipe 中的行号（0~511） |

**地址解码过程**：
1. 从请求地址中提取 `pipe = addr[10:8]`，确定目标 pipe（同时也是目标 node）
2. 提取 `index = addr[19:11]`，确定 pipe 内的行号
3. `offset = addr[7:0]` 在当前实现中用于 256B 粒度内的字节定位

### 5.3 可配置性

| 参数 | 默认值 | 约束 |
|------|--------|------|
| `tile_bytes` | 1MB (2^20) | 必须是 8 x 256 = 2048 的整数倍 |
| `tag_bits` | 8 | 请求标签位宽 |
| `spb_depth` | 4 | SPB FIFO 深度 |
| `mgb_depth` | 4 | MGB FIFO 深度 |

地址位宽根据 `tile_bytes` 自动计算：
```
addr_bits = ceil(log2(tile_bytes))    # 20 for 1MB
offset_bits = ceil(log2(256)) = 8
pipe_bits = ceil(log2(8)) = 3
index_bits = addr_bits - offset_bits - pipe_bits  # 9 for 1MB
```

---

## 6. 节点微架构

每个 node 包含以下组件：

```
                          ┌──────────────────────────────────┐
                          │           Node i                  │
                          │                                   │
  外部请求 ──req_valid──> │  ┌─────────┐    ┌─────────┐      │
  (valid/ready)           │  │ SPB_CW  │    │ SPB_CC  │      │
  req_write ────────────> │  │ depth=4 │    │ depth=4 │      │
  req_addr ─────────────> │  │ 1W2R    │    │ 1W2R    │      │
  req_tag ──────────────> │  └────┬────┘    └────┬────┘      │
  req_data[0:31] ───────> │       │              │            │
  <──── req_ready ─────── │       v              v            │
                          │   ┌──────────────────────┐        │
                          │   │   Request Ring       │        │
                          │   │   CW/CC 注入/转发    │        │
                          │   └──────────────────────┘        │
                          │                                   │
                          │   ┌──────────────────────┐        │
                          │   │   Pipe SRAM          │        │
                          │   │   (32 x byte_mem)    │        │
                          │   └──────────────────────┘        │
                          │                                   │
                          │   ┌──────────────────────┐        │
                          │   │   Response Ring      │        │
                          │   │   CW/CC 注入/转发    │        │
                          │   └──────────────────────┘        │
                          │       │              │            │
                          │  ┌────┴────┐    ┌────┴────┐      │
                          │  │ MGB_CW  │    │ MGB_CC  │      │
                          │  │ depth=4 │    │ depth=4 │      │
                          │  │ 2W1R    │    │ 2W1R    │      │
                          │  └────┬────┘    └────┬────┘      │
                          │       │    RR 仲裁    │           │
                          │       └──────┬───────┘            │
  <──── resp_valid ────── │              │                    │
  <──── resp_tag ──────── │              v                    │
  <──── resp_data[0:31] ─ │         resp output               │
  <──── resp_is_write ─── │                                   │
  ──── resp_ready ──────> │                                   │
                          └──────────────────────────────────┘
```

### 6.1 节点外部接口

每个 node 对外暴露以下信号：

**请求通道（外部 -> TMU）**：

| 信号 | 位宽 | 方向 | 含义 |
|------|------|------|------|
| `n{i}_req_valid` | 1 | input | 请求有效 |
| `n{i}_req_write` | 1 | input | 1=写请求，0=读请求 |
| `n{i}_req_addr` | 20 | input | 字节地址 |
| `n{i}_req_tag` | 8 | input | 请求标签（用于匹配响应） |
| `n{i}_req_data_w{0..31}` | 64 each | input | 写数据（32 个 64-bit word） |
| `n{i}_req_ready` | 1 | output | 请求就绪（反压信号） |

**响应通道（TMU -> 外部）**：

| 信号 | 位宽 | 方向 | 含义 |
|------|------|------|------|
| `n{i}_resp_valid` | 1 | output | 响应有效 |
| `n{i}_resp_tag` | 8 | output | 响应标签（与请求 tag 匹配） |
| `n{i}_resp_data_w{0..31}` | 64 each | output | 响应数据 |
| `n{i}_resp_is_write` | 1 | output | 标识原始请求是否为写操作 |
| `n{i}_resp_ready` | 1 | input | 外部准备好接收响应 |

**握手协议**: 标准 valid/ready 握手。当 `valid & ready` 同时为高时，传输发生。

---

## 7. SPB (Send/Post Buffer)

### 7.1 功能概述

SPB 是请求上 Ring 的缓冲区，位于每个 node 的请求注入端。每个 node 有两个 SPB：
- **SPB_CW**: 缓存将要向 CW 方向发送的请求
- **SPB_CC**: 缓存将要向 CC 方向发送的请求

### 7.2 SPB 规格

| 参数 | 值 |
|------|-----|
| 深度 | 4 entries |
| 端口 | 1 写 2 读（一拍可同时 pick CW 和 CC 各一个请求上 Ring） |
| Bypass | **不支持** bypass SPB 上 Ring（请求必须先入 SPB 再注入 Ring） |
| 反压 | SPB 满时，`req_ready` 拉低，反压外部请求 |

### 7.3 SPB 工作流程

1. 外部请求到达 node，根据 `CW_PREF[src][dst]` 确定方向
2. 请求被写入对应方向的 SPB（CW 或 CC）
3. 当 Ring 对应方向的 slot 空闲时，SPB 头部的请求被注入 Ring
4. Ring 上已有 flit 优先前递（forward），SPB 注入优先级低于 Ring 转发

### 7.4 SPB 注入仲裁

```
if ring_slot_has_flit:
    forward flit (优先)
    SPB 不注入
else:
    if SPB 非空 and 目的不是本地:
        注入 SPB 头部请求到 Ring
```

**本地请求优化**: 如果 SPB 头部请求的目的 node 就是本 node（即 src == dst），则该请求直接被弹出送往本地 pipe，不经过 Ring 传输。

---

## 8. MGB (Merge Buffer)

### 8.1 功能概述

MGB 是响应下 Ring 的缓冲区，位于每个 node 的响应接收端。每个 node 有两个 MGB：
- **MGB_CW**: 缓存从 CW 方向到达的响应
- **MGB_CC**: 缓存从 CC 方向到达的响应

### 8.2 MGB 规格

| 参数 | 值 |
|------|-----|
| 深度 | 4 entries |
| 端口 | 2 写 1 读（一拍可同时接收 CW 和 CC 各一个 flit，单路出队） |
| Bypass | **支持** bypass 下 Ring（队列为空且仅一个方向到达时可 bypass） |
| 反压 | MGB 满时，反压 Ring 上的响应注入 |

### 8.3 MGB Bypass 机制

当满足以下条件时，响应可以 bypass MGB 直接输出：
- MGB 队列为空
- 仅有一个方向（CW 或 CC）有到达的响应
- 外部 `resp_ready` 为高

### 8.4 MGB 出队仲裁

当 CW 和 CC 两个 MGB 都有数据时，采用 **Round-Robin (RR)** 仲裁：

```
rr_reg: 1-bit 寄存器，每次出队后翻转
if only CW has data:  pick CW
if only CC has data:  pick CC
if both have data:    rr_reg==0 ? pick CW : pick CC
```

RR 仲裁确保两个方向的响应不会饿死。

---

## 9. 请求 Ring 数据通路

### 9.1 请求处理流水线

```
外部请求 → SPB入队(1 cycle) → Ring传输(N hops) → Pipe SRAM访问(1 cycle) → 响应注入
```

### 9.2 请求 Ring 每站逻辑

对于 Ring 上的每个 station（按 RING_ORDER 遍历），每拍执行以下逻辑：

**Step 1: 检查到达的 Ring flit**
```
cw_in = 从 CW 方向前一站到达的 flit
cc_in = 从 CC 方向后一站到达的 flit
```

**Step 2: 判断是否为本地请求（需要弹出到 pipe）**
```
ring_cw_local = cw_in.valid AND (cw_in.dst == 本站 node_id)
ring_cc_local = cc_in.valid AND (cc_in.dst == 本站 node_id)
spb_cw_local  = spb_cw.valid AND (spb_cw.dst == 本站 node_id)
spb_cc_local  = spb_cc.valid AND (spb_cc.dst == 本站 node_id)
```

**Step 3: 优先级仲裁（弹出到 pipe）**
```
优先级从高到低:
1. Ring CW 方向到达的本地请求
2. Ring CC 方向到达的本地请求
3. SPB CW 中目的为本地的请求
4. SPB CC 中目的为本地的请求
```

**Step 4: Ring 转发与 SPB 注入**
```
CW 方向:
  if cw_in 非本地: 转发 cw_in（优先）
  else if SPB_CW 非空且非本地: 注入 SPB_CW 头部

CC 方向:
  if cc_in 非本地: 转发 cc_in（优先）
  else if SPB_CC 非空且非本地: 注入 SPB_CC 头部
```

---

## 10. Pipe SRAM 访问

### 10.1 Pipe Stage 寄存器

从请求 Ring 弹出的请求先经过一级 **pipe stage 寄存器**（1 cycle 延迟），然后访问 SRAM：

```
pipe_req_valid → [pipe_stage_valid reg] → SRAM 读/写
pipe_req_meta  → [pipe_stage_meta  reg] → 地址解码
pipe_req_data  → [pipe_stage_data  reg] → 写数据
```

### 10.2 SRAM 读写操作

**写操作**:
- 条件: `pipe_stage_valid & write`
- 将 32 个 64-bit word 写入对应 pipe 的 SRAM
- 写掩码: 全字节写入 (wstrb = 0xFF)
- 响应数据: 返回写入的数据本身

**读操作**:
- 条件: `pipe_stage_valid & ~write`
- 从对应 pipe 的 SRAM 读出 32 个 64-bit word
- 响应数据: 返回读出的数据

### 10.3 响应生成

SRAM 访问完成后，生成响应 flit：
```
rsp_meta = pack(write, src=pipe_id, dst=原始请求的src, tag=原始请求的tag)
rsp_data = write ? 写入数据 : 读出数据
rsp_dir  = CW_PREF[pipe_id][原始请求的src]  # 响应方向
```

响应被送入对应方向的响应注入 FIFO（深度=4），等待注入响应 Ring。

---

## 11. 响应 Ring 数据通路

### 11.1 响应 Ring 每站逻辑

与请求 Ring 类似，但弹出目标是 MGB 而非 pipe：

**Step 1: 检查到达的 Ring flit**
```
cw_in = 从 CW 方向前一站到达的响应 flit
cc_in = 从 CC 方向后一站到达的响应 flit
```

**Step 2: 判断是否为本地响应**
```
ring_cw_local = cw_in.valid AND (cw_in.dst == 本站 node_id)
ring_cc_local = cc_in.valid AND (cc_in.dst == 本站 node_id)
```

**Step 3: 本地响应送入 MGB**
```
cw_local = ring_cw_local OR rsp_inject_cw_local
cc_local = ring_cc_local OR rsp_inject_cc_local
→ 分别送入 MGB_CW 和 MGB_CC
```

**Step 4: Ring 转发与响应注入**
```
CW 方向:
  if cw_in 非本地: 转发（优先）
  else if rsp_inject_cw 非空且非本地: 注入

CC 方向:
  if cc_in 非本地: 转发（优先）
  else if rsp_inject_cc 非空且非本地: 注入
```

### 11.2 MGB 出队到外部

```
MGB_CW 和 MGB_CC 通过 RR 仲裁选择一个输出
→ resp_valid, resp_tag, resp_data, resp_is_write
← resp_ready (外部反压)
```

---

## 12. 时序分析

### 12.1 延迟模型

一次完整的读/写操作延迟由以下阶段组成：

| 阶段 | 延迟 | 说明 |
|------|------|------|
| SPB 入队 | 1 cycle | 请求写入 SPB |
| 请求 Ring 传输 | H hops | H = src 到 dst 的最短跳数 |
| Pipe Stage | 1 cycle | pipe stage 寄存器 |
| SRAM 访问 | 0 cycle | 与 pipe stage 同拍完成 |
| 响应 Ring 传输 | H hops | H = dst 到 src 的最短跳数（与请求相同） |
| MGB bypass/出队 | 1 cycle | 响应输出（bypass 时为 0） |

**总延迟公式**: `Latency = 4 + 2 * H` cycles（最优情况，无竞争）

其中 H 为 Ring 上的跳数。

### 12.2 典型延迟示例

**最短路径示例（Vector 访问 pipe2，H=1）**:

```
Cycle 1: Vector 请求到达 node2 → SPB 入队
Cycle 2: SPB 注入请求 Ring → 请求到达 node2（本地，H=0 实际上是自访问）
Cycle 3: Pipe stage 寄存器 + SRAM 访问
Cycle 4: 响应 bypass MGB 输出 → 数据可用
总延迟: 4 cycles
```

**跨节点示例（node0 访问 pipe2，H=1）**:

```
Cycle 1: node0 请求 → SPB 入队
Cycle 2: SPB 注入请求 Ring（CC 方向，node0→node2 跳 1 hop）
Cycle 3: 请求到达 node2 → 弹出到 pipe2 → pipe stage
Cycle 4: SRAM 访问完成 → 响应注入响应 Ring
Cycle 5: 响应传输 1 hop（node2→node0）
Cycle 6: 响应到达 node0 → MGB bypass 输出
总延迟: 6 cycles = 4 + 2*1
```

**远距离示例（node0 访问 pipe7，H=4）**:

```
总延迟: 4 + 2*4 = 12 cycles
```

### 12.3 各 node 自访问延迟

| 操作 | 延迟 |
|------|------|
| node_i 访问 pipe_i（自身 pipe） | 4 cycles |
| node_i 访问相邻 pipe（H=1） | 6 cycles |
| node_i 访问 H=2 的 pipe | 8 cycles |
| node_i 访问 H=3 的 pipe | 10 cycles |
| node_i 访问 H=4 的 pipe（最远） | 12 cycles |

---

## 13. 反压与流控

### 13.1 请求侧反压

```
req_ready = dir_cw ? SPB_CW.in_ready : SPB_CC.in_ready
```

当对应方向的 SPB 满（4 entries）时，`req_ready` 拉低，外部请求被阻塞。

### 13.2 Ring 反压

Ring 上的 flit 转发优先于 SPB 注入。当 Ring slot 被占用时，SPB 无法注入，但不会丢失数据（SPB 保持 flit 直到 slot 空闲）。

### 13.3 响应侧反压

MGB 满时，Ring 上到达本站的响应无法弹出，会继续在 Ring 上流转（实际上会阻塞 Ring 转发）。

外部 `resp_ready` 为低时，MGB 不出队，可能导致 MGB 满。

---

## 14. 防活锁/饿死机制

### 14.1 Tag 机制

- 每个请求携带 8-bit tag，响应原样返回
- Tag 用于请求-响应匹配，确保外部可以区分不同请求的响应
- Tag 不参与 Ring 路由决策

### 14.2 FIFO 顺序保证

- SPB 和 MGB 均为 FIFO 结构，保证同方向的请求/响应按序处理
- 避免了乱序导致的活锁问题

### 14.3 Round-Robin 仲裁

- MGB 出队采用 RR 仲裁，确保 CW 和 CC 两个方向的响应公平出队
- Pipe 访问时，Ring CW/CC 和 SPB CW/CC 四路请求按固定优先级仲裁
- Ring 转发优先于 SPB 注入，保证 Ring 上的 flit 不会被无限阻塞

### 14.4 静态路由

- 最短路径静态路由消除了动态路由可能引入的活锁
- 请求和响应走独立的 Ring，避免请求-响应死锁

---

## 15. 调试接口

TMU 提供以下调试输出信号，用于波形观察和可视化：

| 信号 | 位宽 | 含义 |
|------|------|------|
| `dbg_req_cw_v{i}` | 1 | 请求 Ring CW 方向 node_i 处 link 寄存器 valid |
| `dbg_req_cc_v{i}` | 1 | 请求 Ring CC 方向 node_i 处 link 寄存器 valid |
| `dbg_req_cw_meta{i}` | variable | 请求 Ring CW 方向 node_i 处 meta 信息 |
| `dbg_req_cc_meta{i}` | variable | 请求 Ring CC 方向 node_i 处 meta 信息 |
| `dbg_rsp_cw_v{i}` | 1 | 响应 Ring CW 方向 node_i 处 link 寄存器 valid |
| `dbg_rsp_cc_v{i}` | 1 | 响应 Ring CC 方向 node_i 处 link 寄存器 valid |
| `dbg_rsp_cw_meta{i}` | variable | 响应 Ring CW 方向 node_i 处 meta 信息 |
| `dbg_rsp_cc_meta{i}` | variable | 响应 Ring CC 方向 node_i 处 meta 信息 |

配套工具：
- `janus/tools/plot_tmu_trace.py`: 将 trace CSV 渲染为 SVG 时序图
- `janus/tools/animate_tmu_trace.py`: 生成 Ring 拓扑动画 SVG
- `janus/tools/animate_tmu_ring_vcd.py`: 从 VCD 波形生成 Ring 动画

---

## 16. 实现代码结构

### 16.1 源文件

| 文件 | 用途 |
|------|------|
| `janus/pyc/janus/tmu/janus_tmu_pyc.py` | TMU RTL 实现（pyCircuit DSL） |
| `janus/tb/tb_janus_tmu_pyc.cpp` | C++ cycle-accurate 测试平台 |
| `janus/tb/tb_janus_tmu_pyc.sv` | SystemVerilog 测试平台 |
| `janus/tools/run_janus_tmu_pyc_cpp.sh` | C++ 仿真运行脚本 |
| `janus/tools/run_janus_tmu_pyc_verilator.sh` | Verilator 仿真运行脚本 |
| `janus/tools/update_tmu_generated.sh` | 重新生成 RTL 脚本 |
| `janus/generated/janus_tmu_pyc/` | 生成的 Verilog 和 C++ header |

### 16.2 代码关键函数/区域

| 代码区域 | 行号范围 | 功能 |
|----------|----------|------|
| `RING_ORDER`, `CW_PREF` | L12-L34 | Ring 拓扑定义与路由表 |
| `_dir_cw()` | L37-L40 | 运行时路由方向选择 |
| `_build_bundle_fifo()` | L82-L129 | FIFO bundle 构建（SPB/MGB 共用） |
| `NodeIo` | L132-L144 | 节点 IO 定义 |
| `build()` 参数处理 | L147-L177 | 可配置参数与地址位宽计算 |
| Node IO 实例化 | L203-L232 | 8 个节点的 IO 端口创建 |
| SPB 构建 | L234-L290 | 每节点 CW/CC 两个 SPB |
| Ring link 寄存器 | L292-L331 | 请求/响应 Ring 的 link 寄存器 |
| 请求 Ring 遍历 | L338-L408 | 请求 Ring 每站逻辑（弹出/转发/注入） |
| Pipe stage 寄存器 | L410-L426 | Pipe 访问前的寄存器级 |
| 响应注入 FIFO | L428-L503 | Pipe 访问后的响应注入缓冲 |
| 响应 Ring 遍历 | L505-L630 | 响应 Ring 每站逻辑 + MGB |
| 调试输出 | L632-L654 | 调试信号输出 |

---

## 17. 测试验证

### 17.1 基础测试用例

测试平台（`tb_janus_tmu_pyc.cpp` / `tb_janus_tmu_pyc.sv`）包含以下测试：

**Test 1: 本地读写（每个 node 访问自身 pipe）**
```
for each node n in [0..7]:
    1. node_n 写 pipe_n: addr = makeAddr(n, n, 0), data = seed(n+1)
    2. 等待写响应，验证 tag 和 data 匹配
    3. node_n 读 pipe_n: 同一地址
    4. 等待读响应，验证读回数据 == 写入数据
```

**Test 2: 跨节点读写（node0 访问 pipe2）**
```
1. node0 写 pipe2: addr = makeAddr(5, 2, 0), data = seed(0xAA), tag = 0x55
2. 等待写响应
3. node0 读 pipe2: 同一地址, tag = 0x56
4. 等待读响应，验证读回数据 == 写入数据
```

### 17.2 验证要点

- Tag 匹配：响应的 tag 必须与请求的 tag 一致
- 数据完整性：读回的 32 个 64-bit word 必须与写入完全一致
- resp_is_write：正确反映原始请求类型
- 超时检测：2000 cycle 内未收到响应则报错

---

## 附录 A: CW_PREF 路由偏好表

基于 RING_ORDER = [0, 1, 3, 5, 7, 6, 4, 2]，预计算的路由偏好（1=CW, 0=CC）：

| src\dst | 0 | 1 | 2 | 3 | 4 | 5 | 6 | 7 |
|---------|---|---|---|---|---|---|---|---|
| **0** | 1 | 1 | 0 | 1 | 0 | 1 | 0 | 1 |
| **1** | 0 | 1 | 0 | 1 | 0 | 1 | 0 | 1 |
| **2** | 1 | 1 | 1 | 1 | 1 | 0 | 1 | 0 |
| **3** | 0 | 0 | 0 | 1 | 0 | 1 | 0 | 1 |
| **4** | 1 | 1 | 0 | 1 | 1 | 1 | 0 | 1 |
| **5** | 0 | 0 | 0 | 0 | 0 | 1 | 0 | 1 |
| **6** | 1 | 1 | 0 | 1 | 1 | 1 | 1 | 1 |
| **7** | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 1 |

## 附录 B: 术语表

| 术语 | 全称 | 含义 |
|------|------|------|
| TMU | Tile Management Unit | Tile 管理单元 |
| TileReg | Tile Register File | Tile 寄存器文件（片上 SRAM 缓冲区） |
| Ring | Ring Interconnect | 环形互联网络 |
| CS | Circuit Station | 环上的站点 |
| CW | Clockwise | 顺时针方向 |
| CC | Counter-Clockwise | 逆时针方向 |
| SPB | Send/Post Buffer | 发送缓冲区（请求上 Ring） |
| MGB | Merge Buffer | 合并缓冲区（响应下 Ring） |
| Flit | Flow control unit | 流控单元（Ring 上传输的最小数据单位） |
| Pipe | Pipeline SRAM | TileReg 的一个分区（128KB） |
| BCC | Block Control Core | 块控制核 |
| TMA | Tile Memory Access | Tile 存储访问单元 |
| RR | Round-Robin | 轮询仲裁 |