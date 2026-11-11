![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white)
![MIT License](https://img.shields.io/badge/license-MIT-2ea44f)
# ReelMind AI

ReelMind AI is an agentic media automation engine that leverages multi-agent workflows to convert trending topics into publication-ready YouTube Shorts.

- Agentic State Machine: Uses LangGraph to coordinate specialized agents (Research, Scriptwriter, Critic) that collaborate, call external tools, and iteratively refine scripts.
- Structured Outputs: Enforces strict data contracts across agent communication using Pydantic schemas.
- Human-in-the-Loop Control: Integrates a Telegram approval interface so humans curate selected trends and approve final scripts.
- Background Execution: Operates asynchronously to handle generative media rendering, assembly, and scheduled YouTube publishing.

## Tech Stack

| Area | Technologies |
| --- | --- |
| Web application and API | FastAPI, Uvicorn |
| Background scheduling | APScheduler and an asyncio video-worker task |
| Human approval | `python-telegram-bot` with inline topic selection |
| Agent workflow | LangGraph, Pydantic, LangChain-Groq, Groq |
| Research | Tavily web search and Google Trends RSS |
| Speech and audio | Edge TTS, Pydub |
| Scene images | Pollinations image API over HTTPX |
| Video assembly | MoviePy, FFmpeg, Pillow |
| Job and publishing data | SQLite |
| YouTube publishing | Google API Python Client, Google OAuth 2.0 |

## How It Works

The following describes the runtime flow from app startup through upload:

1. **Start the application.** FastAPI's lifespan initializes the SQLite database, requeues videos left in `PROCESSING` by an earlier shutdown, starts Telegram polling, launches one video worker, and registers the daily trend job and 15-minute publisher job.
2. **Fetch trends.** At midnight in the server's local timezone, the scheduled job reads Google Trends RSS for India and filters the results to recent English-language topics.
3. **Request human approval.** The app sends up to ten topics to the configured Telegram chat. You can toggle topics with the inline buttons and confirm your selection. No video jobs are created until you confirm.
4. **Create queue jobs.** The confirmation callback creates one SQLite row per selected topic, with a unique job ID and `PENDING` status. Duplicate topics in a selection are removed.
5. **Claim work.** The worker claims the oldest `PENDING` row in a SQLite write transaction and changes its status to `PROCESSING`. This application runs one job at a time.
6. **Research the topic.** The LangGraph workflow uses the research agent and Tavily search to gather a brief and sources.
7. **Write and critique a script.** The scriptwriter produces a structured script, and the critique agent checks it. The graph can route back for revision, up to the configured revision limit. An unapproved result is recorded as `SKIPPED`; an approved result continues to media production.
8. **Create narration and captions.** Edge TTS generates scene audio and word timings. Pydub combines the audio, and the subtitle pipeline writes SRT caption files from those timings.
9. **Generate scene visuals.** The media pipeline requests images from Pollinations using each scene's visual direction. The images are prepared for a vertical video; image-service failures are recorded in the job artifacts and may prevent final rendering if required visuals are unavailable.
10. **Render the MP4.** MoviePy and FFmpeg combine the visuals, Ken Burns motion, captions, narration, and optional background music into a 1080x1920 video at 30 FPS.
11. **Mark the job ready.** The final video path and publishing metadata are saved to SQLite, and the job transitions to `RENDERED`.
12. **Upload at a publishing slot.** A publisher pass runs when a render completes and every 15 minutes. It uploads the oldest rendered video only when an upload slot is due and quota is available. The configured India Standard Time slots are `08:00`, `12:00`, `16:00`, `19:00`, and `21:00`; completing a render outside a due slot does not upload immediately. The publisher attaches captions when available and records the YouTube ID and URL.

The upload schedule and worker run inside the application process. Keep that process running; a sleeping or stopped host pauses the worker, Telegram polling, and scheduled jobs.

## Requirements

- Python 3.11 or newer
- Dependencies from `requirements.txt`
- A Telegram bot and the bot user's Telegram ID
- Groq and Tavily API keys for the agent workflow
- A Google Cloud OAuth client with YouTube Data API v3 enabled
- Persistent writable storage for `data/` and `data/jobs/`

Scene-image generation calls Pollinations over the network. A Pollinations API key is optional in the current implementation.

## Configuration

Set these as environment variables in your shell or hosting provider. Do not commit credentials or OAuth token files.

| Variable | Purpose |
| --- | --- |
| `BOT_TOKEN` | Telegram bot token |
| `USER_ID` | Telegram user ID allowed to select topics |
| `CHAT_ID` | Telegram chat that receives the daily trend digest |
| `GROQ_API_KEY` | Groq model access for the agent workflow |
| `TAVILY_API_KEY` | Tavily web search used during research |
| `POLLINATIONS_API_KEY` | Optional key for scene-image generation |
| `YT_CLIENT_SECRETS` | Path to the Google OAuth client JSON; defaults to `data/youtube_client_secrets.json` |
| `YT_TOKEN_FILE` | Path where the authorized YouTube token is stored; defaults to `data/youtube_token.json` |
| `YT_REDIRECT_URI` | Public OAuth callback URL, ending in `/auth/youtube/callback` |
| `PORT` | Port provided by a hosting platform; local default is `7000` |

Start a conversation with your Telegram bot (for example, send `/start`) before expecting it to message you. The bot uses polling, so run only one application process for this deployment.

## Run Locally

From the repository root, create an environment and install dependencies:

```sh
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

Set the required environment variables, then start the app:

```sh
uvicorn main:app --host 127.0.0.1 --port 7000 --workers 1
```

The FastAPI lifespan initializes the SQLite database, starts Telegram polling, launches the video worker, and registers the daily trend and periodic publisher jobs. Keep this process running for scheduled jobs to execute.

## YouTube Authorization

1. Enable YouTube Data API v3 in Google Cloud and create an OAuth client.
2. Save its client JSON to the configured `YT_CLIENT_SECRETS` path.
3. Configure the OAuth consent screen and add the deployed callback URL as an authorized redirect URI.
4. Start ReelMind and visit `https://<your-host>/auth/youtube` in a browser.
5. Complete Google's consent flow. Keep the token file at `YT_TOKEN_FILE` on persistent storage so it survives restarts.

## API Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/` | Basic liveness response |
| `GET` | `/fetch-trends` | Fetch and return current trends |
| `POST` | `/generate-videos` | Queue topics directly as video jobs |
| `GET` | `/auth/youtube` | Start YouTube OAuth |
| `GET` | `/auth/youtube/callback` | Complete YouTube OAuth |

`POST /generate-videos` accepts a JSON array of topic strings, for example:

```sh
curl -X POST http://127.0.0.1:7000/generate-videos \
  -H 'Content-Type: application/json' \
  -d '["Example topic"]'
```

The Telegram flow is the normal human-in-the-loop entry point; this endpoint is useful for manual jobs and development.

## Storage and Deployment

The application stores its SQLite database at `data/publisher.sqlite`, generated artifacts under `data/jobs/`, and YouTube credentials under `data/` by default. Preserve these files on persistent storage across restarts and deployments.

For a cloud host that provides a `PORT` environment variable, use a single always-on instance and a single worker process:

```sh
uvicorn main:app --host 0.0.0.0 --port "${PORT:-7000}" --workers 1
```

The host must allow outbound network access to Telegram, Google, Groq, Tavily, and the image-generation service. A free hosting tier that sleeps or stops the service will also stop Telegram polling, video processing, and scheduled jobs. The daily trend schedule uses the server's local timezone; configure it explicitly for your deployment if midnight must mean a specific timezone.

## Current Limitations

- Keep this deployment to one app process and one instance: job claiming uses SQLite and Telegram polling is process-local.
- The database and generated media must be on persistent writable storage; ephemeral container storage is not sufficient.
- A shutdown or crash can interrupt a render or upload. Interrupted `PROCESSING` jobs are requeued on startup, but upload recovery and duplicate prevention are not fully automated.
- The liveness endpoint only confirms that the web app responds; it does not verify Telegram, the database, or external APIs.
- Protect the app from untrusted access. In particular, `/generate-videos` can enqueue work and should not be exposed publicly without access controls.

## Repository Layout

```text
main.py                 FastAPI entry point, application lifespan, and video worker
src/agents/             Research, scriptwriting, and critique workflow
src/gateway/            Telegram trend selection bot
src/ingestion/          Google Trends RSS ingestion
src/media/              Audio, subtitles, visuals, and video compilation
src/dal/                SQLite schema and repository operations
src/publisher/          Upload slots, quota checks, and YouTube publishing
src/upload/             YouTube OAuth and API client
assets/                 Fonts and music used by the media pipeline
data/                   Runtime database, OAuth files, and generated jobs
```