// Runs one Reamer Research backtest through the kit's C++ header
// (cpp/include/reamer_research.hpp, 4.10.0 or later) and prints a single JSON
// result line to stdout. Compiled counterpart to reamer_runner.py: same .bin
// dataset, same minimal and 20-bar Donchian workloads, same target-position
// logic.
//
// Usage: reamer_cpp_runner <bin_path> <minimal|realistic>
//
// Build, from this directory, with KIT set to the unpacked kit:
//   c++ -std=c++20 -O2 reamer_cpp_runner.cpp -I"$KIT/include" -L"$KIT/lib"
//       -lreamer_research -Wl,-rpath,"$KIT/lib" -o reamer_cpp_runner
// (one command, split here for width)
// (CMake's find_package(ReamerResearch 4) and pkg-config reamer-research also
// work; see the kit's cpp/README.md.)
//
// Without a licence the engine runs in Evaluation Mode (one ticker, at most
// 30 days of bars or 50,000 bars) and the published tiers stop with an
// evaluation-limit error; a licence removes the limits.
//
// Updated 2026-11-20 for the 4.10+ header. The published results were
// produced by the 4.6.0 version of this file at commit aa2eae4.

#include <reamer_research.hpp>

#include <sys/resource.h>

#include <algorithm>
#include <chrono>
#include <cstdio>
#include <cstring>
#include <fstream>
#include <optional>
#include <vector>

namespace rr = reamer_research;

namespace {

constexpr size_t kChannel = 20;
constexpr double kSize = 1000.0;

struct RawBar {  // 48-byte record written by build_dataset.py
  int64_t ts;
  double open, high, low, close, volume;
};

// 20-bar Donchian breakout over the 20 bars before the current one, moving
// the position to +/-1000 with one order sized to reverse.
struct Realistic {
  size_t lookback = 1;
  double highs[kChannel] = {}, lows[kChannel] = {};
  size_t seen = 0;
  double target = 0.0;

  void on_bar(rr::Data& d) {
    const double high = d.high().back(), low = d.low().back(), close = d.close().back();
    if (seen >= kChannel) {
      const double hi = *std::max_element(highs, highs + kChannel);
      const double lo = *std::min_element(lows, lows + kChannel);
      if (close > hi && target <= 0) target = kSize;
      else if (close < lo && target >= 0) target = -kSize;
      const double position = d.position();
      if (position < target) d.buy_market(target - position);
      else if (position > target) d.sell_market(position - target);
    }
    highs[seen % kChannel] = high;
    lows[seen % kChannel] = low;
    ++seen;
  }
};

struct Minimal {
  size_t lookback = 1;
  void on_bar(rr::Data& d) {
    volatile double c = d.close().back();
    (void)c;
  }
};

}  // namespace

int main(int argc, char* argv[]) {
  const auto t0 = std::chrono::steady_clock::now();
  if (argc != 3) {
    std::fprintf(stderr, "usage: %s <bin_path> <minimal|realistic>\n", argv[0]);
    return 2;
  }
  const bool realistic = std::strcmp(argv[2], "realistic") == 0;

  std::ifstream in(argv[1], std::ios::binary | std::ios::ate);
  if (!in) {
    std::fprintf(stderr, "cannot open %s\n", argv[1]);
    return 1;
  }
  const size_t n = static_cast<size_t>(in.tellg()) / sizeof(RawBar);
  std::vector<RawBar> raw(n);
  in.seekg(0);
  in.read(reinterpret_cast<char*>(raw.data()), n * sizeof(RawBar));

  rr::Bars bars(n);
  for (size_t i = 0; i < n; ++i) {
    bars[i].ts = raw[i].ts;
    bars[i].open = raw[i].open;
    bars[i].high = raw[i].high;
    bars[i].low = raw[i].low;
    bars[i].close = raw[i].close;
    bars[i].volume = raw[i].volume;
    bars[i].notional = 0.0;
    bars[i].tick_count = -1;
  }

  // Every cost is zero and the defaults match the published configuration:
  // round-trip commission mode, bid bars, seed 42, roll 5 days before month end.
  const rr::Options options{.initial_capital = 10'000'000.0, .leverage = 1.0, .rng_seed = 42};
  const rr::Universe universe{{"BENCH", std::move(bars)}};

  const auto t_run = std::chrono::steady_clock::now();
  std::optional<rr::Result> result;
  try {
    result.emplace(realistic ? rr::backtest(Realistic{}, universe, options)
                             : rr::backtest(Minimal{}, universe, options));
  } catch (const std::exception& e) {
    std::fprintf(stderr, "%s\n", e.what());
    return 1;
  }
  const auto t_end = std::chrono::steady_clock::now();
  const auto& summary = result->summary();

  rusage ru{};
  getrusage(RUSAGE_SELF, &ru);
  const double run_s = std::chrono::duration<double>(t_end - t_run).count();
  const double total_s =
      std::chrono::duration<double>(std::chrono::steady_clock::now() - t0).count();
  std::printf(
      "{\"engine\": \"reamer_research_cpp\", \"strategy\": \"%s\", \"bars\": %zu, "
      "\"run_s\": %.3f, \"total_s\": %.3f, \"fills\": %zu, \"round_trips\": %zu, "
      "\"net_pnl\": %.6f, \"peak_rss_mb\": %.1f}\n",
      argv[2], n, run_s, total_s, summary.filled_orders, summary.closed_trades_count,
      summary.net_pnl, ru.ru_maxrss / 1024.0);
  return 0;
}
