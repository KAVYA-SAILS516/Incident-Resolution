# Runbooks (SIMULATED)

These runbooks are **simulated examples written for this POC**. They are not operational procedures from a
real organisation. There is one per error type that actually appears in `data/logs/sample_application.log`:

| File | error_type |
|---|---|
| `database_timeout.json` | DATABASE_TIMEOUT |
| `resource_pressure.json` | RESOURCE_PRESSURE |
| `internal_server_error.json` | INTERNAL_SERVER_ERROR |
| `authentication_failure.json` | AUTHENTICATION_FAILURE |
| `rate_limit_exceeded.json` | RATE_LIMIT_EXCEEDED |
| `slow_request.json` | SLOW_REQUEST |

The Recommendation Agent reads them through the `get_runbook` tool. A recommendation is labelled
`source: "runbook"` only when its title matches one of these steps; everything else is `AI-generated`.
Nothing in a runbook is executed.
