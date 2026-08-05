"""Sandbox worker package: Celery consumer for immutable skill-plan execution.

Both this project and the agent-service it depends on expose a top-level ``app``
package. The worker reuses the agent-service submodules (ORM models, envelope
cipher, skill compiler/schemas) so models stay single-source; this init merges
the agent-service ``app`` submodules onto our own ``app`` name so worker imports
stay ``app.models.execution`` / ``app.core.crypto`` even though our package
already occupies ``app``. The merge is safe because the two package trees share
no module names and the agent-service ``__init__`` files carry no side effects.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

_AGENT_ROOT = Path(__file__).resolve().parents[2] / "agent-service"

# Import the agent-service `app` package while our own `app` is mid-import by
# temporarily hiding ours and putting the agent root first on sys.path, then
# restore our module as the canonical ``app``.
_me = sys.modules[__name__]
sys.modules.pop(__name__, None)
if str(_AGENT_ROOT) in sys.path:
    sys.path.remove(str(_AGENT_ROOT))
sys.path.insert(0, str(_AGENT_ROOT))
try:
    importlib.import_module("app")
finally:
    sys.modules[__name__] = _me

# Extend our package path with the agent-service ``app`` dir so submodule
# imports (``app.core``, ``app.models``, ...) resolve there, then bind the
# reusable submodules onto our package (order follows their dependency chain:
# core -> db -> models -> skills -> repositories).
_AGENT_APP_DIR = _AGENT_ROOT / "app"
if str(_AGENT_APP_DIR) not in _me.__path__:
    _me.__path__.append(str(_AGENT_APP_DIR))
for _name in ("core", "db", "models", "skills", "repositories", "execution"):
    module = importlib.import_module(f"{__name__}.{_name}")
    setattr(_me, _name, module)
