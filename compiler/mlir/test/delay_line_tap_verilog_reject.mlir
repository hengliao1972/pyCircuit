module attributes {pyc.frontend.contract = "pycircuit", pyc.top = @delay_tap} {
  func.func @delay_tap(%clk: !pyc.clock, %rst: !pyc.reset,
                     %in: i32, %init: i32) -> (i32, i32)
      attributes {arg_names = ["clk", "rst", "in", "init"], pyc.base = "delay_tap", pyc.inline = "false", pyc.kind = "module", pyc.params = "{}", pyc.struct.collections = "[]", pyc.struct.metrics = "{\"ast_node_count\":0,\"collection_count\":0,\"collection_instance_count\":0,\"estimated_inline_cost\":0,\"hardware_call_count\":0,\"instance_count\":0,\"loop_count\":0,\"module_call_count\":0,\"module_family_collection_count\":0,\"repeat_pressure\":0,\"repeated_body_clusters\":[],\"source_loc\":0,\"state_alloc_count\":0,\"state_call_count\":0}", pyc.value_param_types = [], pyc.value_params = [], result_names = ["q", "t1"]} {
    %en = pyc.constant 1 : i1
    %q = pyc.delay_line %clk, %rst, %en, %in, %init {depth = 2 : i64} : i32
    %t1 = pyc.delay_tap %q {depth = 2 : i64} : i32
    func.return %q, %t1 : i32, i32
  }
}
