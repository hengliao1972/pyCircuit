# Vec（`Wire[Vector[T]]`）算子参考

本文描述 pyCircuit 当前可用的向量 API。用户侧的“Vec”是
`Wire[Vector[T]]`，其中 `T` 通常为 `Bits(width, signed)`；并不存在需要
直接实例化的独立 `Vec` 类。

除非特别说明，逐元素运算支持一维和多维 Vector，结果形状与输入 Vector
一致；归约支持 rank 1 和 rank 2。

## 算子一览

| 类别 | API / 运算符 | 输出 | 备注 |
| --- | --- | --- | --- |
| 创建 | `m.input(..., shape=[...])` | `Wire[Vector[Bits]]` | `shape` 只能是正整数 `list[int]`；`[]` 为标量 |
| 创建 | `m.vec([x0, x1, ...])` | 最外层新增一维 Vector |  |
| 访问 | `x[i]` | 去掉最外层维度 |  |
| 访问 | `len(x)`、`for lane in x` | 最外层长度、各 lane |  |
| 广播 | `x.broadcast(size=n, dim=d)` | 插入长度为 `n` 的维度 | |
| 拼接 | `m.cat(a, b, ...)` | packed `Wire[Bits]` |  |
| 算术 | `+`、`-`、`*` | 逐元素 Vector |  |
| 除法 | `//`、`%` | 逐元素 Vector | |
| 位运算 | `&`、`\|`、`^`、`~` | 逐元素 Vector |  |
| 比较 | `==`、`!=`、`<`、`>`、`<=`、`>=` | 叶宽为 1 的 Vector | |
| 显式比较 | `.ult()`、`.ugt()`、`.ule()`、`.uge()`、`.slt()` | 叶宽为 1 的 Vector |  |
| 条件选择 | `a if cond else b`、`mux(cond, a, b)` | 逐元素或广播后的 Vector | 
| 优先级选择 | `m.priority_mux(sels, vals, ...)` | `vals[i]` 类型 | `sels` 是一维 `Vector<Nxi1>`；最小索引优先 |
| 左移 | `a << n`、`.shl(amount=...)` | 逐元素 Vector | 移位量可为常量或 `Wire` |
| 右移 | `a >> n`、`.lshr()`、`.ashr()` | 逐元素 Vector | 移位量可为常量或 `Wire`；`>>` 按 signed 属性选择右移类型 |
| 位域 | `.slice(lsb=, width=)` | 逐元素 Vector |  |
| 类型转换 | `trunc()`、`zext()`、`sext()` | 形状不变的 Vector | 逐元素截断/零扩展/符号扩展 |
| 归约 | `.reduce_or()`、`.reduce_and()`、`.reduce_sum()`  | 标量或低一维 Vector | `dim=None` 为全维归约；默认 `dim=None`；`mode="chain"/"tree"` 影响组合结构|


## IR 表示：前端展开为标量 lane

Vector 只存在于前端/API 层。发射 `.pyc` 时，前端把每个逐 lane 运算写成
一条标量 op，模块端口合并为单根 packed `i{leaf*lanes}` 总线（lane 0 在
LSB，入口 `pyc.extract`、出口 `pyc.concat`）。dialect 中没有 `vector<>`
类型，也没有 `pyc.v_*` op。

| 前端 API | 发射后的 IR |
| --- | --- |
| `m.vec([x0, x1, ...])` | 直接复用各 lane 值；端口边界 `pyc.concat` 打包 |
| 标量广播 | 各 lane 复用同一标量引用 |
| `x[i]`、`for lane in x` | `pyc.extract`（或 lane 别名） |
| `x.broadcast(size=n, dim=d)` | lane 引用重排 + `pyc.concat` |
| `x.reduce_or(dim=..., mode=...)` | 逐 lane `pyc.or`（chain / tree 拓扑） |
| `x.reduce_and(dim=..., mode=...)` | 逐 lane `pyc.and` |
| `x.reduce_sum(dim=..., mode=...)` | 逐 lane `pyc.add`；保持叶宽，溢出回绕 |
| `m.priority_mux(sels, vals, ...)` | 逐 lane `pyc.mux` + `pyc.or` |


## 创建与访问

### `Circuit.input`

```python
a = m.input("a", width=8, shape=[4])       # 端口 i32（packed），body 逐 lane i8
b = m.input("b", width=1, shape=[2, 3])    # 端口 i6（packed），body 逐 lane i1
```

- `shape` 必须是 `list[int]`，每一维必须大于 0。
- `shape=[]`（默认值）创建标量 `Wire[Bits]`。
- `shape=[d0, d1, ...]` 创建嵌套 Vector；叶元素为 `Bits(width, signed)`。

### `Circuit.vec`

```python
x0 = m.input("x0", width=8)
x1 = m.input("x1", width=8)
x = m.vec([x0, x1])
```

- 仅接受一个 `list[Wire]` 参数，不能为空。
- 所有元素必须属于同一 `Circuit`，且数据类型完全一致。
- 若元素本身是 Vector，会在最外层再增加一个维度。

### 索引、长度与遍历

```python
lane1 = x[1]             # Wire[T]
lanes = [lane for lane in x]
n = len(x)               # 最外层维度长度
```

- Vector 仅支持常量整数索引，范围为 `0 <= index < len(x)`。
- Vector 不支持 Python slice；需要逐个索引。
- `x[i]` 去掉最外层维度：二维 Vector 索引一次后仍是 Vector。
- 标量 Bits 保留位索引与 slice：`bits[0]`、`bits[2:6]`。

### `broadcast`

```python
rows = x.broadcast(size=2, dim=0)
cols = x.broadcast(size=2, dim=1)
```

在位置 `dim` 插入一个长度为 `size` 的维度，并沿该维复制输入：

- 输入形状为 `[a0, ..., an]` 时，输出形状为
  `[a0, ..., a(dim-1), size, a(dim), ..., an]`。
- `size > 0`，且 `0 <= dim <= rank`。
- 只能对 Vector 调用。

### `Circuit.cat`

```python
packed = m.cat(x[0], x[1], x[2])
```

将多个 Wire 连接为一个 packed Bits 值。参数从左到右为高位到低位
（MSB-first）。至少需要一个 Wire。

## 逐元素算术与位运算

下列 Python 运算符都支持：

| 类别 | 运算符 | 说明 |
| --- | --- | --- |
| 算术 | `+`、`-`、`*` | 逐元素加、减、乘 |
| 除法 | `//`、`%` | 整数除法、余数；有符号输入使用 signed 语义，否则使用 unsigned 语义 |
| 位运算 | `&`、`\|`、`^`、`~` | 逐元素与、或、异或、取反 |
| 比较 | `==`、`!=`、`<`、`>`、`<=`、`>=` | 逐元素比较，结果叶宽为 1 |
| 显式比较 | `.ult()`、`.ugt()`、`.ule()`、`.uge()`、`.slt()` | 强制 unsigned 或 signed 比较 |

```python
sum_v = a + b
masked = a & 0x0F
is_less = a < b
signed_less = a.slt(b)
quotient = a // b
remainder = a % b
```

### 操作数规则

- `Vec op Vec`：两个 Vector 的形状和叶元素数据类型必须匹配。
- `Vec op scalar` 与 `scalar op Vec`：标量会广播到 Vector 的形状。
- Vector 不进行隐式叶宽扩展；Vector-Vector 运算要求相同叶类型。
- 标量参与运算时，以 Vector 叶元素宽度构造/广播该标量。
- `/` 不支持；必须使用 `//`。
- 除数为零的硬件行为不应作为设计语义依赖。

## 选择与优先级选择

### 逐元素条件选择

```python
out = a if cond else b
```

`cond` 必须是宽度为 1 的 Wire 或 Vector。Vector 条件时按 lane 逐元素
选择；标量分支会自动广播到 Vector 形状。也可以显式使用：

```python
out = mux(cond, a, b)
```

Vec 条件与 Vec 分支、Vec 条件与标量分支的选择路径均有端到端 C++ 回归
覆盖。

### `Circuit.priority_mux`

```python
selected = m.priority_mux(sels, vals, mode="chain", default=fallback)
```

- `sels`：一维 `Vector<N x i1>`。
- `vals`：最外层长度同为 `N` 的 Vector；`vals[i]` 是候选值。
- `default`：可选，类型/形状必须与 `vals[i]` 相同。
- 优先级从低索引到高索引：多个 selector 同时为 1 时，最小索引获胜。
- `default=None` 时，所有 selector 为 0 的回退值是 `vals` 的最后一个元素。
- `mode` 只能是 `"chain"`（默认）或 `"tree"`；两者逻辑等价，只影响组合结构。

### `CycleAwareSignal.priority_mux`（V5 cycle-aware）

V5 cycle-aware 前端的实例方法 `sels.priority_mux(vals, *, mode, default)` 在内部对齐
`sels` / `vals` / `default` 三者的 cycle 标签后委托给上面的 `Circuit.priority_mux`。
**`vals` 与 `default` 接受任何 cycle-aware 信号族**（`CycleAwareSignal` / `StateSignal` /
`ForwardSignal`）或裸 `Wire`：

```python
sels = cas(domain, m.input("s", width=1, shape=[N]), cycle=0)
vals = domain.signal(width=W, shape=[N], name="vals")   # ForwardSignal, 无需 .as_cas()
out  = sels.priority_mux(vals, default=zero)             # 直接传 ForwardSignal
```

调用方**无需**手写 `vals.as_cas()` 把 `ForwardSignal` 解包成 `CycleAwareSignal`——实例方法
内部通过 `CycleAwareSignal.as_cas` 统一 unwrap。非 cycle-aware 的标量（`Reg` / `int` /
`LiteralValue`）仍按 `docs/v5_drop_to_cas_or_none_plan.md` 的方向抛 `TypeError`。跨域
（unwrap 后 `domain is not self.domain`）抛 `ValueError`，与既有 CAS 路径一致。

## 移位、位域与类型转换

### 移位

```python
a << 2                    # 常量左移
a << shift                # 动态左移，shift 为 Wire
a >> shift                # 动态右移；由 a 的 signed 属性选择逻辑/算术右移
a.shl(amount=shift)       # 左移，amount 可为 int 或 Wire
a.lshr(amount=shift)      # 逻辑右移，零填充
a.ashr(amount=shift)      # 算术右移，符号填充
```

- `<<` 和 `>>` 接受 Python `int` 常量或动态 `Wire` 移位量。
- `.shl()`、`.lshr()`、`.ashr()` 同时支持常量和动态 `Wire` 移位量。
- `.lshr()` / `.ashr()` 的常量移位量不得为负。

### 位域

```python
field = a.slice(lsb=2, width=4)
field2 = a[2:6]
```

位域操作逐元素应用于 Vector。Vector 也支持 `Wire[i]` 的 lane 索引；两者
根据 Wire 是否为 Vector 自动区分。

### 宽度转换

```python
from pycircuit import sext, trunc, zext

narrow = trunc(a, width=4)
unsigned_wide = zext(a, width=16)
signed_wide = sext(a, width=16)
```

- `trunc` 截断到目标宽度。
- `zext` 以零扩展。
- `sext` 以符号扩展。
- 对 Vector 时逐元素转换，形状保持不变。
- 推荐使用函数式 API；JIT API contract 不接受方法式
  `width inference and slicing`。

可用 `signed-intent annotations` 标记后续的除法、比较和右移语义；
它们不改变位宽或比特模式。

## 归约

```python
any_set = a.reduce_or()
all_set = a.reduce_and()
total = a.reduce_sum()

by_dim = matrix.reduce_sum(dim=1)
tree_total = a.reduce_sum(mode="tree")
```

| 方法 | 语义 | 宽度规则 |
| --- | --- | --- |
| `.reduce_or()` | 对所有 lane 按位 OR | 保持叶元素宽度 |
| `.reduce_and()` | 对所有 lane 按位 AND | 保持叶元素宽度 |
| `.reduce_sum()` | 对所有 lane 相加 | 保持叶元素宽度，溢出回绕 |

### `dim`

- `dim=None`：归约所有维度，返回标量 Wire。
- `dim=int`：仅归约指定维，返回少一维的 Vector；一维 Vector 在指定
  `dim=0` 时返回标量。
- `reduce_or()`、`reduce_and()`、`reduce_sum()` 默认都是 `dim=None`，
  即完整归约。
- `dim` 必须在 `[0, rank)` 范围内。

### `mode`

三个归约操作均支持：

- `"chain"`：从低索引到高索引的线性归约（默认）。
- `"tree"`：平衡树形归约。

模式在前端展开时决定生成的标量 lane 组合拓扑（chain 为链式、tree 为平衡树）；它影响组合路径结构，不改变功能结果，也不会作为属性写进 IR。

## 验证状态

`tests/vec` 覆盖 Vec-Vec、Vec-标量和标量-Vec 的二元算子方向，以及：

- 算术、位运算、比较、signed/unsigned 除法与余数；
- 常量与动态移位、cast、位域；
- 索引、遍历、broadcast、`cat`、二维逐元素运算；
- `priority_mux` 的优先级、显式/隐式默认值；
- 一维/二维以及全维/指定维归约；
- C++ 模拟与 Verilog/Yosys smoke test。

