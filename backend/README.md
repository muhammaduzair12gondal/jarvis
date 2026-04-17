# Real CIPHER Backend

This backend powers your CIPHER assistant with:

- Multi-provider LLM failover (Groq, Together, OpenRouter, Mistral, Gemini)
- Short-term conversation memory (in-process)
- **Long-term persistent memory** (stored on disk)

## Long-term memory ("brain")

CIPHER now learns key facts from your chat (for example: name, preferences, explicit "remember ..." statements) and stores them persistently.

## Secure cloud memory backend (recommended)

To avoid local file storage, use managed PostgreSQL memory storage.

Set these in your `.env`:

- `CIPHER_MEMORY_BACKEND=postgres`
- `CIPHER_MEMORY_DATABASE_URL=postgresql://<user>:<password>@<host>:5432/<db>`
- `CIPHER_DB_SSLMODE=require`

If `CIPHER_MEMORY_BACKEND=postgres` is set, CIPHER stores memories in PostgreSQL instead of local JSON.

### Storage path

- Default on Windows: `D:\JarvisMemory\jarvis_memory.json`
- Optional override with env var: `JARVIS_MEMORY_PATH`

> Note: local JSON path is only used when memory backend is `json`.

Example in `.env`:

`JARVIS_MEMORY_PATH=D:\Real_Jarvis\backend\jarvis_memory.json`

### Memory endpoints

- `GET /memory` → memory stats (`path`, `count`)
- `GET /memory/list?limit=100` → list stored memories (includes `id`, `fact`, `importance`, `pinned`)
- `POST /memory/pin` → pin/unpin memory by id
- `POST /memory/clear` → clear all long-term memories
- `POST /reset` → clears only short-term chat history

Pin/unpin body example:

`{"id":"<memory-id>", "pinned": true}`

## Notes

- Memory retrieval is relevance-based and injected into the model as hidden context.
- Non-pinned memories decay over time and weak/stale entries are pruned automatically.
- Pinned memories do not decay.
- The assistant should use memories only when relevant to the current message.
