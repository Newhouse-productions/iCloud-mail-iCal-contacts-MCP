"""MCP tool annotations, named by what a tool does. Clients use these hints to decide
what to auto-approve and what to confirm."""

from mcp.types import ToolAnnotations

READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=True)
# Writes something new (a draft, an event, a contact, a saved file); repeating it makes another.
CREATES = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=True)
# Changes existing state reversibly (flags, folder moves); repeating it changes nothing more.
MODIFIES = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=True)
# Removes something.
DESTRUCTIVE = ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=True, open_world_hint=True)
# Sends email (or can, when sending is enabled): can't be undone.
SENDS = ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=False, open_world_hint=True)
