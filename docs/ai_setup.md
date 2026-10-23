# AI setup

Two of the features need a language model: **company research** and
**resume bullet suggestions**. Both degrade honestly without one — they show
collected sources and placeholders instead of inventing content.

## Option A — Ollama (recommended: free, private, no key)

```bash
# install from https://ollama.com
ollama serve
ollama pull llama3.1          # ~4 GB; llama3.1:8b works well
```

Then in `.env`:

```bash
OPENAI_BASE_URL=http://localhost:11434/v1
RESEARCH_MODEL=llama3.1
# leave OPENAI_API_KEY empty
```

Ollama exposes an OpenAI-compatible endpoint, so no code changes are needed.

## Option B — OpenCode inference

From the [Console API docs](https://opencode.ai/v2/docs/console/api/inference/):

- Endpoint: `https://opencode.ai/inference/openai/v1/chat/completions`
- Auth: `Authorization: Bearer <service account key>`

```bash
OPENAI_API_KEY=<service account key from https://opencode.ai/console>
OPENAI_BASE_URL=https://opencode.ai/inference/openai/v1
RESEARCH_MODEL=kimi-k2.6
```

### Why not the free models?

OpenCode lists free chat models (`ling-3.1-flash-free`,
`space-bunny-free`, and others), but calling them from outside OpenCode fails:

```json
{"type":"error","error":{"type":"FreeTierError",
 "message":"OpenCode's free tier can only be used from within OpenCode"}}
```

So the free tier is only usable from inside OpenCode itself, not from this
application. Paid models require a service account key.

## Verify

```bash
python -m app.main research Zensar --role "Backend Intern"
python -m app.main resume --query "Backend"
```

Or use the **AI assistant** tab in the dashboard, which shows whether a model is
configured before you run anything.

## Safety rules built into the code

- The model only ever sees evidence you (or the search backend) collected.
- Every claim must carry a source URL, or the section is returned empty.
- Resume bullets must be traceable to a portfolio item you recorded; items with
  no evidence link are flagged as unverified and must not be used as-is.
- Nothing is auto-published to your resume — approval is always yours.