# vmachine -- Pipeline Orchestrator & Dashboard
## Self-Contained PRD for Supergod Integrator

---

## Context

This is the **INTEGRATOR** -- it wires together 4 standalone tools into one product.

- It calls tools via **CLI subprocess** or **Python library import**
- It owns the **database** (SQLite), the **web dashboard** (React), and the **pipeline orchestration**
- It does **NOT** duplicate any logic from the tools -- it only calls them and passes JSON between them
- The tools are: **ytscout**, **scriptforge**, **mediafactory**, **thumbsmith**
- Each tool works independently -- the integrator just chains their JSON inputs/outputs
- The integrator is the only component that knows all four tools exist

---

## Overview

### What It Is

The glue layer. Calls all 4 tools in the right order, stores results in a database, provides a web dashboard, handles YouTube upload, and tracks analytics. This is where 4 standalone tools become one product.

The integrator does NOT duplicate any logic from the tools. It calls their CLIs (or imports their Python modules) and passes JSON between them.

### What It Produces

- A complete video production pipeline: topic in, upload-ready video package out
- A web dashboard for managing channels, niches, pipelines, editors, and analytics
- YouTube upload automation (unlisted -> HD -> public)
- Channel health monitoring and shadowban detection
- Revenue and cost tracking per video, per channel, per niche

### Why It Exists

The 4 tools (ytscout, scriptforge, mediafactory, thumbsmith) each do one thing well but know nothing about each other. vmachine is the orchestrator that:
1. Chains their JSON outputs into a complete pipeline
2. Provides persistent state (SQLite database)
3. Provides a user interface (React dashboard)
4. Handles YouTube upload and analytics
5. Manages batch operations (queue 10-50 videos)
6. Tracks costs, revenue, and channel health

---

## The 4 Tools This Integrates

| Tool | Description | Key CLI Commands |
|------|-------------|------------------|
| **ytscout** | YouTube niche & competitor intelligence | `ytscout scan`, `ytscout analyze`, `ytscout check-topic`, `ytscout suggest-topics`, `ytscout monitor` |
| **scriptforge** | AI script & metadata generator | `scriptforge write`, `scriptforge metadata`, `scriptforge ideate`, `scriptforge score`, `scriptforge batch` |
| **mediafactory** | Voice, visuals, and video assembly | `mediafactory voice`, `mediafactory visuals`, `mediafactory assemble`, `mediafactory produce`, `mediafactory brief` |
| **thumbsmith** | AI thumbnail & brand identity generator | `thumbsmith generate`, `thumbsmith brand`, `thumbsmith analyze`, `thumbsmith score`, `thumbsmith consistency-check`, `thumbsmith apply-template` |

The integrator calls these tools but does not implement their internals.

---

## JSON Contracts

These are the output JSON schemas from all 4 tools. The integrator needs these schemas to know what data flows between tools.

### ytscout Outputs

#### niche_report.json (from `ytscout scan`)

```json
{
  "niches": [
    {
      "name": "submarine documentaries",
      "seed_topic": "submarines",
      "score": {
        "small_channels_with_high_views": 3,
        "no_big_channels": true,
        "no_failing_small_channels": true,
        "niche_age_months": 2,
        "can_create_longer_content": true,
        "monetizable": true,
        "estimated_rpm": 10.0,
        "long_form_potential": true,
        "total": 8
      },
      "example_channels": [
        {
          "name": "Submarine Insider",
          "url": "https://youtube.com/@SubmarineInsider",
          "subscribers": 4200,
          "total_views": 1800000,
          "video_count": 28,
          "avg_views_per_video": 64285,
          "avg_video_length_seconds": 720,
          "first_video_date": "2026-01-15",
          "top_video_views": 450000
        }
      ],
      "verdict": "strong_opportunity",
      "reasoning": "3 small channels averaging 60K+ views/video, no channels over 100K subs, niche is only 2 months old"
    }
  ],
  "scan_metadata": {
    "topics_searched": 3,
    "channels_analyzed": 47,
    "api_quota_used": 2340,
    "timestamp": "2026-03-05T14:30:00Z"
  }
}
```

#### competitor_blueprint.json (from `ytscout analyze`)

```json
{
  "channel": {
    "name": "Submarine Insider",
    "url": "https://youtube.com/@SubmarineInsider",
    "subscribers": 4200,
    "total_views": 1800000,
    "video_count": 28,
    "avg_video_length_seconds": 720,
    "posting_frequency_per_week": 3.5,
    "estimated_rpm": 10.0,
    "first_video_date": "2026-01-15"
  },
  "outlier_videos": [
    {
      "title": "The Submarine That Vanished Without a Trace",
      "url": "https://youtube.com/watch?v=xxx",
      "views": 450000,
      "duration_seconds": 840,
      "publish_date": "2026-02-01",
      "thumbnail_url": "https://i.ytimg.com/vi/xxx/maxresdefault.jpg",
      "is_outlier": true,
      "outlier_ratio": 7.0,
      "transcript": "In the cold depths of the Atlantic..."
    }
  ],
  "title_patterns": {
    "common_structures": ["The [noun] That [dramatic verb]", "[Number] [things] You Never Knew About [topic]"],
    "avg_title_length": 48,
    "power_words_used": ["vanished", "secret", "never", "terrifying", "deadly"],
    "uses_numbers": false,
    "uses_questions": false
  },
  "thumbnail_analysis": {
    "dominant_colors": ["#1a1a2e", "#e94560", "#ffffff"],
    "avg_brightness": 0.35,
    "avg_saturation": 0.65,
    "avg_contrast": 0.8,
    "has_text": true,
    "has_faces": false,
    "layout_pattern": "dark_background_centered_subject",
    "text_colors": ["#ffffff", "#e94560"]
  },
  "voice_profile": {
    "gender": "male",
    "age_range": "middle",
    "accent": "american",
    "tone": "authoritative",
    "speaking_rate_wpm": 145
  },
  "blueprint": {
    "target_video_length_seconds": 1080,
    "target_word_count": 2250,
    "posting_frequency_per_week": 4,
    "title_formulas": ["The [noun] That [dramatic verb]", "Why [topic] [shocking fact]"],
    "thumbnail_style": "dark_dramatic_red_accent",
    "voice_style": "authoritative_male_american",
    "content_mix": "80% proven topics, 20% experimental"
  }
}
```

#### topic_check.json (from `ytscout check-topic`)

```json
{
  "topic": "History of the Titanic",
  "niche": "maritime history",
  "demand_score": 7,
  "supply_level": "medium",
  "existing_videos": 12,
  "avg_views_on_existing": 85000,
  "newest_video_age_days": 45,
  "recommendation": "go",
  "reasoning": "High search volume, moderate competition, no recent coverage",
  "alternative_topics": [
    "The Titanic's Sister Ship Nobody Talks About",
    "What Really Happened Below Deck on the Titanic"
  ]
}
```

### scriptforge Outputs

#### script.json (from `scriptforge write`)

```json
{
  "topic": "The Submarine That Changed Everything",
  "full_text": "Deep beneath the surface of the Pacific Ocean, a steel giant...",
  "word_count": 2280,
  "estimated_duration_seconds": 912,
  "segments": [
    {
      "index": 0,
      "text": "Deep beneath the surface of the Pacific Ocean, a steel giant lurked in the darkness. What it carried would change the course of history forever.",
      "word_count": 28,
      "duration_seconds": 11.2,
      "segment_type": "hook",
      "visual_search_terms": ["submarine underwater dark ocean", "nuclear submarine exterior", "deep ocean darkness"]
    },
    {
      "index": 1,
      "text": "The year was 1960, and the Cold War had reached...",
      "word_count": 85,
      "duration_seconds": 34.0,
      "segment_type": "body",
      "visual_search_terms": ["cold war 1960s", "military submarine crew", "soviet union map"]
    }
  ],
  "hook": "Deep beneath the surface of the Pacific Ocean, a steel giant lurked in the darkness. What it carried would change the course of history forever.",
  "generation_metadata": {
    "model": "gpt-4o",
    "prompt_chain_steps": 3,
    "total_tokens": 8450,
    "cost_usd": 0.042,
    "timestamp": "2026-03-05T15:00:00Z"
  }
}
```

#### metadata.json (from `scriptforge metadata`)

```json
{
  "topic": "The Submarine That Changed Everything",
  "titles": [
    {"text": "The Submarine That Changed Everything", "clickbait_score": 7.5, "power_words": ["changed", "everything"]},
    {"text": "This Secret Submarine Changed History Forever", "clickbait_score": 8.8, "power_words": ["secret", "changed", "forever"]},
    {"text": "The Submarine Nobody Was Supposed to Know About", "clickbait_score": 9.1, "power_words": ["nobody", "supposed"]},
    {"text": "Why This Submarine Terrified the Entire Soviet Navy", "clickbait_score": 8.5, "power_words": ["terrified", "entire"]}
  ],
  "recommended_title": "The Submarine Nobody Was Supposed to Know About",
  "description": "The Submarine Nobody Was Supposed to Know About\n\nIn 1960, the US Navy launched a submarine so advanced...\n\n#submarines #history #coldwar #navy #documentary",
  "tags": ["submarine documentary", "cold war submarines", "nuclear submarine", "navy history", "military documentary", "submarine history", "USS Triton"],
  "category": "Education",
  "language": "en",
  "generation_metadata": {
    "model": "gpt-4o-mini",
    "cost_usd": 0.003
  }
}
```

#### quality_score.json (from `scriptforge score`)

```json
{
  "overall_score": 7.8,
  "dimensions": {
    "hook_strength": 8.5,
    "pacing": 7.0,
    "ending_quality": 6.5,
    "word_density": 8.0,
    "voiceover_readability": 8.5
  },
  "suggestions": [
    "Add a stronger twist around the 3-minute mark -- current pacing dips",
    "Ending feels abrupt -- add one final surprising fact before closing",
    "Consider adding a 'but what they didn't know was...' at segment 12"
  ],
  "comparison_to_reference": {
    "structural_similarity": 0.82,
    "pacing_match": 0.75,
    "word_density_match": 0.90
  }
}
```

#### topic_ideas.json (from `scriptforge ideate`)

```json
{
  "niche": "submarine documentaries",
  "topics": [
    {
      "title": "The Submarine Crew That Mutinied at Sea",
      "category": "proven",
      "reasoning": "Mutiny/drama topics consistently outperform in military niches",
      "estimated_demand": 8
    },
    {
      "title": "Inside the World's Deepest Submarine Base",
      "category": "proven",
      "reasoning": "Similar to competitor's top video about secret facilities",
      "estimated_demand": 7
    }
  ],
  "generation_metadata": {
    "source_outliers_analyzed": 5,
    "model": "gpt-4o",
    "cost_usd": 0.02
  }
}
```

### mediafactory Outputs

#### manifest.json (from `mediafactory produce`)

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

#### editing_brief.json (from `mediafactory brief`)

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

### thumbsmith Outputs

#### manifest.json (from `thumbsmith generate`)

```json
{
  "thumbnails": [
    {
      "variant": "A",
      "path": "thumb_A.png",
      "description": "Dark submarine silhouette, red 'VANISHED' text upper right, high contrast",
      "text_overlay": "VANISHED",
      "text_position": "upper_right",
      "text_color": "#e94560",
      "text_font": "Impact",
      "dominant_colors": ["#1a1a2e", "#e94560", "#ffffff"],
      "processing_applied": ["ai_generation", "color_grading", "text_overlay", "sharpening"]
    },
    {
      "variant": "B",
      "path": "thumb_B.png",
      "description": "Submarine bow emerging from darkness, white 'VANISHED' text center, red glow",
      "text_overlay": "VANISHED",
      "text_position": "center",
      "text_color": "#ffffff",
      "text_font": "Bebas Neue",
      "dominant_colors": ["#0d1117", "#e94560", "#ffffff"],
      "processing_applied": ["ai_generation", "color_grading", "text_overlay", "glow_effect"]
    },
    {
      "variant": "C",
      "path": "thumb_C.png",
      "description": "Split view: submarine left, ocean abyss right, 'VANISHED' across center",
      "text_overlay": "VANISHED",
      "text_position": "center_overlay",
      "text_color": "#ffffff",
      "text_font": "Oswald",
      "dominant_colors": ["#1a1a2e", "#16213e", "#e94560"],
      "processing_applied": ["ai_generation", "color_grading", "text_overlay", "split_composition"]
    }
  ],
  "generation_metadata": {
    "ai_model": "dall-e-3",
    "cost_usd": 0.12,
    "timestamp": "2026-03-05T16:00:00Z"
  }
}
```

#### brand_kit.json (from `thumbsmith brand`)

```json
{
  "channel_name": "Deep Ocean Mysteries",
  "logo": {
    "path": "brand/logo_800x800.png",
    "style": "minimalist_icon"
  },
  "banner": {
    "path": "brand/banner_2560x1440.png",
    "tagline": "Secrets From the Deep"
  },
  "color_palette": ["#1a1a2e", "#e94560", "#ffffff", "#16213e"],
  "fonts": {
    "primary": "Impact",
    "secondary": "Oswald"
  },
  "channel_description": "Deep Ocean Mysteries explores the most incredible stories from beneath the waves. New videos every Monday, Wednesday, and Friday.\n\nBusiness inquiries: deepoceanmysteries@gmail.com",
  "social_profiles": {
    "instagram_bio": "Secrets from the deep. New stories every week.",
    "tiktok_bio": "Ocean mysteries you won't believe. Full videos on YouTube."
  },
  "thumbnail_template": {
    "path": "brand/template.json",
    "layout": "dark_background_centered_subject",
    "text_font": "Impact",
    "text_color": "#ffffff",
    "accent_color": "#e94560",
    "has_border": false
  }
}
```

#### ctr_score.json (from `thumbsmith score`)

```json
{
  "thumbnail_path": "./my_thumbnail.png",
  "predicted_ctr_score": 7.2,
  "dimensions": {
    "contrast": 8.5,
    "text_readability": 6.0,
    "subject_clarity": 7.5,
    "color_vibrancy": 8.0,
    "emotional_intensity": 6.5
  },
  "suggestions": [
    "Increase text size -- currently hard to read at small display sizes",
    "Add slight red glow behind text for better contrast against background",
    "Subject is slightly off-center -- shift 10% left for better visual balance"
  ],
  "niche_comparison": "Above average for submarine documentaries (avg CTR score: 6.1)"
}
```

#### consistency_report.json (from `thumbsmith consistency-check`)

```json
{
  "is_consistent": true,
  "consistency_score": 8.5,
  "checks": {
    "color_palette_match": 0.92,
    "layout_match": 0.85,
    "font_match": 1.0,
    "contrast_match": 0.88,
    "overall_visual_similarity": 0.87
  },
  "notes": "Thumbnail fits channel style well. Slightly warmer color temperature than channel average -- consider cooling by 5%."
}
```

---

## Strategy Rules (MUST implement -- from Steffen Miro methodology)

These are non-negotiable. They come from 100+ videos of proven methodology. Do not generalize or simplify them.

### Upload Process (exact sequence)

1. Upload video as UNLISTED (never as public)
2. Wait for HD processing to complete (can take 3-24 hours for long videos)
3. ONLY THEN switch to PUBLIC
4. Why: first viewers must see HD quality. SD quality = lower watch time = algorithm penalizes
5. Set country to US (highest RPM audience)
6. Set "Not made for kids" (so people can comment -- comments = engagement signal)
7. Category: closest match to niche
8. Video language: English
9. End screens: ONE only (don't confuse viewers with multiple choices)

### Ad Placement Rules

- Place ad breaks every 2 minutes (YouTube decides which to actually show)
- Videos must be 8+ minutes for mid-roll ads
- Videos over 17 minutes get RPM boost
- Enable alcohol and gambling ads for higher RPM

### The 5-Video Test (channel testing framework)

1. Create 5 cheap test videos for a new niche ($15-25 each, total $100-200)
2. Post in this pattern: post, skip day, post, skip day, post, post, skip day, post
3. Wait 3-10 days for YouTube to distribute
4. If ONE video takes off -> continue posting daily
5. If nothing -> diagnose using the framework below
6. Post at least 15 videos before making any decisions about abandoning

### Diagnosis Framework (when videos aren't performing)

- No impressions at all -> bad channel (likely shadowbanned) -> repost same content to NEW channel
- Impressions but low CTR (<3%) -> bad packaging (thumbnails/titles need work)
- Good CTR (>6%) but low AVD (<30%) -> bad content (script too boring or video too short)
- If new channel ALSO fails with same content -> bad niche -> restart niche research

### Shadowban Detection

- Shadow bans are "100% real" on YouTube
- Check traffic source distribution:
  - Healthy: 40%+ from browse/suggested
  - Shadowbanned: only search and external traffic
- If < 100 impressions after 15+ videos -> shadowbanned
- Fix: delete all videos, post same content to a brand new channel
- Can also try: move channel to different (older) email via YouTube permissions

### Monetization Tracking

- Requirements: 1,000 subscribers + 4,000 watch hours
- Average time to monetization with this system: 2-3 weeks
- Track daily progress toward both thresholds
- Alert when within 10% of either threshold

### Channel Health Metrics to Track

- CTR: target above 6%
- AVD (Average View Duration): longer = better, track as percentage of video length
- Impressions: should grow week over week
- Traffic sources: browse + suggested should be > 40% of total
- Revenue per video and per day (after monetization)
- Watch hours accumulation rate

### Q4 Revenue Optimization

- October-December has highest RPMs across ALL niches (holiday ad spending)
- Strategy: launch new channels in September to be monetized by October
- RPMs can 2-3x during Q4

### Team/Editor Management

- Steffen's structure: Manager ($3/video + 5% channel revenue) -> 3 Editors each ($15-25/video)
- Communication via WhatsApp groups (one per channel)
- Tracking via Google Sheets
- Editor hiring: Upwork (#1), Fiverr, X/Twitter, Discord
- Editor performance: track turnaround time, revision rate, cost per video

---

## How It Calls Tools

```python
# Option A: Call as CLI subprocess (most isolated)
result = subprocess.run(["ytscout", "analyze", "--channel", url, "--output-dir", tmp], capture_output=True)
report = json.load(open(f"{tmp}/competitor_blueprint.json"))

# Option B: Import as Python library (faster, same process)
from ytscout import analyze_channel
report = analyze_channel(url)

# The integrator supports both. Default: library import. Fallback: subprocess.
```

---

## Components to Build

### 1. Pipeline Engine (`vmachine/pipeline.py`)

Orchestrates the full video generation flow:

```
1. ytscout.analyze(channel_url)     -> blueprint.json
2. scriptforge.ideate(blueprint)    -> topics.json
3. scriptforge.write(topic, ref)    -> script.json
4. scriptforge.score(script)        -> score.json (gate: > 6/10)
5. scriptforge.metadata(topic)      -> metadata.json
6. mediafactory.produce(script)     -> voiceover.mp3, visuals/, video.mp4
7. thumbsmith.generate(topic, style)-> thumb_A.png, thumb_B.png, thumb_C.png
8. Package everything -> ready for upload
```

- Steps 3+5 run parallel (both need only topic)
- Steps 6+7 run parallel (independent)
- Pipeline state machine with status tracking
- Retry on failure with exponential backoff
- Cost accumulation across all steps
- Batch mode: queue 10-50 videos

### 2. Database (`vmachine/database.py`)

- SQLite via SQLAlchemy
- Tables: niches, channels, competitors, videos, pipelines, editors, analytics
- Stores all JSON outputs from tools (as JSON columns or files)
- The only persistent state in the system

### 3. FastAPI Backend (`vmachine/app.py`)

- REST API wrapping all tool functionality
- Pipeline management endpoints
- CRUD for niches, channels, videos
- WebSocket for real-time pipeline status
- File serving for generated assets

### 4. Web Dashboard (`vmachine/frontend/`)

- React + TypeScript + Tailwind + Vite
- Pages:
  - Dashboard: overview of channels, revenue, active pipelines
  - Niche Research: browse niches, trigger scans
  - Competitor Analysis: blueprints, outlier videos
  - Channel Manager: per-channel videos, health status
  - Video Generator: one-click pipeline trigger
  - Video Queue: batch management
  - Media Library: browse thumbnails, videos, voiceovers
  - Analytics: revenue charts, cost tracking, CTR trends
  - Team Manager: editor assignment, review queue
  - Settings: API keys, voice library, templates
- Real-time pipeline status via WebSocket
- Inline video preview

### 5. YouTube Uploader (`vmachine/uploader.py`)

- YouTube Data API v3 upload
- Upload as unlisted -> wait for HD processing -> switch to public
- Set metadata from scriptforge output
- Add end screens
- Schedule publishing
- Multi-channel support (different OAuth tokens per channel)

### 6. Channel Health Monitor (`vmachine/health.py`)

- YouTube Analytics API integration
- Daily fetch: impressions, CTR, AVD, revenue, traffic sources
- Shadowban detection:
  - < 100 impressions after 15+ videos = flagged
  - Traffic only from search/external = flagged
- Monetization progress tracker (1,000 subs + 4,000 watch hours)
- Alerts system

### 7. Revenue & Cost Analytics (`vmachine/analytics.py`)

- Aggregate costs from all tool outputs
- Aggregate revenue from YouTube Analytics
- Per-video, per-channel, per-niche profitability
- RPM tracking over time
- Projections based on growth rate
- CSV/PDF export

### 8. Team Manager (`vmachine/team.py`)

- Editor roster (name, rate, niche specialization)
- Video assignment with editing brief
- Review queue (submit -> review -> approve/reject)
- Editor performance tracking

---

## Project Structure

```
vmachine/
├── vmachine/
│   ├── __init__.py
│   ├── app.py                  # FastAPI app
│   ├── pipeline.py             # orchestration engine
│   ├── database.py             # SQLAlchemy models + setup
│   ├── uploader.py             # YouTube upload
│   ├── health.py               # channel health monitoring
│   ├── analytics.py            # revenue/cost tracking
│   ├── team.py                 # editor management
│   ├── tool_runner.py          # calls ytscout/scriptforge/mediafactory/thumbsmith
│   └── routes/
│       ├── pipeline_routes.py
│       ├── niche_routes.py
│       ├── channel_routes.py
│       ├── video_routes.py
│       ├── analytics_routes.py
│       └── team_routes.py
├── frontend/
│   ├── src/
│   │   ├── App.tsx
│   │   ├── pages/              # 10 pages listed above
│   │   ├── components/
│   │   └── api/
│   ├── package.json
│   ├── tsconfig.json
│   └── vite.config.ts
├── tests/
│   ├── test_pipeline.py
│   ├── test_uploader.py
│   ├── test_health.py
│   ├── test_analytics.py
│   └── test_team.py
├── pyproject.toml
├── README.md
└── .env.example
```

---

## Dependencies

```
fastapi
uvicorn
sqlalchemy
google-api-python-client    # YouTube upload + analytics
websockets                  # real-time pipeline status
# Plus: ytscout, scriptforge, mediafactory, thumbsmith as dependencies
```

---

## Tests

- Unit: pipeline step sequencing and state transitions
- Unit: tool runner subprocess invocation and JSON parsing
- Unit: database CRUD operations for all tables
- Unit: cost aggregation across tool outputs
- Unit: shadowban detection logic (impressions < 100 after 15+ videos)
- Unit: monetization progress calculation (subs + watch hours)
- Integration: full pipeline end-to-end with mocked tool outputs
- Integration: YouTube upload flow with mocked API
- Integration: health monitoring with mocked Analytics API
- Integration: team management workflow (assign -> review -> approve)
- Integration: dashboard API endpoints return correct data

---

## Success Criteria

### Per-Tool (the integrator validates these outputs)

**ytscout:**
- [ ] `ytscout scan --topics "submarines"` returns scored niches with YouTube data
- [ ] `ytscout analyze --channel <url>` produces complete competitor blueprint JSON
- [ ] `ytscout check-topic` returns demand score and recommendation
- [ ] `ytscout suggest-topics` returns 20 topic ideas from competitor outliers
- [ ] All outputs are valid JSON matching documented schemas
- [ ] Tests pass with recorded API fixtures (no live API needed)

**scriptforge:**
- [ ] `scriptforge write --topic <topic>` produces segmented script with visual search terms
- [ ] `scriptforge metadata` produces ranked titles, description, and tags
- [ ] `scriptforge ideate` produces categorized topic ideas
- [ ] `scriptforge score` produces quality score with improvement suggestions
- [ ] 3-step prompt chain produces noticeably better scripts than single-prompt
- [ ] All outputs are valid JSON matching documented schemas

**mediafactory:**
- [ ] `mediafactory voice` generates voiceover audio from script text
- [ ] `mediafactory visuals` downloads and organizes stock media per segment
- [ ] `mediafactory assemble` produces watchable MP4 from voiceover + visuals
- [ ] Silence removal measurably shortens audio (removes pauses)
- [ ] Auto-captions appear in assembled video
- [ ] `mediafactory brief` generates human-readable editing brief

**thumbsmith:**
- [ ] `thumbsmith generate` produces 3 distinct thumbnail variants at 1280x720
- [ ] `thumbsmith brand` produces logo (800x800), banner (2560x1440), descriptions
- [ ] `thumbsmith score` returns CTR prediction with improvement suggestions
- [ ] `thumbsmith consistency-check` detects style mismatches
- [ ] Post-processing pipeline applies text overlay, color grading, sharpening
- [ ] Templates can be saved and re-applied to new topics

### Integration (vmachine tests these)

- [ ] Full pipeline: topic -> video package in < 10 minutes
- [ ] Dashboard renders and can trigger pipelines
- [ ] YouTube upload works (unlisted -> HD processing -> public)
- [ ] Batch mode: queue and process 10 videos
- [ ] Channel health monitoring detects low-impression channels
