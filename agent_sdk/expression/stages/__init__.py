"""Expression-domain stages — the reply-rendering (response) stage.

The ``respond`` stage lives here, next to the ``respond`` lobe
(``agent_sdk.expression.lobes.respond``): the expression domain owns the
reply-flow surface. It reuses the generic ``Stage`` base from
``agent_sdk.flows.stages.common``.
"""

from agent_sdk.expression.stages.respond import Respond, respond_step

__all__ = ["Respond", "respond_step"]
