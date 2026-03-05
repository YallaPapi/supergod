# mediafactory -- Media Production CLI
## Self-Contained PRD for Supergod Worker 3

---

## Context

This tool is part of a larger system called "Viral Video Machine" -- an automated faceless YouTube cash cow video generation system. However, mediafactory is built as a **STANDALONE tool**. It knows nothing about the other tools in the system (ytscout, scriptforge, thumbsmith). It shares zero code with them. It does not import from them.

mediafactory communicates via **JSON files only**. It receives a script as a JSON input file (typically produced upstream by scriptforge), but it does NOT import or depend on scriptforge in any way. The script JSON contains segments with `visual_search_terms` -- mediafactory uses those to source matching visuals from stock footage APIs and AI generators.

The **Strategy Rules** section below contains non-negotiable requirements derived from Steffen Miro's YouTube Automation course methodology (distilled from ~100 video transcripts). These rules are the core IP -- they are what makes this system produce videos that actually get views, not just generic content. **Workers MUST implement every Strategy Rule as specified. They are not suggestions.**

---

## Overview

### What It Is

A standalone media production CLI. Give it a script, it produces voiceover audio, sources matching visuals, and assembles a complete video. Think of it as "one-person video production studio."

Could be sold standalone: "AI video production pipeline -- script to video in 5 minutes."

### What It Produces

- **Voiceover audio** (MP3) from script text via ElevenLabs, OpenAI TTS, or edge-tts
- **Sourced visuals** (stock video clips and images, organized per script segment)
- **Assembled video** (1920x1080 H.264 MP4 with captions, silence-removed audio, and visual cuts every 5-7 seconds)
- **Editing brief** (JSON + Markdown for human editors when auto-assembly isn't sufficient)
- **Manifest** (JSON describing all produced assets, costs, and metadata)

---

## Standalone CLI Interface

```bash
# Generate voiceover from script
mediafactory voice --script ./script.json --voice "male_authoritative_american" --output-dir ./results/

# Source visuals for script segments
mediafactory visuals --script ./script.json --output-dir ./results/

# Assemble video from voiceover + visuals
mediafactory assemble --voiceover ./results/voiceover.mp3 \
  --visuals-dir ./results/visuals/ \
  --script ./script.json \
  --output-dir ./results/

# Full pipeline: script → voiceover → visuals → video
mediafactory produce --script ./script.json \
  --voice "male_authoritative_american" \
  --output-dir ./results/

# Generate editing brief instead of auto-assembling
mediafactory brief --script ./script.json \
  --voiceover ./results/voiceover.mp3 \
  --visuals-dir ./results/visuals/ \
  --output-dir ./results/

# Manage voice library
mediafactory voices --list
mediafactory voices --clone --audio ./sample.mp3 --name "submarine_narrator"
```

---

## Input/Output Contracts

### produce input (`production_request.json`)

```json
{
  "script": {
    "full_text": "Deep beneath the surface...",
    "segments": [
      {
        "index": 0,
        "text": "Deep beneath the surface of the Pacific Ocean...",
        "duration_seconds": 11.2,
        "visual_search_terms": ["submarine underwater dark ocean", "nuclear submarine exterior"]
      }
    ]
  },
  "voice": {
    "provider": "elevenlabs",
    "voice_id": "pNInz6obpgDQGcFmaJgB",
    "settings": {
      "stability": 0.5,
      "similarity_boost": 0.75
    }
  },
  "visual_preferences": {
    "prefer_video_over_images": true,
    "clip_duration_seconds": 6,
    "resolution": "1920x1080",
    "ai_fallback": true
  },
  "assembly": {
    "mode": "auto",
    "captions": true,
    "caption_style": {
      "font": "Impact",
      "size": 48,
      "color": "#ffffff",
      "background": "#000000aa",
      "position": "bottom"
    },
    "background_music": null,
    "end_screen_seconds": 20
  }
}
```

### produce output (`manifest.json`)

```json
{
  "voiceover": {
    "path": "voiceover.mp3",
    "duration_seconds": 912.5,
    "voice_id": "pNInz6obpgDQGcFmaJgB",
    "provider": "elevenlabs",
    "silence_removed": true,
    "original_duration_seconds": 985.0,
    "silence_removed_seconds": 72.5,
    "cost_usd": 0.85
  },
  "visuals": {
    "total_segments": 45,
    "segments": [
      {
        "segment_index": 0,
        "files": [
          {
            "path": "visuals/seg_00_001.mp4",
            "source": "pexels",
            "type": "video",
            "duration_seconds": 6.0,
            "search_term": "submarine underwater dark ocean",
            "original_url": "https://videos.pexels.com/..."
          }
        ]
      }
    ],
    "total_clips": 38,
    "total_images": 7,
    "sources_used": {"pexels": 25, "pixabay": 13, "ai_generated": 7}
  },
  "assembly": {
    "video_path": "final_video.mp4",
    "duration_seconds": 912.5,
    "resolution": "1920x1080",
    "codec": "h264",
    "has_captions": true,
    "captions_path": "captions.srt",
    "mode": "hybrid",
    "file_size_mb": 245.3
  },
  "editing_brief_path": null,
  "total_cost_usd": 0.95,
  "timestamp": "2026-03-05T15:30:00Z"
}
```

### brief output (`editing_brief.json`)

```json
{
  "video_title": "The Submarine That Changed Everything",
  "total_duration_seconds": 912,
  "voiceover_path": "voiceover.mp3",
  "segments": [
    {
      "index": 0,
      "script_text": "Deep beneath the surface of the Pacific Ocean...",
      "start_time": "00:00:00",
      "end_time": "00:00:11",
      "visual_assets": ["visuals/seg_00_001.mp4"],
      "notes": "Opening shot -- dark underwater footage, slow zoom",
      "transition": "fade_from_black"
    }
  ],
  "style_notes": "Dark, dramatic tone. Quick cuts every 5-7 seconds. No flashy transitions -- simple cuts and dissolves only.",
  "export_settings": {
    "resolution": "1920x1080",
    "fps": 30,
    "codec": "h264"
  }
}
```

---

## Strategy Rules (MUST implement -- from Steffen Miro methodology)

These are non-negotiable. They come from 100+ videos of proven methodology.

**Visual Change Rule:**
- Change the visual (cut to new clip or image) every 5-7 seconds. NEVER let one shot stay longer than 7 seconds.
- This is the #1 retention technique for faceless channels
- Each script segment should map to 1-2 visual assets at 5-7 seconds each

**Silence Removal (replaces Steffen's FireCut plugin):**
- AI voiceover generates unnatural pauses between sentences
- Detect pauses longer than 300ms
- Shorten to 100ms (not remove entirely -- needs to sound natural)
- Steffen's FireCut settings: "120, then 100" -- our equivalent is 300ms threshold → 100ms replacement
- This typically removes 5-10% of audio duration
- Also normalize volume to consistent dB level across the entire voiceover

**Voice Selection Rules:**
- NEVER use the default/popular voices (ElevenLabs "Adam" voice is overused -- audiences recognize it)
- Prefer voice cloning from competitor audio when possible
- Match competitor voice profile: same gender, similar age range, same accent
- Voice should match the niche tone:
  - History/documentary → authoritative, older male
  - Crime/horror → dramatic, slightly lower pitch
  - Finance → confident, professional
  - Animal/nature → warm, friendly

**Voice Cloning Process:**
1. Download competitor's audio (from their video)
2. Upload to ElevenLabs voice cloning
3. Use cloned voice for all videos on that channel
4. This ensures voice consistency AND matches what the audience already likes

**Visual Sourcing Priority:**
1. Stock video clips (Pexels, Pixabay) -- preferred because motion > still images
2. Stock images with Ken Burns effect (pan/zoom to create motion from stills)
3. AI-generated images (DALL-E, OpenArt) -- only when stock is insufficient
4. Never use a single visual source for the entire video -- mix sources

**Assembly Rules:**
- Resolution: 1920x1080 (standard YouTube HD)
- Codec: H.264 MP4
- End screen: leave last 20 seconds for YouTube end screen overlay (can be a static image or loop)
- End screens: ONE only (don't confuse viewers with multiple choices)
- No flashy transitions between clips -- simple cuts or dissolves only
- Background music: optional, very low volume (voiceover must be dominant)

**Editing Brief (when auto-assembly isn't good enough):**
- This is for human editors hired on Upwork/Fiverr ($15-30/video)
- Must include: script text per segment, visual asset file paths, timing markers, voiceover file path
- Editor cost reference: Simple (images + voiceover) $10-20, Basic (stock + voiceover) $15-30, Medium $30-50

---

## Components to Build

1. **Voice Engine** (`mediafactory/voice_engine.py`)
   - Multi-provider support:
     - ElevenLabs API (primary quality)
     - OpenAI TTS API (fallback)
     - edge-tts (free, for testing/development)
   - Voice library management (save/list/delete voices)
   - Voice cloning (upload audio sample → create voice)
   - Full script to speech generation
   - Segment-by-segment generation (for precise timing)
   - Cost tracking per generation

2. **Silence Remover** (`mediafactory/silence_remover.py`)
   - Detect silence/pauses in audio (pydub)
   - Remove pauses > 300ms, shorten to 100ms
   - Configurable threshold
   - Audio normalization (consistent volume levels)
   - Export as WAV and MP3
   - Report: how many seconds of silence removed

3. **Visual Sourcer** (`mediafactory/visual_sourcer.py`)
   - Multi-source parallel search:
     - Pexels API (free stock video + images)
     - Pixabay API (free stock video + images)
     - OpenAI DALL-E (AI-generated fallback)
   - Takes search terms from script segments
   - Ranks results by relevance
   - Prefers video clips over still images
   - Downloads and organizes into segment-mapped folders
   - Trims video clips to target duration (5-7 seconds)
   - Resizes/crops to 1920x1080

4. **Assembly Engine** (`mediafactory/assembler.py`)
   - FFmpeg-based pipeline, 3 modes:
     - Slideshow: images + Ken Burns pan/zoom + voiceover
     - Clip assembly: video clips + voiceover
     - Hybrid: mix of clips and images
   - Auto-captions via whisper (word-level timestamps)
   - Caption rendering (SRT file + optional burn-in)
   - Audio mixing (voiceover + optional background music)
   - Fade in/out at video start/end
   - End screen placeholder (last 20 seconds blank for overlay)
   - Output: 1920x1080 H.264 MP4

5. **Editing Brief Generator** (`mediafactory/brief_generator.py`)
   - For niches where auto-assembly isn't sufficient
   - Maps script segments to visual assets with timestamps
   - Includes style notes, transition suggestions
   - Export as JSON (machine-readable) and Markdown (human-readable)

6. **Stock API Clients** (`mediafactory/stock_clients.py`)
   - Pexels client: search, download, attribution tracking
   - Pixabay client: search, download, attribution tracking
   - Rate limiting and caching

---

## Dependencies

```
elevenlabs                  # ElevenLabs API
openai                      # OpenAI TTS + DALL-E fallback
edge-tts                    # free TTS for testing
pydub                       # audio processing, silence removal
Pillow                      # image processing, resizing
openai-whisper              # caption generation
requests                    # stock API HTTP calls
click                       # CLI framework
# System dependency: ffmpeg (must be installed)
```

---

## Project Structure

```
mediafactory/
├── mediafactory/
│   ├── __init__.py
│   ├── cli.py                  # click CLI entry point
│   ├── voice_engine.py         # multi-provider TTS
│   ├── silence_remover.py      # audio silence detection/removal
│   ├── visual_sourcer.py       # stock footage sourcing
│   ├── assembler.py            # FFmpeg video assembly
│   ├── brief_generator.py      # editing brief for human editors
│   ├── stock_clients.py        # Pexels/Pixabay API clients
│   ├── caption_generator.py    # whisper-based captions
│   └── models.py               # this tool's own dataclasses
├── tests/
│   ├── test_voice_engine.py
│   ├── test_silence_remover.py
│   ├── test_visual_sourcer.py
│   ├── test_assembler.py
│   ├── test_caption_generator.py
│   └── fixtures/               # sample audio, images, API responses
│       ├── sample_voiceover.mp3
│       ├── sample_image.jpg
│       └── pexels_response.json
├── pyproject.toml
├── README.md
└── .env.example
```

---

## Tests

- Unit: silence detection on sample audio (fixture with known silence pattern)
- Unit: audio normalization (verify output dB levels)
- Unit: visual search term → API query construction
- Unit: FFmpeg command construction for each assembly mode
- Unit: SRT caption file generation from word timestamps
- Unit: Ken Burns effect parameter calculation
- Integration: voice generation with mocked ElevenLabs/OpenAI
- Integration: visual sourcing with mocked Pexels/Pixabay
- Integration: full assembly with sample audio + images → verify MP4 output

---

## Success Criteria

- [ ] `mediafactory voice` generates voiceover audio from script text
- [ ] `mediafactory visuals` downloads and organizes stock media per segment
- [ ] `mediafactory assemble` produces watchable MP4 from voiceover + visuals
- [ ] Silence removal measurably shortens audio (removes pauses)
- [ ] Auto-captions appear in assembled video
- [ ] `mediafactory brief` generates human-readable editing brief

---

## Cost Reference (per video)

| Item | Cost |
|------|------|
| mediafactory voice (ElevenLabs) | ~$0.50-1.00 |
| mediafactory visuals (free stock) | $0 |
| mediafactory visuals (AI fallback) | ~$0.10 |
| With human editor instead of auto-assembly | add $15-30 |
