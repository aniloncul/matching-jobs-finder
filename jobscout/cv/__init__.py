"""CV pipeline: the master CV is the only fact source.

The design rule taken from both benchmark repos is the same: **the model never
writes the CV, code does.**  The model may reorder, re-label and re-phrase text
that already exists in :class:`~jobscout.cv.master.MasterCV`; every claim that
cannot be traced back to a fact id is removed or blocks the PDF.

Modules:

``master``     loads the master CV (profile + optional ``cv.master.yaml``)
``payload``    the strict render schema the template consumes
``tailor``     keyword extraction, gaps, ordering, bold labels, page cutting
``llm``        optional JSON-only tailoring and reviewer calls
``reviewer``   fresh-context reviewer with a typed edit list
``verify``     structure/title checks, fact gate, CV <-> letter consistency
``render``     deterministic LaTeX renderer (the model never sees LaTeX)
``pipeline``   build: master -> payload -> tailor -> verify -> PDF
"""

from jobscout.cv.master import (
    CVFact,
    MasterCV,
    MasterCVError,
    inspect_master,
    load_master,
)
from jobscout.cv.payload import CVRenderPayload, CVSchemaError, parse_payload

__all__ = [
    "CVFact",
    "CVRenderPayload",
    "CVSchemaError",
    "MasterCV",
    "MasterCVError",
    "inspect_master",
    "load_master",
    "parse_payload",
]
