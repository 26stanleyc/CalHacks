"""The ASI:One access layer (spec 3.7): the public face of the system on Agentverse.

These agents hold no fraud logic. They turn a chat message into a call on one of the
`contracts.entrypoints.Engine` functions and turn the result back into plain text.

Three rules from spec 3.7 are enforced here, not in the engine:

1. **Chat checks are sandboxed.** A payment submitted through chat is scored for real but
   tagged `source=asi_one`, so it never reaches labels, reflection, the cost ledger or the
   archive. `EntryPointTools` sets the tag itself and never takes it from the model.
2. **Round controls are restricted.** `run_round` and `set_freeze` are refused with
   `NotAllowed` unless the sender is in `TEAM_ALLOWLIST_ADDRESSES`.
3. **Chat text is untrusted.** It reaches the routing LLM only inside a marked data block,
   and never as instructions.
"""

from integrations.asi_one.engine_source import get_engine
from integrations.asi_one.router import ChatRouter
from integrations.asi_one.tools import EntryPointTools

__all__ = ["ChatRouter", "EntryPointTools", "get_engine"]
