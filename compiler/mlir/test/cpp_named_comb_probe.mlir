module attributes {pyc.top = @named_comb_probe, pyc.frontend.contract = "pycircuit"} {
  func.func @named_comb_probe(%in: i8) -> i8 attributes {
      arg_names = ["in"],
      result_names = ["out"],
      pyc.kind = "module",
      pyc.inline = "false",
      pyc.params = "{}",
      pyc.base = "named_comb_probe",
      pyc.struct.metrics = "{\"ast_node_count\":0,\"collection_count\":0,\"collection_instance_count\":0,\"estimated_inline_cost\":0,\"hardware_call_count\":0,\"instance_count\":0,\"loop_count\":0,\"module_call_count\":0,\"module_family_collection_count\":0,\"repeat_pressure\":0,\"repeated_body_clusters\":[],\"source_loc\":0,\"state_alloc_count\":0,\"state_call_count\":0}",
      pyc.struct.collections = "[]",
      pyc.value_params = [],
      pyc.value_param_types = []
    } {
    %result = "pyc.comb"(%in) ({
    ^bb0(%arg: i8):
      %named = "pyc.alias"(%arg) {pyc.name = "debug_named"} : (i8) -> i8
      %sum = "pyc.add"(%named, %arg) : (i8, i8) -> i8
      "pyc.yield"(%sum) : (i8) -> ()
    }) : (i8) -> i8
    return %result : i8
  }
}
