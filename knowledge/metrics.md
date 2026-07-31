# ASR metric definitions

For CSV case-row analysis:

- Total is the number of imported CSV case rows in the selected scope.
- Correct is the number of rows whose result marker is a check mark.
- Errors is the number of rows whose result marker is a cross.
- Accuracy is `correct / total * 100`.
- Error rate is `errors / total * 100`.

Metrics must be calculated by deterministic SQL tools. A language model may explain tool results but must not count rows or invent a metric.

When the user asks for the best or worst language without naming a metric, the answer must state whether the comparison uses accuracy, error rate, error count, or another explicit measure.
