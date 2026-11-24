#pragma once

// Component-level dependency analysis used to decide whether a component can be
// updated in a single phase (sample + publish in one call) or must keep the
// two-phase sample/commit split. This file only computes the graph statistics;
// it never changes emission.

#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <vector>

namespace pyc {

// Tarjan SCC over a component-level adjacency list. Self loops come back as
// singletons and are treated by the caller as acyclic (a component reading its
// own previous value is fine: sample() reads before commit() writes).
inline std::vector<std::vector<unsigned>>
tarjanComponentSccs(unsigned nodeCount,
                    const std::vector<std::vector<unsigned>> &adj) {
  std::vector<int> index(nodeCount, -1);
  std::vector<int> low(nodeCount, 0);
  std::vector<char> onStack(nodeCount, 0);
  std::vector<unsigned> stack;
  std::vector<std::vector<unsigned>> result;
  int counter = 0;

  struct Frame {
    unsigned v;
    std::size_t next;
  };
  std::vector<Frame> frames;

  for (unsigned root = 0; root < nodeCount; ++root) {
    if (index[root] != -1)
      continue;
    index[root] = low[root] = counter++;
    stack.push_back(root);
    onStack[root] = 1;
    frames.push_back({root, 0});
    while (!frames.empty()) {
      const unsigned v = frames.back().v;
      if (frames.back().next < adj[v].size()) {
        const unsigned w = adj[v][frames.back().next++];
        if (index[w] == -1) {
          index[w] = low[w] = counter++;
          stack.push_back(w);
          onStack[w] = 1;
          frames.push_back({w, 0});
        } else if (onStack[w]) {
          low[v] = std::min(low[v], index[w]);
        }
        continue;
      }
      frames.pop_back();
      if (!frames.empty()) {
        const unsigned parent = frames.back().v;
        low[parent] = std::min(low[parent], low[v]);
      }
      if (low[v] == index[v]) {
        std::vector<unsigned> component;
        for (;;) {
          const unsigned w = stack.back();
          stack.pop_back();
          onStack[w] = 0;
          component.push_back(w);
          if (w == v)
            break;
        }
        result.push_back(std::move(component));
      }
    }
  }
  return result;
}

struct ComponentGraphStats {
  unsigned components = 0;
  unsigned edges = 0;
  unsigned cyclicGroups = 0;   // SCC with 2+ members
  unsigned cyclicMembers = 0;  // components that must stay two-phase
  unsigned largestGroup = 0;
  std::vector<std::vector<unsigned>> groups; // sorted by size, descending
};

inline ComponentGraphStats
analyzeComponentGraph(unsigned nodeCount,
                      const std::vector<std::vector<unsigned>> &adj) {
  ComponentGraphStats stats;
  stats.components = nodeCount;
  for (const auto &succ : adj)
    stats.edges += static_cast<unsigned>(succ.size());
  auto components = tarjanComponentSccs(nodeCount, adj);
  for (auto &component : components) {
    if (component.size() < 2)
      continue;
    ++stats.cyclicGroups;
    stats.cyclicMembers += static_cast<unsigned>(component.size());
    stats.largestGroup =
        std::max<unsigned>(stats.largestGroup, static_cast<unsigned>(component.size()));
    stats.groups.push_back(std::move(component));
  }
  std::sort(stats.groups.begin(), stats.groups.end(),
            [](const std::vector<unsigned> &a, const std::vector<unsigned> &b) {
              return a.size() > b.size();
            });
  return stats;
}

} // namespace pyc
