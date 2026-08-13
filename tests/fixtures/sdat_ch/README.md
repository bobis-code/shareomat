# SDAT-CH-2025 test fixtures — pending

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
