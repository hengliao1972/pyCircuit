module attributes {pyc.frontend.contract = "pycircuit", pyc.top = @comb_dirty_scheduler} {
  func.func @comb_dirty_scheduler(%arg0: i8, %arg1: i8, %arg2: i8, %arg3: i8) -> (i8, i8, i8) attributes {arg_names = ["a", "mask", "b", "c"], pyc.base = "comb_dirty_scheduler", pyc.inline = "false", pyc.kind = "module", pyc.params = "{}", pyc.struct.collections = "[]", pyc.struct.metrics = "{\22ast_node_count\22:0,\22collection_count\22:0,\22collection_instance_count\22:0,\22estimated_inline_cost\22:0,\22hardware_call_count\22:0,\22instance_count\22:0,\22loop_count\22:0,\22module_call_count\22:0,\22module_family_collection_count\22:0,\22repeat_pressure\22:0,\22repeated_body_clusters\22:[],\22source_loc\22:0,\22state_alloc_count\22:0,\22state_call_count\22:0}", pyc.value_param_types = [], pyc.value_params = [], result_names = ["producer", "result", "constant_out"]} {
    %0 = pyc.comb(%arg0, %arg1) : (i8, i8) -> i8 {
    ^bb0(%lhs: i8, %rhs: i8):
      %5 = pyc.and %lhs, %rhs : i8, i8 -> i8
      pyc.yield %5 : i8
    }
    %1 = pyc.comb(%0, %arg2) : (i8, i8) -> i8 {
    ^bb0(%lhs: i8, %rhs: i8):
      %5 = pyc.add %lhs, %rhs : i8, i8 -> i8
      pyc.yield %5 : i8
    }
    %2 = pyc.comb(%0, %arg3) : (i8, i8) -> i8 {
    ^bb0(%lhs: i8, %rhs: i8):
      %5 = pyc.add %lhs, %rhs : i8, i8 -> i8
      pyc.yield %5 : i8
    }
    %3 = pyc.comb(%1, %2) : (i8, i8) -> i8 {
    ^bb0(%lhs: i8, %rhs: i8):
      %5 = pyc.xor %lhs, %rhs : i8, i8 -> i8
      pyc.yield %5 : i8
    }
    %4 = pyc.comb() : () -> i8 {
      %5 = pyc.constant 90 : i8
      pyc.yield %5 : i8
    }
    return %0, %3, %4 : i8, i8, i8
  }
}
