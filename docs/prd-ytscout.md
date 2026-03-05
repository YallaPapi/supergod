# ytscout -- YouTube Niche & Competitor Intelligence CLI
## Self-Contained PRD for Supergod Worker 1

---

## Context

- This tool is part of a larger system called "Viral Video Machine" but is built as a **STANDALONE tool**. It is an independent Python CLI that works entirely on its own.
- It knows **nothing** about the other tools in the system (scriptforge, mediafactory, thumbsmith). It does not import from them. It shares zero code with them.
- It communicates via **JSON files only**. It reads JSON input and produces JSON output files. An external integrator (not ytscout's concern) may pass its output to other tools.
- It implements **Steffen Miro's exact methodology** for finding profitable faceless YouTube niches, distilled from ~100 video transcripts of his YouTube Automation course.
- The **Strategy Rules** section below contains **non-negotiable requirements** from this methodology. Every rule, every number, every checklist item MUST be implemented exactly as specified. They are not suggestions. They are the core IP that makes this system produce videos that actually get views.

---

## Overview

### What It Is

A standalone YouTube research CLI. Give it seed topics, it finds profitable niches. Give it a channel URL, it produces a competitor blueprint. Think of it as "NextLev but automated and free."

Could be sold as a standalone SaaS: "$13/month YouTube niche research tool."

### What It Produces

- **Niche reports** with scored opportunities (8-point checklist)
- **Competitor blueprints** with outlier detection, title patterns, thumbnail analysis, voice profiles
- **Topic supply/demand checks** with go/skip recommendations
- **Topic suggestions** based on competitor outliers
- **Market monitoring alerts** for niche saturation and new entrants

---

## Standalone CLI Interface

```bash
# Discover niches from seed topics
ytscout scan --topics "submarines,vintage cars,war stories" --output-dir ./results/

# Analyze a specific competitor channel
ytscout analyze --channel "https://youtube.com/@SomeChannel" --output-dir ./results/

# Check if a topic is worth covering
ytscout check-topic --topic "History of the Titanic" --niche "maritime history" --output-dir ./results/

# Generate topic ideas based on competitor outliers
ytscout suggest-topics --channel "https://youtube.com/@SomeChannel" --count 20 --output-dir ./results/

# Monitor niches for saturation (run on cron)
ytscout monitor --config ./monitor_config.json --output-dir ./results/
```

---

## Input/Output Contracts

### scan

**scan input** (CLI args or JSON):
```json
{
  "seed_topics": ["submarines", "vintage cars", "war stories"],
  "max_results_per_topic": 10,
  "min_views_to_sub_ratio": 2.0,
  "max_channel_age_months": 6,
  "max_big_channels": 0
}
```

**scan output** (`niche_report.json`):
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

### analyze

**analyze output** (`competitor_blueprint.json`):
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

### check-topic

**check-topic output** (`topic_check.json`):
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

---

## Strategy Rules (MUST implement -- from Steffen Miro methodology)

These are non-negotiable. They come from 100+ videos of proven methodology. Do not generalize or simplify them.

**Niche Scoring -- The Exact 8-Point Checklist:**
1. Under 5 smaller channels (<10K subs) getting more views than subscribers -> score 1 if true
2. No channels with 100K+ subscribers in the niche -> score 1 if true (DISQUALIFYING if false)
3. No smaller channels failing (low views relative to video count) -> score 1 if true
4. Niche not older than 6 months (check oldest channel's first video date) -> score 1 if true. For AI-visual niches, max 2 months
5. You can create better/longer content (compare avg video length) -> score 1 if true
6. Interest not required (always true for automation) -> score 1
7. Niche is monetizable (RPM > $4 minimum) -> score 1 if true
8. Bonus: Can post long videos (1-2 hours = highest RPM) -> score 1 if true
- Total out of 8. Score >= 6 = "strong_opportunity", 4-5 = "worth_testing", < 4 = "skip"

**RPM Estimation Lookup Table (hardcode these):**
| Niche Category | Estimated RPM |
|----------------|--------------|
| Sleep/long-form wisdom | $20-30 |
| True crime (long form) | $15-20 |
| Finance/investing | $20-40 |
| Health/seniors | $7-20 |
| History (older audience) | $10-15 |
| Cars/boats/vintage | $5-8 |
| Politics | $4 |
| General faceless | $5 |
| Space (long form) | $6-8 |
| Horror/creepy | $20-30 |
| Military/war | $10-20 |
| Natural disasters | $5-7 |
| Celebrity drama | $8 |
| Factory processes | $5 |
| AI short films | varies |

**Niches to Auto-Flag as "avoid":**
- Motivation/inspirational (oversaturated)
- Kids content (low RPM)
- Top 10 format (saturated)
- Meditation (may not monetize)
- Compilations without voiceover (can't monetize)
- Any niche that's too broad: "finance", "health", "luxury" -- must be sub-niche

**Outlier Detection:**
- A video is an outlier if views > 2x the channel's average views per video
- Focus on RECENT outliers (last 30 days), not all-time popular

**Topic Supply/Demand:**
- Search YouTube for the topic
- If 5+ channels have covered it in the last 30 days -> supply is "high" -> recommend skip
- High views + few videos = high demand = go
- "80% of topics should be proven (based on competitor success), 20% experimental"

**Channel Age Estimation:**
- Niche age = date of oldest channel's first video in the niche
- If > 6 months, the niche is likely too mature

**Voice Profile Detection:**
- From competitor audio: detect gender (male/female), estimate age range (young/middle/older)
- Accent: american, british, australian, other
- Speaking rate: measure words per minute from transcript + video duration

---

## Components to Build

1. **YouTube API Client** (`ytscout/youtube_client.py`)
   - Wrapper around YouTube Data API v3
   - Channel search, video listing, statistics fetching
   - Transcript extraction (youtube-transcript-api)
   - API quota tracking and rate limiting
   - Caching layer (don't re-fetch same channel within 24h)

2. **Niche Scorer** (`ytscout/niche_scorer.py`)
   - Implements Steffen's 8-point checklist algorithmically
   - Input: list of channels in a potential niche
   - Output: NicheScore with reasoning
   - RPM estimation from niche category (lookup table from Steffen's data)

3. **Competitor Analyzer** (`ytscout/competitor_analyzer.py`)
   - Deep channel analysis: all videos, stats, patterns
   - Outlier detection (views > 2x channel average)
   - Title pattern extraction (common words, structures, power words)
   - Posting frequency detection
   - Blueprint generation

4. **Thumbnail Analyzer** (`ytscout/thumbnail_analyzer.py`)
   - Download competitor thumbnails
   - Color analysis (dominant colors via PIL histogram)
   - Brightness/contrast/saturation measurement
   - Text extraction via OCR (pytesseract)
   - Layout pattern detection (where is subject, where is text)

5. **Topic Scanner** (`ytscout/topic_scanner.py`)
   - Supply/demand analysis for specific topics
   - YouTube search to count existing coverage
   - Freshness check (when was topic last covered)
   - Alternative topic suggestion

6. **Market Monitor** (`ytscout/monitor.py`)
   - Re-score niches on schedule
   - Detect new entrants (big channels entering niche)
   - Detect viral opportunities (competitor outlier alerts)
   - Output alerts JSON

---

## Dependencies (this tool only)

```
google-api-python-client    # YouTube Data API
youtube-transcript-api      # transcript extraction
Pillow                      # thumbnail image analysis
pytesseract                 # OCR for thumbnail text
click                       # CLI framework
requests                    # HTTP client
```

---

## Project Structure

```
ytscout/
├── ytscout/
│   ├── __init__.py
│   ├── cli.py                  # click CLI entry point
│   ├── youtube_client.py       # YouTube API wrapper
│   ├── niche_scorer.py         # 8-point scoring
│   ├── competitor_analyzer.py  # channel deep analysis
│   ├── thumbnail_analyzer.py   # image analysis
│   ├── topic_scanner.py        # topic supply/demand
│   ├── monitor.py              # ongoing tracking
│   └── models.py               # this tool's own dataclasses
├── tests/
│   ├── test_niche_scorer.py
│   ├── test_competitor_analyzer.py
│   ├── test_thumbnail_analyzer.py
│   ├── test_topic_scanner.py
│   └── fixtures/               # recorded API responses
├── pyproject.toml
├── README.md
└── .env.example
```

---

## Tests

- Unit: niche scoring with mock channel data (test all 8 criteria)
- Unit: outlier detection algorithm
- Unit: title pattern extraction from sample titles
- Unit: thumbnail color/brightness analysis with sample images
- Unit: topic supply/demand calculation
- Integration: full scan flow with recorded API fixtures
- Integration: full competitor analysis with recorded fixtures

---

## Success Criteria

- [ ] `ytscout scan --topics "submarines"` returns scored niches with YouTube data
- [ ] `ytscout analyze --channel <url>` produces complete competitor blueprint JSON
- [ ] `ytscout check-topic` returns demand score and recommendation
- [ ] `ytscout suggest-topics` returns 20 topic ideas from competitor outliers
- [ ] All outputs are valid JSON matching documented schemas
- [ ] Tests pass with recorded API fixtures (no live API needed)
