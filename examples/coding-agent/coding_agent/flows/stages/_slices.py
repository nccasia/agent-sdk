"""Per-stage tool slices (Claude Code's canonical tool names).

Named here so each stage file reads as one self-describing declaration and the
allowlists stay consistent across the stages that share them.
"""

from __future__ import annotations

READ_TOOLS = ["LS", "Glob", "Grep", "Read", "Bash"]
EDIT_TOOLS = ["Read", "Write", "Edit", "Glob", "Grep", "Bash"]
VERIFY_TOOLS = ["Bash", "Read", "Edit", "Grep"]
NOTE_TOOLS = ["LS", "Glob", "Grep", "Read", "memory", "Bash"]
# `Bash` is included so the model can write the doc via its preferred method
# (e.g. a `cat > FILE` heredoc) as a REAL tool call instead of leaking markup
# when only Write is offered.
DOC_TOOLS = ["memory", "Read", "Glob", "Write", "Bash"]
