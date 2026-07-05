# Deployment Guide

## Prerequisites

- Python 3.12+ or Docker
- DeepSeek API key (https://platform.deepseek.com)
- Twilio account with WhatsApp sandbox or business number
- A server with a public IP or HTTPS domain (Twilio requires HTTPS)

## Quick Start (Local Dev)

```bash
cp .env.example .env
# Edit .env with your real keys
pip install -r requirements.txt
python main.py dashboard --port 8081 &
python main.py server --port 8080 &
```

## Docker Deployment

```bash
cp .env.example .env
# Edit .env with your real keys
docker compose up -d dashboard
# For Twilio server: uncomment the server block in docker-compose.yml
docker compose up -d server
```

## Twilio WhatsApp Setup

1. Go to https://console.twilio.com → Messaging → WhatsApp Sandbox
2. Set webhook URL: `https://your-domain.com/whatsapp/webhook`
3. Method: POST
4. Send the join code from your phone to the sandbox number

For local testing, use ngrok:
```bash
ngrok http 8080
# Use the ngrok HTTPS URL as your Twilio webhook
```

## Environment Variables

| Variable | Required | Purpose |
|----------|----------|---------|
| DEEPSEEK_API_KEY | Yes | LLM API key |
| TWILIO_ACCOUNT_SID | Server only | Twilio credentials |
| TWILIO_AUTH_TOKEN | Server only | Twilio credentials |
| TWILIO_WHATSAPP_NUMBER | Server only | Your WhatsApp number |
| API_TOKEN | Yes | Dashboard + printer agent auth |
| STRIPE_SECRET_KEY | No | Online payment (not in Israel) |
| STRIPE_WEBHOOK_SECRET | No | Online payment (not in Israel) |

## Monitoring

- `/health` — server health with DB + LLM status
- `/metrics` — order counts, LLM calls, errors (token-protected)
- Dashboard `/` — overview with per-restaurant stats
- Server logs in JSON format: set `LOG_JSON=true`

## Backup

The SQLite database (`order_agent.db`) and `orders/` directory contain all data.
Back up both:
```bash
cp order_agent.db order_agent.db.bak
cp -r orders orders.bak
```
