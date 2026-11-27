// A same-tick wire feedback loop remains illegal.
module attributes {pyc.top = @TrueCombCycle, pyc.frontend.contract = "pycircuit"} {
  func.func @TrueCombCycle() -> i1
      attributes {arg_names = [], result_names = ["result"], pyc.kind = "module", pyc.inline = "false", pyc.params = "{}", pyc.base = "TrueCombCycle", pyc.struct.metrics = "{\"ast_node_count\":0,\"collection_count\":0,\"collection_instance_count\":0,\"estimated_inline_cost\":0,\"hardware_call_count\":0,\"instance_count\":0,\"loop_count\":0,\"module_call_count\":0,\"module_family_collection_count\":0,\"repeat_pressure\":0,\"repeated_body_clusters\":[],\"source_loc\":0,\"state_alloc_count\":0,\"state_call_count\":0}", pyc.struct.collections = "[]", pyc.value_params = [], pyc.value_param_types = []} {
    %wire = pyc.wire {pyc.name = "true_cycle"} : i1
    %inverted = pyc.not %wire : i1
    pyc.assign %wire, %inverted : i1
    return %wire : i1
  }
}
