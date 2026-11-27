// comb A yields %r1 from %a alone and %r2 from the wire that comb B drives from
// %r1.  The value graph is acyclic, but at op granularity A and B need each
// other, so the emitter falls back to block order and must iterate.
module attributes {pyc.top = @FalseCycle, pyc.frontend.contract = "pycircuit"} {
  func.func @FalseCycle(%clk : !pyc.clock, %rst : !pyc.reset, %a : i1) -> i1
      attributes {arg_names = ["clk", "rst", "a"], result_names = ["o"], pyc.kind = "module", pyc.inline = "false", pyc.params = "{}", pyc.base = "FalseCycle", pyc.struct.metrics = "{\"ast_node_count\":0,\"collection_count\":0,\"collection_instance_count\":0,\"estimated_inline_cost\":0,\"hardware_call_count\":0,\"instance_count\":0,\"loop_count\":0,\"module_call_count\":0,\"module_family_collection_count\":0,\"repeat_pressure\":0,\"repeated_body_clusters\":[],\"source_loc\":0,\"state_alloc_count\":0,\"state_call_count\":0}", pyc.struct.collections = "[]", pyc.value_params = [], pyc.value_param_types = []} {
    %w = pyc.wire : i1
    %r1, %r2 = pyc.comb(%a, %w) : (i1, i1) -> (i1, i1) {
    ^bb0(%a_arg : i1, %w_arg : i1):
      %x = pyc.not %a_arg : i1
      %y = pyc.and %w_arg, %a_arg : i1, i1 -> i1
      pyc.yield %x, %y : i1, i1
    }
    %s = pyc.comb(%r1) : (i1) -> i1 {
    ^bb0(%p : i1):
      %z = pyc.not %p : i1
      pyc.yield %z : i1
    }
    pyc.assign %w, %s : i1
    return %r2 : i1
  }
}
