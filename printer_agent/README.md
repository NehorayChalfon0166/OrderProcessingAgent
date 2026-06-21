# Printer Agent

Standalone client that polls the OrderProcessingAgent server for unprinted
orders and sends them to a thermal printer. Runs on the restaurant's POS
computer.

## Quick Start

```bash
cd printer_agent
pip install -r requirements.txt
cp config.example.json config.json
# Edit config.json with your server URL, restaurant ID, API token, and printer IP
python agent.py
```

## Configuration

All settings in `config.json`:

| Field | Description |
|---|---|
| `server_url` | Base URL of the OrderProcessingAgent server |
| `restaurant_id` | Restaurant slug to poll orders for |
| `api_token` | Must match the server's `API_TOKEN` env var |
| `printer_ip` | Thermal printer address as `host:port` (port 9100 is standard) |
| `poll_interval` | Seconds between polls (default 5) |
| `mode` | `"escpos"` for real printer, `"file"` for HTML preview |

## Modes

- **`file`** — writes HTML tickets to `printer_agent/tickets/`. Use for
  development and testing without a physical printer.
- **`escpos`** — sends raw ESC/POS bytes to the printer via TCP. Requires
  a network thermal printer reachable at `printer_ip`.

## Error Recovery

- **Printer offline/out of paper:** retries 3 times with backoff (1s, 10s, 30s).
  After 3 failures, leaves the order unprinted — retried on the next poll cycle.
- **Server unreachable:** logs warning, retries on next poll.
- **Crash mid-print:** the agent only marks an order as printed AFTER the
  printer confirms success. Crashed prints are retried.

## Packaging

Build a single `.exe` with PyInstaller:

```bash
pip install pyinstaller
pyinstaller pyinstaller.spec
```

The `.exe` is in `dist/printer_agent`. Copy it with `config.json` to the
restaurant's POS computer. Add a shortcut to the Windows Startup folder
so it launches on boot.
