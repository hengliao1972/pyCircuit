module attributes {pyc.top = @bad, pyc.frontend.contract = "pycircuit"} {
  func.func @bad(%arg0: i1, %arg1: i1) -> i1 attributes {
      arg_names = ["a", "b"], result_names = ["out"], pyc.kind = "module",
      pyc.inline = "false", pyc.params = "{}", pyc.base = "bad",
      pyc.struct.metrics = "{\"ast_node_count\":0,\"collection_count\":0,\"collection_instance_count\":0,\"estimated_inline_cost\":0,\"hardware_call_count\":0,\"instance_count\":0,\"loop_count\":0,\"module_call_count\":0,\"module_family_collection_count\":0,\"repeat_pressure\":0,\"repeated_body_clusters\":[],\"source_loc\":0,\"state_alloc_count\":0,\"state_call_count\":0}",
      pyc.struct.collections = "[]", pyc.value_params = [],
      pyc.value_param_types = []} {
    %0 = arith.addi %arg0, %arg1 : i1
    return %0 : i1
  }
}
