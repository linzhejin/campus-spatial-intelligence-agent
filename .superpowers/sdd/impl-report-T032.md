# T-032: Render Deployment Config & README Polish — Implementation Report

**Date**: 2026-08-11
**Status**: Complete

## Files Created/Modified

| File | Action | Description |
|------|--------|-------------|
| `render.yaml` | Created | Render deployment config (Singapore region, Python 3.11, gunicorn) |
| `README.md` | Rewritten | Complete project README with 15-minute Hello World flow |

## render.yaml Details

- **Service type**: `web` (Python)
- **Region**: Singapore
- **Build**: `pip install -r requirements.txt`
- **Start**: `gunicorn app:app --workers 1 --timeout 60 --bind 0.0.0.0:$PORT`
- **Auto-deploy**: Enabled
- **Environment variables**:
  - `PYTHON_VERSION`: 3.11.0 (hardcoded)
  - `DEEPSEEK_API_KEY`: secret (sync: false, set in Render Dashboard)
  - `AMAP_KEY`: secret (sync: false, set in Render Dashboard)
  - `OPENAI_BASE_URL`: `https://api.deepseek.com/v1` (hardcoded)
  - `LLM_MODEL`: `deepseek-chat` (hardcoded)

## README.md Structure

1. Title & tagline (Chinese + English)
2. 15-minute Hello World (5 steps: env setup, config, network init, start, sample queries)
3. Tech architecture table (6 layers)
4. Core features (6 items with icons)
5. API endpoints (7 endpoints)
6. Render deployment guide (6 steps, including AMAP Referer whitelist)
7. Project docs reference table (6 documents)
8. License (MIT)

## Acceptance Criteria Verification

| Criteria | Status | Notes |
|----------|--------|-------|
| `gunicorn app:app --workers 1 --timeout 60` starts successfully | Configured | Command set as `startCommand` in render.yaml |
| Public URL accessible | Configured | Render auto-provisions `*.onrender.com` URL |
| README has 15-minute Hello World flow | Verified | 5-step flow covering env, config, init, launch, and sample queries |
| AMAP Key Referer whitelist documented | Verified | Step 5 in deployment section: "在高德控制台设置 Referer 白名单：`*.onrender.com`" |

## Notes

- No Python source files were modified.
- The `gunicorn` start command uses `$PORT` which Render injects automatically.
- Secret env vars (`DEEPSEEK_API_KEY`, `AMAP_KEY`) use `sync: false` so they are not committed to the repo — they must be set manually in the Render Dashboard.
