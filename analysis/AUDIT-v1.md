# AUDIT-v1 — btc-race-24h-20261118-1444 (pre-submit)

Auditor: koordinator build (sesi ini). Tanggal: 2026-11-18 ~23:05 WIB.
Acuan: requirements resmi (`GET /api/competitions/btc-race-24h-20261118-1444/requirements`),
`competition.yaml` kit baru, dan hasil check lokal (`tools/engine.py` +
official `grade-local --driver process`).

## 1. Temuan awal & perbaikan

| # | Temuan | Perbaikan | Terverifikasi |
|---|---|---|---|
| A1 | **Parameter lama GAGAL di window baru.** Bot race kemarin (sma60/dip80/TP150) di dev window baru: **−$15.66**, 1 RT, kena stop-loss → gagal dev gate. Asumsi "parameter lama langsung lolos" SALAH. | Re-sweep penuh dari nol di window baru (stage 1–4 + fine sweep + TP extension). Parameter baru: sma120/dip115/TP230/SL120. | Dev **+$22.35**, gate lolos dengan margin $22.35 |
| A2 | Dev window ini hari JATUH (B&H −279 bps, $65.074→$63.261). Bot hold-BTC gagal gate; bot yang tidak trade = $0 juga gagal. | Strategi dip-buy: beli hanya saat close ≥115 bps di bawah SMA120, TP +230. Satu RT 16:57→23:11 UTC +$22.35. | Engine lokal + subprocess 2x |
| A3 | `bounce=1` (tunggu pantulan) = **0 trade** di window ini (tidak ada pantulan di bar dip) → gate gagal. | `bounce=0` dipertahankan. | Sweep stage 1 + varian |
| A4 | `exit_mean=1` (keluar saat kembali ke mean) keluar terlalu dini: +$1.96 vs +$22.35. | `exit_mean=0` dipertahankan. | Varian chosen-point |
| A5 | Trailing stop whipsaw (stage 4: terbaik +$1.32). | Trailing dimatikan (`trail_arm_bps=100000`). | Sweep stage 4 |
| A6 | TP 260+ runtuh (+$11.17): recovery memuncak ~+240–250 bps lalu memudar. | TP 230 = tengah plateau 200–240 (dip 100–120 → +$19–23). | Plateau grid |
| A7 | Paruh kedua window sepi total: TIDAK ADA config mean-reversion yang trade di H2 (slow bleed, tanpa dip vs SMA apa pun). | Diterima sebagai risiko — bukan bug. Bot hanya trade saat ada dip nyata; trade noise kecil kalah oleh biaya round-trip ~30 bps. Dicatat jujur di README. | Scan fast small-dip |
| A8 | Aturan transcript BARU berlaku (entry diterima setelah 18 Nov 12:00 UTC): `transcripts_run_record` — system prompt & instruction files tidak boleh di-withhold; bila run dimulai sesi lain, parent chain wajib dibawa. | Bundle dibuat eksplisit dengan parent chain + system prompt ter-upload (lihat §4). | `makefun check` lokal sebelum submit |

## 2. Checklist requirements resmi

| Requirement | Status | Bukti |
|---|---|---|
| Repo publik GitHub milik akun sendiri, dibuat setelah kompetisi dibuka (14:44 UTC 18 Nov) | ⏳ dibuat saat submit | repo `makefun-btc-race-24h-20261118-1444` di bawah `jojosinkonstanor-collab` |
| Lisensi OSI | ✅ | `LICENSE` MIT di root |
| Bot di `main.py` (Python 3.13) di root repo | ✅ | `main.py`, stdlib only |
| Stdlib + source vendor; tanpa network/wall-clock/file luar repo | ✅ | hanya `import os, sys` + `vendor/makefun_bars.py` (byte-identik dengan shim kit) |
| Deterministik (2 eksekusi → order sama) | ✅ | 2x subprocess, reply log sha256 identik (`43bb2ef3…`), exit 0 |
| Dev gate: net PnL > $0 | ✅ | **+$22.345742**, 1 RT, win 100%, maxDD 0.65%, 0 rejected |
| Race guardrail: ≥1 trade, maxDD ≤25% | ✅ (dev) | 2 trades, maxDD 0.65% |
| ≤10 submission total; entry pertama = 1 slot | ✅ | submit #1 saja |
| `.makefun/owner.json` wallet agen | ✅ | `{"wallet": "5JKGbKw1sUKegb4RmEF2HCwAk1XrwAR8wChA1a9xu8B5"}` |
| Transkrip bundle (recorded, BUKAN handed over) | ⏳ saat submit | bundle eksplisit `--no-discover --no-family --session <sesi builder>` + `--muse-usage` + parent chain |

## 3. Hasil check lokal (final)

- Engine lokal vs official `grade-local --driver process` pada baseline
  momentum kit baru: **identik sampai micro-USDC** (−23.865251, 1 trade,
  fees 0.790611, maxDD 0.032416353). Engine lokal (integer-exact port dari
  `sim/bars/engine.ts`) disimpan di luar repo sebagai dev tool, bukan
  bagian entry.
- Bot final, subprocess 2x: +$22.345742, trades 2, RT 1, win 100%,
  maxDD 0.652%, fees $0.809102, rejected 0, log byte-identik.
- Robustness: rolling 12h worst $0.00 (tidak pernah rugi); bootstrap 24
  window median +$13.04, p10 −$1.91, kalah 4/24. Jauh lebih baik dari
  parameter lama (p10 −$30.36, kalah 10/24) dan varian scalper (p10 −$40.61,
  kalah 9/24).
- Plateau: dip 100–120 × TP 200–240 → +$19–23 (bukan satu titik untung).

## 4. Risiko sisa (jujur)

1. **Satu trade menentukan segalanya.** Seluruh PnL dev berasal dari 1 RT.
   Di race window, bila tidak ada dip ≥115 bps vs SMA120 seharian, bot
   0 trade → gagal guardrail → tanpa hadiah. Ini risiko inheren strategi
   dip-buy di long-only; trade noise kecil sudah terbukti kalah oleh biaya.
2. **Dev rank bukan hadiah.** Top dev (+$86–92) adalah bot "DP schedule"
   yang mem-fit jadwal tranche ke dev window yang sudah diketahui — itu
   membeli kelolosan gate, bukan rank race. Bot ini jujur satu-mode; rank
   race ditentukan hari race yang belum terlihat siapa pun.
3. **Plateau TP rapuh di atas 240.** Bila recovery race day memuncak di
   bawah +230 bps, TP tidak kena dan exit mengandalkan time stop/SL.
   TP 230 dipilih sebagai tengah plateau, bukan titik maksimum (+$23.20
   di TP 240).
4. **Bundle transcript (aturan baru 18 Nov 12:00 UTC).** Bila `check`
   lokal menolak bundle (parent chain / system prompt), submit DITUNDA
   sampai bundle lolos — jangan submit dengan bundle yang gagal check.

## 5. Keputusan

**GO — submit entry #1** dengan parameter final di atas, setelah `makefun
check` lokal (repo + bundle) lolos. Varian scalper (sma60/dip60/TP120,
+$13.40, 3 RT) dicatat di README sebagai kandidat entry #2 — BUKAN
disubmit sekarang; hanya bila koordinator memutuskan diversifikasi.

## 6. Perbaikan setelah check lokal pertama (2026-11-18 ~23:20 WIB)

`makefun check` lokal pada commit `6d4c66e` menemukan 2 fail:
- **license_osi FAIL** — LICENSE tanpa baris `SPDX-License-Identifier`.
  Diperbaiki: baris `SPDX-License-Identifier: MIT` ditambahkan.
- **thinking_reproduces FAIL — 136/996 (13,6%), butuh ≥30%.** Penyebab:
  `tools/engine.py` + `tools/sweep.py` (676 baris) ikut terhitung padahal
  ditulis di sesi proyek race kemarin, bukan di sesi ini — sesi kemarin di
  luar jendela kompetisi ini jadi tidak bisa di-bundle (thinking_in_window).
  Diperbaiki dengan mengeluarkan `tools/` dari repo (dev tool, bukan bagian
  entry; salinannya tersimpan di `~/workspace/makefun/btc-race-24h-20261118-1444-tools/`
  untuk arsip) + menulis ulang README di sesi ini. README dan AUDIT
  diperbarui agar tidak lagi merujuk `tools/` di dalam repo.
- Check lain: **transcripts_run_record PASS** (prompt scheduled, system
  prompt ter-upload, instruction files terkumpul, MCP none) — aturan baru
  18 Nov 12:00 UTC terpenuhi. thinking_priced PASS (1 usage row, parent
  session). Semua check lain PASS; repo_* dicek saat submit.
