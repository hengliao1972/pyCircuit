#include "pyc/Transforms/Passes.h"

#include "pyc/Dialect/PYC/PYCOps.h"

#include "mlir/Dialect/Func/IR/FuncOps.h"
#include "mlir/IR/BuiltinAttributes.h"
#include "mlir/IR/BuiltinOps.h"
#include "mlir/Pass/Pass.h"
#include "llvm/ADT/SmallVector.h"
#include "llvm/ADT/StringMap.h"
#include "llvm/ADT/StringSet.h"
#include "llvm/Support/JSON.h"

#include <string>

using namespace mlir;

namespace pyc {
namespace {

static bool isAlpha(char c) { return (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z'); }

static bool isDigit(char c) { return (c >= '0' && c <= '9'); }

static bool isIdentStart(char c) { return isAlpha(c) || c == '_'; }

static bool isIdentChar(char c) { return isIdentStart(c) || isDigit(c); }

static bool isValidIdent(llvm::StringRef s) {
  if (s.empty())
    return false;
  if (!isIdentStart(s.front()))
    return false;
  for (char c : s.drop_front()) {
    if (!isIdentChar(c))
      return false;
  }
  return true;
}

static std::string sanitizeIdForBackend(llvm::StringRef s) {
  std::string out;
  out.reserve(s.size() + 1);
  for (char c : s) {
    if (isIdentChar(c))
      out.push_back(c);
    else
      out.push_back('_');
  }
  if (out.empty() || isDigit(out.front()))
    out.insert(out.begin(), '_');
  return out;
}

// Decision 0024/0025: field path segments are `ident` optionally followed by
// one or more `[<digits>]` indices, and segments are separated by dots.
static bool isValidFieldSegment(llvm::StringRef seg) {
  if (seg.empty())
    return false;
  std::size_t i = 0;
  if (!isIdentStart(seg[i]))
    return false;
  ++i;
  while (i < seg.size() && isIdentChar(seg[i]))
    ++i;
  while (i < seg.size()) {
    if (seg[i] != '[')
      return false;
    ++i;
    std::size_t digitsBegin = i;
    while (i < seg.size() && isDigit(seg[i]))
      ++i;
    if (i == digitsBegin)
      return false;
    if (i >= seg.size() || seg[i] != ']')
      return false;
    ++i;
  }
  return true;
}

static bool isValidFieldPath(llvm::StringRef path) {
  if (path.empty())
    return false;
  if (path.contains(':'))
    return false;
  llvm::SmallVector<llvm::StringRef, 8> segs;
  path.split(segs, '.', /*MaxSplit=*/-1, /*KeepEmpty=*/true);
  if (segs.empty())
    return false;
  for (llvm::StringRef seg : segs) {
    if (!isValidFieldSegment(seg))
      return false;
  }
  return true;
}

static bool jsonIntFieldNonNegative(Operation *op,
                                    const llvm::json::Object &obj,
                                    llvm::StringRef field,
                                    llvm::StringRef code,
                                    bool &ok) {
  auto it = obj.find(field);
  if (it == obj.end()) {
    op->emitError() << "[" << code << "] missing JSON field `" << field << "`";
    ok = false;
    return false;
  }
  auto iv = it->second.getAsInteger();
  if (!iv || *iv < 0) {
    op->emitError() << "[" << code << "] JSON field `" << field << "` must be a non-negative integer";
    ok = false;
    return false;
  }
  return true;
}

class CheckFrontendContractPass : public PassWrapper<CheckFrontendContractPass, OperationPass<ModuleOp>> {
public:
  MLIR_DEFINE_EXPLICIT_INTERNAL_INLINE_TYPE_ID(CheckFrontendContractPass)

  StringRef getArgument() const override { return "pyc-check-frontend-contract"; }
  StringRef getDescription() const override {
    return "Verify required frontend contract attrs are present and match the supported contract";
  }

  void runOnOperation() override {
    ModuleOp module = getOperation();
    bool ok = true;

    auto emitModule = [&](llvm::StringRef code, llvm::StringRef msg, llvm::StringRef hint) {
      auto d = module.emitError();
      d << "[" << code << "] " << msg;
      if (!hint.empty())
        d << " (hint: " << hint << ")";
    };

    static constexpr const char *kRequiredContract = "pycircuit";
    auto modContract = module->getAttrOfType<StringAttr>("pyc.frontend.contract");
    if (!modContract) {
      emitModule("PYC901", "missing required module attr `pyc.frontend.contract`",
                 "regenerate .pyc with the current pyCircuit frontend and keep module attrs intact");
      ok = false;
    } else if (modContract.getValue() != kRequiredContract) {
      auto d = module.emitError();
      d << "[PYC902] frontend contract mismatch: expected `" << kRequiredContract << "`, got `"
        << modContract.getValue() << "` (hint: regenerate .pyc with matching toolchain)";
      ok = false;
    }

    module.walk([&](func::FuncOp f) {
      auto checkStrAttr = [&](StringRef name, llvm::StringRef code, llvm::StringRef hint) -> StringAttr {
        auto attr = f->getAttrOfType<StringAttr>(name);
        if (!attr) {
          auto d = f.emitError();
          d << "[" << code << "] missing required func attr `" << name << "`";
          if (!hint.empty())
            d << " (hint: " << hint << ")";
          ok = false;
        }
        return attr;
      };

      auto checkArrAttr = [&](StringRef name, llvm::StringRef code, llvm::StringRef hint) -> ArrayAttr {
        auto attr = f->getAttrOfType<ArrayAttr>(name);
        if (!attr) {
          auto d = f.emitError();
          d << "[" << code << "] missing required func attr `" << name << "`";
          if (!hint.empty())
            d << " (hint: " << hint << ")";
          ok = false;
        }
        return attr;
      };

      auto checkBoolAttr = [&](StringRef name, llvm::StringRef code, llvm::StringRef hint) -> BoolAttr {
        auto attr = f->getAttrOfType<BoolAttr>(name);
        if (!attr) {
          auto d = f.emitError();
          d << "[" << code << "] missing required func attr `" << name << "`";
          if (!hint.empty())
            d << " (hint: " << hint << ")";
          ok = false;
        }
        return attr;
      };

      auto kind = checkStrAttr("pyc.kind", "PYC903", "frontend must stamp symbol kind metadata");
      auto inl = checkStrAttr("pyc.inline", "PYC904", "frontend must stamp inline metadata");
      (void)checkStrAttr("pyc.params", "PYC905", "frontend must stamp canonical specialization params");
      (void)checkStrAttr("pyc.base", "PYC906", "frontend must stamp canonical base symbol name");
      auto argNames = checkArrAttr("arg_names", "PYC907", "frontend must stamp canonical port names");
      auto resultNames = checkArrAttr("result_names", "PYC908", "frontend must stamp canonical port names");
      auto structMetrics =
          checkStrAttr("pyc.struct.metrics", "PYC951", "frontend must stamp structural summary metadata");
      auto structCollections =
          checkStrAttr("pyc.struct.collections", "PYC952", "frontend must stamp structural collection metadata");

      if (kind) {
        auto k = kind.getValue();
        if (k != "module" && k != "function" && k != "template") {
          f.emitError() << "[PYC909] invalid `pyc.kind` value: " << k
                        << " (hint: allowed values are module/function/template)";
          ok = false;
        }
      }

      if (inl) {
        auto v = inl.getValue();
        if (v != "true" && v != "false") {
          f.emitError() << "[PYC910] invalid `pyc.inline` value: " << v
                        << " (hint: allowed values are true|false)";
          ok = false;
        }
      }

      if (argNames && argNames.size() != f.getNumArguments()) {
        f.emitError() << "[PYC911] `arg_names` arity mismatch: attr size=" << argNames.size()
                      << " but func has " << f.getNumArguments() << " arguments";
        ok = false;
      }
      if (resultNames && resultNames.size() != f.getNumResults()) {
        f.emitError() << "[PYC912] `result_names` arity mismatch: attr size=" << resultNames.size()
                      << " but func has " << f.getNumResults() << " results";
        ok = false;
      }

      auto checkPortNames = [&](ArrayAttr arr, llvm::StringRef attrName, llvm::StringRef codeBase) {
        if (!arr)
          return;
        llvm::StringSet<> used;
        for (unsigned idx = 0, e = static_cast<unsigned>(arr.size()); idx < e; ++idx) {
          auto s = dyn_cast<StringAttr>(arr[idx]);
          if (!s) {
            f.emitError() << "[" << codeBase << "1] `" << attrName << "` entry #" << idx << " must be a string";
            ok = false;
            continue;
          }
          llvm::StringRef v = s.getValue();
          if (!isValidFieldPath(v)) {
            f.emitError() << "[" << codeBase << "2] invalid canonical port path in `" << attrName << "` entry #"
                          << idx << ": `" << v << "` (expected segments like foo.bar[3]; `:` is reserved)";
            ok = false;
          }
          if (!used.insert(v).second) {
            f.emitError() << "[" << codeBase << "3] duplicate canonical port path in `" << attrName << "`: `" << v
                          << "`";
            ok = false;
          }
        }
      };

      // Decision 0009/0024/0025: port names are canonical field paths.
      checkPortNames(argNames, "arg_names", "PYC92");
      checkPortNames(resultNames, "result_names", "PYC93");

      // Decision 0009/0023: canonical port paths are unique within a module
      // instance namespace; and Decision 0145 requires Verilog trace mapping to
      // be unambiguous. Enforce that the backend-sanitized identifiers are also
      // unique to avoid order-dependent suffixing.
      if (argNames && resultNames) {
        llvm::StringSet<> inUsed;
        llvm::StringMap<llvm::StringRef> sanitizedToRaw;

        for (auto a : argNames) {
          auto s = dyn_cast<StringAttr>(a);
          if (!s)
            continue;
          llvm::StringRef v = s.getValue();
          inUsed.insert(v);
          std::string san = sanitizeIdForBackend(v);
          auto [it, inserted] = sanitizedToRaw.try_emplace(san, v);
          if (!inserted && it->second != v) {
            f.emitError() << "[PYC925] backend port id collision after sanitization: `" << san << "` from `"
                          << it->second << "` and `" << v << "` (hint: rename ports to avoid ambiguous Verilog ids)";
            ok = false;
          }
        }

        for (auto r : resultNames) {
          auto s = dyn_cast<StringAttr>(r);
          if (!s)
            continue;
          llvm::StringRef v = s.getValue();
          if (inUsed.count(v) != 0) {
            f.emitError() << "[PYC924] duplicate canonical port path across `arg_names` and `result_names`: `" << v
                          << "`";
            ok = false;
          }
          std::string san = sanitizeIdForBackend(v);
          auto [it, inserted] = sanitizedToRaw.try_emplace(san, v);
          if (!inserted && it->second != v) {
            f.emitError() << "[PYC925] backend port id collision after sanitization: `" << san << "` from `"
                          << it->second << "` and `" << v << "` (hint: rename ports to avoid ambiguous Verilog ids)";
            ok = false;
          }
        }
      }

      // Decision 0025: instance `name` must be a strict identifier (no escaping).
      // Decision 0017: optional `short_name` is preferred for path segments.
      llvm::StringSet<> segUsed;
      f.walk([&](pyc::InstanceOp inst) {
        auto nameAttr = inst->getAttrOfType<StringAttr>("name");
        if (!nameAttr) {
          inst.emitError() << "[PYC941] missing required instance name "
                           << "(hint: frontend must always stamp InstanceOp `name` for stable canonical paths)";
          ok = false;
          return;
        }
        llvm::StringRef v = nameAttr.getValue();
        if (!isValidIdent(v)) {
          inst.emitError() << "[PYC940] invalid instance name `" << v
                           << "` (expected [A-Za-z_][A-Za-z0-9_]*; no escaping supported)";
          ok = false;
        }

        llvm::StringRef seg = v;
        if (auto shortAttr = inst->getAttrOfType<StringAttr>("short_name")) {
          llvm::StringRef sv = shortAttr.getValue();
          if (!isValidIdent(sv)) {
            inst.emitError() << "[PYC946] invalid instance short_name `" << sv
                             << "` (expected [A-Za-z_][A-Za-z0-9_]*; no escaping supported)";
            ok = false;
          } else {
            seg = sv;
          }
        }

        if (!segUsed.insert(seg).second) {
          inst.emitError() << "[PYC947] duplicate instance path segment `" << seg
                           << "` within module (hint: instance name/short_name must be unique per parent for stable "
                              "canonical paths)";
          ok = false;
        }
      });

      if (!f.isDeclaration()) {
        llvm::StringSet<> portPaths;
        auto addPortPaths = [&](ArrayAttr arr) {
          if (!arr)
            return;
          for (auto a : arr) {
            auto s = dyn_cast<StringAttr>(a);
            if (!s)
              continue;
            portPaths.insert(s.getValue());
          }
        };
        addPortPaths(argNames);
        addPortPaths(resultNames);

        llvm::StringSet<> memNames;
        f.walk([&](Operation *op) {
          llvm::StringRef opKind{};
          if (isa<pyc::ByteMemOp>(op))
            opKind = "byte_mem";
          else if (isa<pyc::SyncMemOp>(op))
            opKind = "sync_mem";
          else if (isa<pyc::SyncMemDPOp>(op))
            opKind = "sync_mem_dp";
          else
            return;

          auto nameAttr = op->getAttrOfType<StringAttr>("name");
          if (!nameAttr) {
            op->emitError() << "[PYC942] missing required `name` attribute for pyc." << opKind
                            << " (hint: pass name=... in the frontend memory API for stable DFX paths)";
            ok = false;
            return;
          }
          llvm::StringRef name = nameAttr.getValue();
          if (!isValidIdent(name)) {
            op->emitError() << "[PYC943] invalid `name` for pyc." << opKind << ": `" << name
                            << "` (expected [A-Za-z_][A-Za-z0-9_]*; no escaping supported)";
            ok = false;
          }
          if (!memNames.insert(name).second) {
            op->emitError() << "[PYC944] duplicate memory name in module: `" << name
                            << "` (hint: use unique memory names within a module instance)";
            ok = false;
          }
          if (portPaths.count(name) != 0) {
            op->emitError() << "[PYC945] memory name collides with port field path: `" << name
                            << "` (hint: rename memory or port to avoid ProbeRegistry canonical_path collision)";
            ok = false;
          }
        });
      }

      auto valueParamNames = f->getAttrOfType<ArrayAttr>("pyc.value_params");
      auto valueParamTypes = f->getAttrOfType<ArrayAttr>("pyc.value_param_types");
      if (bool(valueParamNames) != bool(valueParamTypes)) {
        f.emitError() << "[PYC913] value-param metadata mismatch: both `pyc.value_params` and "
                         "`pyc.value_param_types` must be present together";
        ok = false;
      } else if (valueParamNames && valueParamTypes) {
        if (valueParamNames.size() != valueParamTypes.size()) {
          f.emitError() << "[PYC914] value-param metadata arity mismatch: `pyc.value_params` has "
                        << valueParamNames.size() << " entries but `pyc.value_param_types` has "
                        << valueParamTypes.size();
          ok = false;
        }

        llvm::StringSet<> argNameSet;
        if (argNames) {
          for (Attribute a : argNames) {
            if (auto s = dyn_cast<StringAttr>(a))
              argNameSet.insert(s.getValue());
          }
        }

        auto validValueType = [](StringRef ty) -> bool {
          if (ty == "!pyc.clock" || ty == "!pyc.reset")
            return true;
          if (!ty.starts_with("i"))
            return false;
          unsigned w = 0;
          return !ty.drop_front().getAsInteger(10, w) && w > 0;
        };

        for (unsigned idx = 0, e = static_cast<unsigned>(valueParamNames.size()); idx < e; ++idx) {
          auto nameAttr = dyn_cast<StringAttr>(valueParamNames[idx]);
          auto typeAttr = dyn_cast<StringAttr>(valueParamTypes[idx]);
          if (!nameAttr) {
            f.emitError() << "[PYC915] `pyc.value_params` entry #" << idx << " must be a string";
            ok = false;
            continue;
          }
          if (!typeAttr) {
            f.emitError() << "[PYC916] `pyc.value_param_types` entry #" << idx << " must be a string";
            ok = false;
            continue;
          }
          if (!argNameSet.contains(nameAttr.getValue())) {
            f.emitError() << "[PYC917] value-param `" << nameAttr.getValue()
                          << "` is not present in `arg_names`";
            ok = false;
          }
          if (!validValueType(typeAttr.getValue())) {
            f.emitError() << "[PYC918] invalid value-param type `" << typeAttr.getValue()
                          << "` for `" << nameAttr.getValue() << "` (expected iN/!pyc.clock/!pyc.reset)";
            ok = false;
          }
        }
      }

      if (structMetrics) {
        llvm::Expected<llvm::json::Value> parsed = llvm::json::parse(structMetrics.getValue());
        if (!parsed) {
          f.emitError() << "[PYC953] invalid JSON in `pyc.struct.metrics`";
          ok = false;
        } else {
          auto *obj = parsed->getAsObject();
          if (!obj) {
            f.emitError() << "[PYC954] `pyc.struct.metrics` must encode a JSON object";
            ok = false;
          } else {
            (void)jsonIntFieldNonNegative(f, *obj, "source_loc", "PYC955", ok);
            (void)jsonIntFieldNonNegative(f, *obj, "ast_node_count", "PYC956", ok);
            (void)jsonIntFieldNonNegative(f, *obj, "hardware_call_count", "PYC957", ok);
            (void)jsonIntFieldNonNegative(f, *obj, "loop_count", "PYC958", ok);
            (void)jsonIntFieldNonNegative(f, *obj, "module_call_count", "PYC959", ok);
            (void)jsonIntFieldNonNegative(f, *obj, "state_call_count", "PYC960", ok);
            (void)jsonIntFieldNonNegative(f, *obj, "estimated_inline_cost", "PYC961", ok);
            (void)jsonIntFieldNonNegative(f, *obj, "instance_count", "PYC962", ok);
            (void)jsonIntFieldNonNegative(f, *obj, "state_alloc_count", "PYC963", ok);
            (void)jsonIntFieldNonNegative(f, *obj, "collection_count", "PYC964", ok);
            (void)jsonIntFieldNonNegative(f, *obj, "collection_instance_count", "PYC965", ok);
            (void)jsonIntFieldNonNegative(f, *obj, "module_family_collection_count", "PYC966", ok);

            auto clusterIt = obj->find("repeated_body_clusters");
            if (clusterIt == obj->end() || !clusterIt->second.getAsArray()) {
              f.emitError() << "[PYC967] `pyc.struct.metrics` missing `repeated_body_clusters` array";
              ok = false;
            } else {
              auto *arr = clusterIt->second.getAsArray();
              for (std::size_t idx = 0; idx < arr->size(); ++idx) {
                auto *entry = (*arr)[idx].getAsObject();
                if (!entry) {
                  f.emitError() << "[PYC968] `pyc.struct.metrics.repeated_body_clusters[" << idx
                                << "]` must be an object";
                  ok = false;
                  continue;
                }
                auto fp = entry->getString("fingerprint");
                if (!fp || fp->empty()) {
                  f.emitError() << "[PYC969] repeated-body cluster #" << idx
                                << " must provide a non-empty `fingerprint`";
                  ok = false;
                }
                (void)jsonIntFieldNonNegative(f, *entry, "count", "PYC970", ok);
                (void)jsonIntFieldNonNegative(f, *entry, "node_count", "PYC971", ok);
                (void)jsonIntFieldNonNegative(f, *entry, "hardware_calls", "PYC972", ok);
                (void)jsonIntFieldNonNegative(f, *entry, "module_calls", "PYC973", ok);
                (void)jsonIntFieldNonNegative(f, *entry, "state_calls", "PYC974", ok);
                (void)jsonIntFieldNonNegative(f, *entry, "loop_extent_hint", "PYC975", ok);
              }
            }
          }
        }
      }

      if (structCollections) {
        llvm::Expected<llvm::json::Value> parsed = llvm::json::parse(structCollections.getValue());
        if (!parsed) {
          f.emitError() << "[PYC976] invalid JSON in `pyc.struct.collections`";
          ok = false;
        } else {
          auto *arr = parsed->getAsArray();
          if (!arr) {
            f.emitError() << "[PYC977] `pyc.struct.collections` must encode a JSON array";
            ok = false;
          } else {
            for (std::size_t idx = 0; idx < arr->size(); ++idx) {
              auto *entry = (*arr)[idx].getAsObject();
              if (!entry) {
                f.emitError() << "[PYC978] `pyc.struct.collections[" << idx << "]` must be an object";
                ok = false;
                continue;
              }
              auto kindStr = entry->getString("collection_kind");
              if (!kindStr || kindStr->empty()) {
                f.emitError() << "[PYC979] structural collection #" << idx
                              << " must provide `collection_kind`";
                ok = false;
              }
              (void)jsonIntFieldNonNegative(f, *entry, "key_count", "PYC980", ok);
              auto familyFlag = entry->getBoolean("from_module_family");
              if (!familyFlag.has_value()) {
                f.emitError() << "[PYC981] structural collection #" << idx
                              << " must provide boolean `from_module_family`";
                ok = false;
              }
            }
          }
        }
      }

      // Decision 0142 / TODO B1: validate the standardized commit/retire bundle
      // interface when declared. Semantics are enforced here (gate-first): the
      // frontend only records `pyc.commit_iface`; the MLIR verifier owns the
      // required-field and validity-gating (Decision 0146) contract.
      if (Attribute rawCommit = f->getAttr("pyc.commit_iface")) {
        auto commitDict = dyn_cast<DictionaryAttr>(rawCommit);
        if (!commitDict) {
          f.emitError() << "[PYC1001] `pyc.commit_iface` must be a dictionary attribute";
          ok = false;
        } else {
          auto schema = commitDict.getAs<StringAttr>("schema");
          if (!schema || schema.getValue().empty()) {
            f.emitError() << "[PYC1002] `pyc.commit_iface` missing non-empty string `schema`";
            ok = false;
          }
          auto fieldsDict = commitDict.getAs<DictionaryAttr>("fields");
          if (!fieldsDict || fieldsDict.empty()) {
            f.emitError() << "[PYC1003] `pyc.commit_iface` missing non-empty `fields` dictionary";
            ok = false;
          } else {
            llvm::StringSet<> resultSet;
            if (resultNames) {
              for (Attribute a : resultNames)
                if (auto s = dyn_cast<StringAttr>(a))
                  resultSet.insert(s.getValue());
            }
            llvm::StringSet<> seenPorts;
            llvm::StringSet<> fieldNames;
            for (const NamedAttribute &na : fieldsDict) {
              llvm::StringRef fname = na.getName().getValue();
              auto portAttr = dyn_cast<StringAttr>(na.getValue());
              if (!portAttr) {
                f.emitError() << "[PYC1005] `pyc.commit_iface` field `" << fname
                              << "` must map to a string port name";
                ok = false;
                continue;
              }
              fieldNames.insert(fname);
              if (!resultSet.contains(portAttr.getValue())) {
                f.emitError() << "[PYC1005] `pyc.commit_iface` field `" << fname
                              << "` maps to port `" << portAttr.getValue()
                              << "` which is not a declared result port";
                ok = false;
              }
              if (!seenPorts.insert(portAttr.getValue()).second) {
                f.emitError() << "[PYC1007] `pyc.commit_iface` duplicate port path `"
                              << portAttr.getValue() << "`";
                ok = false;
              }
            }
            // Data-driven required fields. The framework mandates none of its
            // own: the required set is supplied by the declaring design/schema
            // (e.g. a LinxCore profile), keeping PyCircuit schema-agnostic.
            if (auto reqArr = commitDict.getAs<ArrayAttr>("required")) {
              for (Attribute a : reqArr) {
                auto rs = dyn_cast<StringAttr>(a);
                if (!rs) {
                  f.emitError() << "[PYC1008] `pyc.commit_iface.required` entries must be strings";
                  ok = false;
                  continue;
                }
                if (!fieldNames.contains(rs.getValue())) {
                  f.emitError() << "[PYC1004] `pyc.commit_iface` missing required field `"
                                << rs.getValue() << "`";
                  ok = false;
                }
              }
            }
            // Data-driven validity gating (Decision 0146), executed by a generic
            // engine: for each declared group, if any member data field is
            // present its `valid` strobe must be present too. Group names and
            // membership are caller data -- the framework knows no `wb`/`mem`.
            if (auto grpDict = commitDict.getAs<DictionaryAttr>("groups")) {
              for (const NamedAttribute &g : grpDict) {
                llvm::StringRef gname = g.getName().getValue();
                auto gspec = dyn_cast<DictionaryAttr>(g.getValue());
                if (!gspec) {
                  f.emitError() << "[PYC1009] `pyc.commit_iface` group `" << gname
                                << "` must be a dictionary";
                  ok = false;
                  continue;
                }
                auto validName = gspec.getAs<StringAttr>("valid");
                auto members = gspec.getAs<ArrayAttr>("members");
                if (!validName || validName.getValue().empty() || !members) {
                  f.emitError() << "[PYC1009] `pyc.commit_iface` group `" << gname
                                << "` must provide string `valid` and array `members`";
                  ok = false;
                  continue;
                }
                bool anyMember = false;
                for (Attribute mv : members) {
                  auto ms = dyn_cast<StringAttr>(mv);
                  if (!ms) {
                    f.emitError() << "[PYC1009] `pyc.commit_iface` group `" << gname
                                  << "` members must be strings";
                    ok = false;
                    continue;
                  }
                  if (fieldNames.contains(ms.getValue()))
                    anyMember = true;
                }
                if (anyMember && !fieldNames.contains(validName.getValue())) {
                  f.emitError() << "[PYC1006] `pyc.commit_iface` group `" << gname
                                << "` has member fields but is missing its validity strobe `"
                                << validName.getValue() << "` (Decision 0146 validity gating)";
                  ok = false;
                }
              }
            }
          }
        }
      }
    });

    if (!ok)
      signalPassFailure();
  }
};

} // namespace

std::unique_ptr<::mlir::Pass> createCheckFrontendContractPass() {
  return std::make_unique<CheckFrontendContractPass>();
}

static PassRegistration<CheckFrontendContractPass> pass;

} // namespace pyc
