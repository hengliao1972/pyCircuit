from __future__ import annotations

from pycircuit import Circuit


def build(m: Circuit) -> None:
    dom = m.domain("sys")

    in_valid = m.input("in_valid", width=1)
    in_data = m.input("in_data", width=8)
    out0_ready = m.input("out0_ready", width=1)
    out1_ready = m.input("out1_ready", width=1)

    val0 = m.out("val0", domain=dom, width=1, init=0)
    val1 = m.out("val1", domain=dom, width=1, init=0)
    val2 = m.out("val2", domain=dom, width=1, init=0)
    val3 = m.out("val3", domain=dom, width=1, init=0)
    data0 = m.out("data0", domain=dom, width=8, init=0)
    data1 = m.out("data1", domain=dom, width=8, init=0)
    data2 = m.out("data2", domain=dom, width=8, init=0)
    data3 = m.out("data3", domain=dom, width=8, init=0)

    out0_valid = val0.out()
    out0_data = data0.out()
    out1_valid = val1.out()
    out1_data = data1.out()

    pop0 = out0_valid & out0_ready
    pop1 = out1_valid & out1_ready & pop0
    in_ready = (~val3.out()) | pop0
    push = in_valid & in_ready

    s0_v0 = val0.out()
    s0_v1 = val1.out()
    s0_v2 = val2.out()
    s0_v3 = val3.out()
    s0_d0 = data0.out()
    s0_d1 = data1.out()
    s0_d2 = data2.out()
    s0_d3 = data3.out()

    s1_v0 = s0_v1
    s1_v1 = s0_v2
    s1_v2 = s0_v3
    s1_v3 = m.const(0, width=1)
    s1_d0 = s0_d1
    s1_d1 = s0_d2
    s1_d2 = s0_d3
    s1_d3 = s0_d3

    a1_v0 = pop0.select(s1_v0, s0_v0)
    a1_v1 = pop0.select(s1_v1, s0_v1)
    a1_v2 = pop0.select(s1_v2, s0_v2)
    a1_v3 = pop0.select(s1_v3, s0_v3)
    a1_d0 = pop0.select(s1_d0, s0_d0)
    a1_d1 = pop0.select(s1_d1, s0_d1)
    a1_d2 = pop0.select(s1_d2, s0_d2)
    a1_d3 = pop0.select(s1_d3, s0_d3)

    s2_v0 = a1_v1
    s2_v1 = a1_v2
    s2_v2 = a1_v3
    s2_v3 = m.const(0, width=1)
    s2_d0 = a1_d1
    s2_d1 = a1_d2
    s2_d2 = a1_d3
    s2_d3 = a1_d3

    a2_v0 = pop1.select(s2_v0, a1_v0)
    a2_v1 = pop1.select(s2_v1, a1_v1)
    a2_v2 = pop1.select(s2_v2, a1_v2)
    a2_v3 = pop1.select(s2_v3, a1_v3)
    a2_d0 = pop1.select(s2_d0, a1_d0)
    a2_d1 = pop1.select(s2_d1, a1_d1)
    a2_d2 = pop1.select(s2_d2, a1_d2)
    a2_d3 = pop1.select(s2_d3, a1_d3)

    en0 = push & ~a2_v0
    en1 = push & a2_v0 & ~a2_v1
    en2 = push & a2_v0 & a2_v1 & ~a2_v2
    en3 = push & a2_v0 & a2_v1 & a2_v2 & ~a2_v3

    val0.set(a2_v0 | en0)
    val1.set(a2_v1 | en1)
    val2.set(a2_v2 | en2)
    val3.set(a2_v3 | en3)
    data0.set(en0.select(in_data, a2_d0))
    data1.set(en1.select(in_data, a2_d1))
    data2.set(en2.select(in_data, a2_d2))
    data3.set(en3.select(in_data, a2_d3))

    m.output("in_ready", in_ready)
    m.output("out0_valid", out0_valid)
    m.output("out0_data", out0_data)
    m.output("out1_valid", out1_valid)
    m.output("out1_data", out1_data)


build.__pycircuit_name__ = "issue_queue_2picker"
