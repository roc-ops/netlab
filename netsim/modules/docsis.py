#
# DOCSIS access-side configuration module (RocContLab #48, hybrid-lab #57 Layer 2).
#
# Attribute-only module: the schema lives in docsis.yml; validation + data-carry are all netlab
# needs here, because the DOCSIS config is rendered OUT-OF-BAND by the render/casa bridge (which
# reads nodes.<node>.docsis and drives the guardrailed casa_render), not by `netlab initial`.
# netlab requires this .py to import cleanly for every listed module; the empty _Module subclass
# below makes the module's presence explicit and leaves a hook point for later transform logic
# (e.g. expanding docsis-profiles/channel-plans into canonical per-channel objects).
#
from . import _Module


class DOCSIS(_Module):
    pass
