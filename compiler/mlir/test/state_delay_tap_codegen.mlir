module attributes {pyc.top = @state_delay_tap_codegen,
                   pyc.frontend.contract = "pycircuit"} {
  func.func @state_delay_tap_codegen(
      %clk: !pyc.clock, %rst: !pyc.reset, %en: i1, %in: i8) -> (i8, i8)
      attributes {arg_names = ["clk", "rst", "en", "in"],
                  result_names = ["tap", "tail"],
                  pyc.kind = "module", pyc.inline = "false", pyc.params = "{}",
                  pyc.base = "state_delay_tap_codegen",
                  pyc.struct.metrics = "{\22ast_node_count\22:0,\22collection_count\22:0,\22collection_instance_count\22:0,\22estimated_inline_cost\22:0,\22hardware_call_count\22:0,\22instance_count\22:0,\22loop_count\22:0,\22module_call_count\22:0,\22module_family_collection_count\22:0,\22repeat_pressure\22:0,\22repeated_body_clusters\22:[],\22source_loc\22:0,\22state_alloc_count\22:0,\22state_call_count\22:0}",
                  pyc.struct.collections = "[]", pyc.value_params = [],
                  pyc.value_param_types = []} {
    %init = pyc.constant 0 : i8
    %q0 = pyc.reg %clk, %rst, %en, %in, %init : i8
    %q1 = pyc.reg %clk, %rst, %en, %q0, %init : i8
    %q2 = pyc.reg %clk, %rst, %en, %q1, %init : i8
    %q3 = pyc.reg %clk, %rst, %en, %q2, %init : i8
    %side = pyc.add %q1, %init : i8, i8 -> i8
    func.return %side, %q3 : i8, i8
  }
}
