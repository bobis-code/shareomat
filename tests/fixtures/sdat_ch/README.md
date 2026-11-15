# SDAT-CH-2025 test fixtures

Since 12.11.2026 the tests build synthetic E66/E31 documents in the official
structure with `tests/sdat_fixtures.py` (element names, namespace and codes
from the VSE SDAT-CH 2025 XSDs and examples, all IDs and values synthetic).
The official VSE example files are not committed (licence not clarified);
`tests/test_sdat_ch_parser.py::test_official_vse_examples` runs against them
when the VSE ZIP is unpacked locally and `SHAREOMAT_VSE_EXAMPLES` points to it.

Original note (before the official material was available):

This directory is a placeholder (docs/sdat_leg_import.md, Phase 2).

No official VSE SDAT-CH-2025 XSDs or example E31/E66 messages are
available in this repo yet. Fabricating fixtures without them risks
baking in wrong namespaces/element paths that `shareomat.core.pipeline
.raw.sdat_ch.parse_sdat_ch()` would then be silently written against —
exactly what docs/sdat_leg_import.md §32 warns against.

Once official reference material is available, add here:

```text
e66_consumption.xml
e66_production.xml
e31_leg.xml
replace.xml
cancellation.xml
estimated_values.xml
```

(derived from the official examples, or minimal originals if the real
files cannot be committed for licensing reasons.)
