# Multilingual ASR data scope

The current provider contains seven languages: Arabic, English, French, German, Italian, Portuguese, and Spanish. Each language contains six domains: carControl, generalControl, mediaControl, naviControl, phone, and systemControl.

CSV case rows are the default source for case-level metrics and search. Each row contains a case number, result, reference text, and ASR hypothesis text.

Raw `*_output.json` files are a separate run-summary scope. Their totals can differ from CSV rows. The platform reports those differences and never silently merges the two scopes.

The French carControl CSV currently contains 4,147 rows while its JSON summary declares 4,178. This is a source-quality warning, not something the platform repairs automatically.
