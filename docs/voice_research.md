# Voice Call Integration — Research & Architecture Options

Status: **RESEARCH** (not yet planned for implementation)

## Context

The project already has a working WhatsApp channel via Twilio. The existing
`docs/twilio_integration.md` outlines a `<Gather input="speech">` approach for
voice. This document re-evaluates that choice against what's available in 2026,
researches costs, and recommends an architecture.

## The Core Question

`process_turn(user_message: str) -> str` is the universal interface. The voice
layer needs to:

1. Convert speech → text → feed to `process_turn`
2. Take the response text → convert to speech → play to caller
3. Loop until the order is terminal (COMPLETED/CANCELLED)

The question is **how** to do steps 1 and 2. Two fundamentally different
approaches exist.

---

## Approach A: `<Gather input="speech">` (TwiML Polling)

This is the approach already planned in `twilio_integration.md`.

### How it works

```
Caller → Twilio → POST /voice/incoming (your server)
                  ← TwiML: <Gather input="speech" action="/voice/speech-result">
                             <Say>Welcome! What would you like?</Say>
                           </Gather>
Caller speaks → Twilio transcribes (Google STT or Deepgram internally)
             → POST /voice/speech-result?SpeechResult=... (your server)
             → process_turn(SpeechResult) → response text
             ← TwiML: <Say>response text</Say>
                       <Gather input="speech" action="/voice/speech-result">...</Gather>
```

Each turn is an HTTP request/response cycle. Twilio handles all STT internally;
you never touch raw audio.

### STT Provider (2026 update)

As of 2025, `<Gather>` supports multiple STT providers, selectable per utterance:

| Provider | Models |
|----------|--------|
| Google STT v1 | `default`, `phone_call`, `numbers_and_commands` |
| Google STT v2 | `googlev2_telephony`, `googlev2_telephony_short` (newer, better) |
| Deepgram | Nova-2 and other models |
| "Twilio Picks" (default) | Auto-selects best provider per utterance |

For a Hebrew-language ordering system, `googlev2_telephony` would be the
recommended choice — Google's telephony model has broader language support.

Set via: `<Gather input="speech" speechModel="googlev2_telephony" language="he-IL">`

### TTS: `<Say>` with neural voices

Twilio's `<Say>` supports Amazon Polly neural voices. Hebrew support via Polly
needs verification — if unavailable, fall back to the standard voice.

### Pros

- **Simplicity.** No WebSocket server, no audio processing, no external STT/TTS
  API keys to manage. Everything is plain HTTP.
- **Already partially designed.** `twilio_integration.md` has the TwiML generation
  functions (`build_incoming_call_twiml`, `build_agent_reply_twiml`,
  `build_goodbye_twiml`) spec'd out.
- **Time to MVP:** days, not weeks. The infrastructure is nearly identical to
  the WhatsApp webhook — same server, same `process_turn` call.
- **No additional API costs** beyond Twilio voice minutes. STT and TTS are
  bundled into the per-minute rate.

### Cons

- **Latency: 2-5 seconds per turn.** The polling loop adds overhead: Twilio
  transcribes → HTTP POST → your server → `process_turn` (LLM call, 1-3s) →
  HTTP response → Twilio synthesizes → plays. Each round trip adds up.
- **Turn-based only.** No barge-in (interrupting the bot mid-speech). The
  caller must wait for the bot to finish talking before speaking.
- **Short utterances.** Designed for IVR-style input, not extended conversation.
  Long, complex orders may hit recognition limits.
- **No partial transcripts.** You only get the final transcription, not
  incremental results. You can't detect hesitation or mid-speech corrections.
- **Hebrew STT quality with `<Gather>` is unverified.** Google's `googlev2_telephony`
  supports Hebrew but real-world accuracy with menu items, mixed Hebrew-English
  (common in Israeli restaurants), and accented speech is unknown.

---

## Approach B: Media Streams (WebSocket, Bidirectional Audio)

### How it works

```
Caller → Twilio → POST /voice/incoming (your server)
                 ← TwiML: <Connect><Stream url="wss://your-server/stream"/></Connect>
                 → WebSocket opens, raw μ-law 8kHz audio streams in both directions

Your WebSocket server:
  Inbound audio → STT service (Deepgram/AssemblyAI) → text
  text → process_turn() → response text
  response text → TTS service (ElevenLabs/OpenAI/Deepgram) → audio
  audio → Twilio → caller hears it
```

Audio flows continuously. Your server is a bridge between Twilio's audio and
your STT/LLM/TTS pipeline.

### Provider options (DeepSeek-compatible)

Since the project uses DeepSeek (not OpenAI), the OpenAI Realtime API
(speech-to-speech) path is not directly applicable. Instead, you'd use
separate STT and TTS services:

**STT options:**

| Provider | Model | Latency | Hebrew Support | Cost |
|----------|-------|---------|----------------|------|
| Deepgram | Nova-2 | ~300ms | Yes | ~$0.0059/min |
| AssemblyAI | Universal-3 Pro | ~500ms | Limited | ~$0.015/min |
| Google STT | chirp_telephony | ~400ms | Yes | ~$0.016/min |

**TTS options:**

| Provider | Model | Latency | Hebrew Support | Cost |
|----------|-------|---------|----------------|------|
| ElevenLabs | Multilingual v2 | ~200ms | Yes (good) | ~$0.015/1K chars |
| Deepgram | Aura-2 | ~200ms | Limited | ~$0.015/1K chars |
| OpenAI TTS | tts-1 | ~300ms | Limited | ~$0.015/1K chars |
| Google TTS | WaveNet | ~300ms | Yes | ~$0.016/1K chars |

### Pros

- **Low latency: 650-1200ms end-to-end.** Streaming audio with incremental
  transcripts means you can respond while the user is still speaking.
- **True barge-in.** Caller can interrupt the bot mid-speech — cancel TTS the
  moment VAD detects speech. Feels like a real conversation.
- **Full STT/TTS control.** Custom vocabulary (menu items!), language models,
  fine-tuned recognition for restaurant-domain speech.
- **Raw audio access.** Can run custom processing — sentiment, voice
  authentication, language detection.

### Cons

- **Significant complexity.** WebSocket server, audio encoding (μ-law ↔ PCM16,
  8kHz ↔ 16/24kHz conversions), connection lifecycle, reconnection logic.
- **Multiple external services.** STT API + TTS API + your DeepSeek LLM =
  three API calls per turn, each with its own latency, error modes, and costs.
- **Time to MVP: 4-12 weeks.** Substantially more engineering than `<Gather>`.
- **Higher operational cost** at low volume (paying for STT + TTS separately
  on top of Twilio voice minutes).
- **Infrastructure burden.** WebSocket servers need persistent connections,
  careful memory management, and scale differently than stateless HTTP.

---

## Approach C: Deepgram Voice Agent API (Managed Middle Ground)

Deepgram offers a managed voice agent API that bundles STT + LLM + TTS into
one pipeline. You configure it with a system prompt and function definitions,
and it handles the audio streaming internally.

### How it works

```
Caller → Twilio → Media Streams → Deepgram Voice Agent API
                                    ↓ (handles STT + LLM + TTS internally)
                                  Your server (function calling webhook)
                                    ↓
                                  process_turn() equivalent runs as tool calls
```

### Pros

- Less infrastructure than raw Media Streams (Deepgram manages the audio pipeline)
- Function calling support (your tools can be called from the voice agent)
- Single API to manage instead of STT + TTS separately

### Cons

- **Vendor lock-in.** Your entire voice pipeline is Deepgram's black box.
- **LLM is Deepgram's choice, not yours.** You can't use DeepSeek as the
  reasoning model — Deepgram's API uses its own LLM or OpenAI under the hood.
  **This is a dealbreaker** for this project, which is built around DeepSeek.
- **Function calling mismatch.** The DeepSeek tool definitions and Deepgram's
  function schema may not align.

**Verdict: Not suitable.** The project's core value is the DeepSeek-powered
agent loop with custom tools. Offloading the LLM to Deepgram defeats the purpose.

---

## Israel Voice Pricing (Twilio)

Pricing retrieved July 2026 from Twilio's published rates:

### Per-minute rates

| Direction | Local | Mobile | Toll-Free |
|-----------|-------|--------|-----------|
| **Outbound** (you call customer) | $0.0294 | $0.0646 | $0.1344 |
| **Inbound** (customer calls you) | $0.0107 | $0.0350 | $0.1344 |

General outbound rate (not split by type): **$0.0659/min**.
Palestine Region outbound: **$0.3625/min**.

### Monthly phone number fees

| Number Type | Monthly Cost |
|-------------|-------------|
| Local | **$5.50/mo** |
| Mobile | **$15.00/mo** |
| Toll-Free | **$22.00/mo** |

### What this means

- **Inbound calls are cheap.** A 5-minute order on a local number costs ~$0.05
  in voice minutes.
- **Outbound calls (e.g., order confirmation callback) cost more** — ~$0.15-0.32
  for a 5-minute mobile call.
- **A local number ($5.50/mo)** is sufficient for inbound ordering. No need for
  mobile or toll-free numbers.
- **Total per-order cost estimate (inbound, `<Gather>` approach):** ~$0.05-0.10
  in Twilio charges for a typical 5-10 minute ordering call.
- **Total per-order cost estimate (Media Streams):** ~$0.05-0.10 Twilio voice +
  ~$0.03-0.06 STT + ~$0.01-0.03 TTS = ~$0.09-0.19 per order. Still negligible.

---

## Comparison Matrix

| Dimension | `<Gather>` (TwiML) | Media Streams (WebSocket) |
|-----------|-------------------|--------------------------|
| **Time to MVP** | Days (plan exists) | 4-12 weeks |
| **Latency per turn** | 2-5s | 650-1200ms |
| **Barge-in** | No | Yes |
| **Hebrew STT** | Google `googlev2_telephony` (built-in) | Deepgram/Google (your choice) |
| **Custom vocabulary** | Limited (`hints` attr) | Full control |
| **Raw audio access** | No | Yes |
| **Infrastructure** | Stateless HTTP (same server) | WebSocket server + STT + TTS |
| **External API keys** | None (Twilio handles STT+TTS) | STT service + TTS service |
| **Works with DeepSeek** | ✅ (HTTP request/response) | ✅ (text in, text out) |
| **Cost per order** | ~$0.05-0.10 | ~$0.09-0.19 |
| **Scaling** | Horizontal (stateless HTTP) | More complex (WebSocket state) |
| **Error recovery** | Simple (HTTP retry) | Complex (WS reconnect + audio state) |

---

## How `process_turn` Fits In Both Approaches

The key insight: **`process_turn` doesn't care about the channel.** It takes
text, returns text. Both voice approaches converge on this:

### `<Gather>` path

```python
# POST /voice/speech-result?SpeechResult=...
speech_text = request.query_params["SpeechResult"]
caller_id = request.form["From"]

session = router.get_or_create(caller_id)
response_text = process_turn(session, speech_text)

if session.state in ("COMPLETED", "CANCELLED"):
    return build_goodbye_twiml(response_text)
else:
    return build_agent_reply_twiml(response_text, action_url)
```

### Media Streams path

```python
# WebSocket handler
async def handle_audio(ws):
    # Audio flows in continuously, STT produces incremental transcripts
    # When STT detects end of utterance:
    utterance_text = stt_final_result

    session = router.get_or_create(caller_id)
    response_text = process_turn(session, utterance_text)

    # Send response_text to TTS, stream audio back to Twilio
    await stream_tts_response(ws, response_text)

    if session.state in ("COMPLETED", "CANCELLED"):
        await ws.close()
```

**`process_turn` is the same in both cases.** The voice handler is a thin I/O
adapter — speech-to-text on the way in, text-to-speech on the way out.

---

## Voice-Specific Concerns (Both Approaches)

### 1. Latency of `process_turn`

`process_turn` calls DeepSeek twice (two-call agent loop: tool call → response).
Each LLM call takes 1-3 seconds. Total: 2-6 seconds of silence while the
caller waits.

**Mitigation for `<Gather>`:** Play a brief `<Say>` hold message ("Let me check
that...") before the LLM call, but this adds complexity to the synchronous flow.

**Mitigation for Media Streams:** Stream a filler audio buffer ("Let me look at
the menu...") while waiting for the LLM. More natural.

### 2. Hebrew + English Mix

Israeli restaurant orders are often code-switched: "אני רוצה large pepperoni
עם extra cheese." Hebrew STT engines vary in how well they handle this.

- Google `googlev2_telephony` — Hebrew support listed but mixed-language
  accuracy unknown. Needs real-world testing.
- Deepgram Nova-2 — has multilingual models, can be configured with
  `language="he"` + `multilanguage=true`.
- The project's DeepSeek LLM already handles mixed Hebrew/English input
  (prompts are in English, customer messages can be Hebrew).

### 3. Spelling Out Details

Voice orders involve details that are easy in text but hard in speech:
- "Make that a large" — needs `<Gather>` to capture the follow-up
- "No, not pepperoni, pepperoncini" — STT can easily confuse these
- Phone numbers, addresses, credit card digits — better handled via DTMF
  (`<Gather input="dtmf speech">`)

### 4. Session Identity

Voice sessions are keyed on `From` (caller ID), sanitized to digits. Same
pattern as WhatsApp (`WaId`). Cross-channel: a WhatsApp session and a Voice
session from the same phone number are separate — intentional design per
`twilio_integration.md`.

### 5. Concurrency

Same per-phone `asyncio.Lock` pattern from WhatsApp applies. A second call
from the same number waits for the first to finish processing.

---

## Recommendation: Start with `<Gather>`, Keep Media Streams as v2

### Why `<Gather>` first

1. **The plan already exists.** `twilio_integration.md` has the architecture
   spec'd out — `voice_handler.py` with three TwiML builder functions, two
   FastAPI endpoints (`/voice/incoming`, `/voice/speech-result`).
2. **It shares infrastructure with WhatsApp.** Same FastAPI server, same
   `process_turn` call, same session router. The voice endpoints are ~50
   lines of code each.
3. **Fast time to value.** You can have voice ordering working in days, not
   weeks. This validates demand before investing in a low-latency pipeline.
4. **Good enough for restaurant ordering.** Ordering is turn-based by nature:
   "What would you like?" → "A large pizza" → "Added. Anything else?" → "No
   that's all" → "Your total is ₪45. Goodbye." The 2-5s latency between turns
   is acceptable — it mirrors the natural pace of placing an order.
5. **No new external dependencies.** Twilio handles STT and TTS. No Deepgram,
   ElevenLabs, or AssemblyAI API keys to manage.
6. **Hebrew STT is the biggest unknown** — `<Gather>` lets you test it quickly
   without building a whole audio pipeline. If Google's `googlev2_telephony`
   Hebrew accuracy is poor, you'll know within an hour of testing.

### When to upgrade to Media Streams

Consider Media Streams when:
- **Hebrew STT via `<Gather>` is inadequate** and you need Deepgram's custom
  models with menu-item vocabulary boosting.
- **Barge-in becomes essential** — customers complain about waiting for the
  bot to finish talking.
- **Latency is hurting conversion** — drop-offs during the 2-5s silence
  between turns.
- **You've validated demand** — enough voice order volume to justify the
  engineering investment.

### Hybrid approach (medium-term)

A common pattern: use `<Gather>` for structured collection (phone number via
DTMF, confirmation yes/no) and Media Streams for the open-ended ordering
conversation. This gives you the best of both — simple where simple works,
streaming where conversation matters.

---

## Build Order for `<Gather>` MVP

This is already in `twilio_integration.md` but updated with 2026 specifics:

1. **Buy a Twilio phone number** — Israeli local number: **$5.50/mo**.
   Purchase at https://console.twilio.com/develop/phone-numbers/buy.
2. **Configure voice webhook** — point the number's "A call comes in" webhook
   to `https://<your-domain>/voice/incoming` (POST, HTTP).
3. **Implement `voice_handler.py`** — three functions:
   - `build_incoming_call_twiml(action_url)` — greet + `<Gather>`
   - `build_agent_reply_twiml(text, action_url)` — `<Say>` response + `<Gather>` follow-up
   - `build_goodbye_twiml(text)` — `<Say>` + `<Hangup/>`
4. **Add `/voice/incoming` and `/voice/speech-result` endpoints** to `server.py`.
5. **Set `<Gather>` attributes** for Hebrew:
   ```xml
   <Gather input="speech" language="he-IL" speechModel="googlev2_telephony"
           speechTimeout="auto" action="/voice/speech-result">
   ```
6. **Test with real Hebrew speech.** This is the critical validation gate.
   If STT accuracy is poor, re-evaluate the Media Streams path.
7. **Add `hints` for menu items** — `<Gather hints="פיצה, המבורגר, קולה, צ'יפס">`
   to boost recognition of common orders.

---

## Open Questions

1. **Hebrew STT quality with `<Gather>` + `googlev2_telephony`** — unknown.
   Must test with real Israeli-accented Hebrew + mixed Hebrew/English orders.
   This is the single biggest risk to the `<Gather>` approach.
2. **`<Say>` Hebrew voice quality** — does Twilio/Polly have a Hebrew neural
   voice? If not, is the standard voice acceptable?
3. **Outbound calling** — do you want to call customers back for order
   confirmation, delivery updates, etc.? Outbound uses the same `<Gather>`
   pattern but initiated via REST API (`twilio.rest.Client.calls.create()`).
   Requires a Twilio number capable of outbound dialing.
4. **Call recording for quality assurance** — `<Gather>` supports recording
   via the `record` attribute on `<Dial>`, or via `<Record>` verb before
   the conversation. Media Streams gives you raw audio that you can save
   directly.
5. **DTMF fallback** — should callers be able to press digits instead of
   speaking? Set `<Gather input="dtmf speech">` for dual mode.
6. **Twilio trial limitations** — the free trial gives 75 voice minutes with
   speech recognition. After that, pay-as-you-go. A local Israeli number
   ($5.50/mo) requires an upgraded (non-trial) account.

---

## Sources

- [Twilio `<Gather>` Reference](https://www.twilio.com/docs/voice/twiml/gather)
- [Twilio Voice Pricing — Israel](https://www.twilio.com/en-us/voice/pricing/il)
- [Twilio SMS Pricing — Israel](https://www.twilio.com/en-us/sms/pricing/il)
- [Multi-Provider Speech Recognition in `<Gather>` (2025 GA)](https://www.twilio.com/en-us/changelog/-gather--new-multi-provider-speech-recognition-models---upcoming)
- [AI Voice Assistant with Twilio + OpenAI Realtime API (Twilio Blog, Aug 2025)](https://www.twilio.com/en-us/blog/voice-ai-assistant-openai-realtime-api-python)
- [Building an Outbound Voice Agent with Twilio + Deepgram (Apr 2026)](https://www.twilio.com/en-us/blog/partners/integrations/building-an-outbound-voice-agent-with-twilio-and-deepgram)
- [Twilio Phone Agent with AssemblyAI Universal-3 Pro (Apr 2026)](https://www.assemblyai.com/blog/twilio-phone-agent-with-assemblyai-universal-3-pro-streaming)
- [Media Streams Overview](https://www.twilio.com/docs/voice/media-streams)
- [Deepgram vs Twilio: Real-Time Transcription Guide](https://deepgram.com/learn/deepgram-vs-twilio-real-time-transcription)
- [Clawphone — Twilio Voice/SMS Gateway (HN discussion on `<Gather>` vs Media Streams tradeoffs)](https://news.ycombinator.com/item?id=47131873)
