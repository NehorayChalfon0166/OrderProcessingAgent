# Multi-Restaurant System

## Overview

The system supports multiple restaurants from a single deployment. Each
restaurant is fully isolated — its own menu, its own WhatsApp number,
and independent sessions and orders.

## How It Works

### Restaurant Registry

`restaurant.py` provides `RestaurantRegistry` — loaded once at startup
from `restaurants.json`. It creates a `Catalogue` and `PricingEngine`
per restaurant and indexes them by both ID and Twilio phone number.

### Routing

Every Twilio webhook carries two phone numbers:

| Field | Who | Used for |
|---|---|---|
| `From` / `WaId` | The customer | `session_id` — identifies the person |
| `To` | The restaurant's WhatsApp number | Looked up in `RestaurantRegistry` |

The `To` number determines which restaurant is being contacted. After
routing, `restaurant_id` (a slug like `"marios_pizzeria"`) carries the
restaurant identity through the rest of the flow.

### Session Isolation

Sessions and orders live in restaurant-scoped subdirectories:

```
sessions/{restaurant_id}/{phone_digits}.json
orders/{restaurant_id}/{phone_digits}_{timestamp}.json
```

The same customer can have active orders at different restaurants
simultaneously — each is a separate session.

### Session vs Order

| | Session | Order |
|---|---|---|
| **What** | Live conversation | Finalized receipt |
| **Mutable?** | Yes — every turn | No — written once |
| **How many?** | One active per (restaurant, customer) | Many over time |
| **Replaced?** | Terminal sessions overwritten | Never |

## Adding a Restaurant

1. Get the menu from the restaurant owner (PDF, image, or text).
2. Convert it to JSON using the reference schema in [menu_schema.md](menu_schema.md).
   Share the schema doc with any LLM along with the menu — prompt:
   _"Convert this restaurant menu into the JSON format defined above."_
3. Save the output as `menus/{restaurant_slug}.json`.
4. Add the entry to `restaurants.json`:
   ```json
   {
     "restaurants": {
       "your_slug": {
         "name": "Display Name",
         "menu_path": "menus/your_slug.json",
         "twilio_phone": "+1234567890"
       }
     }
   }
   ```
5. Set up the Twilio WhatsApp number to POST webhooks to your server.
6. Restart the server.

No code changes needed — the registry loads new restaurants on startup.

## Configuration

`restaurants.json` keys:

| Field | Required | Description |
|---|---|---|
| `name` | Yes | Human-readable display name |
| `menu_path` | Yes | Path to the menu JSON file |
| `twilio_phone` | Yes | Twilio WhatsApp number (with country code) |

## Error Handling

- Missing `restaurants.json` → `FileNotFoundError` at startup
- Missing `twilio_phone` → `ValueError` at startup
- Unknown `To` number in webhook → HTTP 500 `"Unknown restaurant"`
- Empty `restaurants` key → `ValueError`
