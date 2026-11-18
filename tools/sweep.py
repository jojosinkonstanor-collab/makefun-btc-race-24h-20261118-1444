"""Parameter sweep for the dip-buy bot, with robustness checks.

Scoring, for every candidate:
  * dev: full 1,440-bar dev window (the gate window)
  * half1 / half2: each 12 h half, run standalone
  * roll12: worst net PnL over the thirteen rolling 12 h windows
  * boot: median and 10th percentile over 24 block-bootstrapped 24 h windows
    (1 h blocks sampled with replacement, seeded), a harsher, unseen regime

Selection prefers a high worst-case / 10th percentile over the single best
dev number: the race window is a different day, so a plateau beats a spike.
"""

import itertools
import random
import sys

sys.path.insert(0, "tools")
sys.path.insert(0, ".")
from engine import load_bars, Replay  # noqa: E402
import main  # noqa: E402

BARS = load_bars(
    "btc-race-24h-20261118-1444-kit/dataset/bars/CBBTC.ndjson.gz")


def run_on(bars, params, window_bars=None):
    p = dict(params)
    if window_bars is not None:
        p["window_bars"] = window_bars
    decide = main.make_decide(p)
    return Replay(bars).run_inprocess(decide)


def halves(bars):
    return bars[:720], bars[720:]


def rolling_12h(bars):
    return [bars[s:s + 720] for s in range(0, 721, 60)]


def bootstrap_windows(bars, n=24, seed=12345):
    rng = random.Random(seed)
    blocks = [bars[i:i + 60] for i in range(0, 1440, 60)]
    out = []
    for _ in range(n):
        w = []
        for _ in range(24):
            w.extend(rng.choice(blocks))
        out.append(w)
    return out


BOOT = bootstrap_windows(BARS)
ROLL = rolling_12h(BARS)
H1, H2 = halves(BARS)


def evaluate(params, full=True):
    """Cheap screen: dev + halves. Returns (dev_pnl, h1, h2, trades, maxdd)."""
    d = run_on(BARS, params)
    a = run_on(H1, params, window_bars=720)
    b = run_on(H2, params, window_bars=720)
    return {"dev": d["net_pnl_usdc"], "h1": a["net_pnl_usdc"],
            "h2": b["net_pnl_usdc"], "trades": d["trades"],
            "maxdd": d["max_drawdown"], "fees": d["fees_usdc"],
            "win": d["win_rate"], "rt": d["round_trips"]}


def robustness(params):
    """Expensive check for finalists: rolling 12 h worst + bootstrap."""
    roll = [run_on(w, params, window_bars=720)["net_pnl_usdc"] for w in ROLL]
    boot = [run_on(w, params)["net_pnl_usdc"] for w in BOOT]
    boot.sort()
    return {"roll_worst": min(roll), "roll_med": sorted(roll)[len(roll) // 2],
            "boot_med": boot[len(boot) // 2],
            "boot_p10": boot[len(boot) // 10],
            "boot_losing": sum(1 for x in boot if x <= 0)}


def show(tag, params, ev, rb=None):
    line = (f"{tag} dev=${ev['dev']:+.2f} h1=${ev['h1']:+.2f} "
            f"h2=${ev['h2']:+.2f} trades={ev['trades']} "
            f"maxDD={ev['maxdd'] * 100:.2f}% win={ev['win']} rt={ev['rt']}")
    if rb:
        line += (f" | rollWorst=${rb['roll_worst']:+.2f} "
                 f"rollMed=${rb['roll_med']:+.2f} bootMed=${rb['boot_med']:+.2f} "
                 f"bootP10=${rb['boot_p10']:+.2f} bootLosing={rb['boot_losing']}/24")
    print(line, flush=True)
    return line


BASE = dict(main.PARAMS)


def stage1():
    """Sweep the entry; exits held at moderate values."""
    results = []
    for ref_mode, span, dip, bounce, trend in itertools.product(
            ["high", "sma"], [60, 120, 240], [40, 60, 80, 100, 130],
            [0, 1], ["none", "sma120", "sma360"]):
        p = dict(BASE)
        p["ref_mode"] = ref_mode
        if ref_mode == "high":
            p["high_len"] = span
        else:
            p["ref_len"] = span
        p["dip_bps"] = dip
        p["bounce"] = bounce
        p["trend"] = trend
        p["trend_len"] = 360 if trend == "sma360" else 120
        ev = evaluate(p)
        # screen: must clear the gate on the full window and on both halves
        score = min(ev["dev"], ev["h1"] + ev["h2"])  # halves sum ~ dev anyway
        results.append((ev["dev"] + min(ev["h1"], ev["h2"]), ev, p))
    results.sort(key=lambda r: r[0], reverse=True)
    print("=== stage 1: entry sweep (top 15 by dev + worst half) ===")
    for _, ev, p in results[:15]:
        show(f"{p['ref_mode']}{p.get('high_len') or p.get('ref_len')} "
             f"dip{p['dip_bps']} b{p['bounce']} {p['trend']}", p, ev)
    return results




def stage2():
    """Joint entry x exits sweep, trend off, bounce off (stage-1 winners)."""
    results = []
    for ref_len, dip, tp, sl, mh, cd, ta, tr in itertools.product(
            [30, 60, 120], [40, 60, 80, 100],
            [80, 120, 180], [60, 100, 150],
            [60, 120, 240], [0, 5],
            [100, 100000], [60]):
        p = dict(BASE)
        p.update({"ref_mode": "sma", "ref_len": ref_len, "dip_bps": dip,
                  "bounce": 0, "trend": "none", "tp_bps": tp, "sl_bps": sl,
                  "max_hold": mh, "cooldown": cd, "trail_arm_bps": ta,
                  "trail_bps": tr})
        ev = evaluate(p)
        results.append((ev["dev"] + min(ev["h1"], ev["h2"]), ev, p))
    results.sort(key=lambda r: r[0], reverse=True)
    print("=== stage 2: entry x exits (top 20) ===")
    for _, ev, p in results[:20]:
        show(f"sma{p['ref_len']} dip{p['dip_bps']} tp{p['tp_bps']} "
             f"sl{p['sl_bps']} mh{p['max_hold']} cd{p['cooldown']} "
             f"ta{p['trail_arm_bps']}", p, ev)
    return results




def stage3():
    """Deep-dip sweep: the day's money is in 2-3 deep dip episodes."""
    results = []
    for ref, dip, tp, sl, mh, em in itertools.product(
            [("high", 120), ("high", 240), ("sma", 60), ("sma", 300)],
            [80, 90, 100, 110],
            [150, 180, 220, 300],
            [60, 100],
            [180, 360, 720],
            [0, 1]):
        p = dict(BASE)
        mode, span = ref
        p["ref_mode"] = mode
        if mode == "high":
            p["high_len"] = span
            p["ref_len"] = 60
        else:
            p["ref_len"] = span
        p.update({"dip_bps": dip, "bounce": 0, "trend": "none",
                  "tp_bps": tp, "sl_bps": sl, "max_hold": mh,
                  "cooldown": 0, "trail_arm_bps": 100000,
                  "exit_mean": em})
        ev = evaluate(p)
        results.append((ev["dev"] + min(ev["h1"], ev["h2"]), ev, p))
    results.sort(key=lambda r: r[0], reverse=True)
    print("=== stage 3: deep dips (top 20) ===")
    for _, ev, p in results[:20]:
        ref = f"{p['ref_mode']}{p['high_len'] if p['ref_mode']=='high' else p['ref_len']}"
        show(f"{ref} dip{p['dip_bps']} tp{p['tp_bps']} sl{p['sl_bps']} "
             f"mh{p['max_hold']} em{p['exit_mean']}", p, ev)
    return results


    {"1": stage1, "2": stage2, "3": stage3, "4": stage4}[sys.argv[1] if len(sys.argv) > 1 else "1"]()


def stage4():
    """Ride the recovery: trailing stop instead of a fixed TP."""
    results = []
    for ref, dip, tp, sl, ta, tr, mh in itertools.product(
            [("sma", 60), ("sma", 300)],
            [80, 90, 100],
            [150, 300, 100000],
            [60, 80, 120],
            [80, 100, 150],
            [40, 60, 90],
            [360, 720]):
        p = dict(BASE)
        mode, span = ref
        p["ref_mode"] = mode
        p["ref_len"] = span
        p.update({"dip_bps": dip, "bounce": 0, "trend": "none",
                  "tp_bps": tp, "sl_bps": sl, "trail_arm_bps": ta,
                  "trail_bps": tr, "max_hold": mh, "cooldown": 0,
                  "exit_mean": 0})
        ev = evaluate(p)
        results.append((ev["dev"] + min(ev["h1"], ev["h2"]), ev, p))
    results.sort(key=lambda r: r[0], reverse=True)
    print("=== stage 4: trailing riders (top 20) ===")
    for _, ev, p in results[:20]:
        show(f"sma{p['ref_len']} dip{p['dip_bps']} tp{p['tp_bps']} "
             f"sl{p['sl_bps']} arm{p['trail_arm_bps']} tr{p['trail_bps']} "
             f"mh{p['max_hold']}", p, ev)
    return results


if __name__ == "__main__":
    import sys
    {"1": stage1, "2": stage2, "3": stage3, "4": stage4}[
        sys.argv[1] if len(sys.argv) > 1 else "1"]()
