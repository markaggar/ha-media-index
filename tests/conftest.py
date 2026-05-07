"""Root conftest — make custom_components.media_index sub-modules importable.

custom_components/media_index/__init__.py imports voluptuous and homeassistant
which are HA-only packages not installed in the test environment.  We register
stub entries for those packages in sys.modules *before* any test collection so
that "from custom_components.media_index.exif_parser import ExifParser" works
without pulling in the full HA stack.
"""
import sys
import os
import importlib.util
from types import ModuleType

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO_ROOT)

# ---------------------------------------------------------------------------
# Stub the HA-specific imports that __init__.py needs so that the package
# namespace is importable.  The stub modules are empty; unit tests only
# import exif_parser and cache_manager, neither of which uses HA APIs.
# ---------------------------------------------------------------------------
_HA_STUBS = [
    "voluptuous",
    "homeassistant",
    "homeassistant.core",
    "homeassistant.const",
    "homeassistant.config_entries",
    "homeassistant.helpers",
    "homeassistant.helpers.config_validation",
    "homeassistant.helpers.entity",
    "homeassistant.helpers.entity_platform",
    "homeassistant.helpers.event",
    "homeassistant.helpers.storage",
    "homeassistant.components",
    "homeassistant.components.websocket_api",
    "homeassistant.components.websocket_api.decorators",
    "homeassistant.exceptions",
]

for _mod_name in _HA_STUBS:
    if _mod_name not in sys.modules:
        _m = ModuleType(_mod_name)
        # Give the stub a truthful __package__ so sub-module lookups don't crash
        _m.__package__ = _mod_name
        _m.__path__ = []          # marks it as a package
        sys.modules[_mod_name] = _m

# Ensure the custom_components namespace package exists
for _ns in ("custom_components", "custom_components.media_index"):
    if _ns not in sys.modules:
        _m = ModuleType(_ns)
        _m.__package__ = _ns
        _m.__path__ = [os.path.join(_REPO_ROOT, *_ns.split("."))]
        sys.modules[_ns] = _m

