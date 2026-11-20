// A fused multi-result comb may carry a reg D wire for one result while
// producing that wire's driver from the reg Q on another result.  The reg is a
// same-tick cut, and result-specific dependencies must not manufacture a loop.
module attributes {pyc.top = @RegFeedback, pyc.frontend.contract = "pycircuit"} {
  func.func @RegFeedback(%clk : !pyc.clock, %rst : !pyc.reset) -> i1
      attributes {arg_names = ["clk", "rst"], result_names = ["held"], pyc.kind = "module", pyc.inline = "false", pyc.params = "{}", pyc.base = "RegFeedback", pyc.struct.metrics = "{\"ast_node_count\":0,\"collection_count\":0,\"collection_instance_count\":0,\"estimated_inline_cost\":0,\"hardware_call_count\":0,\"instance_count\":0,\"loop_count\":0,\"module_call_count\":0,\"module_family_collection_count\":0,\"repeat_pressure\":0,\"repeated_body_clusters\":[],\"source_loc\":0,\"state_alloc_count\":2,\"state_call_count\":2}", pyc.struct.collections = "[]", pyc.value_params = [], pyc.value_param_types = []} {
    %next = pyc.wire {pyc.name = "held__next"} : i1
    %enable = pyc.constant 1 : i1
    %init = pyc.constant 0 : i1
    %q = pyc.reg %clk, %rst, %enable, %next, %init : i1
    %held = pyc.alias %q {pyc.name = "held"} : i1
    %driver, %other_next = pyc.comb(%held, %next) : (i1, i1) -> (i1, i1) {
    ^bb0(%q_arg : i1, %d_arg : i1):
      %other = pyc.not %d_arg : i1
      pyc.yield %q_arg, %other : i1, i1
    }
    %other_q = pyc.reg %clk, %rst, %enable, %other_next, %init : i1
    pyc.assign %next, %driver : i1
    return %held : i1
  }
}
