// pycc fixture: undeclared combinational names become lazy addWire lookups.
// The same module is also the pyc-opt pass fixture for observation demand.
//
// COMB-LABEL: func.func @observation_demand_comb_module
// COMB: pyc.lazy_probe_wires
// COMB-SAME: name = "ready_state"
// COMB-SAME: name = "ready_alias"
// COMB: pyc.name = "ready_state"
// COMB-SAME: pyc.observe_lazy
// COMB: pyc.name = "ready_alias"
// COMB-SAME: pyc.observe_lazy

module attributes {pyc.top = @observation_demand_comb_module,
                   pyc.frontend.contract = "pycircuit"} {
  func.func @observation_demand_comb_module(%a: i8, %b: i8) -> i8
      attributes {arg_names = ["a", "b"],
                  result_names = ["out"],
                  pyc.kind = "module", pyc.inline = "false", pyc.params = "{}",
                  pyc.base = "observation_demand_comb_module",
                  pyc.struct.metrics = "{\22ast_node_count\22:0,\22collection_count\22:0,\22collection_instance_count\22:0,\22estimated_inline_cost\22:0,\22hardware_call_count\22:0,\22instance_count\22:0,\22loop_count\22:0,\22module_call_count\22:0,\22module_family_collection_count\22:0,\22repeat_pressure\22:0,\22repeated_body_clusters\22:[],\22source_loc\22:0,\22state_alloc_count\22:0,\22state_call_count\22:0}",
                  pyc.struct.collections = "[]", pyc.value_params = [],
                  pyc.value_param_types = []} {
    %y = pyc.and %a, %b {pyc.name = "ready_state"} : i8, i8 -> i8
    %n = pyc.alias %y {pyc.name = "ready_alias"} : i8
    func.return %y : i8
  }
}
