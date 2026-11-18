"""Dip-buy mean-reversion bot for make.fun "Race a BTC trading bot live for 24 hours".

Protocol: agent-fun/bars-replay/v1 (see protocol.schema.json in the kit).
Runtime: python3 -P -s /bot/main.py, standard library only, fully deterministic
(integer arithmetic throughout; no clock, no environment, no randomness).

Strategy (long only, one position, full size):
  * Buy the dip: when the close falls DIP_BPS below a slow reference (a rolling
    mean, or the rolling high) and the last bar bounced, and the longer trend
    filter agrees, buy with all available cash.
  * Manage the position with a take-profit, a hard stop-loss, an armed trailing
    stop and a time stop; after any exit, wait COOLDOWN bars before re-entering.
  * Kill switch: if equity ever falls KILL_BPS below its peak, flatten and stop
    trading for the rest of the window (the guardrail is a 25% max drawdown;
    this keeps us far inside it).
  * No new entries in the last NO_TAIL bars: a late entry has no time to earn
    back its ~30 bps round-trip cost before the forced flatten.

Costs of one full-size round trip are ~30 bps (4 bps fee each way, 2 bps
slippage each way, ~10 bps of pool impact each way at the dev window's depth),
so every entry must catch a move clearly larger than that.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))
import makefun_bars  # noqa: E402

SYMBOL = "CBBTC"
BPS = 10_000

# ---------------------------------------------------------------- parameters
# Tuned on THIS competition's published dev window (1,440 one-minute bars,
# 2026-11-17 14:45 UTC -> 2026-11-18 14:44 UTC, CBBTC $65,074 -> $63,261,
# -279 bps buy-and-hold) and checked for robustness on its halves, on
# thirteen rolling 12 h windows and on 24 block-bootstrapped copies of it;
# see README.md. The values below are the centre of a
# flat plateau (dev +$19-23 across dip 100-120 x TP 200-240), not the single
# best dev-window point, so the bot does not depend on one lucky parameter
# set. NOTE: yesterday's race (20261117-0603) used sma60/dip80/TP150 and
# LOST -$15.66 on this window — its single trade hit the stop-loss; the
# parameters here were re-swept from scratch on this window.
PARAMS = {
    # Dip reference: "sma" (mean of the last REF_LEN closes) or "high" (the
    # highest close of the last HIGH_LEN bars).
    "ref_mode": "sma",
    "ref_len": 120,       # SMA window when ref_mode == "sma"
    "high_len": 120,      # rolling-high window when ref_mode == "high"
    "dip_bps": 115,       # entry: close at least this far below the reference
    # Entry confirmation: 1 = require the latest close to be above the bar
    # before it (a bounce); 0 = enter on the dip alone.
    "bounce": 0,          # bounce=1 was tested: it never triggers on this
                          # window (no bounce at the dip bar) -> 0 trades
    # Trend filter: "none", "sma120" or "sma360" (close must be above it).
    "trend": "none",
    "trend_len": 360,
    # Exits, all in bps against the position's cost (quote units in/out).
    "tp_bps": 230,        # take profit (plateau 200-240; 260 collapses)
    "sl_bps": 120,        # hard stop loss
    "trail_arm_bps": 100000,  # trailing stop disabled (it whipsaws)
    "trail_bps": 60,
    "max_hold": 720,      # time stop: exit after this many bars held (12 h)
    "cooldown": 0,        # bars to wait after an exit before re-entering
    "no_tail": 90,        # no new entries in the last N bars of the window
    "kill_bps": 400,      # equity drawdown from peak that halts trading
    "max_slip_bps": 100,  # max slippage vs the close the order was decided on
    # Mean-reversion exit: 1 = also exit once the close recovers to the
    # entry reference's mean (SMA ref_len) after at least this many bars.
    "exit_mean": 0,       # tested: exits far too early (+$1.96 vs +$22.35)
    "exit_mean_min_hold": 5,
    "window_bars": 1440,  # bars in the window (24 h of 1-minute bars)
}


class _Series:
    """Rolling integer indicators over one symbol's closes."""

    def __init__(self, params):
        self.p = params
        self.closes = []          # all closes seen (ints, price_scale 5)
        self._sma_sum = {}        # span -> running sum of the last `span` closes
        self._spans = {params["ref_len"], params["trend_len"], 120}
        self._high = []           # deque of (index, close) for the rolling high
        self.high_len = params["high_len"]

    def push(self, close):
        i = len(self.closes)
        self.closes.append(close)
        for span in self._spans:
            s = self._sma_sum.get(span, 0) + close
            if i >= span:
                s -= self.closes[i - span]
            self._sma_sum[span] = s
        # rolling high over the last high_len closes, monotone queue
        while self._high and self._high[-1][1] <= close:
            self._high.pop()
        self._high.append((i, close))
        while self._high and self._high[0][0] <= i - self.high_len:
            self._high.pop(0)

    def sma(self, span):
        """Mean of the last `span` closes, or None while fewer are known."""
        if len(self.closes) < span:
            return None
        return self._sma_sum[span] // span

    def rolling_high(self):
        return self._high[0][1] if self._high else None

    def reference(self):
        if self.p["ref_mode"] == "high":
            return self.rolling_high()
        return self.sma(self.p["ref_len"])

    def bounced(self):
        if len(self.closes) < 2:
            return False
        return self.closes[-1] > self.closes[-2]


class _Trader:
    """One position's lifecycle: entry signal, exits, cooldown, kill switch."""

    def __init__(self, params):
        self.p = params
        self.series = _Series(params)
        self.peak_equity = None
        self.halted = False
        self.entry_close = None   # close of the bar the entry was decided on
        self.peak_close = None    # highest close since entry
        self.held_bars = 0
        self.cooldown_left = 0
        self._last_seq = -1

    # -- helpers ---------------------------------------------------------
    def _trend_ok(self, close):
        mode = self.p["trend"]
        if mode == "none":
            return True
        span = self.p["trend_len"] if mode == "sma360" else 120
        ref = self.series.sma(span)
        if ref is None:
            return False
        return close >= ref

    def _dip_bps(self, close):
        ref = self.series.reference()
        if ref is None or ref <= 0:
            return None
        return (ref - close) * BPS // ref

    # -- main decision ---------------------------------------------------
    def decide(self, state, batch):
        """Return the orders for this batch (possibly [])."""
        p = self.p
        seq = state.seq
        # Track equity peak for the kill switch (equity is marked at the
        # latest close the harness sent us; it only ever falls from here on
        # if the market falls, so a peak is a peak).
        if self.peak_equity is None or state.equity > self.peak_equity:
            self.peak_equity = state.equity
        if (
            self.peak_equity is not None
            and state.equity * BPS <= self.peak_equity * (BPS - p["kill_bps"])
        ):
            self.halted = True

        for bar in batch["bars"]:
            if bar["symbol"] == SYMBOL:
                self.series.push(int(bar["c"]))
        if not self.series.closes:
            return []
        close = self.series.closes[-1]

        held = state.positions.get(SYMBOL)
        if self.cooldown_left > 0:
            self.cooldown_left -= 1

        # --- manage an open position -----------------------------------
        if held is not None:
            self.held_bars += 1
            if self.peak_close is None or close > self.peak_close:
                self.peak_close = close
            value = state.value(SYMBOL, held.qty)
            cost = held.cost
            exit_now = False
            if value * BPS >= cost * (BPS + p["tp_bps"]):
                exit_now = True  # take profit
            elif value * BPS <= cost * (BPS - p["sl_bps"]):
                exit_now = True  # hard stop loss
            elif self.held_bars >= p["max_hold"]:
                exit_now = True  # time stop
            elif (
                p["exit_mean"]
                and self.held_bars >= p["exit_mean_min_hold"]
                and (mean := self.series.sma(p["ref_len"])) is not None
                and close >= mean
            ):
                exit_now = True  # recovered to the mean
            elif (
                self.peak_close is not None
                and self.entry_close is not None
                and self.peak_close * BPS
                >= self.entry_close * (BPS + p["trail_arm_bps"])
                and close * BPS <= self.peak_close * (BPS - p["trail_bps"])
            ):
                exit_now = True  # trailing stop
            if exit_now:
                self.peak_close = None
                self.entry_close = None
                self.held_bars = 0
                self.cooldown_left = p["cooldown"]
                return [
                    {
                        "side": "sell",
                        "symbol": SYMBOL,
                        "qty": held.qty,
                        "max_slippage_bps": p["max_slip_bps"],
                    }
                ]
            return []

        # --- flat: look for an entry ------------------------------------
        if self.halted or state.warming_up:
            return []
        if seq >= p["window_bars"] - p["no_tail"]:
            return []  # no time left to earn the round-trip cost back
        if self.cooldown_left > 0:
            return []
        dip = self._dip_bps(close)
        if dip is None or dip < p["dip_bps"]:
            return []
        if p["bounce"] and not self.series.bounced():
            return []
        if not self._trend_ok(close):
            return []
        notional = state.cash
        room = state.weight_room(SYMBOL)
        if room < notional:
            notional = room
        if notional < int(state.hello["fees"]["min_notional_quote_units"]):
            return []
        self.entry_close = close
        self.peak_close = close
        self.held_bars = 0
        return [
            {
                "side": "buy",
                "symbol": SYMBOL,
                "notional_quote_units": notional,
                "max_slippage_bps": p["max_slip_bps"],
            }
        ]


def make_decide(params):
    """A fresh decide function (with its own trader state) for `params`."""
    trader = _Trader(params)

    def decide(state, batch):
        return trader.decide(state, batch)

    return decide


def on_batch(state, batch):
    return _DEFAULT_DECIDE(state, batch)


_DEFAULT_DECIDE = make_decide(PARAMS)


if __name__ == "__main__":
    makefun_bars.run(on_batch)
