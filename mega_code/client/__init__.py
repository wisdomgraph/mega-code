"""MEGA-Code open-source client package.

Canonical imports:
    from mega_code.client.api import create_client
    from mega_code.client.api.protocol import MegaCodeBaseClient
    from mega_code.client.api.remote import MegaCodeRemote
    from mega_code.client.models import Turn, TurnSet, SessionMetadata
    from mega_code.client.schema import SessionStats, estimate_cost
    from mega_code.client.stats import load_stats, save_stats
"""

import importlib

# Lazy re-exports so that `from mega_code.client import create_client` works
# without capturing early references (estimate_cost is patched at runtime by
# mega_code/__init__.py for enterprise pricing).
_LAZY_IMPORTS = {
    "create_client": "mega_code.client.api",
    "MegaCodeBaseClient": "mega_code.client.api.protocol",
    "MegaCodeRemote": "mega_code.client.api.remote",
    "Turn": "mega_code.client.models",
    "TurnSet": "mega_code.client.models",
    "SessionMetadata": "mega_code.client.models",
    "SessionStats": "mega_code.client.schema",
    "estimate_cost": "mega_code.client.schema",
    "load_stats": "mega_code.client.stats",
    "save_stats": "mega_code.client.stats",
}


__all__ = list(_LAZY_IMPORTS.keys())


def __getattr__(name):
    if name in _LAZY_IMPORTS:
        module = importlib.import_module(_LAZY_IMPORTS[name])
        return getattr(module, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
