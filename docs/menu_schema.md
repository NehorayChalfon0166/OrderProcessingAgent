# Menu JSON Schema

The menu file defines a restaurant's full catalogue — categories, items, add-ons,
deals, and operational settings. One file per restaurant in `menus/{slug}.json`.

This document is the reference schema. Use it to hand-author menus or to share
with an LLM for parsing ("convert this restaurant menu into the following JSON format").

---

## Top-Level Fields

```json
{
  "restaurant_name": "string",
  "currency": "ISO 4217 code (e.g., \"ILS\", \"USD\")",
  "delivery_fee": "number",
  "min_order_amount": "number",
  "estimated_delivery_time": "string (e.g., \"30-45 min\")",
  "categories": [],
  "addons": [],
  "deals": []
}
```

All fields are required except `addons` and `deals` (omit or use `[]` if none).

---

## Categories

Each category has a stable `id` (deals reference this) and a display `name`.

```json
{
  "id": "pizzas",
  "name": "Pizzas",
  "items": []
}
```

Field | Type | Required | Notes
------|------|----------|------
`id` | string | yes | Stable identifier. snake_case. Used by deals to reference this category.
`name` | string | yes | Display name shown to customers and on kitchen tickets.
`items` | array | yes | Items in this category. Can be empty.

---

## Items

An item represents a single orderable product. It has one of two pricing models:

**Flat-price items** (sides, drinks, desserts) — a single `price`:

```json
{
  "id": "side_fries",
  "name": "Seasoned Fries",
  "description": "Crispy golden fries with Italian herb seasoning",
  "price": 4.99,
  "available": true
}
```

**Variant-priced items** (pizzas, shawarma — anything that comes in sizes):

```json
{
  "id": "pizza_margherita",
  "name": "Margherita",
  "description": "Tomato sauce, fresh mozzarella, basil",
  "available": true,
  "variants": [
    {"id": "small",  "name": "Small (10\")",  "price": 9.99,  "available": true},
    {"id": "medium", "name": "Medium (12\")", "price": 12.99, "available": true},
    {"id": "large",  "name": "Large (16\")",  "price": 15.99, "available": true}
  ],
  "default_variant": "medium",
  "addons": ["extra_cheese", "mushrooms", "olives"],
  "max_addons": 6
}
```

### Item Fields

Field | Type | Required | Notes
------|------|----------|------
`id` | string | yes | Unique identifier. snake_case. Used in tool calls and lookups.
`name` | string | yes | Display name. Customer-facing, fuzzy-matched.
`description` | string | yes | One-line description. Shown in hints.
`available` | boolean | yes | Set `false` to 86 this item. Runtime toggle.
`price` | number | one of* | Flat price. Use for items without variants. Mutually exclusive with `variants`.
`variants` | array | one of* | Variant (size) pricing. Mutually exclusive with `price`.
`default_variant` | string | with variants | `id` of the default variant. Used when the customer doesn't specify a size.
`addons` | array of strings | no | List of addon IDs available for this item. Omit or use `[]` if none.
`max_addons` | integer | no | Soft cap on how many addons per item. Defaults to unlimited if omitted.

*Exactly one of `price` or `variants` must be present.

### Variant Object

```json
{"id": "large", "name": "Large (16\")", "price": 15.99, "available": true}
```

Field | Type | Required | Notes
------|------|----------|------
`id` | string | yes | Stable identifier (e.g., "small", "medium", "large", "laffa", "pita").
`name` | string | yes | Display name. Can include details like "(16\")" for context.
`price` | number | yes | Price for this variant.
`available` | boolean | yes | Set `false` when this specific variant is out of stock. The item remains orderable in other variants.

---

## Add-Ons

Global add-ons that can be added to items. Referenced by `id` in each item's
`addons` list. Add-ons always cost extra — they increase the item price.

```json
{
  "id": "extra_cheese",
  "name": "Extra Cheese",
  "price": 1.50,
  "available": true
}
```

Field | Type | Required | Notes
------|------|----------|------
`id` | string | yes | Unique identifier. snake_case.
`name` | string | yes | Display name.
`price` | number | yes | Surcharge added to item unit price.
`available` | boolean | yes | Set `false` when out of stock.

The `addons` array is top-level on the menu. Individual items declare which
add-ons are valid for them via their `addons` field. An addon not listed on
the item won't be offered.

---

## Deals

Fixed-price bundles. A deal is registered as a product (can be added to cart
via `add_to_cart`). It references categories for what's included — the LLM
collects the customer's specific choices conversationally.

```json
{
  "id": "deal_family",
  "name": "Family Deal",
  "description": "2 Large Pizzas + 1 Side + 2 Large Drinks",
  "price": 34.99,
  "available": true,
  "items": [
    {"category": "pizzas", "quantity": 2, "variant": "large"},
    {"category": "sides", "quantity": 1},
    {"category": "drinks", "quantity": 2, "variant": "large"}
  ]
}
```

Field | Type | Required | Notes
------|------|----------|------
`id` | string | yes | Unique identifier. snake_case.
`name` | string | yes | Display name. Customer-facing.
`description` | string | yes | What's in the deal. Shown to the customer.
`price` | number | yes | Fixed deal price.
`available` | boolean | yes | Set `false` to disable this deal.
`items` | array | yes | What the deal includes. At least one entry.

### Deal Item Object

```json
{"category": "pizzas", "quantity": 2, "variant": "large"}
```

Field | Type | Required | Notes
------|------|----------|------
`category` | string | yes | `id` of the category this deal item comes from.
`quantity` | integer | yes | How many items from this category are included.
`variant` | string | no | Forced variant. If the category has variants, this picks which one is included in the deal.

### How Deals Work at Runtime

1. Customer: "I want the Family Deal"
2. `add_to_cart` adds the deal as one cart item at the fixed `price`
3. The cart item gets `missing_options` derived from `items[]` — e.g. "Choose 2 large pizzas", "Choose 1 side", "Choose 2 large drinks"
4. The LLM asks the customer to pick
5. Customer specifies choices
6. LLM calls `update_item` with the deal item reference, writing choices into `special_instructions`
7. The kitchen ticket prints: "Family Deal — Margherita, Pepperoni, Garlic Bread, 2x Coca Cola"

---

## Currency

All prices in the menu are in the currency specified by the top-level `currency`
field. This is used by the Stripe integration to set the checkout currency.

---

## Availability Runtime

The `available` boolean on items, variants, add-ons, and deals is the static
default. At runtime, the menu management tool (Component 6) toggles these off
for out-of-stock items. Changes persist to the menu JSON file.

- An unavailable item is excluded from the catalogue entirely (not offered).
- An unavailable variant makes that specific size unorderable.
- An unavailable add-on is excluded from the options for items it belongs to.
- An unavailable deal is excluded from the catalogue.

If an `available` field is missing, it defaults to `true`.

---

## Half-and-Half / Split Items

The schema does not model split items (e.g., "half Margherita, half Pepperoni").
This is captured via `special_instructions` on the cart item — it prints on
the kitchen ticket and the chef reads it. The LLM handles any pricing nuances
conversationally (e.g., charging for the more expensive half).

---

## Known Limitations

- **"Choose N from category" deals** (e.g., "pick any 3 rolls for ₪120")
  are not supported. Deals have a fixed set of included categories + quantities.
- **Add-ons with their own variants** are not supported. All add-ons are flat-price.
- **Time-based or conditional pricing** (lunch specials, happy hour) is not modeled.
- **Minimum add-ons** (`min_addons`) is not yet implemented. Only `max_addons`
  acts as a soft cap on add-ons per item.

---

## Full Example

A shawarma restaurant menu showing the complete schema:

```json
{
  "restaurant_name": "Abu Dhabi Shawarma",
  "currency": "ILS",
  "delivery_fee": 10.00,
  "min_order_amount": 30.00,
  "estimated_delivery_time": "20-30 min",
  "categories": [
    {
      "id": "shawarma",
      "name": "Shawarma",
      "items": [
        {
          "id": "shawarma_chicken",
          "name": "Chicken Shawarma",
          "description": "Marinated chicken with tahini, hummus, pickles, and salad",
          "available": true,
          "variants": [
            {"id": "laffa", "name": "Laffa", "price": 38.0, "available": true},
            {"id": "pita", "name": "Pita", "price": 32.0, "available": true},
            {"id": "plate", "name": "Plate", "price": 48.0, "available": true}
          ],
          "default_variant": "laffa",
          "addons": ["extra_meat", "fries_inside", "egg"],
          "max_addons": 4
        },
        {
          "id": "shawarma_lamb",
          "name": "Lamb Shawarma",
          "description": "Spiced lamb with sumac onions, tahini, and pickles",
          "available": true,
          "variants": [
            {"id": "laffa", "name": "Laffa", "price": 42.0, "available": true},
            {"id": "pita", "name": "Pita", "price": 36.0, "available": true},
            {"id": "plate", "name": "Plate", "price": 52.0, "available": true}
          ],
          "default_variant": "laffa",
          "addons": ["extra_meat", "fries_inside", "egg"],
          "max_addons": 4
        }
      ]
    },
    {
      "id": "sides",
      "name": "Sides",
      "items": [
        {
          "id": "side_fries",
          "name": "French Fries",
          "description": "Crispy golden fries",
          "price": 15.0,
          "available": true
        },
        {
          "id": "side_salad",
          "name": "Israeli Salad",
          "description": "Diced tomatoes, cucumbers, onion, parsley, lemon dressing",
          "price": 12.0,
          "available": true
        },
        {
          "id": "side_hummus",
          "name": "Hummus Plate",
          "description": "Creamy hummus with olive oil, pine nuts, and warm pita",
          "price": 22.0,
          "available": true
        }
      ]
    },
    {
      "id": "drinks",
      "name": "Drinks",
      "items": [
        {
          "id": "drink_cola",
          "name": "Coca Cola",
          "description": "Ice cold, 330ml can",
          "price": 8.0,
          "available": true
        },
        {
          "id": "drink_water",
          "name": "Mineral Water",
          "description": "500ml bottle",
          "price": 6.0,
          "available": true
        },
        {
          "id": "drink_lemonade",
          "name": "Fresh Lemonade",
          "description": "House-made with mint",
          "price": 10.0,
          "available": true
        }
      ]
    }
  ],
  "addons": [
    {"id": "extra_meat", "name": "Extra Meat", "price": 12.0, "available": true},
    {"id": "fries_inside", "name": "Fries Inside", "price": 5.0, "available": true},
    {"id": "egg", "name": "Fried Egg", "price": 5.0, "available": true}
  ],
  "deals": [
    {
      "id": "deal_student",
      "name": "Student Meal",
      "description": "Shawarma + Fries + Drink — the classic combo",
      "price": 45.0,
      "available": true,
      "items": [
        {"category": "shawarma", "quantity": 1},
        {"category": "sides", "quantity": 1},
        {"category": "drinks", "quantity": 1}
      ]
    },
    {
      "id": "deal_family",
      "name": "Family Pack",
      "description": "2 Shawarma + 2 Sides + 2 Drinks",
      "price": 95.0,
      "available": true,
      "items": [
        {"category": "shawarma", "quantity": 2},
        {"category": "sides", "quantity": 2},
        {"category": "drinks", "quantity": 2}
      ]
    }
  ]
}
```

---

## Onboarding a New Restaurant

1. Get the menu from the restaurant owner (PDF, image, text, or WhatsApp message).
2. Share this schema doc with any LLM (Claude, ChatGPT, etc.) along with the menu.
   Prompt: _"Convert this restaurant menu into the JSON format defined above."_
3. Review the output — spot-check prices, categories, and deal definitions.
4. Save the JSON as `menus/{restaurant_slug}.json`.
5. Add the restaurant to `restaurants.json`:
   ```json
   {
     "restaurants": {
       "abu_dhabi_shawarma": {
         "name": "Abu Dhabi Shawarma",
         "menu_path": "menus/abu_dhabi_shawarma.json",
         "twilio_phone": "+972..."
       }
     }
   }
   ```
6. Restart the server. The restaurant is live.
