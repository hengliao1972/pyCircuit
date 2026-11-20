module attributes {pyc.top = @fork_merge_model,
                   pyc.frontend.contract = "pycircuit"} {
  func.func @fork_merge_model(%clk: !pyc.clock, %rst: !pyc.reset, %a: i8, %b: i8) -> i8
      attributes {arg_names = ["clk", "rst", "a", "b"],
                  result_names = ["out"],
                  pyc.kind = "module", pyc.inline = "false", pyc.params = "{}",
                  pyc.base = "fork_merge_model",
                  pyc.struct.metrics = "{\22ast_node_count\22:0,\22collection_count\22:0,\22collection_instance_count\22:0,\22estimated_inline_cost\22:0,\22hardware_call_count\22:0,\22instance_count\22:0,\22loop_count\22:0,\22module_call_count\22:0,\22module_family_collection_count\22:0,\22repeat_pressure\22:0,\22repeated_body_clusters\22:[],\22source_loc\22:0,\22state_alloc_count\22:0,\22state_call_count\22:0}",
                  pyc.struct.collections = "[]", pyc.value_params = [],
                  pyc.value_param_types = []} {
    %one = pyc.constant 1 : i1
    %zero = pyc.constant 0 : i8
    %n = pyc.and %a, %b : i8, i8 -> i8
    %q1 = pyc.reg %clk, %rst, %one, %n, %zero : i8
    %q2 = pyc.reg %clk, %rst, %one, %q1, %zero : i8
    %qc = pyc.reg %clk, %rst, %one, %a, %zero : i8
    %t = pyc.xor %q1, %q2 : i8, i8 -> i8
    %out = pyc.xor %t, %qc : i8, i8 -> i8
    func.return %out : i8
  }
}
