Your updated plan is sharp and aligned with production-grade AI engineering. Targeting 1–2 minute videos fits YouTube Shorts, as YouTube increased the maximum Shorts duration to 3 minutes for original audio and royalty-free tracks.

However, rendering 5 videos a day (7.5 to 10 minutes of total video daily) entirely through generative AI video models requires a clear strategy around API costs, generation times, and asset stitching.

---

### AI Video Generation Toolkit for Developers

To generate video programmatically in Python, choose between two primary approaches:

| Approach | Recommended APIs | How It Works | Developer Pros / Cons |
| --- | --- | --- | --- |
| *Hybrid Pipeline* (Recommended) | *Fal.ai* / *Replicate* (Flux.1 for images) + *Runway Gen-3* / *Luma Dream Machine API* | Generate AI keyframe images for each script scene, then convert key moments into 3–5s motion clips. Stitch together locally using MoviePy/FFmpeg. | *Pros:* Full creative control, precise pacing, ~80% cheaper. |



*Cons:* Requires writing local stitching logic. |
| *All-in-One Direct API* | *InVideo AI API* / *HeyGen API* | Pass raw script & voice choice via single API call; server returns finished compiled MP4. | *Pros:* Instant setup, handles captions/B-roll automatically.



*Cons:* Higher cost per minute, less customization. |

> *Cost Efficiency Tip:* Generating 10 minutes of pure AI video via high-end models can run $15–$30/day in API credits. Combining high-resolution AI keyframe images (zoom/pan effects) with 2–3 short AI video clips per Short keeps cost under $2–$3/day while maintaining visual dynamic quality.

---

### End-to-End System Architecture

text
[ Google Trends RSS ] ──┐
                        ├──► [ Trend Ingestion Engine ] ──► [ Telegram Bot (HITL Gate) ]
[ Reddit API (PRAW) ] ──┘                                           │
                                                               (Select 5 Topics)
                                                                    ▼
                                                    [ CrewAI / LangGraph Pipeline ]
                                                     ┌─────────────────────────────┐
                                                     │ 1. Researcher Agent         │
                                                     │ 2. Scriptwriter Agent       │
                                                     │ 3. Critic/Fact-Check Agent ◄┼──┐ (Retry Loop)
                                                     └──────────────┬──────────────┘  │
                                                                    │ Validated       └──┘
                                                                    ▼
                                                     [ Media Rendering Engine ]
                                                     ┌─────────────────────────────┐
                                                     │ - Edge-TTS (Voiceover)      │
                                                     │ - Fal.ai / Runway (Visuals) │
                                                     │ - MoviePy (Assembly)        │
                                                     └──────────────┬──────────────┘
                                                                    ▼
                                                     [ SQLite Queue Manager ]
                                                                    │ (Rate-Limited Upload)
                                                                    ▼
                                                     [ YouTube Data API v3 ]



---

### System Components & Module Breakdown

#### Module 1: Multi-Source Trend Aggregator (src/ingestion/)

* *Sources:*
* *Google Trends RSS:* Fetches real-time search volume spikes in India.
* *Reddit API (praw):* Scrapes r/india, r/IndiaTrending, and r/AskIndia for hot posts (>100 score).


* *Processing:* Normalizes topic titles, removes duplicates using string similarity matching (fuzzywuzzy), and scores topics based on cross-source validation (higher weight if present on both Google and Reddit).

#### Module 2: Human-in-the-Loop Gateway (src/gateway/)

* *Framework:* python-telegram-bot (async).
* *Workflow:*
1. Telegram bot sends a digest message with top 10 trends and inline keyboard buttons [Topic 1], [Topic 2], etc.
2. You tap 5 topic buttons directly in Telegram.
3. Clicking *"Confirm Selection"* triggers the agent execution callback asynchronously without polling delays.



#### Module 3: Agent Core & Critic Validation (src/agents/)

Built with *CrewAI* or *LangGraph*, utilizing structured Pydantic output schemas to guarantee formatting.


       ┌────────────────────────┐
       │    Researcher Agent    │ ──► Searches web & compiles context
       └───────────┬────────────┘
                   ▼
       ┌────────────────────────┐
       │   Scriptwriter Agent   │ ──► Writes 60-90s script & visual prompts
       └───────────┬────────────┘
                   ▼
┌──────────────────────────────────────┐
│         Critic Agent         │
├──────────────────────────────────────┤
│ Checks:                              │
│ 1. Length (Word count 150-220 words) │ ──► [ Fail ] ──► Send back to Writer
│ 2. Hallucination / Fact Match        │                  with instructions
│ 3. Retention Hook Strength (1-10)    │
└──────────────────┬───────────────────┘
                   │ [ Pass ]
                   ▼
       [ Send to Media Pipeline ]



#### Module 4: Media Production Pipeline (src/media/)

1. *Audio Synthesis (edge-tts):* Generates high-definition Indian English or Hindi voiceover (en-IN-PrabhatNeural / hi-IN-MadhurNeural).
2. *Visual Generation ~~(Replicate / Fal.ai API)~~:* Sends scene descriptions from the script to Flux.1 (fast image generation) or Runway/Luma (short motion clips). 
P.S. all the video generation are paid, going to use image generation(Pollinations.ai) and then stich it to video
3. *Subtitle Engine (whisper / stable-ts):* Generates precise word-level timestamped .srt files for animated, burn-in captions.
4. *Video Compiler (MoviePy / FFmpeg):*
* Crops media to 9:16 ratio (1080x1920).
* Applies slow pan/zoom (Ken Burns effect) to static keyframes.
* Overlays animated subtitles, audio track, and subtle royalty-free background music.



#### Module 5: Queue Manager & YouTube Publisher (src/publisher/)

* *Database:* SQLite or PostgreSQL instance tracking video status (PENDING, RENDERED, QUEUED, UPLOADED).
* *YouTube Upload Strategy:* Because YouTube's Data API limits standard accounts to 10,000 quota units/day (and an upload costs ~1,600 units), uploading 5 videos at once drains 80% of your daily limit.
* *The Queue:* A cron task picks up rendered videos from SQLite and uploads them spaced hours apart (e.g., 8:00 AM, 12:00 PM, 4:00 PM, 7:00 PM, 9:00 PM), marking uploads as UNLISTED or SCHEDULED for human safety.

---

### Recommended Folder Structure for Your Repo

text
india-trend-agent/
├── config/
│   ├── settings.py         # API Keys (OpenAI, Telegram, Replicate, YouTube)
│   └── prompts.yaml        # Agent prompts & Critic guidelines
├── data/
│   └── app.db              # SQLite DB for video queue & topic state
├── src/
│   ├── ingestion/          # Google Trends & Reddit scrapers
│   ├── gateway/            # Telegram bot interface
│   ├── agents/             # CrewAI definition (Researcher, Writer, Critic)
│   ├── media/              # TTS, Image/Video AI generator, MoviePy render
│   └── publisher/          # YouTube API client & queue runner
├── templates/              # Subtitle fonts, background audio assets
├── main.py                 # System entrypoint
└── requirements.txt



---

Which Python framework for multi-agent orchestration do you prefer to start with for Module 3: *CrewAI* (easier high-level abstractions, fast setup) or *LangGraph* (fine-grained state machine control over loops and callbacks)?