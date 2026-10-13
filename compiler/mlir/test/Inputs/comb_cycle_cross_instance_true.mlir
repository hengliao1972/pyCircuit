module attributes {pyc.top = @Top, pyc.frontend.contract = "pycircuit"} {
  func.func @Identity(%input : i1) -> i1
      attributes {arg_names = ["input"], result_names = ["output"], pyc.kind = "module", pyc.inline = "false", pyc.params = "{}", pyc.base = "Identity", pyc.struct.metrics = "{\"ast_node_count\":0,\"collection_count\":0,\"collection_instance_count\":0,\"estimated_inline_cost\":0,\"hardware_call_count\":0,\"instance_count\":0,\"loop_count\":0,\"module_call_count\":0,\"module_family_collection_count\":0,\"repeat_pressure\":0,\"repeated_body_clusters\":[],\"source_loc\":0,\"state_alloc_count\":0,\"state_call_count\":0}", pyc.struct.collections = "[]", pyc.value_params = [], pyc.value_param_types = []} {
    return %input : i1
  }

  func.func @Top() attributes {arg_names = [], result_names = [], pyc.kind = "module", pyc.inline = "false", pyc.params = "{}", pyc.base = "Top", pyc.struct.metrics = "{\"ast_node_count\":0,\"collection_count\":0,\"collection_instance_count\":0,\"estimated_inline_cost\":0,\"hardware_call_count\":0,\"instance_count\":2,\"loop_count\":0,\"module_call_count\":2,\"module_family_collection_count\":0,\"repeat_pressure\":0,\"repeated_body_clusters\":[],\"source_loc\":0,\"state_alloc_count\":0,\"state_call_count\":0}", pyc.struct.collections = "[]", pyc.value_params = [], pyc.value_param_types = []} {
    %a_input = pyc.wire {pyc.name = "a_input"} : i1
    %b_input = pyc.wire {pyc.name = "b_input"} : i1
    %a = pyc.instance %a_input
        {callee = @Identity, name = "a"} : (i1) -> i1
    %b = pyc.instance %b_input
        {callee = @Identity, name = "b"} : (i1) -> i1
    pyc.assign %a_input, %b : i1
    pyc.assign %b_input, %a : i1
    return
  }
}
