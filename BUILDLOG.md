# Build log

The project was assembled with AI assistance from the capstone brief. AI helped draft the FastAPI routes, SQLite schema, browser widget bundle, test cases, and documentation. The implementation was reviewed and corrected manually, including the authenticated widget retrieval control-flow bug, tenant filters on every owner query, response cache headers, and deterministic provider/notification failure modes.

The owner should be able to explain the request path: authentication resolves a tenant, public submissions validate before database access, abuse controls run before enrichment, enrichment is best-effort, storage is the critical operation, and notifications are wrapped so they cannot turn a successful lead into a failed request.
