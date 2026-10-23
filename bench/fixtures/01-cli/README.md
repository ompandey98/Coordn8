# logtally

`logtally` reads application log files and prints a count of entries per level and per
component. It is a small command-line tool: one entry point, a parser, a filter, an
aggregator and two renderers (a text table and JSON).

```
logtally app.log --level warn --by component
logtally app.log other.log --since 2026-01-01T00:00:00 --format json
```

Exit status is 0 when every line parsed, 2 when some lines were skipped as malformed
(the count of skipped lines is printed on stderr), and 64 on a usage error.
