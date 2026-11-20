module attributes {pyc.top = @state_retime_codegen,
                   pyc.frontend.contract = "pycircuit"} {
  func.func @state_retime_codegen(
      %clk: !pyc.clock, %rst: !pyc.reset, %en: i1, %in: i8, %rhs: i8)
      -> (i8, i8, i8, i8, i1)
      attributes {arg_names = ["clk", "rst", "en", "in", "rhs"],
                  result_names = ["stage0", "stage1", "tail", "branch", "less"],
                  pyc.kind = "module", pyc.inline = "false", pyc.params = "{}",
                  pyc.base = "state_retime_codegen",
                  pyc.struct.metrics = "{\22ast_node_count\22:0,\22collection_count\22:0,\22collection_instance_count\22:0,\22estimated_inline_cost\22:0,\22hardware_call_count\22:0,\22instance_count\22:0,\22loop_count\22:0,\22module_call_count\22:0,\22module_family_collection_count\22:0,\22repeat_pressure\22:0,\22repeated_body_clusters\22:[],\22source_loc\22:0,\22state_alloc_count\22:0,\22state_call_count\22:0}",
                  pyc.struct.collections = "[]", pyc.value_params = [],
                  pyc.value_param_types = []} {
    %init0 = pyc.constant 3 : i8
    %five = pyc.constant 5 : i8
    %init1 = pyc.constant 8 : i8
    %mask = pyc.constant 165 : i8
    %init2 = pyc.constant 173 : i8
    %side_mask = pyc.constant 60 : i8
    %seven = pyc.constant 7 : i8
    %lhsInit = pyc.constant 7 : i8
    %rhsInit = pyc.constant 19 : i8
    %q0 = pyc.reg %clk, %rst, %en, %in, %init0 : i8
    %next1 = pyc.add %q0, %five : i8, i8 -> i8
    %q1 = pyc.reg %clk, %rst, %en, %next1, %init1 : i8
    %next2 = pyc.xor %q1, %mask : i8, i8 -> i8
    %q2 = pyc.reg %clk, %rst, %en, %next2, %init2 : i8
    %branch = pyc.reg %clk, %rst, %en, %q0, %init0 : i8
    %stage0 = pyc.xor %q0, %side_mask : i8, i8 -> i8
    %stage1 = pyc.add %q1, %seven : i8, i8 -> i8
    %lhsQ = pyc.reg %clk, %rst, %en, %in, %lhsInit : i8
    %rhsQ = pyc.reg %clk, %rst, %en, %rhs, %rhsInit : i8
    %less = pyc.ult %lhsQ, %rhsQ : i8, i8 -> i1
    func.return %stage0, %stage1, %q2, %branch, %less : i8, i8, i8, i8, i1
  }
}
