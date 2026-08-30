#include "pyc/Support/PassIRDumper.h"

#include "mlir/IR/Operation.h"
#include "mlir/Pass/Pass.h"

#include "llvm/ADT/SmallString.h"
#include "llvm/ADT/StringRef.h"
#include "llvm/Support/FileSystem.h"
#include "llvm/Support/Path.h"
#include "llvm/Support/Regex.h"
#include "llvm/Support/raw_ostream.h"

#include <cstdio>
#include <memory>
#include <string>
#include <system_error>
#include <vector>

using namespace mlir;

namespace pyc {

namespace {

/// Strip the dialect prefix ("pyc-") from a pass argument name so file names
/// are short and sortable. Returns "unknown" if the name is empty.
///
/// We prefer `pass->getArgument()` (e.g. "pyc-eliminate-wires") over
/// `pass->getName()`, because the latter returns the mangled C++ class name
/// (e.g. "pyc::(anonymous namespace)::EliminateWiresPass") for PassWrapper
/// subclasses, which is unreadable after sanitization.
std::string passShortName(Pass *pass) {
  if (!pass)
    return "unknown";
  std::string s;
  if (pass->getArgument().empty()) {
    // Fallback for unregistered passes (shouldn't happen for pyc passes).
    s = pass->getName().str();
  } else {
    s = pass->getArgument().str();
  }
  if (s.empty())
    return "unknown";
  // Strip "pyc-" prefix for shorter file names; keep other dialects intact.
  if (s.rfind("pyc-", 0) == 0)
    s = s.substr(4);
  return s;
}

/// Make `s` safe to embed in a file name: keep [A-Za-z0-9._-], replace others
/// with '_'. This guards against unusual pass names without constraining the
/// common case.
std::string sanitizeForFileName(llvm::StringRef s) {
  std::string out;
  out.reserve(s.size());
  for (char c : s) {
    if ((c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') ||
        c == '.' || c == '-' || c == '_')
      out.push_back(c);
    else
      out.push_back('_');
  }
  return out;
}

/// Format a 4-digit zero-padded sequence number. Wraps at 9999 to keep file
/// names a fixed width; rare for real designs (pipeline depth ~25 passes).
std::string formatSeq(unsigned n) {
  char buf[8];
  if (n > 9999)
    n = 9999;
  std::snprintf(buf, sizeof(buf), "%04u", n);
  return std::string(buf);
}

/// Append up to `maxLines` non-empty-trailing lines of `ir` to `os`. When the
/// limit kicks in, append a trailing `// truncated at N lines` marker. When
/// `maxLines == 0`, write the whole buffer.
void writeTruncated(llvm::raw_ostream &os, const std::string &ir, uint64_t maxLines) {
  if (maxLines == 0) {
    os << ir;
    return;
  }
  uint64_t written = 0;
  size_t pos = 0;
  while (pos < ir.size() && written < maxLines) {
    size_t nl = ir.find('\n', pos);
    if (nl == std::string::npos) {
      os.write(ir.data() + pos, ir.size() - pos);
      os << '\n';
      break;
    }
    os.write(ir.data() + pos, nl - pos + 1);
    pos = nl + 1;
    ++written;
  }
  if (pos < ir.size())
    os << "// truncated at " << maxLines << " lines\n";
}

} // namespace

struct PassIRDumper::Impl {
  PassIRDumperOptions opts;

  /// Monotonic counter shared across all phases; guarantees lexical order =
  /// execution order. Incremented once per emitted file.
  unsigned seq = 0;
  /// Monotonic per-pass index (separate from seq) so before/after of the same
  /// pass share a stable `NN` number, making diff pairs easy to spot.
  unsigned passIndex = 0;

  /// Nested-pass depth stack. `runBeforePass` pushes; `runAfterPass*` pops.
  /// Top-level (module) passes are level 0; func-nested are level 1+.
  std::vector<unsigned> levelStack;

  /// Optional compiled regex. Constructed once. Uses llvm::Regex (no exceptions,
  /// matches LLVM/MLIR -fno-exceptions build default).
  std::unique_ptr<llvm::Regex> re;

  bool enabled = false;
  std::string resolvedDir;

  explicit Impl(PassIRDumperOptions o) : opts(std::move(o)) {
    if (opts.dir.empty() || opts.dir == "auto")
      return; // "auto" must be resolved by the caller; treat as disabled here.
    if (opts.phase != "before" && opts.phase != "after" && opts.phase != "both") {
      llvm::errs() << "warning: invalid --dump-pass-ir-phase '" << opts.phase
                   << "' (expected: before|after|both); falling back to both\n";
      opts.phase = "both";
    }
    enabled = true;
    resolvedDir = opts.dir;
    if (!opts.filterRegex.empty()) {
      auto compiled = std::make_unique<llvm::Regex>(opts.filterRegex);
      std::string err;
      if (!compiled->isValid(err)) {
        llvm::errs() << "warning: invalid --dump-pass-ir-filter regex '"
                     << opts.filterRegex << "': " << err << "; ignoring filter\n";
      } else {
        re = std::move(compiled);
      }
    }
    std::error_code ec = llvm::sys::fs::create_directories(resolvedDir);
    if (ec) {
      llvm::errs() << "warning: cannot create --dump-pass-ir dir '" << resolvedDir
                   << "': " << ec.message() << "; disabling IR dump\n";
      enabled = false;
    }
  }

  bool matchesPass(const std::string &shortName) const {
    if (!re)
      return true;
    return re->match(shortName);
  }

  /// Write the IR of `op` for phase `which` ("before"/"after"). `failed` adds
  /// the `__FAILED` suffix and a `// PASS FAILED` header line.
  void write(const std::string &shortName, unsigned level, Operation *op,
             const char *which, bool failed) {
    if (!enabled || !op)
      return;
    if (!matchesPass(shortName))
      return;

    const unsigned curSeq = seq++;
    std::string fileName = formatSeq(curSeq) + "_" + which + "_" +
                           formatSeq(passIndex) + "_" +
                           sanitizeForFileName(shortName) + "__L" +
                           std::to_string(level);
    if (failed)
      fileName += "_FAILED";
    fileName += ".mlir";

    std::string ir;
    llvm::raw_string_ostream irOs(ir);
    if (failed)
      irOs << "// PASS FAILED\n";
    {
      OpPrintingFlags flags;
      flags.printGenericOpForm(false);
      flags.useLocalScope();
      flags.enableDebugInfo(false);
      op->print(irOs, flags);
    }
    irOs.flush();

    llvm::SmallString<256> path(resolvedDir);
    llvm::sys::path::append(path, fileName);
    std::error_code ec;
    llvm::raw_fd_ostream os(path, ec, llvm::sys::fs::OF_Text);
    if (ec) {
      llvm::errs() << "warning: cannot write IR dump '" << path << "': "
                   << ec.message() << "\n";
      return;
    }
    writeTruncated(os, ir, opts.maxLines);
  }
};

PassIRDumper::PassIRDumper(PassIRDumperOptions opts)
    : impl_(std::make_unique<Impl>(std::move(opts))) {}

PassIRDumper::~PassIRDumper() = default;

void PassIRDumper::runBeforePass(Pass *pass, Operation *op) {
  if (!impl_->enabled)
    return;
  // Determine nesting level: top-level pass has no parent on the stack.
  const unsigned level = static_cast<unsigned>(impl_->levelStack.size());
  impl_->levelStack.push_back(level);
  ++impl_->passIndex; // assign a fresh index for this pass run

  const std::string name = passShortName(pass);
  if (impl_->opts.wantBefore())
    impl_->write(name, level, op, "before", /*failed=*/false);
}

void PassIRDumper::runAfterPass(Pass *pass, Operation *op) {
  if (!impl_->enabled)
    return;
  unsigned level = 0;
  if (!impl_->levelStack.empty()) {
    level = impl_->levelStack.back();
    impl_->levelStack.pop_back();
  }
  if (impl_->opts.wantAfter())
    impl_->write(passShortName(pass), level, op, "after", /*failed=*/false);
}

void PassIRDumper::runAfterPassFailed(Pass *pass, Operation *op) {
  if (!impl_->enabled)
    return;
  unsigned level = 0;
  if (!impl_->levelStack.empty()) {
    level = impl_->levelStack.back();
    impl_->levelStack.pop_back();
  }
  // Always write the after-image on failure, even if phase == "before", so the
  // failure state is never lost.
  impl_->write(passShortName(pass), level, op, "after", /*failed=*/true);
}

} // namespace pyc
