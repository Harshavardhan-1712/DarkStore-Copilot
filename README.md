# DarkStore Copilot

AI-assisted order picking for dark stores. A picker walks an aisle-sorted route, talks to the
app in Telugu or Hindi, gets policy-checked substitutions when a shelf is empty, and photographs
the packed bag before a rider can take it.

**The line that defines the architecture:** DynamoDB is the only thing that decides inventory,
SKU identity, stock, price and order state. Bedrock listens, reads photos, and speaks the
picker's language. Nothing Bedrock returns changes state until Python has validated it.

## Run it in two minutes, no AWS account

```bash
pip install -r backend/requirements.txt
python backend/local_server.py            # in-memory store + stand-in models, port 8080

cd frontend && npm install
echo "VITE_API_BASE=http://localhost:8080" > .env
npm run dev
```

The local server swaps DynamoDB for a dict and Bedrock for a keyword matcher that emits the
same JSON contract — the guardrails, state machine and transaction logic are the real ones.

## Deploy

```bash
sam build -t infra/template.yaml
sam deploy --guided \
  --parameter-overrides BedrockTextModel=<model-or-inference-profile-id> \
                        BedrockVisionModel=<multimodal-model-id>
python infra/seed_data.py --table <stack>-inventory --region ap-south-1
```

Bedrock model IDs differ by region and account entitlement, so they are parameters, not
constants. Put the stack's `ApiBaseUrl` output into `frontend/.env` as `VITE_API_BASE`.

## Flow

```
CREATED ──start──► PICKING ──every line terminal──► BAG_VERIFICATION ──VERIFIED──► READY_FOR_PICKUP ──► DISPATCHED
                      │                                    │
             AVAILABLE ├─► PICKED                           └─ FAILED: fix the bag, shoot again
                       └─► OOS / EXCEPTION ─► SUBSTITUTED
```

| Endpoint | What it does |
| --- | --- |
| `POST /orders` | Stores the order and builds the pick path: serpentine walk by aisle then shelf, so the picker never doubles back. |
| `POST /orders/{id}/start` | `CREATED → PICKING`. |
| `POST /orders/{id}/intent` | Speech in, validated intent out, action executed. |
| `POST /orders/{id}/pick` | Tap equivalent of `CONFIRM_PICK`. |
| `POST /orders/{id}/exception` | Flags `OOS` or `EXCEPTION`, returns eligible swaps. |
| `GET  /orders/{id}/substitutes` | Eligible and blocked swaps, with the reason for each block. |
| `POST /orders/{id}/substitute` | Applies a swap: stock, line, invoice and audit move in one transaction. |
| `POST /orders/{id}/verify-bag` | Multimodal check against the manifest. |
| `POST /orders/{id}/dispatch` | Requires the handover token from the QR code. |
| `POST /speech` | Amazon Polly TTS for Hindi/English; Telugu falls back to browser speech. |
| `GET /dashboard` | Operations metrics and recent-order pipeline for the demo dashboard. |

## Where correctness lives

**Bedrock output contract** (`backend/app/contracts.py`). Five keys, three allowed actions, and
`selected_sku` must be one the backend already put in scope — the current line or a registered
substitute. Anything else is a hallucination, not a decision. A malformed schema, an unknown
action, an extra key, an empty spoken response or an invented SKU all produce
`manual_fallback()`: an audit row, and on-screen guidance in the picker's language. No 500s.

**Substitution policy** (`backend/app/substitution.py`). A candidate must be a registered
substitute, in the same category, in stock for the full quantity, not delisted, and priced
between 60% and 110% of the original. Categories like pharma and baby formula can never be
auto-substituted. Every rejection is logged with its reason code.

**Atomicity** (`backend/app/dynamo.py`). Substitutions use `TransactWriteItems`: reserve the
substitute's stock under a `stock_qty >= :q` condition, rewrite the line, adjust
`invoice_total`, write the audit row. Either all four land or none do — the bag and the bill
can never disagree. Picks work the same way, so a double-tap or a retry cannot oversell.

**Bag verification fails closed** (`validate_bag_verdict`). A `VERIFIED` verdict below 0.55
confidence becomes `FAILED`, as does a pass that names missing items. SKUs the model invented
are dropped before anyone sees them. Only a real pass mints the handover token.

## Voice

The picker taps the mic to capture Telugu, Hindi or English with the browser Speech Recognition API.
The utterance, current line and eligible substitutes go to Bedrock, which returns a validated action
and local-language response. For spoken output, Hindi/English use **Amazon Polly** through the
`POST /speech` endpoint; Telugu falls back to browser speech because Amazon Polly does not currently
provide a Telugu voice. If Polly is unavailable, the app automatically falls back to browser speech
so voice output never blocks the picking workflow.

Every action voice can take has a button next to it. Mic permission denied, noisy aisle, thick
gloves: the job still gets done.

## Demo reliability

`Ctrl+Shift+D`, or four taps on the progress rail, opens a panel that sends pre-baked intents
straight to `/intent`. They run through the same guardrail as live speech, so the audience sees
the real validation path — including two deliberate failures: a hallucinated SKU and malformed
JSON, both of which fall back to manual guidance instead of breaking.

## What is not in here

Deliberate MVP gaps, in the order they would matter:

- **No auth on the API.** Every endpoint is open. Before any real store, put a Cognito authorizer
  (or an API key per device) on the HTTP API and check `picker_id` against the token.
- **Dashboard order data uses a bounded scan for the prototype.** A production queue/dashboard should
  use GSIs and paginated queries rather than table scans.
- **Bag verification trusts one photo.** No weight check, no per-item count beyond what is
  visible. Pair it with a scale or a scan step before it gates real dispatch.
- **Route ignores travel cost between zones** — it sorts within a serpentine walk but does not
  solve a TSP across a multi-zone store.

## Tests

```bash
cd backend && python -m pytest -q      # 27 tests
```

Routing determinism, guardrail rejection, substitution policy bounds, state-machine legality.
No AWS calls, no network.

## Operations dashboard

The PWA includes a lightweight **Operations Control** view with orders today, average pick time,
OOS events, AI substitutions, bag-verification rate, guardrail rejections, pipeline status and
recent orders. It refreshes every 15 seconds. The prototype uses a bounded DynamoDB scan; production
should replace this with indexed, paginated queries.

## Amazon Polly setup

The Lambda role includes `polly:SynthesizeSpeech`. Configure `POLLY_HI_VOICE` and `POLLY_EN_VOICE`
if a different supported voice is desired. The default is `Aditi`, using `hi-IN` for Hindi and `en-IN`
for English. Telugu remains browser-TTS fallback because Polly does not currently provide a Telugu voice.

## Final MVP additions
- Item-level product visual verification: picker can upload/take a product photo before confirming a pick. AWS deployment sends the image to Bedrock vision; local mode uses a clearly labelled demo adapter.
- Final bag verification remains a second visual checkpoint before QR handover.
- Offline resilience: active order is cached on-device and pick/OOS actions can be queued locally and replayed when connectivity returns. Substitution and visual AI decisions intentionally require a live connection because stock, price and model inference must be current.
- Operations Control Tower: workload, order flow, stockout recovery, item visual checks, bag accuracy, live picker queue and aisle load.
- Amazon Polly: Hindi/English output through the `/speech` API with browser TTS fallback. Telugu uses browser TTS because Polly has no Telugu voice.
- Voice input remains browser SpeechRecognition for the prototype; use HTTPS/localhost and Chrome/Edge.
