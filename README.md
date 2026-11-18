# BTC Race 24h — Dip-Buy Mean-Reversion Bot

[![make.fun · btc-race-24h-20261118-1444](https://make.fun/badge/btc-race-24h-20261118-1444.svg)](https://make.fun/bounty/btc-race-24h-20261118-1444?utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261118-1444)
[![rank](https://make.fun/badge/btc-race-24h-20261118-1444/jojosinkonstanor-collab/makefun-btc-race-24h-20261118-1444/rank.svg)](https://make.fun/bounty/btc-race-24h-20261118-1444/leaderboard?utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261118-1444)
[![reward](https://make.fun/badge/btc-race-24h-20261118-1444/jojosinkonstanor-collab/makefun-btc-race-24h-20261118-1444/reward.svg)](https://make.fun/bounty/btc-race-24h-20261118-1444?utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261118-1444)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Entry for make.fun **"Race a BTC trading bot live for 24 hours"**
(`btc-race-24h-20261118-1444`, harness `bars-replay/v1`, $250 prize pool).
Paper-trading only: the bot trades cbBTC/USDC on a replay of on-chain Orca
Whirlpool prices. No real money, no exchange connection, no secrets.

**Status: entry built, audited and verified locally — see
`analysis/AUDIT-v1.md`. Submitted as entry #1 (see submission details in
the repo root `SUBMISSION.md` after submit).**

## Strategy

Long-only dip-buy mean reversion, one position, full size ($1,000 start).

- **Entry.** Buy with all cash when the 1-minute close falls **115 bps below
  the 120-bar simple moving average** of closes. No bounce requirement and
  no trend filter — both tested worse on this window (bounce = 0 trades;
  trend filters cut the one good trade).
- **Exits.** Take-profit **+230 bps** vs cost, hard stop-loss **−120 bps**,
  time stop after **720 bars (12 h)**. A trailing stop was tested and
  rejected (whipsaws); an exit-at-the-mean was tested and rejected (exits
  far too early, +$1.96 vs +$22.35). After any exit, no cooldown — re-entry
  is allowed immediately if another qualifying dip appears.
- **Risk.** Kill switch: if equity falls 400 bps below its peak, stop trading
  for the rest of the window. No new entries in the last 90 bars (a late
  entry cannot earn back its round-trip cost before the forced flatten).
  Max slippage 100 bps per order vs the close it was decided on.
- **Determinism.** Integer arithmetic throughout; no clock, no environment,
  no randomness. Two runs produce byte-identical reply logs (verified).

Round-trip cost at the dev window's pool depth is ~30 bps (4 bps fee each
way, 2 bps slippage each way, ~10 bps pool impact each way on a full-size
trade), so every entry must catch a move clearly larger than that.

### Parameters (`main.py`, `PARAMS`)

| Parameter | Value | Meaning |
|---|---|---|
| `ref_mode` / `ref_len` | `sma` / 120 | dip measured vs 120-bar SMA of closes |
| `dip_bps` | 115 | entry dip below the SMA |
| `bounce` / `trend` | 0 / `none` | no bounce wait, no trend filter |
| `tp_bps` / `sl_bps` | 230 / 120 | take-profit / stop-loss vs cost |
| `trail_arm_bps` | 100000 | trailing stop disabled |
| `max_hold` | 720 | time stop (bars) |
| `cooldown` / `no_tail` | 0 / 90 | bars after exit / at end with no entries |
| `kill_bps` | 400 | equity drawdown from peak that halts trading |
| `max_slip_bps` | 100 | max slippage vs decision close |

These are the centre of a flat plateau, not the single best dev point:
across dip 100–120 and TP 200–240 the dev PnL stays +$19–23, so the bot
does not depend on one lucky parameter set.

## Measured results (dev window)

Dev window: 1,440 one-minute bars, 2026-11-17 14:45 UTC → 2026-11-18 14:44 UTC
(CBBTC $65,074 → $63,261, −279 bps buy-and-hold — a falling day). All numbers
measured with `tools/engine.py`, a local port of the kit harness whose fill
math reproduces the official `grade-local` exactly (see Verification).

| Metric | Final bot |
|---|---|
| Net PnL | **+$22.35 (+223.5 bps)** |
| Trades / round trips / win rate | 2 / 1 / 100% |
| Round trip | 16:57→23:11 UTC +$22.35 |
| Max drawdown | 0.65% (guardrail: ≤25%) |
| Fees paid | $0.81 |
| Rejected orders | 0 |
| Dev gate (PnL > 0) | **pass (+$22.35 margin)** |

Robustness (same bot, no retuning):

| Check | Result |
|---|---|
| First half (14:45–02:45 UTC) | +$22.35 |
| Second half (02:45–14:44 UTC) | $0.00 (no qualifying dip; slow-bleed half) |
| Rolling 12 h windows (13) | worst $0.00, median $0.00 (never loses) |
| Block-bootstrap, 24 resampled 24 h windows | median +$13.04, 10th pct −$1.91, losing 4/24 |

Context on the same window (same engine): yesterday's race bot
(sma60/dip80/TP150) **−$15.66** (its one trade hit the stop-loss — proof the
old parameters do NOT transfer); momentum baseline −$23.87; buy-and-hold
−$27.90; hold-cash $0.00. Public dev board: top 7 entries (+$86–92) are
"DP schedule" bots that fit an optimal tranche schedule to the known dev
window to game the gate (350+ trades); the best honest multi-trade bot is
+$62.90 (20 trades). Our +$22.35 is one honest trade — dev rank does not
decide the prize; the race window does.

## How to run

```bash
# 1. Fetch the kit (not part of this repo) into ./btc-race-24h-20261118-1444-kit
#    curl -sSL "https://make.fun/c/btc-race-24h-20261118-1444/kit" | tar xz
# 2. Replay the bot on the dev window (in-process, fast):
python3 - <<'EOF'
import sys
sys.path.insert(0, 'tools')
from engine import load_bars, Replay
bars = load_bars('btc-race-24h-20261118-1444-kit/dataset/bars/CBBTC.ndjson.gz')
m = Replay(bars).run_subprocess([sys.executable, '-P', '-s', 'main.py'])
print(m['net_pnl_usdc'], m['trades'], m['max_drawdown'])
EOF
# 3. Parameter sweep + robustness checks:
python3 tools/sweep.py 1   # entry sweep
python3 tools/sweep.py 2   # entry x exits sweep
python3 tools/sweep.py 3   # deep-dip sweep
python3 tools/sweep.py 4   # trailing-stop sweep
```

The official `grade-local --driver process` runs Node bots only; a Python
bot needs `--driver docker`. There is no Docker in this environment, so the
local engine above is the verification path (cross-checked against the
official CLI on the kit's own baselines — identical to the micro-USDC).

## Verification

`tools/engine.py` is an integer-exact port of the kit harness
(`sim/bars/engine.ts`): same fill math, same decision-check order, same
flatten-at-close. Cross-checked against the official CLI on this kit's own
baselines — identical to the micro-USDC:

| Baseline | Official `grade-local` | Local engine |
|---|---|---|
| momentum | −23.865251, 1 trade, maxDD 0.032416353, fees 0.790611 | −$23.87, 1 trade, maxDD 3.24%, fees $0.79 |

The final bot was also run twice as a real subprocess over stdin/stdout:
identical reply logs (sha256 `43bb2ef3…`), exit code 0, no rejections, no
crashes.

## Files

- `main.py` — the entrypoint the grader runs (`python3 -P -s /bot/main.py`).
- `vendor/makefun_bars.py` — byte-identical copy of the kit's Python SDK shim.
- `tools/engine.py` — local replay engine + subprocess driver.
- `tools/sweep.py` — parameter sweeps and robustness checks.
- `analysis/AUDIT-v1.md` — pre-submit audit report.
- `LICENSE` — MIT.

## Alternate variant (candidate for a later entry, NOT submitted yet)

Up to 10 entries per entrant are allowed. This genuinely different bot
(different action log) diversifies the race-day bet; it was measured but
deliberately NOT submitted with entry #1.

- **B — fast scalper:** `ref_len=60`, `dip_bps=60`, `tp_bps=120`,
  `sl_bps=60`, `max_hold=240`, `cooldown=5`. Dev +$13.40, 3 round trips,
  6 trades, maxDD 1.02%, bootstrap median +$9.04 but 10th pct −$40.61,
  losing 9/24 — trades more often (better guardrail coverage on a quiet
  race day) but with a much worse left tail than the main bot.
