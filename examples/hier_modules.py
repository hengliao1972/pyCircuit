from __future__ import annotations

from pycircuit import Circuit, module


@module(name="Core")
def core(m: Circuit, *, WIDTH: int = 8) -> None:
    clk = m.clock("clk")
    rst = m.reset("rst")

    in_valid = m.input("in_valid", width=1)
    in_data = m.input("in_data", width=WIDTH)
    out_ready = m.input("out_ready", width=1)

    fire = in_valid & out_ready
    data_next = in_data + m.const(1, width=WIDTH)

    data_q = m.reg_wire(clk, rst, fire, data_next, 0).out()
    valid_q = m.reg_wire(clk, rst, m.const(1, width=1), fire, 0).out()

    m.output("out_valid", valid_q)
    m.output("out_data", data_q)


def build(m: Circuit, *, N: int = 4, WIDTH: int = 8) -> None:
    """Hierarchy demo: instantiate N copies of a parametric Core module."""

    clk = m.clock("clk")
    rst = m.reset("rst")

    in_valid = m.input("in_valid", width=1)
    in_data = m.input("in_data", width=WIDTH)
    out_ready = m.input("out_ready", width=1)

    out_valid = m.const(0, width=1)
    out_data = m.const(0, width=WIDTH)

    for i in range(N):
        c = m.instance(
            core,
            name=f"core{i}",
            params={"WIDTH": WIDTH},
            clk=clk,
            rst=rst,
            in_valid=in_valid,
            in_data=in_data,
            out_ready=out_ready,
        )
        out_valid = out_valid | c["out_valid"]
        out_data = out_data ^ c["out_data"]

    m.output("out_valid", out_valid)
    m.output("out_data", out_data)
