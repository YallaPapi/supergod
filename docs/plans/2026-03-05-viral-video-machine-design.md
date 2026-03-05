# Viral Video Machine -- Design Document

## What This Is

An automated system that generates faceless YouTube cash cow videos using Steffen Miro's exact methodology. One click: pick a niche + topic -> get a ready-to-upload video with thumbnail, title, description, and tags.

The system handles: niche research assistance, script generation, AI voiceover, visual assembly, thumbnail creation, and metadata optimization. The only manual step is editing (which Steffen himself says "cannot be done with AI yet") -- but we minimize it with structured editing briefs.

---

## Architecture Overview

```
                    +------------------+
                    |   Web Dashboard  |
                    |  (Control Panel) |
                    +--------+---------+
                             |
                    +--------v---------+
                    |   Orchestrator   |
                    |  (Task Pipeline) |
                    +--------+---------+
                             |
          +------------------+------------------+
          |          |          |          |     |
     +----v---+ +---v----+ +--v-----+ +-v----+ +v--------+
     | Niche  | | Script | | Voice  | |Visual| |Thumbnail|
     |Research| | Engine | | Engine | |Engine| | Engine  |
     +--------+ +--------+ +--------+ +------+ +---------+
          |          |          |          |          |
     NextLev    ChatGPT    GenAI Pro  Stock APIs  ChatGPT
     YouTube    Claude     ElevenLabs Pexels      OpenArt
     API                              Pixabay     Photoshop
                                      Storyblocks  (auto)
```

---

## System Components

### 1. Niche Research Assistant

**Purpose:** Help identify profitable niches using Steffen's 8-point criteria.

**How it works:**
- Connects to YouTube Data API v3
- User provides seed topics or broad categories
- System searches for channels with:
  - <5 small channels getting more views than subs
  - No channels with 100K+ subscribers
  - No failing small channels
  - Niche age <6 months
- Scores each niche on the 8-point checklist
- Uses NextLev API (if available) or scrapes RPM estimates
- Outputs ranked niche opportunities with data

**Inputs:** Seed topics (e.g., "history", "cars", "crime")
**Outputs:** Ranked niche list with scores, example channels, estimated RPM, competition level

### 2. Competitor Analyzer

**Purpose:** Study competitor channels to extract winning patterns.

**How it works:**
- Takes a competitor channel URL
- Fetches all video data (titles, views, publish dates, durations, thumbnails)
- Identifies outlier videos (views >> channel average)
- Extracts transcript from top-performing videos
- Analyzes thumbnail patterns (colors, layout, text placement)
- Analyzes title patterns (word choices, structure, length)
- Determines optimal video length (1.5x competitor average)
- Generates a "Channel Blueprint" document

**Inputs:** Competitor channel URL(s)
**Outputs:** Blueprint with video length target, title formulas, thumbnail style guide, top topics, transcript references

### 3. Script Engine

**Purpose:** Generate retention-optimized scripts using competitor transcripts as reference.

**How it works:**
- Takes topic + competitor transcript reference + target word count
- Uses Steffen's exact ChatGPT prompt chain:
  1. Feed competitor transcript: "study and analyze this transcript"
  2. Generate meta-prompt for the specific topic
  3. Feed meta-prompt back with reference to generate script
- Structures script with: Hook -> Body (twist every 20-30 seconds) -> Quick ending (no long outro)
- Word count targets: 1,500 words per 10 minutes
- Outputs script + ChatGPT image search terms for visual sourcing

**Inputs:** Topic, competitor transcript, target video length, niche context
**Outputs:** Full script (segmented), visual search terms per segment, suggested title variations

### 4. Voice Engine

**Purpose:** Generate AI voiceover from script.

**How it works:**
- Integrates with GenAI Pro API (primary) and ElevenLabs API (fallback)
- Voice selection: match competitor voice profile (age, gender, accent)
- Voice cloning: upload competitor audio sample, clone voice
- Generate voiceover from full script
- Post-processing: detect and remove AI pauses/silences (replaces FireCut)
- Export as WAV/MP3

**Inputs:** Script text, voice profile (or audio sample for cloning)
**Outputs:** Voiceover audio file (silence-removed)

### 5. Visual Engine

**Purpose:** Source and organize visual assets for each script segment.

**How it works:**
- Takes script segments + search terms from Script Engine
- For each segment, searches multiple sources in parallel:
  - Pexels API (free stock footage)
  - Pixabay API (free stock footage)
  - Storyblocks API (premium, if user has account)
  - Google Images (for still images)
  - ChatGPT/OpenArt (AI-generated images when no stock available)
- Downloads and organizes clips/images into folders mapped to script segments
- Generates an "editing brief" document: script segment -> matching visual -> timing notes
- Clips are trimmed to 5-7 second segments (Steffen's rule: change visual every 5-7 seconds)

**Inputs:** Script segments with search terms, niche visual style preferences
**Outputs:** Organized media folder + editing brief document (for human editor or future AI assembly)

### 6. Thumbnail Engine

**Purpose:** Generate click-worthy thumbnails matching competitor style.

**How it works:**
- Takes competitor thumbnail samples as style reference
- Uses Steffen's thumbnail prompt:
  ```
  Pretend you are a professional thumbnail designer. Write me a prompt
  to copy exactly the style of thumbnails. Target clickbait and CTR
  for the topic [TOPIC]. Thumbnail text: [TEXT].
  ```
- Generates base image via ChatGPT image generation or OpenArt
- Applies post-processing:
  - Background removal on subject
  - Add bold text overlay (power words from script engine)
  - Add red arrow PNG if style requires it
  - Color grading: brightness up, saturation up, texture/clarity up
  - Match competitor color palette
- Generates 3 thumbnail variants for A/B testing
- Output at 1280x720 (YouTube standard)

**Inputs:** Topic, competitor thumbnail samples, title text, power words
**Outputs:** 3 thumbnail variants (PNG)

### 7. Metadata Engine

**Purpose:** Generate optimized title, description, and tags.

**How it works:**
- Title generation: analyzes competitor title patterns, generates 10 variants, ranks by clickbait score
- Description: video title as line 1, keyword-rich paragraph, channel description boilerplate, links
- Tags: integrates with rapidtags.io or generates from title + topic keywords
- Adds power words: "exposed, destroyed, game over, total ripoff, avoid, stop buying"

**Inputs:** Video topic, competitor title patterns, script summary
**Outputs:** Ranked title options, full description, tag list

### 8. Assembly Engine (Future -- v2)

**Purpose:** Auto-assemble the final video from voiceover + visuals.

**Current state:** Steffen says "editing cannot be done with AI yet." For v1, we output an editing brief that a human editor ($15-30) follows. For v2, we explore:
- FFmpeg-based auto-assembly (voiceover + image slideshow with Ken Burns effect + captions)
- CapCut API integration if available
- Simple assembly: voiceover track + images timed to script segments + auto-captions

Even basic auto-assembly covers 60% of Steffen's simpler niches (images + voiceover channels).

### 9. Web Dashboard

**Purpose:** Control panel for the entire operation.

**Features:**
- Niche research results browser
- Competitor analyzer with visual charts
- One-click video generation pipeline
- Video queue management (batch generation)
- Team management (assign editing tasks)
- Revenue tracking (connect YouTube Analytics API)
- Channel health monitoring (impressions, CTR, AVD)
- Cost tracking per video
- Google Sheets export for editor briefs

---

## One-Click Flow

```
User clicks "Generate Video"
  |
  v
[1] Pick niche (from saved niches or enter new)
  |
  v
[2] Pick topic (from generated ideas or enter custom)
  |
  v
[3] System runs in parallel:
     |-- Script Engine -> generates script + search terms
     |-- Thumbnail Engine -> generates 3 thumbnails
     |-- Metadata Engine -> generates title/desc/tags
  |
  v
[4] Voice Engine -> generates voiceover from script
  |
  v
[5] Visual Engine -> sources clips/images per segment
  |
  v
[6] Assembly Engine -> either:
     a) Auto-assembles simple video (images + voiceover + captions)
     b) Generates editing brief + media package for human editor
  |
  v
[7] Output: Complete video package
     - Video file (if auto-assembled) OR editing brief + media
     - 3 thumbnail variants
     - Title + description + tags
     - Upload-ready metadata
```

---

## Tech Stack

| Component | Technology |
|-----------|-----------|
| Backend | Python (FastAPI) |
| Frontend | React + Tailwind (dashboard) |
| Database | SQLite (channels, niches, videos, analytics) |
| Task Queue | Celery + Redis (parallel pipeline steps) |
| AI Scripts | OpenAI API (ChatGPT), Anthropic API (Claude) |
| AI Voice | GenAI Pro API, ElevenLabs API |
| AI Images | OpenAI DALL-E, OpenArt API |
| Stock Media | Pexels API, Pixabay API |
| YouTube Data | YouTube Data API v3 |
| Video Assembly | FFmpeg (auto-assembly), Pillow (thumbnails) |
| Image Processing | Pillow, rembg (background removal) |
| Deployment | Single server (can run on any VPS) |

---

## MVP Scope (v1)

Build these first (ordered by impact):

1. **Script Engine** -- ChatGPT integration with Steffen's exact prompt chain
2. **Voice Engine** -- GenAI Pro/ElevenLabs integration with silence removal
3. **Thumbnail Engine** -- AI image gen + text overlay + color grading
4. **Metadata Engine** -- Title/description/tag generation
5. **Simple Assembly** -- FFmpeg: voiceover + image slideshow + auto-captions
6. **Web Dashboard** -- Basic UI to trigger pipeline and view results

### MVP Delivers
- Input: topic + competitor channel URL
- Output: complete video package (video file, thumbnails, metadata)
- Time: ~5 minutes per video
- Cost: ~$0.50-2.00 per video (API costs)

### v2 Adds
- Niche Research Assistant (YouTube API integration)
- Competitor Analyzer (automated pattern extraction)
- Batch generation (queue 50 videos at once)
- YouTube upload integration (auto-publish)
- Analytics dashboard (revenue tracking)
- Team management (editor assignment, review workflow)
- Multi-channel management
- A/B thumbnail testing

---

## Revenue Model

This system itself could be productized:

1. **Use it yourself** -- Run 10+ channels, $50-100K/month potential
2. **SaaS** -- Sell access to other YTA operators ($97-297/month)
3. **Agency** -- Run channels for clients, charge $500-2K/month per channel
4. **Course upsell** -- Teach the system, sell the tool as add-on

---

## Why This Is a Good Supergod Test Project

- **4-5 independent subsystems** that can be built in parallel by different workers
- **Clear interfaces** between components (script -> voice -> visuals -> assembly)
- **Testable** -- each component can be validated independently
- **Real product** -- generates actual revenue if it works
- **Moderate complexity** -- not trivial, not impossibly hard
- **Well-defined requirements** -- Steffen's methodology is extremely specific

### Suggested Supergod Decomposition
| Worker | Subsystem |
|--------|-----------|
| Worker 1 | Script Engine (ChatGPT prompt chain + transcript analysis) |
| Worker 2 | Voice Engine (GenAI Pro/ElevenLabs + silence removal) |
| Worker 3 | Visual Engine (Pexels/Pixabay/stock API + media organization) |
| Worker 4 | Thumbnail Engine (AI image gen + Pillow post-processing) |
| Integrator | Pipeline orchestrator + Web Dashboard + Assembly Engine |
