module attributes {pyc.top = @Top, pyc.frontend.contract = "pycircuit"} {
  func.func @A(
      %forward : i1 {pyc.clock_domain = "core", test.custom_arg = "A-forward"},
      %feedback : i1 {pyc.clock_domain = "feedback", test.custom_arg = "A-feedback"})
      -> (i1 {test.custom_result = "A-out"}, i1 {test.custom_result = "A-unused"})
      attributes {arg_names = ["forward", "feedback"], result_names = ["out", "unused"], pyc.kind = "module", pyc.inline = "false", pyc.params = "{}", pyc.base = "A", pyc.struct.metrics = "{\"ast_node_count\":0,\"collection_count\":0,\"collection_instance_count\":0,\"estimated_inline_cost\":0,\"hardware_call_count\":0,\"instance_count\":0,\"loop_count\":0,\"module_call_count\":0,\"module_family_collection_count\":0,\"repeat_pressure\":0,\"repeated_body_clusters\":[],\"source_loc\":0,\"state_alloc_count\":0,\"state_call_count\":0}", pyc.struct.collections = "[]", pyc.value_params = [], pyc.value_param_types = []} {
    %zero = arith.constant 0 : i1
    return %forward, %zero : i1, i1
  }

  func.func @B(
      %forward : i1 {pyc.clock_domain = "core", test.custom_arg = "B-forward"},
      %feedback : i1 {pyc.clock_domain = "feedback", test.custom_arg = "B-feedback"})
      -> (i1 {test.custom_result = "B-out"}, i1 {test.custom_result = "B-unused"})
      attributes {arg_names = ["forward", "feedback"], result_names = ["out", "unused"], pyc.kind = "module", pyc.inline = "false", pyc.params = "{}", pyc.base = "B", pyc.struct.metrics = "{\"ast_node_count\":0,\"collection_count\":0,\"collection_instance_count\":0,\"estimated_inline_cost\":0,\"hardware_call_count\":0,\"instance_count\":0,\"loop_count\":0,\"module_call_count\":0,\"module_family_collection_count\":0,\"repeat_pressure\":0,\"repeated_body_clusters\":[],\"source_loc\":0,\"state_alloc_count\":0,\"state_call_count\":0}", pyc.struct.collections = "[]", pyc.value_params = [], pyc.value_param_types = []} {
    %zero = arith.constant 0 : i1
    return %forward, %zero : i1, i1
  }

  func.func @Top() -> (i1, i1) attributes {arg_names = [], result_names = ["a_out", "b_out"], pyc.kind = "module", pyc.inline = "false", pyc.params = "{}", pyc.base = "Top", pyc.struct.metrics = "{\"ast_node_count\":0,\"collection_count\":0,\"collection_instance_count\":0,\"estimated_inline_cost\":0,\"hardware_call_count\":0,\"instance_count\":2,\"loop_count\":0,\"module_call_count\":2,\"module_family_collection_count\":0,\"repeat_pressure\":0,\"repeated_body_clusters\":[],\"source_loc\":0,\"state_alloc_count\":0,\"state_call_count\":0}", pyc.struct.collections = "[]", pyc.value_params = [], pyc.value_param_types = []} {
    %zero = arith.constant 0 : i1
    %a_feedback = pyc.wire {pyc.name = "a_feedback"} : i1
    %b_feedback = pyc.wire {pyc.name = "b_feedback"} : i1
    %a0, %a1 = pyc.instance %zero, %b_feedback
        {callee = @A, name = "a"} : (i1, i1) -> (i1, i1)
    %b0, %b1 = pyc.instance %zero, %a_feedback
        {callee = @B, name = "b"} : (i1, i1) -> (i1, i1)
    pyc.assign %a_feedback, %a0 : i1
    pyc.assign %b_feedback, %b0 : i1
    return %a0, %b0 : i1, i1
  }
}
