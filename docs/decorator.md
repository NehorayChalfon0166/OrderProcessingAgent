# Subsystem: `@tool` Decorator

Status: **SETTLED**

Bridge between `llm_client.build_tool_definitions()` and the Python tool functions
in `tools.py`. Converts type-annotated functions into OpenAI function-calling
JSON Schemas.

## Strict Mode: Skipped for Now

DeepSeek's `/beta` strict mode mandates all properties in `required` and
`additionalProperties: false`. Our tools have optional parameters with defaults
(`size=None`, `quantity=1`, `toppings=[]`). Strict mode would force these to
be required on the LLM side — the model would have to pass `size=""` explicitly
even when irrelevant.

We use **standard mode** (`https://api.deepseek.com`, not `/beta`). The tool
set is small, parameters are obvious from context, and state-gating is the
real safety. Revisit strict mode if the model hallucinates parameters.

## Decorator API

```python
@tool(description="Add an item to the cart")
def add_to_cart(
    product_name: str,
    quantity: int = 1,
    size: str | None = None,
    toppings: list[str] = [],
    options: dict[str, str] = {},
    special_instructions: str | None = None,
) -> AddToCartResult:
    ...
```

### What the decorator stores

```python
add_to_cart.__tool_name__ = "add_to_cart"          # function name
add_to_cart.__tool_description__ = "Add an item..." # from decorator arg
add_to_cart.__tool_schema__ = {JSON Schema dict}    # generated from type hints
```

### Type Hint → JSON Schema Mapping

| Python type | JSON Schema |
|---|---|
| `str` | `{"type": "string"}` |
| `int` | `{"type": "integer"}` |
| `float` | `{"type": "number"}` |
| `bool` | `{"type": "boolean"}` |
| `str \| None` (or `Optional[str]`) | `{"type": "string", "nullable": true}` |
| `list[str]` | `{"type": "array", "items": {"type": "string"}}` |
| `dict[str, str]` | `{"type": "object", "additionalProperties": {"type": "string"}}` |

Parameters with defaults are NOT in `required`. Parameters without defaults ARE.

### Implementation

```python
import inspect
from functools import wraps
from typing import get_type_hints, get_origin, get_args

def tool(description: str):
    """Decorator that stores tool metadata for LLM function calling."""
    def decorator(fn):
        # Generate JSON Schema from type hints
        hints = get_type_hints(fn)
        # Exclude return type
        hints.pop("return", None)

        properties = {}
        required = []

        sig = inspect.signature(fn)
        for name, param in sig.parameters.items():
            if name in ("session", "catalogue", "pricing"):
                continue  # injected dependencies, not LLM parameters

            py_type = hints.get(name, str)
            properties[name] = _python_type_to_json_schema(py_type)

            if param.default is inspect.Parameter.empty:
                required.append(name)

        schema = {
            "type": "object",
            "properties": properties,
            "required": required,
        }

        @wraps(fn)
        def wrapper(*args, **kwargs):
            return fn(*args, **kwargs)

        wrapper.__tool_name__ = fn.__name__
        wrapper.__tool_description__ = description
        wrapper.__tool_schema__ = schema
        return wrapper
    return decorator
```

### How `llm_client.build_tool_definitions()` uses it

```python
def build_tool_definitions(tool_funcs: list[callable]) -> list[dict]:
    return [
        {
            "type": "function",
            "function": {
                "name": f.__tool_name__,
                "description": f.__tool_description__,
                "parameters": f.__tool_schema__,
            }
        }
        for f in tool_funcs
    ]
```

### Dependency Injection

Tool functions take `session`, `catalogue`, and `pricing` as first arguments.
The decorator skips these when building the schema — they're injected by the
agent loop, not provided by the LLM.

```python
@tool(description="...")
def add_to_cart(
    session: OrderSession,       # injected
    catalogue: Catalogue,        # injected
    pricing: PricingEngine,      # injected
    product_name: str,           # LLM parameter
    quantity: int = 1,           # LLM parameter
) -> AddToCartResult:
```

The agent loop calls: `tool_fn(session, catalogue, pricing, **tc.arguments)`
