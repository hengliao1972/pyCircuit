#include "pyc/Transforms/Passes.h"
#include "pyc/Transforms/CombDepGraph.h"

#include "pyc/Dialect/PYC/PYCOps.h"
#include "pyc/Dialect/PYC/PYCTypes.h"

#include "mlir/Dialect/Arith/IR/Arith.h"
#include "mlir/Dialect/Func/IR/FuncOps.h"
#include "mlir/IR/BuiltinOps.h"
#include "mlir/IR/SymbolTable.h"
#include "mlir/Pass/Pass.h"
#include "llvm/ADT/BitVector.h"
#include "llvm/ADT/DenseMap.h"
#include "llvm/ADT/DenseSet.h"
#include "llvm/ADT/SmallVector.h"
#include "llvm/ADT/Twine.h"

#include <string>

using namespace mlir;

namespace pyc {
namespace {

static bool isClockType(Type t) { return isa<pyc::ClockType>(t); }
static bool isResetType(Type t) { return isa<pyc::ResetType>(t); }
static bool isIntType(Type t) { return isa<IntegerType>(t); }

static bool isSequentialCut(Operation *op) {
  return isa<pyc::RegOp, pyc::DelayLineOp, pyc::FifoOp, pyc::ByteMemOp, pyc::SyncMemOp,
             pyc::SyncMemDPOp, pyc::AsyncFifoOp, pyc::CdcSyncOp>(op);
}

struct FuncClockSummary {
  unsigned numArgs = 0;
  unsigned numResults = 0;

  // Argument numbers (in the callee signature) that are ClockType.
  llvm::SmallVector<unsigned> clockArgNums{};

  // For each result: bitvector over `clockArgNums` describing which clock
  // domains the value depends on through stateful cut points.
  llvm::SmallVector<llvm::BitVector> resultDomains{};

  // For each argument (by arg number): bitvector over `clockArgNums` describing
  // which clock domains sample values that depend on this argument through the
  // combinational graph.
  //
  // Used at call sites to reject passing a domain-specific value (derived from
  // some other clocked state) into a callee input that is sampled by a different
  // clock domain.
  llvm::SmallVector<llvm::BitVector> argSinkDomains{};
};

class ClockDomainCache {
public:
  ClockDomainCache(ModuleOp module, CombDepGraphCache &combCache) : module_(module), combCache_(combCache) {}

  bool hadError() const { return hadError_; }
  void setError() { hadError_ = true; }

  const FuncClockSummary *getFuncSummary(func::FuncOp func) {
    if (!func)
      return nullptr;

    Operation *key = func.getOperation();
    if (auto it = cache_.find(key); it != cache_.end())
      return it->second.get();

    if (func.isDeclaration() || func.getBody().empty())
      return nullptr;

    if (!inProgress_.insert(key).second) {
      func.emitError("recursive instance graph detected while checking clock domain legality");
      hadError_ = true;
      return nullptr;
    }

    auto summary = std::make_unique<FuncClockSummary>();
    summary->numArgs = static_cast<unsigned>(func.getNumArguments());
    summary->numResults = static_cast<unsigned>(func.getNumResults());

    for (auto [i, arg] : llvm::enumerate(func.getArguments())) {
      if (isClockType(arg.getType()))
        summary->clockArgNums.push_back(static_cast<unsigned>(i));
    }

    const unsigned numClks = static_cast<unsigned>(summary->clockArgNums.size());
    summary->argSinkDomains.resize(summary->numArgs);
    for (unsigned i = 0; i < summary->numArgs; ++i)
      summary->argSinkDomains[i].resize(numClks, false);
    summary->resultDomains.resize(summary->numResults);
    for (unsigned i = 0; i < summary->numResults; ++i)
      summary->resultDomains[i].resize(numClks, false);

    llvm::DenseMap<Value, unsigned> clockBitByValue;
    for (unsigned b = 0; b < numClks; ++b) {
      unsigned argNum = summary->clockArgNums[b];
      if (argNum < func.getNumArguments())
        clockBitByValue.try_emplace(func.getArgument(argNum), b);
    }

    ArrayAttr argNamesAttr = func->getAttrOfType<ArrayAttr>("arg_names");
    auto clockLabel = [&](unsigned bit) -> std::string {
      if (bit >= summary->clockArgNums.size())
        return std::string("<clk?>");
      unsigned argNum = summary->clockArgNums[bit];
      if (argNamesAttr && argNum < argNamesAttr.size()) {
        if (auto s = dyn_cast<StringAttr>(argNamesAttr[argNum]))
          return s.getValue().str();
      }
      return (llvm::Twine("clk#") + llvm::Twine(argNum)).str();
    };

    auto formatClockSet = [&](const llvm::BitVector &dom) -> std::string {
      std::string out = "{";
      bool first = true;
      for (int i = dom.find_first(); i >= 0; i = dom.find_next(i)) {
        if (!first)
          out += ", ";
        first = false;
        out += clockLabel(static_cast<unsigned>(i));
      }
      out += "}";
      return out;
    };

    llvm::DenseMap<Value, llvm::SmallVector<Value>> wireDrivers;
    func.walk([&](pyc::AssignOp a) {
      Value dst = a.getDst();
      if (!dst || !dst.getDefiningOp<pyc::WireOp>())
        return;
      wireDrivers[dst].push_back(a.getSrc());
    });

    class Analyzer {
    public:
      Analyzer(ModuleOp module,
               func::FuncOp func,
               FuncClockSummary &sum,
               llvm::DenseMap<Value, unsigned> &clockBitByValue,
               llvm::DenseMap<Value, llvm::SmallVector<Value>> &wireDrivers,
               ClockDomainCache &clockCache,
               CombDepGraphCache &combCache)
          : module_(module), func_(func), sum_(sum), clockBitByValue_(clockBitByValue), wireDrivers_(wireDrivers),
            clockCache_(clockCache), combCache_(combCache) {}

      llvm::BitVector domain(Value v) {
        if (!v || !isIntType(v.getType()))
          return emptyDom();
        if (auto it = domMemo_.find(v); it != domMemo_.end())
          return it->second;
        if (!domVisiting_.insert(v).second)
          return emptyDom();

        llvm::BitVector out = emptyDom();

        if (auto barg = dyn_cast<BlockArgument>(v)) {
          (void)barg;
          domVisiting_.erase(v);
          domMemo_.try_emplace(v, out);
          return out;
        }

        Operation *def = v.getDefiningOp();
        if (!def) {
          domVisiting_.erase(v);
          domMemo_.try_emplace(v, out);
          return out;
        }

        // Sequential cut points define clock domains.
        if (auto r = dyn_cast<pyc::RegOp>(def)) {
          out = domOfClock(r.getClk());
          domVisiting_.erase(v);
          domMemo_.try_emplace(v, out);
          return out;
        }
        if (auto delay = dyn_cast<pyc::DelayLineOp>(def)) {
          out = domOfClock(delay.getClk());
          domVisiting_.erase(v);
          domMemo_.try_emplace(v, out);
          return out;
        }
        if (auto f = dyn_cast<pyc::FifoOp>(def)) {
          out = domOfClock(f.getClk());
          domVisiting_.erase(v);
          domMemo_.try_emplace(v, out);
          return out;
        }
        if (auto m = dyn_cast<pyc::SyncMemOp>(def)) {
          out = domOfClock(m.getClk());
          domVisiting_.erase(v);
          domMemo_.try_emplace(v, out);
          return out;
        }
        if (auto m = dyn_cast<pyc::SyncMemDPOp>(def)) {
          out = domOfClock(m.getClk());
          domVisiting_.erase(v);
          domMemo_.try_emplace(v, out);
          return out;
        }
        if (auto m = dyn_cast<pyc::ByteMemOp>(def)) {
          out = domOfClock(m.getClk());
          domVisiting_.erase(v);
          domMemo_.try_emplace(v, out);
          return out;
        }
        if (auto c = dyn_cast<pyc::CdcSyncOp>(def)) {
          out = domOfClock(c.getClk());
          domVisiting_.erase(v);
          domMemo_.try_emplace(v, out);
          return out;
        }
        if (auto a = dyn_cast<pyc::AsyncFifoOp>(def)) {
          unsigned resIdx = 0;
          if (auto r = dyn_cast<OpResult>(v))
            resIdx = r.getResultNumber();
          // Results: (in_ready, out_valid, out_data)
          if (resIdx == 0)
            out = domOfClock(a.getInClk());
          else
            out = domOfClock(a.getOutClk());
          domVisiting_.erase(v);
          domMemo_.try_emplace(v, out);
          return out;
        }

        if (isa<arith::ConstantOp, pyc::ConstantOp>(def)) {
          domVisiting_.erase(v);
          domMemo_.try_emplace(v, out);
          return out;
        }

        if (auto w = dyn_cast<pyc::WireOp>(def)) {
          (void)w;
          auto it = wireDrivers_.find(v);
          if (it != wireDrivers_.end()) {
            for (Value src : it->second)
              out |= domain(src);
          }
          domVisiting_.erase(v);
          domMemo_.try_emplace(v, out);
          return out;
        }

        if (auto a = dyn_cast<pyc::AliasOp>(def)) {
          out = domain(a.getIn());
          domVisiting_.erase(v);
          domMemo_.try_emplace(v, out);
          return out;
        }

        if (auto ra = dyn_cast<pyc::ResetActiveOp>(def)) {
          out = domain(ra.getRst());
          domVisiting_.erase(v);
          domMemo_.try_emplace(v, out);
          return out;
        }

        if (auto comb = dyn_cast<pyc::CombOp>(def)) {
          unsigned resIdx = 0;
          if (auto r = dyn_cast<OpResult>(v))
            resIdx = r.getResultNumber();
          out = domainOfCombResult(comb, resIdx);
          domVisiting_.erase(v);
          domMemo_.try_emplace(v, out);
          return out;
        }

        if (auto inst = dyn_cast<pyc::InstanceOp>(def)) {
          unsigned resIdx = 0;
          if (auto r = dyn_cast<OpResult>(v))
            resIdx = r.getResultNumber();
          out = domainOfInstanceResult(inst, resIdx);
          domVisiting_.erase(v);
          domMemo_.try_emplace(v, out);
          return out;
        }

        // Generic combinational op: union operand domains.
        for (Value opnd : def->getOperands()) {
          if (opnd && isIntType(opnd.getType()))
            out |= domain(opnd);
          if (opnd && isa<pyc::ResetType>(opnd.getType()))
            out |= domain(opnd);
        }

        domVisiting_.erase(v);
        domMemo_.try_emplace(v, out);
        return out;
      }

      llvm::BitVector argDeps(Value v) {
        if (!v || !isIntType(v.getType()))
          return emptyArgs();
        if (auto it = argMemo_.find(v); it != argMemo_.end())
          return it->second;
        if (!argVisiting_.insert(v).second)
          return emptyArgs();

        llvm::BitVector out = emptyArgs();

        if (auto barg = dyn_cast<BlockArgument>(v)) {
          Operation *parent = barg.getOwner() ? barg.getOwner()->getParentOp() : nullptr;
          if (auto f = dyn_cast_or_null<func::FuncOp>(parent)) {
            if (f == func_) {
              out.set(static_cast<unsigned>(barg.getArgNumber()));
              argVisiting_.erase(v);
              argMemo_.try_emplace(v, out);
              return out;
            }
          }

          if (auto comb = dyn_cast_or_null<pyc::CombOp>(parent)) {
            unsigned idx = static_cast<unsigned>(barg.getArgNumber());
            auto inputs = comb.getInputs();
            if (idx < inputs.size())
              out = argDeps(inputs[idx]);
            argVisiting_.erase(v);
            argMemo_.try_emplace(v, out);
            return out;
          }

          argVisiting_.erase(v);
          argMemo_.try_emplace(v, out);
          return out;
        }

        Operation *def = v.getDefiningOp();
        if (!def) {
          argVisiting_.erase(v);
          argMemo_.try_emplace(v, out);
          return out;
        }

        if (isSequentialCut(def)) {
          argVisiting_.erase(v);
          argMemo_.try_emplace(v, out);
          return out;
        }

        if (isa<arith::ConstantOp, pyc::ConstantOp>(def)) {
          argVisiting_.erase(v);
          argMemo_.try_emplace(v, out);
          return out;
        }

        if (auto w = dyn_cast<pyc::WireOp>(def)) {
          (void)w;
          auto it = wireDrivers_.find(v);
          if (it != wireDrivers_.end()) {
            for (Value src : it->second)
              out |= argDeps(src);
          }
          argVisiting_.erase(v);
          argMemo_.try_emplace(v, out);
          return out;
        }

        if (auto a = dyn_cast<pyc::AliasOp>(def)) {
          out = argDeps(a.getIn());
          argVisiting_.erase(v);
          argMemo_.try_emplace(v, out);
          return out;
        }

        if (auto ra = dyn_cast<pyc::ResetActiveOp>(def)) {
          out = argDeps(ra.getRst());
          argVisiting_.erase(v);
          argMemo_.try_emplace(v, out);
          return out;
        }

        if (auto comb = dyn_cast<pyc::CombOp>(def)) {
          unsigned resIdx = 0;
          if (auto r = dyn_cast<OpResult>(v))
            resIdx = r.getResultNumber();
          out = argDepsOfCombResult(comb, resIdx);
          argVisiting_.erase(v);
          argMemo_.try_emplace(v, out);
          return out;
        }

        if (auto inst = dyn_cast<pyc::InstanceOp>(def)) {
          unsigned resIdx = 0;
          if (auto r = dyn_cast<OpResult>(v))
            resIdx = r.getResultNumber();
          out = argDepsOfInstanceResult(inst, resIdx);
          argVisiting_.erase(v);
          argMemo_.try_emplace(v, out);
          return out;
        }

        for (Value opnd : def->getOperands()) {
          if (opnd && isIntType(opnd.getType()))
            out |= argDeps(opnd);
        }

        argVisiting_.erase(v);
        argMemo_.try_emplace(v, out);
        return out;
      }

    private:
      llvm::BitVector emptyDom() const {
        llvm::BitVector v;
        v.resize(static_cast<unsigned>(sum_.clockArgNums.size()), false);
        return v;
      }
      llvm::BitVector emptyArgs() const {
        llvm::BitVector v;
        v.resize(static_cast<unsigned>(sum_.numArgs), false);
        return v;
      }

      llvm::BitVector domOfClock(Value clk) {
        llvm::BitVector out = emptyDom();
        auto it = clockBitByValue_.find(clk);
        if (it == clockBitByValue_.end()) {
          // Clocks must be explicit function args (Decision 0126).
          func_.emitError("clock domain verifier: clock value is not a function clock argument");
          clockCache_.setError();
          return out;
        }
        out.set(it->second);
        return out;
      }

      llvm::BitVector domainOfCombResult(pyc::CombOp comb, unsigned resIdx) {
        llvm::BitVector out = emptyDom();
        if (comb.getBody().empty())
          return out;
        Block &b = comb.getBody().front();
        auto yield = dyn_cast_or_null<pyc::YieldOp>(b.getTerminator());
        if (!yield)
          return out;
        if (resIdx >= yield.getValues().size())
          return out;
        return domain(yield.getValues()[resIdx]);
      }

      llvm::BitVector domainOfInstanceResult(pyc::InstanceOp inst, unsigned resIdx) {
        llvm::BitVector out = emptyDom();
        auto calleeAttr = inst->getAttrOfType<FlatSymbolRefAttr>("callee");
        if (!calleeAttr) {
          inst.emitError("pyc.instance missing required `callee` attr");
          clockCache_.setError();
          return out;
        }
        auto sym = SymbolTable::lookupSymbolIn(module_, calleeAttr.getValue());
        auto callee = dyn_cast_or_null<func::FuncOp>(sym);
        if (!callee) {
          inst.emitError("pyc.instance callee is not a func.func symbol: ") << calleeAttr.getValue();
          clockCache_.setError();
          return out;
        }

        const FuncClockSummary *calleeSum = clockCache_.getFuncSummary(callee);
        if (!calleeSum)
          return out;
        if (resIdx >= calleeSum->resultDomains.size())
          return out;

        const llvm::BitVector &calleeDom = calleeSum->resultDomains[resIdx];
        auto inputs = inst.getInputs();
        for (int bit = calleeDom.find_first(); bit >= 0; bit = calleeDom.find_next(bit)) {
          unsigned clkBit = static_cast<unsigned>(bit);
          if (clkBit >= calleeSum->clockArgNums.size())
            continue;
          unsigned argNum = calleeSum->clockArgNums[clkBit];
          if (argNum >= inputs.size())
            continue;
          Value clk = inputs[argNum];
          auto it = clockBitByValue_.find(clk);
          if (it == clockBitByValue_.end()) {
            inst.emitError("clock domain verifier: instance clock operand is not a function clock argument");
            clockCache_.setError();
            continue;
          }
          out.set(it->second);
        }

        return out;
      }

      llvm::BitVector argDepsOfCombResult(pyc::CombOp comb, unsigned resIdx) {
        llvm::BitVector out = emptyArgs();
        if (comb.getBody().empty())
          return out;
        Block &b = comb.getBody().front();
        auto yield = dyn_cast_or_null<pyc::YieldOp>(b.getTerminator());
        if (!yield)
          return out;
        if (resIdx >= yield.getValues().size())
          return out;
        return argDeps(yield.getValues()[resIdx]);
      }

      llvm::BitVector argDepsOfInstanceResult(pyc::InstanceOp inst, unsigned resIdx) {
        llvm::BitVector out = emptyArgs();

        auto calleeAttr = inst->getAttrOfType<FlatSymbolRefAttr>("callee");
        if (!calleeAttr) {
          inst.emitError("pyc.instance missing required `callee` attr");
          clockCache_.setError();
          return out;
        }
        auto sym = SymbolTable::lookupSymbolIn(module_, calleeAttr.getValue());
        auto callee = dyn_cast_or_null<func::FuncOp>(sym);
        if (!callee) {
          inst.emitError("pyc.instance callee is not a func.func symbol: ") << calleeAttr.getValue();
          clockCache_.setError();
          return out;
        }

        const FuncCombSummary *combSum = combCache_.getFuncSummary(callee);
        if (!combSum || resIdx >= combSum->results.size())
          return out;

        const CombResultSummary &rs = combSum->results[resIdx];
        auto inputs = inst.getInputs();
        unsigned n = std::min<unsigned>(static_cast<unsigned>(inputs.size()), rs.argDeps.size());
        for (unsigned i = 0; i < n; ++i) {
          if (!rs.argDeps.test(i))
            continue;
          out |= argDeps(inputs[i]);
        }
        return out;
      }

      ModuleOp module_;
      func::FuncOp func_;
      FuncClockSummary &sum_;
      llvm::DenseMap<Value, unsigned> &clockBitByValue_;
      llvm::DenseMap<Value, llvm::SmallVector<Value>> &wireDrivers_;
      ClockDomainCache &clockCache_;
      CombDepGraphCache &combCache_;

      llvm::DenseMap<Value, llvm::BitVector> domMemo_;
      llvm::DenseSet<Value> domVisiting_;

      llvm::DenseMap<Value, llvm::BitVector> argMemo_;
      llvm::DenseSet<Value> argVisiting_;
    };

    Analyzer analyzer(module_, func, *summary, clockBitByValue, wireDrivers, *this, combCache_);

    auto checkSampled = [&](Operation *op, Value clk, ArrayRef<Value> sampled) {
      auto clkIt = clockBitByValue.find(clk);
      if (clkIt == clockBitByValue.end()) {
        op->emitError("clock domain verifier: sampled clock is not a function clock argument");
        hadError_ = true;
        return;
      }
      const unsigned clkBit = clkIt->second;

      for (Value v : sampled) {
        if (!v || !isIntType(v.getType()))
          continue;
        llvm::BitVector dom = analyzer.domain(v);
        llvm::BitVector bad = dom;
        if (clkBit < bad.size())
          bad.reset(clkBit);
        if (bad.any()) {
          op->emitError("clock-domain violation: sampled operand depends on ") << formatClockSet(dom)
                                                                              << " but is sampled by "
                                                                              << clockLabel(clkBit);
          hadError_ = true;
        }

        llvm::BitVector deps = analyzer.argDeps(v);
        for (int a = deps.find_first(); a >= 0; a = deps.find_next(a)) {
          unsigned argNum = static_cast<unsigned>(a);
          if (argNum >= summary->argSinkDomains.size())
            continue;
          if (isClockType(func.getArgument(argNum).getType()) || isResetType(func.getArgument(argNum).getType()))
            continue;
          summary->argSinkDomains[argNum].set(clkBit);
        }
      }
    };

    // Intra-function sampled-operand checks (no combinational cross-domain paths).
    func.walk([&](Operation *op) {
      if (auto r = dyn_cast<pyc::RegOp>(op)) {
        checkSampled(op, r.getClk(), {r.getEn(), r.getNext(), r.getInit()});
        return;
      }
      if (auto delay = dyn_cast<pyc::DelayLineOp>(op)) {
        checkSampled(op, delay.getClk(), {delay.getEn(), delay.getNext(), delay.getInit()});
        return;
      }
      if (auto f = dyn_cast<pyc::FifoOp>(op)) {
        checkSampled(op, f.getClk(), {f.getInValid(), f.getInData(), f.getOutReady()});
        return;
      }
      if (auto m = dyn_cast<pyc::SyncMemOp>(op)) {
        checkSampled(op, m.getClk(), {m.getRen(), m.getRaddr(), m.getWvalid(), m.getWaddr(), m.getWdata(), m.getWstrb()});
        return;
      }
      if (auto m = dyn_cast<pyc::SyncMemDPOp>(op)) {
        checkSampled(op,
                     m.getClk(),
                     {m.getRen0(), m.getRaddr0(), m.getRen1(), m.getRaddr1(), m.getWvalid(), m.getWaddr(), m.getWdata(), m.getWstrb()});
        return;
      }
      if (auto m = dyn_cast<pyc::ByteMemOp>(op)) {
        checkSampled(op, m.getClk(), {m.getRaddr(), m.getWvalid(), m.getWaddr(), m.getWdata(), m.getWstrb()});
        return;
      }
      if (auto a = dyn_cast<pyc::AsyncFifoOp>(op)) {
        checkSampled(op, a.getInClk(), {a.getInValid(), a.getInData()});
        checkSampled(op, a.getOutClk(), {a.getOutReady()});
        return;
      }
      // NOTE: cdc_sync is an explicit CDC primitive; its input may legally come
      // from another clock domain, so it is intentionally not checked here.
    });

    // Call-site checks: prevent passing a value derived from one domain into a
    // callee argument that is sampled by a different domain.
    func.walk([&](pyc::InstanceOp inst) {
      auto calleeAttr = inst->getAttrOfType<FlatSymbolRefAttr>("callee");
      if (!calleeAttr)
        return;
      auto sym = SymbolTable::lookupSymbolIn(module_, calleeAttr.getValue());
      auto callee = dyn_cast_or_null<func::FuncOp>(sym);
      if (!callee)
        return;

      const FuncClockSummary *calleeSum = getFuncSummary(callee);
      if (!calleeSum)
        return;

      auto inputs = inst.getInputs();
      // Ensure all callee clocks map to known caller clock args.
      for (unsigned cb = 0; cb < calleeSum->clockArgNums.size(); ++cb) {
        unsigned argNum = calleeSum->clockArgNums[cb];
        if (argNum >= inputs.size())
          continue;
        Value clk = inputs[argNum];
        if (!clockBitByValue.contains(clk)) {
          inst.emitError("clock domain verifier: instance clock operand is not a function clock argument");
          hadError_ = true;
        }
      }

      const unsigned argCount = std::min<unsigned>(calleeSum->numArgs, static_cast<unsigned>(inputs.size()));
      for (unsigned a = 0; a < argCount; ++a) {
        const llvm::BitVector &sinkDom = calleeSum->argSinkDomains[a];
        if (!sinkDom.any())
          continue;
        Value opnd = inputs[a];
        if (!opnd || !isIntType(opnd.getType()))
          continue;

        llvm::BitVector opndDom = analyzer.domain(opnd);
        if (!opndDom.any())
          continue;

        if (sinkDom.count() > 1) {
          inst.emitError("clock-domain violation: callee argument is sampled in multiple clock domains; "
                         "only domain-neutral values may be passed here");
          hadError_ = true;
          continue;
        }

        llvm::BitVector allowed;
        allowed.resize(numClks, false);
        for (int bit = sinkDom.find_first(); bit >= 0; bit = sinkDom.find_next(bit)) {
          unsigned cb = static_cast<unsigned>(bit);
          if (cb >= calleeSum->clockArgNums.size())
            continue;
          unsigned clkArgNum = calleeSum->clockArgNums[cb];
          if (clkArgNum >= inputs.size())
            continue;
          Value clk = inputs[clkArgNum];
          auto it = clockBitByValue.find(clk);
          if (it == clockBitByValue.end())
            continue;
          allowed.set(it->second);
        }

        llvm::BitVector bad = opndDom;
        llvm::BitVector allowedInv = allowed;
        allowedInv.flip();
        bad &= allowedInv;
        if (bad.any()) {
          inst.emitError("clock-domain violation: operand depends on ") << formatClockSet(opndDom)
                                                                        << " but callee samples this argument in "
                                                                        << formatClockSet(allowed);
          hadError_ = true;
        }
      }
    });

    // Result domains for callers.
    llvm::SmallVector<func::ReturnOp> returns;
    func.walk([&](func::ReturnOp r) { returns.push_back(r); });
    if (returns.size() != 1u) {
      func.emitError("expected exactly one func.return in a hardware module");
      hadError_ = true;
    } else {
      func::ReturnOp ret = returns.front();
      if (ret.getNumOperands() != summary->numResults) {
        ret.emitError("return arity mismatch for clock-domain summary: expected ")
            << summary->numResults << " values, got " << ret.getNumOperands();
        hadError_ = true;
      } else {
        for (unsigned i = 0; i < summary->numResults; ++i)
          summary->resultDomains[i] = analyzer.domain(ret.getOperand(i));
      }
    }

    const FuncClockSummary *out = summary.get();
    cache_.try_emplace(key, std::move(summary));
    inProgress_.erase(key);
    return out;
  }

private:
  ModuleOp module_;
  CombDepGraphCache &combCache_;
  llvm::DenseMap<Operation *, std::unique_ptr<FuncClockSummary>> cache_;
  llvm::DenseSet<Operation *> inProgress_;
  bool hadError_ = false;
};

struct CheckClockDomainsPass : public PassWrapper<CheckClockDomainsPass, OperationPass<ModuleOp>> {
  MLIR_DEFINE_EXPLICIT_INTERNAL_INLINE_TYPE_ID(CheckClockDomainsPass)

  StringRef getArgument() const override { return "pyc-check-clock-domains"; }
  StringRef getDescription() const override {
    return "Verify multi-clock/CDC legality (no combinational cross-domain paths; CDC only via explicit primitives)";
  }

  void runOnOperation() override {
    ModuleOp module = getOperation();
    CombDepGraphCache combCache(module);
    ClockDomainCache clockCache(module, combCache);

    for (func::FuncOp f : module.getOps<func::FuncOp>()) {
      // Only check hardware modules; other helper functions may exist.
      auto kind = f->getAttrOfType<StringAttr>("pyc.kind");
      if (!kind || kind.getValue() != "module")
        continue;
      (void)clockCache.getFuncSummary(f);
      if (clockCache.hadError())
        break;
    }

    if (clockCache.hadError())
      signalPassFailure();
  }
};

} // namespace

std::unique_ptr<::mlir::Pass> createCheckClockDomainsPass() { return std::make_unique<CheckClockDomainsPass>(); }

static PassRegistration<CheckClockDomainsPass> pass;

} // namespace pyc
