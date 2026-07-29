# Decision log

Append-only running notes. Formats:
`YYYY-MM-DD CHUNK-<id>: <surprise/workaround/deviation and how it was resolved>`
`DEBT: <what and why it was accepted>` · `CARD?: <follow-up work idea>` ·
`HANDOFF: <exact state + next step>` · `RETRO-MARKER <date>`

---

2026-07-29 CHUNK-1: Graph decoding rejects duplicate object keys and nonstandard
JSON constants as schema errors; accepted records are normalized independently
from the SHA-256 fingerprint of their exact source bytes.
