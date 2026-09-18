# DarkStore Copilot — Final Verification Report

Everything below was run in this working copy. Nothing is claimed as implemented unless it is
wired end to end: React UI → HTTP → `app/handler.py` → policy engine → adapter → state/audit.

---

## 1. Files changed

**Backend — new**
- `backend/app/voice_lexicon.py` — deterministic multilingual intent fast path (en / te / hi).
- `backend/app/demo.py` — 9 seeded demo scenarios, built by replaying the real handler functions.
- `backend/tests/test_voice_lexicon.py` — 10 tests.
- `backend/tests/test_flows.py` — 37 end-to-end flow tests.

**Backend — modified**
- `backend/app/handler.py` — new routes, `ITEM_ALREADY_TAKEN` / concurrent-pick workflow,
  `client_action_id` idempotency on every mutating route, policy chain in responses,
  live exceptions + recent decisions, analysis aggregation, bag verdict enrichment,
  `line.get("substituted_from") or line["sku_id"]` fix.
- `backend/app/state_machine.py` — new item state `CONTESTED` (distinct from `OOS`).
- `backend/app/contracts.py` — `ITEM_ALREADY_TAKEN` action, `SKU_OPTIONAL_ACTIONS`.
- `backend/app/dynamo.py` — `list_inventory()`, `reset_store()` (refuses against real DynamoDB).
- `backend/app/bedrock.py` — `verify_bag(..., context=...)` so the local adapter is deterministic.
- `backend/local_server.py` — in-memory reset/seed, lexicon-backed stand-in transcription,
  deterministic bag fail-then-pass, boots with the 9 demo orders seeded.
- `backend/tests/conftest.py` — path setup for the new tests.

**Frontend — new**
- `src/hooks/useVoiceMachine.js`, `src/lib/image.js`
- `src/components/VoicePanel.jsx`, `PolicyChain.jsx`, `ConcurrentPick.jsx`,
  `ActivityFeed.jsx`, `DemoControlCenter.jsx`, `OrderList.jsx`, `Analysis.jsx`

**Frontend — rewritten / modified**
- `src/App.jsx` (screen routing, voice machine wiring, offline-safe queue, handover token)
- `src/api.js`, `src/styles.css`
- `src/components/RackTicket.jsx`, `ActionBar.jsx`, `BagCheck.jsx`, `ProductVerify.jsx`,
  `Dashboard.jsx`, `ProgressRail.jsx`, `DemoPanel.jsx`

Architecture is unchanged: React/Vite PWA → Python handler → DynamoDB adapter → Bedrock adapters.
No feature was removed.

---

## 2. Features implemented

| Spec | Feature | Where it is wired |
|---|---|---|
| 2 | All required routes exist and respond | `handler.py`, 24 `@route` handlers |
| 3 | 8 realistic seeded orders `ORD-DEMO-101…109` | `demo.py`, seeded at server boot |
| 4 | `POST /demo/reset` + visible ↻ Reset demo button | `DemoControlCenter.jsx` |
| 5 | Visible Demo Control Center (9 scenarios) + hidden Ctrl+Shift+D fallback kept | `DemoControlCenter.jsx`, `DemoPanel.jsx` |
| 6 | Voice state machine READY→LISTENING→HEARD→UNDERSTANDING→POLICY CHECK→ACTION→RESPONDING→DONE (+ERROR) | `useVoiceMachine.js`, `VoicePanel.jsx` |
| 7 | Full AI → POLICY → ACTION chain shown | `policy_chain` from handler → `PolicyChain.jsx` |
| 8 | English / Telugu / Hindi, deterministic fast path, Bedrock only on ambiguity | `voice_lexicon.py` + `_interpret()` |
| 9 | First-class `ITEM_ALREADY_TAKEN` / concurrent pick, separated from stockout | `_handle_concurrent_pick()`, `CONTESTED` state, dashboard + analysis split |
| 10 | `client_action_id` idempotency end to end | `newActionId()` → all mutating routes → `_already_processed` |
| 11 | One-item-at-a-time mobile picker | `RackTicket.jsx`, `ActionBar.jsx` |
| 12 | 📷 Verify product with MATCH / MISMATCH / UNCLEAR, fails closed, blocks confirm on mismatch | `ProductVerify.jsx` + `App.jsx` guard |
| 13 | Bag verification with visible failure, counts, named missing items, working retry | `BagCheck.jsx` + `verify-bag` |
| 14 | AI activity / audit feed | `GET /orders/{id}/audit` → `ActivityFeed.jsx` |
| 15 | Control Tower + LIVE EXCEPTIONS + RECENT AI/POLICY DECISIONS, no hard-coded numbers | `GET /dashboard` → `Dashboard.jsx` |
| 16 | Separate Analysis workspace | `GET /analysis` → `Analysis.jsx` |
| 17 | Offline-first for picks/exceptions only | `OFFLINE_SAFE = {"pick","exception"}` in `App.jsx` |
| 18 | Substitution policy preserved; `substituted_from` fix | `substitution.py` untouched, `handler.py` fix |
| 19 | Deterministic serpentine routing kept | `routing.py` untouched |
| 20 | QR rider handover kept, works for seeded orders | `Handover.jsx` + `order.handover_token` |
| 22 | Local demo runs with no AWS | `local_server.py` in-memory adapters |
| 23 | Loading / error / empty states, timeouts, retry | `api.js` + every screen |

The LLM never mutates state: `_interpret()` returns an intent only; all stock, price, order and
audit changes happen in handler code behind `validate_intent` and the policy engine.

---

## 3. New API routes

- `GET /orders` — shift board
- `GET /orders/{id}/audit` — per-order AI/policy audit trail
- `GET /audit` — store-wide audit
- `GET /inventory` — full inventory
- `GET /analysis` — analysis workspace aggregation
- `POST /orders/{id}/speech` — per-order TTS (previously only `POST /speech`)
- `GET /demo/scenarios`
- `POST /demo/reset`
- `POST /demo/scenario`

Pre-existing routes were kept and still work (24 routes total).

---

## 4. New voice intents

- `ITEM_ALREADY_TAKEN` — new first-class action (contracts, state machine, handler, UI).
- Existing `CONFIRM_PICK`, `SUBSTITUTE_ITEM`, `FLAG_EXCEPTION` now also resolve through the
  deterministic lexicon in English, Telugu and Hindi (Latin transliteration + native script),
  e.g. "teesukunnanu", "le liya", "evaro teesukunnaru", "koi le gaya", "stock ledu", "khatam".
  Ambiguous utterances return `None` from the fast path and are sent to Bedrock.
  A stockout phrase with no policy-approved substitute downgrades to `FLAG_EXCEPTION`.

---

## 5. New demo scenarios

`ORD-DEMO-101` main 10-line order parked on Amul Taaza Milk 500ml · `102` shelf stockout ·
`103` product verification · `104` bag verification failed · `105` ready for rider (token issued) ·
`106` dispatched · `107` multilingual voice · `108` guardrail rejection · `109` concurrent pick.

Each is produced by replaying real handler calls, so stock levels, invoices, order states and
audit trails are genuine — not fixtures.

---

## 6. Concurrent pick (ITEM_ALREADY_TAKEN)

1. Picker says "evaro teesukunnaru" / "koi le gaya" / "someone already took it", or taps
   **👤 Someone took it**.
2. Lexicon classifies `ITEM_ALREADY_TAKEN` first (before the confirm rules, so
   "evaro teesukunnaru" can never be mistaken for "teesukunnanu").
3. `_handle_concurrent_pick()` re-reads the live order. If the line is already terminal it
   writes `CONCURRENT_PICK_DUPLICATE_BLOCKED` and returns `CONCURRENT_PICK_NO_CHANGE` —
   no double mutation.
4. Otherwise the line moves to **`CONTESTED`**, not `OOS`, with audit event
   `ITEM_CONCURRENT_PICK` carrying policy `CONCURRENT_PICK`, store stock,
   `duplicate_pick_prevented` and `client_action_id`.
5. A policy-approved substitute is offered; if none exists the line is marked
   `MANUAL_RESOLUTION_REQUIRED`.
6. Dashboard and Analysis count concurrent picks and shelf stockouts separately.

---

## 7. Tests

```
python -m py_compile app/handler.py   → OK
python -m pytest -q                   → 76 passed in 0.22s
```
(29 tests before this work, 76 now.) Coverage includes normal pick, stock decrement, pre-start
refusal, unknown SKU, stockout + policy-approved-only substitutions, pharma never substituted,
substitution committing stock and invoice together, concurrent pick not treated as stockout,
model cannot claim a pick, duplicate `client_action_id` on pick/exception/substitute/voice,
Telugu and Hindi fast paths, ambiguous utterance falling through to Bedrock, hallucinated SKU
rejection, product verify not mutating state, bag fail-then-pass with counts, dispatch token
enforcement, demo reset determinism, and every new route.

Also run and passing in this copy:
- **HTTP smoke test**, 43 assertions against the running local server → 0 failures.
- **Browser test** (Playwright, mobile viewport, 430×900) → 40+ assertions, 0 failures:
  picker screen, concurrent-pick sheet with policy chain, substitution, product verify modal,
  bag fail → retry → QR, dispatch, control tower live exceptions and decision feed, analysis
  workspace, demo control center 9 scenarios, reset, hidden dev panel guardrail, AI activity
  feed, 9-row shift board, no empty or dead buttons, no HTTP ≥400 to the API.

---

## 8. Frontend build

```
npm run build → ✓ built, 126 modules
dist/assets/index-*.css  32.56 kB
dist/assets/index-*.js  232.05 kB
```

---

## 9. Running it locally (no AWS needed)

```bash
# terminal 1
cd backend
python local_server.py            # PORT=8080 by default; seeds the 9 demo orders

# terminal 2
cd frontend
npm install
npm run dev                       # frontend/.env already has VITE_API_BASE=http://localhost:8080
```

Then: **Open the demo order** → speak or use the on-screen actions → 👤 Someone took it →
apply substitute → 📷 Verify product → finish lines → bag check (fails once, passes on retry) →
QR handover → Ops and Analysis. The ↻ Reset demo button restores the exact seeded state.

---

## 10. Remaining limitations (honest list)

- **Local adapters are stand-ins, by design.** With no AWS, transcription uses the lexicon on
  typed/simulated text, `verify_item` matches on SKU keywords, and `verify_bag` deliberately
  fails attempt 1 and passes attempt 2 so the retry path is demonstrable. Bedrock adapter code
  is unchanged and is what runs when deployed; the UI labels these as `local-demo`.
- **Browser speech recognition** depends on the browser's Web Speech API (Chrome/Edge). Where it
  is unavailable the voice panel reports it and the on-screen buttons carry the same actions.
- **The voice state machine's UNDERSTANDING → POLICY CHECK split is displayed on short timers**
  while the single `/intent` request is in flight. The stages are real server-side steps but the
  backend does not stream per-stage events, so the UI advances them locally.
- **Offline queue covers picks and exceptions only.** Substitutions, AI interpretation, product
  and bag verification and dispatch require the server and are refused while offline rather than
  queued — a stale substitution decision is unsafe.
- **`reset_store()` refuses to run against real DynamoDB.** Demo reset is local-only on purpose.
- **Analysis metrics are per-process.** The local server keeps state in memory, so restarting it
  reseeds from scratch (the Analysis screen labels the data as seeded demo orders).
- **No frontend unit tests.** UI verification is the Playwright browser test above, not Vitest.
- In this sandbox the only console errors are two 403s from `fonts.googleapis.com` (outbound
  network is blocked here); the CSS carries system-font fallbacks, so layout is unaffected.
