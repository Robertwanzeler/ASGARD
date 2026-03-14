#include <chrono>
#include <csignal>
#include <cstdint>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <random>
#include <string>
#include <thread>

using SteadyClock = std::chrono::steady_clock;

static volatile std::sig_atomic_t g_stop = 0;
void on_sigint(int) { g_stop = 1; }

struct KpmIndication {
    uint64_t t_ms = 0;
    double prb_util = 0.0;
    double dl_mbps = 0.0;
    double ul_mbps = 0.0;
    double rlc_buf_kb = 0.0;
    double ue_count = 0.0;
};

struct ControlAction {
    uint64_t t_ms = 0;
    std::string action;
    double severity = 0.0;
    std::string reason;
};

class FakeE2Node {
public:
    explicit FakeE2Node(uint32_t seed = 7)
        : rng_(seed),
          noise_(0.0, 1.0),
          drift_(0.0, 0.01),
          ue_dist_(10, 80) {}

    KpmIndication make_kpm(uint64_t t_ms) {
        if (t_ms > 5000) {
            prb_ += 0.07 + 0.01 * noise_(rng_);
            rlc_buf_ += 55.0 + 10.0 * noise_(rng_);
        } else {
            prb_ += drift_(rng_) + 0.02 * noise_(rng_);
            rlc_buf_ += -12.0 + 6.0 * noise_(rng_);
        }

        prb_ = clamp01(prb_);
        if (rlc_buf_ < 0) rlc_buf_ = 0;

        double base_dl = 120.0 * (1.0 - 0.90 * prb_);
        double base_ul = 40.0 * (1.0 - 0.75 * prb_);

        KpmIndication k;
        k.t_ms = t_ms;
        k.prb_util = prb_;
        k.dl_mbps = std::max(0.0, base_dl + 6.0 * noise_(rng_));
        k.ul_mbps = std::max(0.0, base_ul + 3.0 * noise_(rng_));
        k.rlc_buf_kb = rlc_buf_;
        k.ue_count = static_cast<double>(ue_dist_(rng_));
        return k;
    }

private:
    static double clamp01(double x) {
        if (x < 0) return 0;
        if (x > 1) return 1;
        return x;
    }

    std::mt19937 rng_;
    std::normal_distribution<double> noise_;
    std::normal_distribution<double> drift_;
    std::uniform_int_distribution<int> ue_dist_;

    double prb_ = 0.55;
    double rlc_buf_ = 120.0;
};

class SimplePolicy {
public:
    ControlAction decide(const KpmIndication& k) {
        ControlAction a;
        a.t_ms = k.t_ms;

        if (k.prb_util > 0.70 && k.rlc_buf_kb > 100.0) {
            a.action = "THROTTLE";
            a.severity = severity_from(k);
            a.reason = "Congestion: PRB high and RLC buffer high";
            return a;
        }

        if (k.prb_util < 0.35 && k.ue_count > 50) {
            a.action = "BOOST";
            a.severity = 0.4;
            a.reason = "Underutilized: low PRB with many UEs";
            return a;
        }

        a.action = "NOOP";
        a.severity = 0.0;
        a.reason = "Within thresholds";
        return a;
    }

private:
    static double severity_from(const KpmIndication& k) {
        double s_prb = (k.prb_util - 0.70) / 0.30;
        if (s_prb < 0) s_prb = 0;
        if (s_prb > 1) s_prb = 1;

        double s_buf = (k.rlc_buf_kb - 100.0) / 500.0;
        if (s_buf < 0) s_buf = 0;
        if (s_buf > 1) s_buf = 1;

        double s_dl = 0.0;
        if (k.dl_mbps < 60.0) {
            s_dl = (60.0 - k.dl_mbps) / 60.0;
            if (s_dl > 1) s_dl = 1;
        }

        double s = (s_prb + s_buf + s_dl) / 3.0;
        if (s < 0) s = 0;
        if (s > 1) s = 1;
        return s;
    }
};

class FakeE2Client {
public:
    void send_control(const ControlAction& a) {
        if (a.action != "NOOP") {
            std::cout << "[E2CTL] t=" << a.t_ms
                      << " action=" << a.action
                      << " severity=" << std::fixed << std::setprecision(2)
                      << a.severity
                      << " reason=" << a.reason << "\n";
        }
    }
};

static uint64_t ms_since(const SteadyClock::time_point& t0) {
    return (uint64_t)std::chrono::duration_cast<std::chrono::milliseconds>(
        SteadyClock::now() - t0).count();
}

int main(int argc, char** argv) {
    std::signal(SIGINT, on_sigint);

    int period_ms = 200;
    int runtime_s = 15;
    std::string csv_path = "demo.csv";

    if (argc >= 2) period_ms = std::stoi(argv[1]);
    if (argc >= 3) runtime_s = std::stoi(argv[2]);
    if (argc >= 4) csv_path = argv[3];

    std::cout << "xApp Simulator (with congestion after 5s)\n"
              << "Period: " << period_ms << " ms\n"
              << "Runtime: " << runtime_s << " s\n"
              << "CSV: " << csv_path << "\n\n";

    std::ofstream csv(csv_path);
    csv << "t_ms,prb_util,dl_mbps,ul_mbps,rlc_buf_kb,ue_count,action,severity,reason\n";

    FakeE2Node node;
    SimplePolicy policy;
    FakeE2Client e2;

    auto t0 = SteadyClock::now();
    uint64_t end_ms = (uint64_t)runtime_s * 1000;

    while (!g_stop) {
        uint64_t t_ms = ms_since(t0);
        if (t_ms >= end_ms) break;

        KpmIndication k = node.make_kpm(t_ms);
        ControlAction a = policy.decide(k);

        e2.send_control(a);

        std::cout << "[KPM] t=" << k.t_ms
                  << " prb=" << std::fixed << std::setprecision(2) << k.prb_util
                  << " dl=" << k.dl_mbps
                  << " ul=" << k.ul_mbps
                  << " rlc_buf=" << k.rlc_buf_kb
                  << " ue=" << k.ue_count
                  << " -> " << a.action << "\n";

        csv << k.t_ms << ","
            << k.prb_util << ","
            << k.dl_mbps << ","
            << k.ul_mbps << ","
            << k.rlc_buf_kb << ","
            << k.ue_count << ","
            << a.action << ","
            << a.severity << ","
            << "\"" << a.reason << "\"\n";

        std::this_thread::sleep_for(std::chrono::milliseconds(period_ms));
    }

    std::cout << "\nSimulation finished.\n";
    return 0;
}