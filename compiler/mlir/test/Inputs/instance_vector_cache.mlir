module attributes {pyc.frontend.contract = "pycircuit", pyc.top = @top} {
  func.func @child(%in: i3072) -> i3072 attributes {
      arg_names = ["in"], result_names = ["out"], pyc.kind = "module",
      pyc.inline = "false", pyc.params = "{}", pyc.base = "child",
      pyc.struct.metrics = "{\"ast_node_count\":0,\"collection_count\":0,\"collection_instance_count\":0,\"estimated_inline_cost\":0,\"hardware_call_count\":0,\"instance_count\":0,\"loop_count\":0,\"module_call_count\":0,\"module_family_collection_count\":0,\"repeat_pressure\":0,\"repeated_body_clusters\":[],\"source_loc\":0,\"state_alloc_count\":0,\"state_call_count\":0}",
      pyc.struct.collections = "[]", pyc.value_params = [],
      pyc.value_param_types = []} {
    return %in : i3072
  }

  func.func @top(%in: i3072) -> i3072 attributes {
      arg_names = ["in"], result_names = ["out"], pyc.kind = "module",
      pyc.inline = "false", pyc.params = "{}", pyc.base = "top",
      pyc.struct.metrics = "{\"ast_node_count\":0,\"collection_count\":0,\"collection_instance_count\":0,\"estimated_inline_cost\":0,\"hardware_call_count\":0,\"instance_count\":1,\"loop_count\":0,\"module_call_count\":1,\"module_family_collection_count\":0,\"repeat_pressure\":0,\"repeated_body_clusters\":[],\"source_loc\":0,\"state_alloc_count\":0,\"state_call_count\":0}",
      pyc.struct.collections = "[]", pyc.value_params = [],
      pyc.value_param_types = []} {
    %0 = pyc.instance %in {callee = @child, name = "u_child"} :
        (i3072) -> i3072
    return %0 : i3072
  }
}