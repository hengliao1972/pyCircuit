.PHONY: help configure tools smoke vec-smoke install package clean

BUILD_DIR ?= .pycircuit_out/toolchain/build
INSTALL_PREFIX ?= .pycircuit_out/toolchain/install

help:
	@echo "Targets:"
	@echo "  configure  Configure CMake (needs LLVM_DIR/MLIR_DIR or LLVM_CONFIG=llvm-config-19)"
	@echo "  tools      Build pycc + pyc-opt + runtime"
	@echo "  smoke      Run compiler + simulation smoke checks"
	@echo "  vec-smoke  Run generated Vec operator pytest checks"
	@echo "  install    Install toolchain into $(INSTALL_PREFIX)"
	@echo "  package    Build a TGZ via CPack"
	@echo "  clean      Remove $(BUILD_DIR), $(INSTALL_PREFIX), and dist/"

configure:
	@set -e; \
	if [ -z "$$LLVM_DIR" ] || [ -z "$$MLIR_DIR" ]; then \
	  if [ -n "$$LLVM_CONFIG" ]; then \
	    LLVM_DIR="$$( "$$LLVM_CONFIG" --cmakedir )"; \
	    MLIR_DIR="$$( dirname "$$LLVM_DIR" )/mlir"; \
	  else \
	    echo "error: set LLVM_DIR and MLIR_DIR or LLVM_CONFIG"; \
	    exit 2; \
	  fi; \
	fi; \
	cmake -G Ninja -S . -B "$(BUILD_DIR)" \
	  -DCMAKE_BUILD_TYPE=Release \
	  -DCMAKE_INSTALL_PREFIX="$(INSTALL_PREFIX)" \
	  -DLLVM_DIR="$$LLVM_DIR" \
	  -DMLIR_DIR="$$MLIR_DIR"

tools: configure
	ninja -C "$(BUILD_DIR)" pycc pyc4_runtime
	ninja -C "$(BUILD_DIR)" pyc-opt 2>/dev/null || true

smoke: tools
	PYC_TOOLCHAIN_ROOT="$(INSTALL_PREFIX)" PYCC="$(INSTALL_PREFIX)/bin/pycc" bash flows/scripts/run_examples.sh
	PYC_TOOLCHAIN_ROOT="$(INSTALL_PREFIX)" PYCC="$(INSTALL_PREFIX)/bin/pycc" bash flows/scripts/run_sims.sh

vec-smoke:
	PYC_TOOLCHAIN_ROOT="$(INSTALL_PREFIX)" PYCC="$(INSTALL_PREFIX)/bin/pycc" bash tests/vec/run_vec_ops.sh

install: tools
	cmake --install "$(BUILD_DIR)" --prefix "$(INSTALL_PREFIX)"

package: tools
	(cd "$(BUILD_DIR)" && cpack -G TGZ)

clean:
	rm -rf "$(BUILD_DIR)" "$(INSTALL_PREFIX)" dist
