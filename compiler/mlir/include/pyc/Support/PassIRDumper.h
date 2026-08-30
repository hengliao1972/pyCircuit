#pragma once

#include <cstdint>
#include <memory>
#include <string>

#include "mlir/Pass/PassInstrumentation.h"

namespace pyc {

/// Options controlling per-pass IR dump (see `pycc --dump-pass-ir*` flags).
struct PassIRDumperOptions {
  /// Output directory. Empty disables dumping. The special value "auto" means
  /// "<--out-dir>/pass_ir" (resolved by the caller before construction).
  std::string dir;
  /// Which phase(s) to dump: "before", "after", or "both" (default).
  std::string phase = "both";
  /// Regex (ECMAScript-ish; compiled with llvm::Regex, no exceptions) to filter
  /// pass short names. Empty matches everything. Match is performed against the
  /// pass short name (e.g. "eliminate-wires" without the "pyc-" prefix).
  std::string filterRegex;
  /// Maximum number of IR lines per file. 0 = unlimited. Files exceeding the
  /// limit are truncated and a `// truncated at N lines` marker is appended.
  uint64_t maxLines = 0;

  bool wantBefore() const { return phase == "both" || phase == "before"; }
  bool wantAfter() const { return phase == "both" || phase == "after"; }
};

/// A `mlir::PassInstrumentation` that writes the IR before and/or after every
/// pass run into files under a fixed directory. Files are named so that lexical
/// order matches execution order and pairs are directly diffable:
///
///   NNNN_<before|after>_<NN>_<pass-short>__L<level>[_FAILED].mlir
///
/// Designed for diagnostics only: it never modifies the IR, never fails the
/// pipeline on its own, and writes a `// PASS FAILED` marker if a pass fails.
class PassIRDumper final : public mlir::PassInstrumentation {
public:
  explicit PassIRDumper(PassIRDumperOptions opts);
  ~PassIRDumper() override;

  void runBeforePass(mlir::Pass *pass, mlir::Operation *op) override;
  void runAfterPass(mlir::Pass *pass, mlir::Operation *op) override;
  void runAfterPassFailed(mlir::Pass *pass, mlir::Operation *op) override;

private:
  struct Impl;
  std::unique_ptr<Impl> impl_;
};

} // namespace pyc
