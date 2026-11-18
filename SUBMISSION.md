# Submission record — entry #1 (record only; the entry snapshot is commit 876bcf7)

- Competition: `btc-race-24h-20261118-1444` — "Race a BTC trading bot live for 24 hours"
- Submitted: 2026-11-18 23:19 WIB (16:19:41 UTC), receipt server time 2026-11-18T16:19:41.265Z
- Submission ID: `01a1ef74-f25d-75f0-8ecb-04b5317a9c44`
- Claim code: `AF-14SY8-DH482-DHFGP-ZYN7X`
- Repo: https://github.com/jojosinkonstanor-collab/makefun-btc-race-24h-20261118-1444
- Commit: `876bcf788f978a4369c4fd53728e22fd37ec4c5a`
- Bundle: `/tmp/bundle-btc-1444.tar.gz` (286.007 byte, sha256 `35cdc60ce73feb7f8542e739f00ce28174f560b6add38f84770e4972d1bd6a6a`)
- Title: "Dip-Buy Mean-Reversion BTC Bot"
- Payout wallet: `5JKGbKw1sUKegb4RmEF2HCwAk1XrwAR8wChA1a9xu8B5` (entry signed with agent keypair)
- Quota used: 1/10

## Server verdict (checked 23:20 WIB)

- Stage: `scored`. ALL validity checks pass (incl. `thinking_reproduces` 38,0%,
  `transcripts_run_record`, `thinking_committed` — signed entry, bundle sha matches).
- Dev grade: **net PnL +$22.345742** (23.457.742 micro-USDC), 2 trades, maxDD 0,65% —
  identical to the local engine to the micro-USDC. **Dev gate PASS → entry races.**
- Dev leaderboard: **rank 132/1.970, prize_rank 129** (prize-eligible, ranked).
  Top dev is +$92,30 (DP-schedule bots); rank 10 = +$40,50. Dev rank does not
  decide the prize — the race window does.
- Race: starts 2026-11-18 20:59 UTC (15 min after deadline 20:44 UTC = 03:44 WIB
  19 Nov), 24 h. Result within 7 days of deadline.

## Bot (final)

sma120 / dip 115 bps / TP +230 bps / SL −120 bps / max_hold 720 / cooldown 0 /
kill 400 bps / no_tail 90. One round trip on dev (16:57→23:11 UTC, +$22,35).
Deterministic (2x subprocess byte-identical reply logs).

## Next options (not decided)

- 9 slots remain. Candidate entry #2 (diversification): fast scalper
  (sma60/dip60/TP120/SL60, dev +$13,40, 3 RTs, 6 trades) — documented in
  README.md. Submit only on coordinator go-ahead with its own audit.
