"""Faithful local port of the kit harness (sim/bars/engine.ts) for one symbol.

Implements the bars-replay/v1 market model exactly, in integers:
  * orders decided on batch k fill at bar k+1's open, in reply order, against
    a constant-product pool of that bar's open depth (single hop here);
  * fee 4 bps, slippage 2 bps, price impact from the pool;
  * max_slippage_bps measured from the latest close at decision time;
  * decision checks in the harness's order; flatten at the last close.
Used both to drive a decide() function in-process (fast sweeps) and to drive
the real bot as a subprocess over stdin/stdout (end-to-end verification).

All money is integer quote units (1 USDC = 1,000,000). Prices are integers at
price_scale 5, quantities at qty_scale 8.
"""

import gzip
import json
import math
import subprocess

BPS = 10_000
FEE_BPS = 4
SLIP_BPS = 2
MIN_NOTIONAL = 1_000_000          # 1 USDC
START_CASH = 1_000_000_000        # 1,000 USDC
QUOTE_SCALE = 6
PRICE_SCALE = 5
QTY_SCALE = 8
SYMBOL = "CBBTC"
MINT = "cbbtcf3aa214zXHbiAZQwf4122FBYbraNdFqgw4iMij"


def load_bars(path, symbol=SYMBOL):
    """Bars of one symbol's file; the wire form carries the symbol (the
    dataset lines themselves omit it, as the harness adds it)."""
    bars = []
    with gzip.open(path, "rt") as f:
        for line in f:
            bar = json.loads(line)
            bar["symbol"] = symbol
            bars.append(bar)
    return bars


def make_hello(bars):
    return {
        "type": "hello",
        "protocol": "agent-fun/bars-replay/v1",
        "seed": "0",
        "symbols": [
            {"symbol": SYMBOL, "mint": MINT, "price_scale": PRICE_SCALE,
             "qty_scale": QTY_SCALE}
        ],
        "capital": {"quote": "USDC", "quote_scale": QUOTE_SCALE,
                    "quote_units": str(START_CASH)},
        "bar_interval": "1m",
        "first_close_ms": bars[0]["t"],
        "caps": {"max_open_positions": 1, "max_orders_per_bar": 16,
                 "max_weight_bps": 10000, "shorts": False, "leverage": False},
        "fees": {"fee_bps": FEE_BPS, "slippage_bps": SLIP_BPS,
                 "min_notional_quote_units": str(MIN_NOTIONAL)},
        "latency_bars": 1,
    }


class Holding:
    __slots__ = ("qty", "cost")

    def __init__(self, qty, cost):
        self.qty = qty
        self.cost = cost


class FakeState:
    """The subset of the shim's BarsState interface the strategy uses."""

    def __init__(self, hello):
        self.hello = hello
        self.seq = -1
        self.t = -1
        self.cash = START_CASH
        self.equity = START_CASH
        self.positions = {}
        self.latest = {}
        self.warmup_bars = hello.get("warmup_bars", 0)

    @property
    def warming_up(self):
        return self.warmup_bars > 0 and self.seq < self.warmup_bars

    def close(self, symbol):
        bar = self.latest.get(symbol)
        return None if bar is None else int(bar["c"])

    def value(self, symbol, qty, price=None):
        if price is None:
            price = self.close(symbol)
        if price is None:
            return 0
        return qty * price * 10 ** QUOTE_SCALE // 10 ** (QTY_SCALE + PRICE_SCALE)

    def depth_at(self, symbol):
        bar = self.latest.get(symbol)
        return None if bar is None else [int(d) for d in bar["c_depth"]]

    def weight_room(self, symbol):
        held = self.positions.get(symbol)
        room = (self.hello["caps"]["max_weight_bps"] * self.equity // BPS
                - self.value(symbol, held.qty if held else 0))
        return room if room > 0 else 0


class Pool:
    """Exact port of TS BarsPool for one hop (depth D, USDC raw units).

    State: y = yn/yd (reduced), D^2 = kn/kd (constant), unit = 10^6.
    """

    def __init__(self, depth_raw):
        d = depth_raw * 10 ** QUOTE_SCALE
        self.yn, self.yd = d, 1
        self.kn, self.kd = d * d, 1
        self.unit = 10 ** 6

    def buy(self, spend):
        yn2 = self.yn + spend * self.unit * self.yd
        num = self.yn * yn2 * self.kd
        den = self.yd * self.yd * self.kn
        g = math.gcd(yn2, self.yd)
        self.yn, self.yd = yn2 // g, self.yd // g
        return num, den

    def sell(self, value):
        t = self.kn * self.yd + value * self.unit * self.yn * self.kd
        num = t * self.yd
        den = self.kd * self.yn * self.yn
        g = math.gcd(self.kn * self.yn, t)
        self.yn, self.yd = self.kn * self.yn // g, t // g
        return num, den


def _buy_fill(notional, open_price, pool):
    """(price, spend, fee, qty) for a buy of `notional` at `open_price`."""
    spend = notional * (BPS - FEE_BPS) // BPS
    num, den = pool.buy(spend)
    price = open_price * (BPS + SLIP_BPS) * num // (BPS * den)
    qty = (spend * 10 ** (QTY_SCALE + PRICE_SCALE)
           // (price * 10 ** QUOTE_SCALE))
    return price, spend, notional - spend, qty


def _sell_fill(qty, open_price, pool):
    """(price, gross, fee, proceeds) for a sell of `qty` at `open_price`."""
    value = (qty * open_price * 10 ** QUOTE_SCALE
             // 10 ** (QTY_SCALE + PRICE_SCALE))
    num, den = pool.sell(value)
    slipped = open_price * (BPS - SLIP_BPS) * den // (BPS * num)
    price = slipped if slipped >= 1 else 1
    gross = (qty * price * 10 ** QUOTE_SCALE
             // 10 ** (QTY_SCALE + PRICE_SCALE))
    proceeds = gross * (BPS - FEE_BPS) // BPS
    return price, gross, gross - proceeds, proceeds


class Replay:
    """One run of the harness over `bars`, driven by `reply_fn(batch, state)`."""

    def __init__(self, bars, hello=None):
        self.bars = bars
        self.hello = hello or make_hello(bars)
        self.n = len(bars)

    def run(self, reply_fn):
        state = FakeState(self.hello)
        cash = START_CASH
        qty = 0
        cost = 0
        pending = []          # (order, seq, index, close_at_decision)
        used_ids = set()
        fills = []            # protocol fills delivered in the next batch
        resolutions = []      # (order, status, reason)
        equity_pts = [START_CASH]
        trades = 0
        fees = 0
        round_trips = []
        open_rt = None        # [opened_bar_t, pnl]
        next_cid = [1]

        def account():
            pos = []
            if qty > 0:
                pos.append({"symbol": SYMBOL, "qty": str(qty),
                            "cost_quote_units": str(cost)})
            eq = cash
            if qty > 0:
                eq += (qty * int(state.latest[SYMBOL]["c"]) * 10 ** QUOTE_SCALE
                       // 10 ** (QTY_SCALE + PRICE_SCALE))
            return {"cash_quote_units": str(cash), "positions": pos,
                    "equity_quote_units": str(eq)}

        def rej_fill(order, decided_t, fill_t, reason):
            return {"client_id": order["client_id"], "symbol": SYMBOL,
                    "side": order["side"], "status": "rejected",
                    "reason": reason, "decided_bar": decided_t,
                    "fill_bar": fill_t, "price": "0", "qty": "0",
                    "notional_quote_units": "0", "fee_quote_units": "0"}

        for k, bar in enumerate(self.bars):
            t = bar["t"]
            # 1. resolve the previous batch's orders at this bar's open
            pool = Pool(int(bar["o_depth"][0]))
            for (order, seq, idx, close_dec) in pending:
                decided_t = self.bars[seq]["t"]
                if order["side"] == "buy":
                    price, spend, fee, q = _buy_fill(
                        order["notional_quote_units"], int(bar["o"]), pool)
                    if price * BPS > close_dec * (BPS + order["max_slippage_bps"]):
                        resolutions.append((order, "rejected", "max_slippage"))
                        fills.append(rej_fill(order, decided_t, t, "max_slippage"))
                        continue
                    if q == 0:
                        resolutions.append((order, "rejected", "zero_amount"))
                        fills.append(rej_fill(order, decided_t, t, "zero_amount"))
                        continue
                    cash -= order["notional_quote_units"]
                    assert cash >= 0
                    if qty == 0:
                        open_rt = [t, 0]
                    qty += q
                    cost += order["notional_quote_units"]
                    trades += 1
                    fees += fee
                    open_rt[1] -= order["notional_quote_units"]
                    resolutions.append((order, "filled", None))
                    fills.append({"client_id": order["client_id"], "symbol": SYMBOL,
                                  "side": "buy", "status": "filled", "reason": None,
                                  "decided_bar": decided_t, "fill_bar": t,
                                  "price": str(price), "qty": str(q),
                                  "notional_quote_units":
                                      str(order["notional_quote_units"]),
                                  "fee_quote_units": str(fee)})
                else:
                    q = order["qty"]
                    price, gross, fee, proceeds = _sell_fill(
                        q, int(bar["o"]), pool)
                    if price * BPS < close_dec * (BPS - order["max_slippage_bps"]):
                        resolutions.append((order, "rejected", "max_slippage"))
                        fills.append(rej_fill(order, decided_t, t, "max_slippage"))
                        continue
                    assert q <= qty
                    cash += proceeds
                    cost = cost * (qty - q) // qty if qty else 0
                    qty -= q
                    trades += 1
                    fees += fee
                    open_rt[1] += proceeds
                    if qty == 0:
                        round_trips.append((open_rt[0], t, open_rt[1]))
                        open_rt = None
                    resolutions.append((order, "filled", None))
                    fills.append({"client_id": order["client_id"], "symbol": SYMBOL,
                                  "side": "sell", "status": "filled", "reason": None,
                                  "decided_bar": decided_t, "fill_bar": t,
                                  "price": str(price), "qty": str(q),
                                  "notional_quote_units": str(proceeds),
                                  "fee_quote_units": str(fee)})
            pending = []

            # 2. the bar closes
            state.latest[SYMBOL] = bar
            state.seq = k
            state.t = t
            state.cash = cash
            state.equity = int(account()["equity_quote_units"])
            state.positions = ({SYMBOL: Holding(qty, cost)} if qty > 0 else {})
            batch = {"type": "batch", "seq": k, "t": t, "bars": [bar],
                     "fills": fills, "account": account()}
            fills = []
            equity_pts.append(state.equity)

            # 3. the bot replies; decide() checks, in the harness's order.
            # The wire carries amounts as u64 decimal strings; the harness
            # reads them as integers, so normalize here (ints pass through).
            raw_orders = reply_fn(batch, state)
            orders = []
            for o in raw_orders:
                o = dict(o)
                if o["side"] == "buy":
                    o["notional_quote_units"] = int(o["notional_quote_units"])
                else:
                    o["qty"] = int(o["qty"])
                o["max_slippage_bps"] = int(o["max_slippage_bps"])
                orders.append(o)
            is_last = k == self.n - 1
            buys = 0
            sold = 0
            open_syms = {SYMBOL} if qty > 0 else set()
            for idx, order in enumerate(orders):
                cid = order.get("client_id")
                if cid is None:
                    cid = "o%d" % next_cid[0]
                    next_cid[0] += 1
                    order["client_id"] = cid
                reject = None
                amount = (order["notional_quote_units"] if order["side"] == "buy"
                          else order["qty"])
                close_dec = int(bar["c"])
                if idx >= 16:
                    reject = "max_orders_per_bar"
                elif cid in used_ids:
                    reject = "duplicate_client_id"
                elif order["symbol"] != SYMBOL:
                    reject = "not_in_universe"
                elif amount == 0:
                    reject = "zero_amount"
                elif is_last:
                    reject = "after_end"
                elif order["side"] == "buy":
                    held_val = (qty * close_dec * 10 ** QUOTE_SCALE
                                // 10 ** (QTY_SCALE + PRICE_SCALE))
                    if amount < MIN_NOTIONAL:
                        reject = "below_min_notional"
                    elif amount > cash - buys:
                        reject = "insufficient_cash"
                    elif SYMBOL not in open_syms and len(open_syms) >= 1:
                        reject = "max_open_positions"
                    elif (held_val + buys + amount) * BPS > 10000 * state.equity:
                        reject = "max_weight"
                    if reject is None:
                        buys += amount
                        open_syms.add(SYMBOL)
                else:
                    avail = qty - sold
                    if amount == 0 or amount > avail:
                        reject = "insufficient_position"
                    elif (amount != avail and
                          amount * close_dec * 10 ** QUOTE_SCALE
                          // 10 ** (QTY_SCALE + PRICE_SCALE) < MIN_NOTIONAL):
                        reject = "below_min_notional"
                    if reject is None:
                        sold += amount
                used_ids.add(cid)
                if reject is not None:
                    resolutions.append((order, "rejected", reject))
                else:
                    pending.append((order, k, idx, close_dec))

        # finish: the last batch's orders are rejected; flatten at last close
        for (order, seq, idx, close_dec) in pending:
            resolutions.append((order, "rejected", "after_end"))
        last = self.bars[-1]
        if qty > 0:
            price, gross, fee, proceeds = _sell_fill(
                qty, int(last["c"]), Pool(int(last["c_depth"][0])))
            cash += proceeds
            fees += fee
            open_rt[1] += proceeds
            round_trips.append((open_rt[0], last["t"], open_rt[1]))
            qty = 0
            cost = 0
        equity_pts.append(cash)
        return _metrics(equity_pts, cash, trades, fees, round_trips, resolutions)

    # -- convenience wrappers -------------------------------------------
    def run_inprocess(self, decide_fn):
        return self.run(lambda batch, state: decide_fn(state, batch))

    def run_subprocess(self, bot_cmd):
        proc = subprocess.Popen(bot_cmd, stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE)
        assert proc.stdin is not None and proc.stdout is not None
        proc.stdin.write((json.dumps(self.hello, separators=(",", ":")) + "\n")
                         .encode())
        proc.stdin.flush()
        replies_log = []
        holder = {}

        def reply_fn(batch, state):
            proc.stdin.write((json.dumps(batch, separators=(",", ":")) + "\n")
                             .encode())
            proc.stdin.flush()
            raw = proc.stdout.readline()
            if not raw:
                raise RuntimeError("bot exited early (stdout closed)")
            msg = json.loads(raw.decode())
            assert msg["type"] == "reply" and msg["seq"] == batch["seq"], msg
            replies_log.append(raw.decode().rstrip("\n"))
            return msg["orders"]

        try:
            metrics = self.run(reply_fn)
            holder["metrics"] = metrics
        finally:
            final_cash = int(holder.get("metrics", {}).get(
                "final_cash_usdc", 0) * 1_000_000)
            done = {"type": "done", "seq": self.n, "reason": "end_of_data",
                    "account": {"cash_quote_units": str(final_cash),
                                "positions": [],
                                "equity_quote_units": str(final_cash)}}
            try:
                proc.stdin.write((json.dumps(done) + "\n").encode())
                proc.stdin.flush()
            except BrokenPipeError:
                pass
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        metrics = holder["metrics"]
        metrics["replies_log"] = replies_log
        metrics["bot_returncode"] = proc.returncode
        return metrics


def _metrics(equity_pts, final_cash, trades, fees, round_trips, resolutions):
    net = final_cash - START_CASH
    peak = equity_pts[0]
    max_dd = 0.0
    for e in equity_pts:
        if e > peak:
            peak = e
        dd = (peak - e) / peak if peak else 0.0
        if dd > max_dd:
            max_dd = dd
    # hourly Sharpe on hourly equity marks (start, each hour end, final cash)
    marks = [equity_pts[0]]
    n = len(equity_pts) - 2  # minus start and final
    for h in range(1, 25):
        marks.append(equity_pts[min(h * 60, n)])
    marks.append(final_cash)
    rets = [(marks[i] - marks[i - 1]) / marks[i - 1] if marks[i - 1] else 0.0
            for i in range(1, len(marks))]
    sharpe = None
    if len(rets) >= 2:
        mean = sum(rets) / len(rets)
        var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
        if var > 0:
            sharpe = mean / math.sqrt(var)
    wins = sum(1 for _, _, p in round_trips if p > 0)
    return {
        "net_pnl_usdc": net / 1e6,
        "net_pnl_quote_units": net,
        "return_bps": net / START_CASH * 10000,
        "final_cash_usdc": final_cash / 1e6,
        "max_drawdown": max_dd,
        "trades": trades,
        "fees_usdc": fees / 1e6,
        "round_trips": len(round_trips),
        "win_rate": (wins / len(round_trips)) if round_trips else None,
        "sharpe_hourly": sharpe,
        "rejected": sum(1 for _, s, _ in resolutions if s == "rejected"),
        "round_trips_detail": round_trips,
    }
