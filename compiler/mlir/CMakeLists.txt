cmake_minimum_required(VERSION 3.20)
project(pyc_mlir LANGUAGES CXX C)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

include(GNUInstallDirs)

find_package(LLVM REQUIRED CONFIG)
find_package(MLIR REQUIRED CONFIG)
set(PYC_LLVM_VERSION "${LLVM_PACKAGE_VERSION}" PARENT_SCOPE)

message(STATUS "Using LLVMConfig.cmake in: ${LLVM_DIR}")
message(STATUS "Using MLIRConfig.cmake in: ${MLIR_DIR}")

list(APPEND CMAKE_MODULE_PATH "${MLIR_CMAKE_DIR}")
include(TableGen)
include(AddLLVM)
include(AddMLIR)
include(HandleLLVMOptions)

include_directories(SYSTEM
  ${LLVM_INCLUDE_DIRS}
  ${MLIR_INCLUDE_DIRS}
)
add_definitions(${LLVM_DEFINITIONS})

set(PYC_SOURCE_DIR "${CMAKE_CURRENT_SOURCE_DIR}")
set(PYC_BINARY_DIR "${CMAKE_CURRENT_BINARY_DIR}")
get_filename_component(PYC_REPO_ROOT "${PYC_SOURCE_DIR}/../.." ABSOLUTE)

# TableGen files include via `include "pyc/..."`, so ensure the project `include/`
# directory is on the TableGen include path.
include_directories("${PYC_SOURCE_DIR}/include")

file(MAKE_DIRECTORY "${PYC_BINARY_DIR}/include/pyc/Dialect/PYC")

# --- TableGen: types ---
set(LLVM_TARGET_DEFINITIONS "${PYC_SOURCE_DIR}/include/pyc/Dialect/PYC/PYCTypes.td")
mlir_tablegen(include/pyc/Dialect/PYC/PYCTypes.h.inc -gen-typedef-decls -dialect=pyc)
mlir_tablegen(include/pyc/Dialect/PYC/PYCTypes.cpp.inc -gen-typedef-defs -dialect=pyc)
add_public_tablegen_target(PYCTypesIncGen)

# --- TableGen: ops ---
set(LLVM_TARGET_DEFINITIONS "${PYC_SOURCE_DIR}/include/pyc/Dialect/PYC/PYCOps.td")
mlir_tablegen(include/pyc/Dialect/PYC/PYCOps.h.inc -gen-op-decls)
mlir_tablegen(include/pyc/Dialect/PYC/PYCOps.cpp.inc -gen-op-defs)
add_public_tablegen_target(PYCOpsIncGen)

add_library(pyc_dialect
  lib/Dialect/PYC/PYCDialect.cpp
  lib/Dialect/PYC/PYCTypes.cpp
  lib/Dialect/PYC/PYCOps.cpp
)
add_dependencies(pyc_dialect
  PYCTypesIncGen
  PYCOpsIncGen
)
llvm_update_compile_flags(pyc_dialect)
target_include_directories(pyc_dialect PUBLIC
  "${PYC_SOURCE_DIR}/include"
  "${PYC_BINARY_DIR}/include"
)
target_link_libraries(pyc_dialect PUBLIC
  MLIRIR
  MLIRSupport
  LLVMSupport
)

add_library(pyc_transforms
  lib/Transforms/CombDepGraph.cpp
  lib/Transforms/CheckFrontendContractPass.cpp
  lib/Transforms/CheckHierarchyDisciplinePass.cpp
  lib/Transforms/CheckFlatTypesPass.cpp
  lib/Transforms/CheckLogicDepthPass.cpp
  lib/Transforms/CheckNoDynamicPass.cpp
  lib/Transforms/CheckCombCyclesPass.cpp
  lib/Transforms/CheckClockDomainsPass.cpp
  lib/Transforms/CombCanonicalizePass.cpp
  lib/Transforms/CollectCompileStatsPass.cpp
  lib/Transforms/EliminateDeadInstancesPass.cpp
  lib/Transforms/EliminateDeadStatePass.cpp
  lib/Transforms/EliminateWiresPass.cpp
  lib/Transforms/FlattenInstancesPass.cpp
  lib/Transforms/InlineFunctionsPass.cpp
  lib/Transforms/PackI1RegsPass.cpp
  lib/Transforms/FuseCombPass.cpp
  lib/Transforms/CppPlacementPass.cpp
  lib/Transforms/LowerSCFToPYCStaticPass.cpp
  lib/Transforms/PrunePortsPass.cpp
  lib/Transforms/SLPPackWiresPass.cpp
  lib/Support/PassIRDumper.cpp
  lib/Transforms/VectorUnrollPass.cpp
)
llvm_update_compile_flags(pyc_transforms)
target_include_directories(pyc_transforms PUBLIC
  "${PYC_SOURCE_DIR}/include"
  "${PYC_BINARY_DIR}/include"
)
target_link_libraries(pyc_transforms PUBLIC
  pyc_dialect
  MLIRArithDialect
  MLIRFuncDialect
  MLIRPass
  MLIRSCFDialect
  LLVMSupport
)

# pyc-opt requires MLIRRegisterAllPasses; Homebrew LLVM may not export it.
# Build script uses "ninja pycc" only so pyc-opt is optional; if you run
# "ninja" (all), pyc-opt may fail to link on some systems.
if(TARGET MLIRRegisterAllPasses)
  add_executable(pyc-opt EXCLUDE_FROM_ALL tools/pyc-opt.cpp)
  set_target_properties(pyc-opt PROPERTIES
    RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/bin"
    INSTALL_RPATH "${PYC_INSTALL_RPATH}"
    BUILD_WITH_INSTALL_RPATH OFF
  )
  llvm_update_compile_flags(pyc-opt)
  target_include_directories(pyc-opt PRIVATE
    "${PYC_SOURCE_DIR}/include"
    "${PYC_BINARY_DIR}/include"
  )
  target_link_libraries(pyc-opt PRIVATE
    pyc_dialect
    pyc_transforms
    MLIRArithDialect
    MLIRFuncDialect
    MLIRFuncInlinerExtension
    MLIRRegisterAllPasses
    MLIRSCFDialect
    MLIROptLib
  )
  set(PYC_OPT_BUILT 1)
else()
  message(STATUS "MLIRRegisterAllPasses not found; skipping pyc-opt (pycc only)")
endif()

add_executable(pycc tools/pycc.cpp lib/Emit/VerilogEmitter.cpp lib/Emit/CppEmitter.cpp)
llvm_update_compile_flags(pycc)
set_target_properties(pycc PROPERTIES
  RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/bin"
  INSTALL_RPATH "${PYC_INSTALL_RPATH}"
  BUILD_WITH_INSTALL_RPATH OFF
)
target_include_directories(pycc PRIVATE
  "${PYC_SOURCE_DIR}/include"
  "${PYC_BINARY_DIR}/include"
  "${PYC_REPO_ROOT}/runtime"
)
target_link_libraries(pycc PRIVATE
  pyc_dialect
  pyc_transforms
  MLIRArithDialect
  MLIRIR
  MLIRSupport
  MLIRFuncDialect
  MLIRFuncInlinerExtension
  MLIRParser
  MLIRPass
  MLIRSCFDialect
  MLIRTransforms
  LLVMSupport
)

# Install binaries for packaging / reuse.
set(PYC_INSTALL_TARGETS pycc)

if(PYC_OPT_BUILT)
  list(APPEND PYC_INSTALL_TARGETS pyc-opt)
endif()
install(TARGETS ${PYC_INSTALL_TARGETS} RUNTIME DESTINATION "${CMAKE_INSTALL_BINDIR}")
