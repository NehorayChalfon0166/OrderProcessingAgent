# Dashboard Plan — Admin Interface for Order Processing Agent

Decisions recorded as we discuss each component.

---

## Phase 1: `manage_menu` Tool (master)

**What:** CLI tool for editing restaurant menus. Deterministic, no LLM.
**Branch:** master (core logic)

### Decisions

| Decision | Choice | Why |
|---|---|---|
| CLI style | Explicit commands, not LLM | Menu changes involve money. No hallucination risk. |
| Actions | `set-price`, `out-of-stock`, `in-stock`, `describe` | Covers 90% of daily changes. `add-item` deferred. |
| Hot reload | No — require server restart | Avoids race conditions during active orders. |
| File location | New `menu_manager.py` | Separate from `catalogue.py` (read vs write). |
| Atomicity | Write temp file → fsync → rename | Menu JSON is never partially written. |

### Commands

```
python main.py manage-menu set-price pizza_margherita large 60
python main.py manage-menu 86 pizza_margherita large
python main.py manage-menu un86 pizza_margherita large
python main.py manage-menu describe pizza_margherita "New description"
```

### Files

| File | Purpose |
|---|---|
| `menu_manager.py` | `manage_menu()` function + action validation + atomic write |
| `main.py` | `manage-menu` CLI subcommand (parses args, calls `manage_menu`) |
| `tests/test_menu_manager.py` | Unit tests for each action, atomicity, edge cases |

---

## Phase 2: Dashboard Server (`dashboard-integration`)

### Stack (researched and decided)

| Layer | Choice | Why |
|---|---|---|
| Server | FastAPI (separate process, port 8081) | Same framework as Twilio server, imports same core |
| Templates | Jinja2 | Built into FastAPI, no extra dependency |
| CSS | Pico.css (7KB, CDN link) | Classless — semantic HTML looks good by default. Dark mode, responsive, no build step |
| Interactivity | HTMX (14KB, CDN script) | Buttons can POST/GET without writing JS. Confirmations, inline loading |
| Auth | Token (`?token=` query param, `API_TOKEN` env var) | Same pattern as printer API |
| Startup | `python main.py dashboard --port 8081` | Consistent with `cli` and `server` subcommands |

### Server architecture decisions

| Decision | Choice | Why |
|---|---|---|
| Separate process | Yes — dedicated port | Dashboard crash doesn't affect order processing |
| Same database | Yes — reads same SQLite (WAL mode) | Zero data duplication, concurrent reads safe |
| Template location | `dashboard_templates/` directory | Separate from server templates |
| Static files | `dashboard_static/` — just `style.css` | Minimal custom CSS for sidebar layout; Pico.css handles components |

### Pages

| Page | Route | Purpose |
|---|---|---|
| Overview | `/` | Orders today, active sessions, unprinted count, health. Per-restaurant stats |
| Orders | `/orders` | Table: recent orders. Filters: restaurant, date, type, payment |
| Order detail | `/orders/{id}` | One order: items, customer, totals, printed status. Read-only |
| Sessions | `/sessions` | Active sessions: who's ordering right now, cart contents, last activity |
| Session detail | `/sessions/{id}` | Full conversation history for debugging |
| Menu editor | `/menu/{restaurant_id}` | View + edit menu. Calls `manage_menu` in backend |

All pages read-only except the menu editor (POST actions).

### Filtering

Every list endpoint supports these query params:

| Param | Example | What it does |
|---|---|---|
| `restaurant_id` | `?restaurant_id=marios_pizzeria` | Filter to one restaurant |
| `limit` | `?limit=100` | Rows per page (default 50) |
| `order_type` | `?order_type=delivery` | Delivery or pickup (orders only) |
| `payment_method` | `?payment_method=cash` | Cash or link (orders only) |

Overview page groups stats by restaurant automatically. Single-restaurant
deployments see the numbers directly without grouping noise.

### File structure (`dashboard-integration` branch)

```
dashboard.py              # FastAPI app, token middleware, all routes
dashboard_templates/      # Jinja2 HTML templates
  base.html               # Layout: sidebar nav, content area, footer
  overview.html
  orders.html
  order_detail.html
  sessions.html
  session_detail.html
  menu_editor.html
dashboard_static/
  style.css               # Sidebar grid + minor custom tweaks (Pico.css handles the rest)
main.py                   # Modified: add `dashboard` subcommand
tests/
  test_dashboard.py       # Tests for all endpoints, auth, filtering
```

No `node_modules`, no `package.json`, no build step. Pure Python + HTML + one CSS file.

---

## Phase 3: Restaurant Management (master + dashboard)

**What:** Add/edit restaurants from CLI and dashboard. Currently requires
hand-editing `restaurants.json` and restarting the server.

### Core logic (master)

New function `save_restaurant()` in `restaurant.py`. Writes `restaurants.json`
atomically (temp file → rename). Validates required fields (name, phone,
menu_path). Does NOT reload the registry — restart required.

### CLI (master)

```
python main.py restaurant add my_restaurant --name "My Restaurant" --phone +123 --owner +456
python main.py restaurant edit my_restaurant --name "New Name"
```

### Dashboard (dashboard-integration)

Page at `/restaurants` — list with edit links, add form. Changes require
restart — dashboard shows a banner.

### Decisions

| Decision | Choice | Why |
|---|---|---|
| Core logic location | `restaurant.py` | Same file that reads it. Read + write in one place. |
| Atomicity | Temp file → rename | Same pattern as menu. Never partially written. |
| Hot reload | No — require restart | Registry loads at startup. Restart is safe. |
| Menu template | Auto-create blank menu on restaurant add | User then fills it via menu editor. |

---

