module attributes {pyc.frontend.contract = "pycircuit", pyc.top = @vec_tap} {
  func.func @vec_tap(%clk: !pyc.clock, %rst: !pyc.reset,
                     %in: vector<4xi8>, %init: vector<4xi8>) -> (vector<4xi8>, vector<4xi8>)
      attributes {arg_names = ["clk", "rst", "in", "init"], pyc.base = "vec_tap", pyc.inline = "false", pyc.kind = "module", pyc.params = "{}", pyc.struct.collections = "[]", pyc.struct.metrics = "{\"ast_node_count\":0,\"collection_count\":0,\"collection_instance_count\":0,\"estimated_inline_cost\":0,\"hardware_call_count\":0,\"instance_count\":0,\"loop_count\":0,\"module_call_count\":0,\"module_family_collection_count\":0,\"repeat_pressure\":0,\"repeated_body_clusters\":[],\"source_loc\":0,\"state_alloc_count\":0,\"state_call_count\":0}", pyc.value_param_types = [], pyc.value_params = [], result_names = ["q", "t1"]} {
    %en = pyc.constant 1 : i1
    %q = pyc.delay_line %clk, %rst, %en, %in, %init {depth = 2 : i64} : vector<4xi8>
    %t1 = pyc.delay_tap %q {depth = 2 : i64} : vector<4xi8>
    func.return %q, %t1 : vector<4xi8>, vector<4xi8>
  }
}
