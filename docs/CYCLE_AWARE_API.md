# PyCircuit Cycle-Aware API Reference

**Version: 2.0**

---

## Overview

The cycle-aware system is a new programming paradigm for PyCircuit that tracks signal timing cycles automatically. Key features include:

- **Cycle-aware Signals**: Each signal carries its cycle information
- **Automatic Cycle Balancing**: Automatic DFF insertion when combining signals of different cycles
- **Domain-based Cycle Management**: `next()`, `prev()`, `push()`, `pop()` methods for cycle control
- **JIT Compilation**: Python source code compiles to MLIR hardware description
- **Unified signal model**: create signals via `domain.signal(...)` and drive them via `.set(...)`

## Installation

```python
from pycircuit import (
    CycleAwareCircuit,
    CycleAwareDomain,
    CycleAwareSignal,
    compile_cycle_aware,
    mux,
)
```

---

## Core Components

### CycleAwareCircuit

The main circuit builder class that manages clock domains and signal generation.

```python
m = CycleAwareCircuit("my_circuit")
```

**Methods:**

| Method | Description |
|--------|-------------|
| `create_domain(name)` | Create a new clock domain |
| `get_default_domain()` | Get the default clock domain |
| `ca_const(value, width, domain=None)` | Create a constant signal (legacy helper; prefer `domain.const(...)`) |
| `output(name, signal)` | Register an output signal |
| `emit_mlir()` | Generate MLIR representation |

### CycleAwareDomain

Manages clock cycle state for a specific clock domain.

```python
domain = m.create_domain("clk")
```

**Methods:**

| Method | Description |
|--------|-------------|
| `input(name, width)` | Create an input port signal |
| `signal(name, width, reset=None)` | Create a wire/flop in the current cycle (`reset!=None` → flop, `reset=None` → wire) |
| `const(value, width)` | Create a constant in the current cycle |
| `next()` | Advance current cycle by 1 |
| `prev()` | Decrease current cycle by 1 |
| `push()` | Save current cycle to stack |
| `pop()` | Restore cycle from stack |
| `cycle(signal, reset_value, name)` | Insert DFF register (legacy helper; prefer `signal(..., reset=...)`) |

Notes:
- `create_signal(...)` / `create_const(...)` exist for backward compatibility.

### CycleAwareSignal

Wrapper that carries cycle information along with the underlying MLIR signal.

**Attributes:**

| Attribute | Description |
|-----------|-------------|
| `sig` | Underlying MLIR Signal |
| `cycle` | Current cycle number |
| `domain` | Associated CycleAwareDomain |
| `name` | Signal name for debugging |
| `signed` | Whether signal is signed |

**Operator Overloading:**

All standard Python operators are overloaded with automatic cycle balancing:

```python
# Arithmetic
result = a + b  # Addition
result = a - b  # Subtraction
result = a * b  # Multiplication

# Bitwise
result = a & b   # AND
result = a | b   # OR
result = a ^ b   # XOR
result = ~a      # NOT
result = a << n  # Left shift
result = a >> n  # Right shift

# Comparison
result = a.eq(b)  # Equal
result = a.lt(b)  # Less than
result = a.gt(b)  # Greater than
result = a.le(b)  # Less or equal
result = a.ge(b)  # Greater or equal
```

**Signal Methods:**

| Method | Description |
|--------|-------------|
| `select(true_val, false_val)` | Conditional selection (mux) |
| `trunc(width)` | Truncate to width bits |
| `zext(width)` | Zero extend to width bits |
| `sext(width)` | Sign extend to width bits |
| `slice(high, low)` | Extract bit slice |
| `named(name)` | Add debug name |
| `as_signed()` | Mark as signed |
| `as_unsigned()` | Mark as unsigned |

---

## Automatic Cycle Balancing

When combining signals with different cycles, the system automatically inserts DFF chains to align timing.

### Rule

```
output_cycle = max(input_cycles)
earlier_signals → automatically delayed via DFF insertion
```

### Example

```python
def design(m: CycleAwareCircuit, domain: CycleAwareDomain):
    # Cycle 0: Input
    data_in = domain.create_signal("data_in", width=8)
    
    # Save reference at Cycle 0
    data_at_cycle0 = data_in
    
    domain.next()  # -> Cycle 1
    stage1 = domain.cycle(data_in, reset_value=0, name="stage1")
    
    domain.next()  # -> Cycle 2
    stage2 = domain.cycle(stage1, reset_value=0, name="stage2")
    
    # data_at_cycle0 is at Cycle 0, stage2 is at Cycle 2
    # System automatically inserts 2-level DFF chain for data_at_cycle0
    combined = data_at_cycle0 + stage2  # Output at Cycle 2
    
    m.output("result", combined.sig)
```

Generated MLIR shows automatic DFF insertion:

```mlir
%data_delayed1 = pyc.reg %clk, %rst, %en, %data_at_cycle0, %reset_val : i8
%data_delayed2 = pyc.reg %clk, %rst, %en, %data_delayed1, %reset_val : i8
%result = pyc.add %data_delayed2, %stage2 : i8
```

---

## Cycle Management

### next() / prev()

Advance or decrease the current cycle counter.

```python
# Cycle 0
a = domain.create_signal("a", width=8)

domain.next()  # -> Cycle 1
b = domain.cycle(a, name="b")

domain.next()  # -> Cycle 2
c = domain.cycle(b, name="c")

domain.prev()  # -> Cycle 1
# Can add more signals at Cycle 1
d = (a + 1)  # Also at Cycle 1 (with auto balancing)
```

### push() / pop()

Save and restore cycle state for nested function calls.

```python
def helper_function(domain: CycleAwareDomain, data):
    domain.push()  # Save caller's cycle
    
    # Internal cycle management
    domain.next()
    result = domain.cycle(data, name="helper_reg")
    domain.next()
    final = result + 1
    
    domain.pop()  # Restore caller's cycle
    return final

def main_design(m: CycleAwareCircuit, domain: CycleAwareDomain):
    data = domain.create_signal("data", width=8)
    
    # Call helper - its internal next() doesn't affect our cycle
    result = helper_function(domain, data)
    
    # Still at our original cycle
    domain.next()  # Our own cycle advancement
```

### cycle()

Insert a DFF register (single-cycle delay).

```python
# Basic register
reg = domain.cycle(data, name="data_reg")

# Register with reset value
counter_reg = domain.cycle(counter_next, reset_value=0, name="counter")
```

---

## JIT Compilation

### compile_cycle_aware()

Compile a Python function to a CycleAwareCircuit.

```python
def my_design(m: CycleAwareCircuit, domain: CycleAwareDomain, width: int = 8):
    # Design logic
    data = domain.create_signal("data", width=width)
    processed = data + 1
    domain.next()
    output = domain.cycle(processed, name="output")
    m.output("out", output.sig)

# Compile
circuit = compile_cycle_aware(my_design, name="my_circuit", width=16)

# Generate MLIR
mlir_code = circuit.emit_mlir()
```

### Parameters

| Parameter | Description |
|-----------|-------------|
| `fn` | Python function to compile |
| `name` | Circuit name (optional) |
| `domain_name` | Default clock domain name (default: "clk") |
| `**params` | Additional parameters passed to function |

### Return Statement

The JIT compiler handles return statements by registering outputs:

```python
def design(m: CycleAwareCircuit, domain: CycleAwareDomain):
    data = domain.create_signal("data", width=8)
    result = data + 1
    return result  # Automatically becomes output "result"
```

---

## Global Functions

### mux()

Conditional selection with automatic cycle balancing.

```python
result = mux(condition, true_value, false_value)
```

**Parameters:**

- `condition`: CycleAwareSignal (1-bit) for selection
- `true_value`: Value when condition is true (CycleAwareSignal or int)
- `false_value`: Value when condition is false (CycleAwareSignal or int)

**Example:**

```python
enable = domain.create_signal("enable", width=1)
data = domain.create_signal("data", width=8)
result = mux(enable, data + 1, data)  # Increment when enabled
```

---

## Complete Example

```python
# -*- coding: utf-8 -*-
"""Counter with enable - cycle-aware implementation."""

from pycircuit import (
    CycleAwareCircuit,
    CycleAwareDomain,
    compile_cycle_aware,
    mux,
)


def counter_with_enable(
    m: CycleAwareCircuit,
    domain: CycleAwareDomain,
    width: int = 8,
):
    """8-bit counter with enable control."""
    
    # Cycle 0: Inputs
    enable = domain.create_signal("enable", width=1)
    
    # Counter initial value
    count = domain.create_const(0, width=width, name="count_init")
    
    # Combinational logic
    count_next = count + 1
    count_with_enable = mux(enable, count_next, count)
    
    # Cycle 1: Register
    domain.next()
    count_reg = domain.cycle(count_with_enable, reset_value=0, name="count")
    
    # Output
    m.output("count", count_reg.sig)


if __name__ == "__main__":
    circuit = compile_cycle_aware(counter_with_enable, name="counter", width=8)
    print(circuit.emit_mlir())
```

---

## Migration from Legacy API

| Legacy API | Cycle-Aware API |
|------------|-----------------|
| `Circuit` | `CycleAwareCircuit` |
| `ClockDomain` | `CycleAwareDomain` |
| `Wire` / `Reg` | `CycleAwareSignal` |
| `compile()` | `compile_cycle_aware()` |
| Manual DFF insertion | Automatic via `domain.cycle()` |
| No cycle tracking | Full cycle tracking |

---

## Best Practices

1. **Use descriptive names**: The `named()` method helps with debugging
   ```python
   result = (a + b).named("sum_ab")
   ```

2. **Mark cycle boundaries clearly**: Use comments to document pipeline stages
   ```python
   # === Stage 1: Fetch ===
   domain.next()
   ```

3. **Use push/pop for helper functions**: Avoid cycle state leakage
   ```python
   def helper(domain, data):
       domain.push()
       # ... logic ...
       domain.pop()
       return result
   ```

4. **Let automatic balancing work**: Trust the system to insert DFFs when needed

---

**Copyright (C) 2024-2026 PyCircuit Contributors**
