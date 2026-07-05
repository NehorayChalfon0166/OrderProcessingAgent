# Twilio Integration — Architecture Plan

## Context

The core agent (`master` branch) is a working CLI order-processing system.
`process_turn()` takes a string, returns a string. The Twilio integration is
an I/O layer supporting **two channels**: WhatsApp and Voice. The core agent
loop, tools, catalogue, pricing, and session persistence require **zero changes**.

Unlike Meta's WhatsApp Cloud API (which has a business verification blocker
for new accounts), Twilio offers a WhatsApp Sandbox that works immediately
with no business verification. Plus, Twilio's Voice API enables phone-call
ordering with speech recognition — a feature Meta's API can't provide.

## Twilio Free Trial

- **No credit card required**, 30 days
- **100 free WhatsApp messages** (service conversations — customer texts first)
- **75 free voice minutes** (includes speech recognition + transcription)
- **Up to 5 verified phone numbers** (sign-up number auto-verified)
- **WhatsApp Sandbox**: shared number `+14155238886`, recipients join via `join <code>`
- **Trial restriction**: may limit custom message bodies for SMS; WhatsApp
  sandbox allows free-form messages within the 24h customer service window
  (needs verification during setup)

## Architecture

```
WhatsApp User → Twilio API → Webhook (FastAPI) → Twilio Client (async reply)
Phone Caller  → Twilio API → Webhook (FastAPI) → Voice Handler (TwiML reply)
                                   ↑                    ↓
                            Signature Verify    SessionRouter.get_or_create()
                                                 process_turn()
                                                 session.save()
```

### Key Differences from Meta's WhatsApp API

| Aspect | Meta (Cloud API) | Twilio |
|---|---|---|
| Webhook format | JSON (`application/json`) | Form-encoded (`application/x-www-form-urlencoded`) |
| Reply mechanism | REST API only (async) | TwiML response (sync) OR REST API (async) |
| Phone format | `972539534345` | `whatsapp:+972539534345` |
| Sender identity | `from` in JSON | `From` + `WaId` in form data |
| Signature | `X-Hub-Signature-256` (SHA256 HMAC) | `X-Twilio-Signature` (SHA1 HMAC) |
| Voice support | None | Full — `<Gather input="speech">`, `<Say>`, `<Dial>` |
| Business verification | Required for production | Not required for sandbox |

## New Files

| File | Purpose |
|---|---|
| `twilio_client.py` | Thin wrapper around Twilio REST API + signature validation + payload parsing |
| `voice_handler.py` | TwiML generation for voice calls (Gather speech → transcribe → respond) |
| `server.py` | FastAPI webhook receiver for WhatsApp + Voice endpoints |

### `twilio_client.py`

```python
class TwilioClient:
    def __init__(self, account_sid, auth_token, whatsapp_number)

    def send_whatsapp_message(to: str, text: str) -> str
        # Uses twilio.rest.Client.messages.create()
        # From: "whatsapp:+14155238886", To: "whatsapp:+972..."
        # Returns message SID

    @staticmethod
    def validate_webhook(url: str, params: dict, signature: str, auth_token: str) -> bool
        # Uses twilio.request_validator.RequestValidator
        # Validates X-Twilio-Signature header

    @staticmethod
    def extract_whatsapp_message(form_data: dict) -> tuple[str, str] | None
        # Returns (wa_id, body) or None
        # WaId is the clean WhatsApp ID (no "whatsapp:" prefix)
        # Skips non-text messages (media, location, etc.)
```

### `voice_handler.py`

```python
def build_incoming_call_twiml(action_url: str) -> str
    # Returns TwiML XML:
    # <Response>
    #   <Gather input="speech" action="{action_url}" speechTimeout="auto">
    #     <Say>Welcome to Mario's Pizzeria! What would you like to order?</Say>
    #   </Gather>
    #   <Say>Sorry, I didn't catch that. Goodbye!</Say>
    # </Response>

def build_agent_reply_twiml(text: str, action_url: str) -> str
    # Returns TwiML XML:
    # <Response>
    #   <Say>{escaped_text}</Say>
    #   <Gather input="speech" action="{action_url}" speechTimeout="auto">
    #     <Say>Anything else?</Say>
    #   </Gather>
    # </Response>

def build_goodbye_twiml(text: str) -> str
    # Returns TwiML XML:
    # <Response><Say>{escaped_text}</Say><Hangup/></Response>
```

### `server.py`

```python
# Endpoints:

POST /whatsapp/webhook
    # 1. Validate X-Twilio-Signature
    # 2. Extract WaId + Body via TwilioClient.extract_whatsapp_message()
    # 3. SessionRouter.get_or_create(WaId) → session (keyed on WaId)
    # 4. Async: process_turn(session, text, ...) → response
    # 5. TwilioClient.send_whatsapp_message(WaId, response)
    # 6. Return empty TwiML: <Response></Response>

POST /voice/incoming
    # 1. Return voice_handler.build_incoming_call_twiml(action_url)

POST /voice/speech-result
    # 1. Extract Caller (From) + SpeechResult
    # 2. SessionRouter.get_or_create(From) → session
    # 3. process_turn(session, SpeechResult, ...) → response
    # 4. If session is complete/cancelled: return build_goodbye_twiml(response)
    # 5. Else: return build_agent_reply_twiml(response, action_url)
```

## Files Modified

| File | Change |
|---|---|
| `config.py` | Add Twilio config (`TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_WHATSAPP_NUMBER`) |
| `main.py` | Add `server` subcommand (same as whatsapp-integration branch) |
| `requirements.txt` | Add `twilio`, `fastapi`, `uvicorn` |
| `.env.example` | Add Twilio section |

## Unchanged

- `models.py`, `catalogue.py`, `pricing.py`, `tools.py`, `session.py`
- `prompts.py`, `llm_client.py`, `agent_loop.py`

## Session Identity

- **WhatsApp**: sessions keyed on `WaId` (e.g. `972539534345`) — same as
  the whatsapp-integration branch approach (phone digits only)
- **Voice**: sessions keyed on caller ID (`From`), sanitized to digits
- **Cross-channel**: WhatsApp and Voice sessions are separate files even
  for the same phone number (different channels, different session files).
  This is intentional — a customer might have a WhatsApp order in progress
  while calling about a different matter.

## Flow: WhatsApp

```
1. User texts "I want a pizza" to sandbox number
2. Twilio POSTs form-encoded data to /whatsapp/webhook
3. Server validates signature
4. Extract WaId="972539534345", Body="I want a pizza"
5. router.get_or_create("972539534345") → OrderSession
6. process_turn(session, "I want a pizza", ...) → "What size?"
7. session.save()
8. twilio_client.send_whatsapp_message("972539534345", "What size?")
9. Return <Response></Response> (empty TwiML — we already replied via REST)
```

## Flow: Voice

```
1. Customer calls your Twilio phone number
2. Twilio POSTs to /voice/incoming
3. Server returns TwiML: <Say>Welcome...</Say><Gather input="speech" action="/voice/speech-result">
4. Customer speaks: "I want a large pepperoni pizza"
5. Twilio transcribes, POSTs to /voice/speech-result with SpeechResult
6. router.get_or_create(caller_id) → OrderSession
7. process_turn(session, "I want a large pepperoni pizza", ...) → "Added! Anything else?"
8. If not complete: return TwiML with <Say> + another <Gather>
9. If complete: return TwiML with <Say> + <Hangup/>
```

## Concurrency: Per-Session Lock

Same pattern as whatsapp-integration. Per-phone `asyncio.Lock`:

```python
_locks: dict[str, asyncio.Lock] = {}

def _get_lock(identity: str) -> asyncio.Lock:
    if identity not in _locks:
        _locks[identity] = asyncio.Lock()
    return _locks[identity]
```

Applied to both WhatsApp and Voice endpoints. Second message/call waits for
the first to complete before processing.

## WhatsApp: Sync vs Async Reply

Twilio expects a TwiML response to its webhook. Two options:

**Option A: Synchronous TwiML** — call `process_turn()` and return `<Say>`
directly. Clean, but the LLM call blocks the webhook for 2–5 seconds.
Twilio's timeout is 15 seconds — safe but slow for the user.

**Option B: Async REST** (chosen) — return empty `<Response></Response>`
immediately, then call `process_turn()` and send the reply via REST API.
This is the same pattern we used with Meta. The user sees the reply a
few seconds later as a separate message. This also handles long LLM calls
gracefully (they're async, not bound to the webhook timeout).

For Voice, we MUST respond synchronously — the caller is on the phone.
The LLM call blocks for 2–5 seconds, then we return TwiML with the reply.

## Session Lifecycle

Sessions are keyed on phone number (sanitized to digits). Multiple orders
from the same phone are separate entities:

- If an active session exists and was updated < 2 hours ago → resume it
- If an active session exists with `updated_at` > 2 hours ago → ask the
  customer "You have an unfinished order from earlier. Continue or start
  fresh?" (deterministic, no LLM involved — the router compares timestamps)
- If no active session → create new
- Customer can say "cancel" / "start over" at any time → `cancel_order`
  tool cancels current order, next message creates a fresh session

## Two-Call Agent Loop

See `docs/agent_loop.md` for full design. Summary:

1. **Call 1:** LLM with tools → returns tool calls, executes them silently
2. **Call 2:** LLM without tools → sees tool results, responds naturally

This ensures the customer never sees "Adding..." without a follow-up
"Added!" — every turn produces a complete response.

## Dependencies to Add

```
twilio>=9.0.0
fastapi>=0.110.0
uvicorn>=0.29.0
```

## Setup

1. Sign up at https://www.twilio.com/try-twilio (no credit card)
2. Get Account SID + Auth Token from Twilio Console dashboard
3. Activate WhatsApp Sandbox at https://www.twilio.com/console/sms/whatsapp/sandbox
4. Copy the sandbox join code
5. Set webhook URL in sandbox settings to `https://<ngrok>/whatsapp/webhook`
6. Buy a Twilio phone number ($1/month) for Voice, or use a trial number

## `.env` Variables

```bash
TWILIO_ACCOUNT_SID=ACxxxxxxxx
TWILIO_AUTH_TOKEN=xxxxxxxx
TWILIO_WHATSAPP_NUMBER=+14155238886  # sandbox number
```

## Testing Strategy

### Unit Tests (no API)
- `twilio_client.extract_whatsapp_message()` — parse form-encoded payloads
- `twilio_client.validate_webhook()` — signature validation
- `voice_handler.build_incoming_call_twiml()` — TwiML output
- `voice_handler.build_agent_reply_twiml()` — TwiML with agent text

### Integration (requires Twilio trial)
- Send "join <code>" from WhatsApp → verify sandbox join
- Send "Hi" → verify greeting response
- Full order flow via WhatsApp
- Full order flow via Voice call

## Build Order

1. `config.py` — add Twilio env vars
2. `requirements.txt` — add `twilio`, `fastapi`, `uvicorn`
3. `twilio_client.py` — REST client + signature + extract
4. `voice_handler.py` — TwiML generation
5. `session_router.py` — port from whatsapp-integration (phone → session)
6. `server.py` — FastAPI with WhatsApp + Voice webhooks
7. `main.py` — server/CLI split
8. Manual: Twilio signup + sandbox activation + ngrok

## Risk: Trial Template Restriction

Twilio trial docs state: "Pre-defined content only: SMS, WhatsApp, and
Email must use Twilio-provided templates."

However, the WhatsApp Sandbox docs explicitly state that within the 24h
customer service window (opened by `join <code>`), free-form messages are
allowed. This may mean the template restriction applies only to
business-initiated messages or SMS, not to service conversations in the
sandbox. **Needs verification during setup.** If templates are required,
we can create generic templates matching the agent's response patterns.
