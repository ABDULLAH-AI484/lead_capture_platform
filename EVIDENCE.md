# Evidence checklist

Run `pytest -q` for executable proof. The tests cover the following contract items:

| Requirement | Proof |
|---|---|
| Authenticated CRUD | `test_auth_and_tenant_isolation` creates and reads a widget with a bearer token. |
| Tenant isolation | The same test proves the second seeded tenant receives 404. |
| Embed snippet | `GET /widgets/{id}/embed` returns a one-line `<script>` URL. |
| Cached config and versioned JS | `test_public_cache_cors_and_submission` checks `max-age=60`; `/widget.js?v=1` uses immutable long caching. |
| Cross-origin + preflight | The same test sends Origin and OPTIONS headers. |
| Validation and spam control | `test_honeypot_and_rate_limit` proves 422 for the honeypot. |
| Rate limiting | The same test proves 429 after a burst. |
| Geo fallback | `test_fallback_and_noncritical_failure` toggles provider A down and receives provider B data. |
| All providers down | The same test receives 201 with `geo: null`. |
| Failed side effect | It sets `SIDE_EFFECT_MODE=fail` and still receives 201. |
| Dashboard isolation/aggregation | `/dashboard/submissions` and `/dashboard/stats` filter by owner id in SQL. |

Manual browser proof: run API on port 8000 and `python3 -m http.server 5500 --directory demo`, then open `http://localhost:5500` and submit the rendered widget.
