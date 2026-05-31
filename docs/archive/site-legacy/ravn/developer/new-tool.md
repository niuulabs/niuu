# Adding a New Tool

This guide walks through adding a new built-in tool to Ravn.

## Step 1: Implement ToolPort

Create a new adapter in `src/ravn/adapters/tools/`:

```python
# src/ravn/adapters/tools/my_tool.py
from ravn.ports.tool import ToolPort


class MyTool(ToolPort):
    """Short description of what the tool does."""

    def __init__(self, some_setting: str = "default"):
        self._setting = some_setting

    @property
    def name(self) -> str:
        return "my_tool"

    @property
    def description(self) -> str:
        return "Does something useful. Accepts a 'query' parameter."

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The query to process.",
                },
            },
            "required": ["query"],
        }

    @property
    def required_permission(self) -> str:
        return "my_tool:execute"

    async def execute(self, tool_input: dict) -> str:
        query = tool_input["query"]
        # Do the work
        result = f"Processed: {query} with {self._setting}"
        return result
```

### ToolPort Interface

| Property/Method | Type | Description |
|----------------|------|-------------|
| `name` | str | Unique tool identifier. |
| `description` | str | Human-readable description (shown to LLM). |
| `input_schema` | dict | JSON Schema for input parameters. |
| `required_permission` | str | Permission string checked before execution. |
| `execute(tool_input)` | async → str | Execute the tool and return result. |

## Step 2: Register in Builtin Registry

Add the tool to `src/ravn/adapters/tools/builtin_registry.py`:

```python
BUILTIN_TOOLS["my_tool"] = BuiltinToolDef(
    adapter="ravn.adapters.tools.my_tool.MyTool",
    groups=frozenset({"extended"}),
    kwargs_fn=lambda settings, ctx: {
        "some_setting": "configured_value",
    },
    required_context=frozenset(),
    condition=None,  # or lambda s: s.some_feature.enabled
)
```

### Registration Fields

| Field | Type | Description |
|-------|------|-------------|
| `adapter` | str | Fully-qualified class path. |
| `groups` | frozenset | Tool groups: `core`, `extended`, `skill`, `platform`, `mimir`, `cascade`. |
| `kwargs_fn` | callable | Builds constructor kwargs from settings + runtime context. |
| `required_context` | frozenset | Runtime context keys that must be non-None. |
| `condition` | callable or None | Predicate on Settings — tool is skipped if False. |

## Step 3: Add Config (If Needed)

If the tool needs configuration, add a config class in `src/ravn/config.py`:

```python
class MyToolConfig(BaseModel):
    enabled: bool = True
    some_setting: str = "default"
```

Add it to the `Settings` class:

```python
class Settings(BaseSettings):
    # ... existing fields ...
    my_tool: MyToolConfig = MyToolConfig()
```

Update the `kwargs_fn` to read from config:

```python
kwargs_fn=lambda settings, ctx: {
    "some_setting": settings.my_tool.some_setting,
},
condition=lambda s: s.my_tool.enabled,
```

## Step 4: Write Tests

Create tests in `tests/test_ravn/test_adapters/test_tools/`:

```python
# tests/test_ravn/test_adapters/test_tools/test_my_tool.py
import pytest
from ravn.adapters.tools.my_tool import MyTool


@pytest.fixture
def tool():
    return MyTool(some_setting="test")


class TestMyTool:
    def test_name(self, tool):
        assert tool.name == "my_tool"

    def test_schema(self, tool):
        schema = tool.input_schema
        assert "query" in schema["properties"]
        assert "query" in schema["required"]

    @pytest.mark.asyncio
    async def test_execute(self, tool):
        result = await tool.execute({"query": "hello"})
        assert "Processed: hello" in result

    @pytest.mark.asyncio
    async def test_execute_error(self, tool):
        with pytest.raises(KeyError):
            await tool.execute({})
```

## Custom Tools (No Registry)

For tools that don't need to be built-in, use the dynamic adapter pattern
in config:

```yaml
tools:
  custom:
    - adapter: "mypackage.tools.MyTool"
      name: "my_custom_tool"
      kwargs:
        some_setting: "value"
```

This requires no code changes to Ravn itself.
