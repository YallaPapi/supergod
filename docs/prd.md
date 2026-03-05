# Viral Video Machine -- Product Requirements Document

## Overview

Automated faceless YouTube cash cow video generation system. One click: topic in, upload-ready video package out.

This is built as **4 standalone tools + 1 glue layer**. Each tool is an independent Python CLI that works on its own, reads JSON input, writes JSON + files as output. The integrator wires them into a pipeline with a web dashboard.

### Methodology Source

This system implements the exact methodology from Steffen Miro's YouTube Automation course, distilled from ~100 video transcripts (see `docs/steffen_miro_strategy.md` for full reference). Every tool in this PRD has a **"Strategy Rules"** section containing hardcoded rules, exact prompts, specific numbers, and non-negotiable requirements from this methodology. These Strategy Rules are the core IP -- they're what makes this system produce videos that actually get views, not just generic content. **Workers MUST implement every Strategy Rule as specified. They are not suggestions.**

---

## The 4 Standalone Tools + Integrator

```
Tool 1: ytscout        "YouTube niche & competitor intelligence"
Tool 2: scriptforge    "AI script & metadata generator"
Tool 3: mediafactory   "Voice, visuals, and video assembly"
Tool 4: thumbsmith     "AI thumbnail & brand identity generator"
Integrator: vmachine   "Pipeline orchestrator + dashboard"

                    vmachine (pipeline + dashboard)
                    ┌──────────────────────────────┐
                    │  ytscout → scriptforge ───┐   │
                    │                           ├──→│ upload-ready
                    │             mediafactory ─┘   │ video package
                    │             thumbsmith ───────→│
                    └──────────────────────────────┘
```

Each tool is a **standalone project** that could be published on PyPI independently. They know nothing about each other. They share ZERO code. The integrator is the only thing that knows all four exist.

---

## How Tools Communicate

**JSON files.** That's it. No shared models, no shared database, no imports between tools.

Each tool reads a JSON config/input file and produces a JSON output file + any generated assets (audio, images, video). The integrator passes output from one tool as input to the next.

```
ytscout --input seed_topics.json --output-dir ./output/intel/
  → writes: niche_report.json, competitor_blueprint.json, topic_suggestions.json

scriptforge --input topic_request.json --output-dir ./output/content/
  → writes: script.json, metadata.json, quality_score.json

mediafactory --input production_request.json --output-dir ./output/media/
  → writes: voiceover.mp3, visuals/, assembled_video.mp4, manifest.json

thumbsmith --input thumbnail_request.json --output-dir ./output/thumbs/
  → writes: thumb_A.png, thumb_B.png, thumb_C.png, brand_kit/, manifest.json
```

The integrator reads each tool's output JSON, transforms it into the next tool's input JSON, and chains them.

---

## Tool 1: ytscout (Worker 1)

### What It Is

A standalone YouTube research CLI. Give it seed topics, it finds profitable niches. Give it a channel URL, it produces a competitor blueprint. Think of it as "NextLev but automated and free."

Could be sold as a standalone SaaS: "$13/month YouTube niche research tool."

### Standalone CLI Interface

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

### Input/Output Contracts

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

### Strategy Rules (MUST implement -- from Steffen Miro methodology)

These are non-negotiable. They come from 100+ videos of proven methodology. Do not generalize or simplify them.

**Niche Scoring -- The Exact 8-Point Checklist:**
1. Under 5 smaller channels (<10K subs) getting more views than subscribers → score 1 if true
2. No channels with 100K+ subscribers in the niche → score 1 if true (DISQUALIFYING if false)
3. No smaller channels failing (low views relative to video count) → score 1 if true
4. Niche not older than 6 months (check oldest channel's first video date) → score 1 if true. For AI-visual niches, max 2 months
5. You can create better/longer content (compare avg video length) → score 1 if true
6. Interest not required (always true for automation) → score 1
7. Niche is monetizable (RPM > $4 minimum) → score 1 if true
8. Bonus: Can post long videos (1-2 hours = highest RPM) → score 1 if true
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
- If 5+ channels have covered it in the last 30 days → supply is "high" → recommend skip
- High views + few videos = high demand = go
- "80% of topics should be proven (based on competitor success), 20% experimental"

**Channel Age Estimation:**
- Niche age = date of oldest channel's first video in the niche
- If > 6 months, the niche is likely too mature

**Voice Profile Detection:**
- From competitor audio: detect gender (male/female), estimate age range (young/middle/older)
- Accent: american, british, australian, other
- Speaking rate: measure words per minute from transcript + video duration

### Components to Build

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

### Dependencies (this tool only)
```
google-api-python-client    # YouTube Data API
youtube-transcript-api      # transcript extraction
Pillow                      # thumbnail image analysis
pytesseract                 # OCR for thumbnail text
click                       # CLI framework
requests                    # HTTP client
```

### Project Structure
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

### Tests
- Unit: niche scoring with mock channel data (test all 8 criteria)
- Unit: outlier detection algorithm
- Unit: title pattern extraction from sample titles
- Unit: thumbnail color/brightness analysis with sample images
- Unit: topic supply/demand calculation
- Integration: full scan flow with recorded API fixtures
- Integration: full competitor analysis with recorded fixtures

---

## Tool 2: scriptforge (Worker 2)

### What It Is

A standalone AI scriptwriting CLI. Give it a topic and a reference transcript, it produces a retention-optimized script, metadata (titles, description, tags), and a quality score. Think of it as "professional YouTube scriptwriter in a box."

Could be sold standalone: "AI script generator optimized for YouTube retention -- $29/month."

### Standalone CLI Interface

```bash
# Generate a script from topic + reference
scriptforge write --topic "The Submarine That Changed Everything" \
  --reference ./competitor_transcript.txt \
  --target-minutes 15 \
  --output-dir ./results/

# Generate just metadata (title, description, tags)
scriptforge metadata --topic "The Submarine That Changed Everything" \
  --niche "submarine documentaries" \
  --output-dir ./results/

# Generate topic ideas from competitor data
scriptforge ideate --competitor-data ./competitor_blueprint.json \
  --count 20 \
  --output-dir ./results/

# Score an existing script
scriptforge score --script ./my_script.txt \
  --reference ./competitor_transcript.txt \
  --output-dir ./results/

# Batch: generate scripts for multiple topics
scriptforge batch --topics ./topic_list.json \
  --reference ./competitor_transcript.txt \
  --target-minutes 15 \
  --output-dir ./results/
```

### Input/Output Contracts

**write input** (`script_request.json`):
```json
{
  "topic": "The Submarine That Changed Everything",
  "reference_transcript": "In the cold depths of the Atlantic...(full transcript text)...",
  "target_duration_minutes": 15,
  "target_word_count": 2250,
  "niche": "submarine documentaries",
  "style_notes": "authoritative tone, dramatic pacing, twist every 30 seconds",
  "visual_search_terms_per_segment": true
}
```

**write output** (`script.json`):
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

**metadata output** (`metadata.json`):
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

**score output** (`quality_score.json`):
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

**ideate output** (`topic_ideas.json`):
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

### Strategy Rules (MUST implement -- from Steffen Miro methodology)

These are non-negotiable. They come from 100+ videos of proven methodology.

**The Exact 3-Step Prompt Chain (use these exact prompts, not paraphrases):**

Step 1 -- Feed competitor transcript to LLM:
```
Study and analyze this transcript. Understand the structure, pacing, hooks,
and retention techniques used. Pay attention to how the narrator creates
curiosity, where twists are placed, and how the ending is handled.

[PASTE FULL COMPETITOR TRANSCRIPT]
```

Step 2 -- Generate meta-prompt:
```
Here's a faceless YouTube automation niche I want to copy. Pretend you're a
professional script writing genius or guru. These are the best performing
videos so we can replicate their success and virality. Using the script I'm
going to provide as a reference. Write me a prompt to give ChatGPT to write
me a script but for this topic: [TOPIC]. Make sure it's insanely optimized
for AVD and viewer retention and flows the exact same as the reference I'm
about to provide. Make me a prompt and also include the script in the prompt.
```

Step 3 -- Execute the meta-prompt generated in step 2 against the LLM, with these additions:
- "Without headings, without music instructions"
- "Make it about [WORD_COUNT] words, ask me to continue after every 1,000 words"
- Continue generating until target word count is reached

**Script Structure Rules:**
- Hook: first 30 seconds MUST create a curiosity gap. Never reveal the answer upfront.
- Body: insert a twist, revelation, or "but what they didn't know was..." every 20-30 seconds
- Ending: QUICK. No "thanks for watching", no "don't forget to subscribe", no long outro. Just end the story.
- No section headings in the script (they bleed into voiceover)
- No music cues or sound effect instructions

**Word Count / Duration Rules:**
- 150 words per minute of video (1,500 words = 10 minutes)
- ALWAYS target 1.5x the competitor's average video length
- If competitor averages 8 minutes → target 12 minutes → 1,800 words
- If competitor averages 15 minutes → target 22 minutes → 3,300 words

**Visual Search Terms:**
- For every 5-7 seconds of script, generate 3 search terms for stock footage/images
- Terms should be concrete and searchable ("nuclear submarine underwater" not "tension builds")

**Ideation Prompt (exact wording):**
```
Pretend you are a YouTube guru and faceless channel expert. Write me a prompt
to copy this video style of ideas. I want perfect ideation model. Write me
video ideas based off these outliers that performed well on their channel.
Target high clickbait and CTR. Write me the expert prompt to give ChatGPT.

[PASTE LIST OF COMPETITOR'S TOP-PERFORMING VIDEO TITLES + VIEW COUNTS]
```

**Topic Split:** When generating topics, label 80% as "proven" (directly based on competitor outliers) and 20% as "experimental" (adjacent topics not yet covered).

**Title Rules:**
- Main keyword FIRST in the title
- Under 60 characters (so it doesn't get cut off on mobile)
- Power words database (hardcode these): "exposed, destroyed, game over, robbing you blind, total ripoff, waste of money, avoid, stop buying, never, worst, secret, hidden, banned, terrifying, vanished, deadly"
- "People respond way more to negativity than to positive things" -- negative framing scores higher
- Use ChatGPT to make titles more clickbaity after initial generation

**Description Rules:**
- Line 1: exact video title
- Lines 2-6: keyword-rich paragraph summarizing the video
- Then: channel description boilerplate (same on every video for the channel)
- Include: contact email for sponsorship inquiries
- Include: social media links
- Include: relevant hashtags (3-5)

**Tag Rules:**
- Generate from title keywords + topic keywords
- Include long-tail variations
- 15-30 tags per video

### Components to Build

1. **LLM Client** (`scriptforge/llm_client.py`)
   - Unified wrapper for OpenAI and Anthropic APIs
   - Prompt chain execution (multi-step conversations)
   - Token counting and cost tracking
   - Retry with exponential backoff
   - Response caching (don't regenerate same prompt)

2. **Script Engine** (`scriptforge/script_engine.py`)
   - Steffen's 3-step prompt chain:
     1. "study and analyze this transcript"
     2. "write me a prompt to generate a script for [TOPIC] in the same style"
     3. Execute the generated meta-prompt
   - Script segmentation (break into timed segments)
   - Visual search term generation per segment
   - Word count targeting (150 words/minute)
   - Hook/body/twist/ending structure enforcement

3. **Metadata Engine** (`scriptforge/metadata_engine.py`)
   - Title generation (10 variants, scored by clickbait potential)
   - Power words database and scoring
   - Description template with keyword stuffing
   - Tag extraction from title + script content
   - Title length enforcement (< 60 chars)

4. **Topic Ideation** (`scriptforge/ideation.py`)
   - Takes competitor outlier data (from ytscout output JSON)
   - Generates topic ideas via LLM
   - Categorizes as "proven" vs "experimental"
   - Deduplication against existing topics

5. **Script Scorer** (`scriptforge/scorer.py`)
   - Analyzes script against retention heuristics
   - Scores hook, pacing, ending, density, readability
   - Compares structural similarity to reference transcript
   - Generates improvement suggestions via LLM

6. **Prompt Library** (`scriptforge/prompts.py`)
   - Steffen's exact prompt templates (hardcoded)
   - Niche-specific prompt variations
   - Prompt versioning (track which prompts produce best scripts)

### Dependencies (this tool only)
```
openai                      # OpenAI API
anthropic                   # Anthropic API (fallback)
tiktoken                    # token counting
click                       # CLI framework
```

### Project Structure
```
scriptforge/
├── scriptforge/
│   ├── __init__.py
│   ├── cli.py                  # click CLI entry point
│   ├── llm_client.py           # OpenAI/Anthropic wrapper
│   ├── script_engine.py        # 3-step prompt chain
│   ├── metadata_engine.py      # titles, descriptions, tags
│   ├── ideation.py             # topic idea generation
│   ├── scorer.py               # script quality scoring
│   ├── prompts.py              # prompt templates
│   └── models.py               # this tool's own dataclasses
├── tests/
│   ├── test_script_engine.py
│   ├── test_metadata_engine.py
│   ├── test_ideation.py
│   ├── test_scorer.py
│   └── fixtures/               # sample transcripts, mock LLM responses
├── pyproject.toml
├── README.md
└── .env.example
```

### Tests
- Unit: prompt chain construction (verify correct prompt text)
- Unit: script segmentation (word count → segment timing)
- Unit: title clickbait scoring (power words, length, structure)
- Unit: tag extraction from sample text
- Unit: script quality scoring dimensions
- Integration: full script generation with mocked LLM (recorded responses)
- Integration: metadata generation end-to-end with mocks

---

## Tool 3: mediafactory (Worker 3)

### What It Is

A standalone media production CLI. Give it a script, it produces voiceover audio, sources matching visuals, and assembles a complete video. Think of it as "one-person video production studio."

Could be sold standalone: "AI video production pipeline -- script to video in 5 minutes."

### Standalone CLI Interface

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

### Input/Output Contracts

**produce input** (`production_request.json`):
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

**produce output** (`manifest.json`):
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

**brief output** (`editing_brief.json`):
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

### Strategy Rules (MUST implement -- from Steffen Miro methodology)

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

### Components to Build

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

### Dependencies (this tool only)
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

### Project Structure
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

### Tests
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

## Tool 4: thumbsmith (Worker 4)

### What It Is

A standalone AI thumbnail and branding CLI. Give it a topic and reference thumbnails, it produces 3 click-optimized thumbnail variants plus full channel branding. Think of it as "professional thumbnail designer + brand agency in a box."

Could be sold standalone: "AI YouTube thumbnail generator -- 3 A/B variants in 30 seconds."

### Standalone CLI Interface

```bash
# Generate 3 thumbnail variants
thumbsmith generate --topic "The Submarine That Changed Everything" \
  --text "VANISHED" \
  --style-ref ./competitor_thumbnails/ \
  --output-dir ./results/

# Generate full channel brand kit (logo, banner, descriptions)
thumbsmith brand --channel-name "Deep Ocean Mysteries" \
  --niche "submarine documentaries" \
  --color-palette "#1a1a2e,#e94560,#ffffff" \
  --output-dir ./results/

# Analyze competitor thumbnails for style extraction
thumbsmith analyze --thumbnails ./competitor_thumbnails/ --output-dir ./results/

# Score a thumbnail for predicted CTR
thumbsmith score --thumbnail ./my_thumbnail.png \
  --niche "submarine documentaries" \
  --output-dir ./results/

# Check thumbnail consistency against channel style
thumbsmith consistency-check --thumbnail ./new_thumb.png \
  --channel-thumbs ./existing_channel_thumbs/ \
  --output-dir ./results/

# Apply a saved template to a new topic
thumbsmith apply-template --template ./templates/dark_dramatic.json \
  --topic "The Submarine That Changed Everything" \
  --text "VANISHED" \
  --output-dir ./results/
```

### Input/Output Contracts

**generate input** (`thumbnail_request.json`):
```json
{
  "topic": "The Submarine That Changed Everything",
  "text_overlay": "VANISHED",
  "style_reference": {
    "thumbnail_paths": ["./refs/thumb1.jpg", "./refs/thumb2.jpg", "./refs/thumb3.jpg"],
    "analysis": {
      "dominant_colors": ["#1a1a2e", "#e94560", "#ffffff"],
      "layout_pattern": "dark_background_centered_subject",
      "has_text": true,
      "text_style": "bold_white_with_red_accent",
      "has_arrows": false,
      "has_border": false,
      "contrast_level": "high"
    }
  },
  "ai_image_prompt_hint": "dark underwater scene with submarine silhouette, dramatic lighting",
  "output_size": [1280, 720],
  "variants": 3
}
```

**generate output** (`manifest.json`):
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

**brand output** (`brand_kit.json`):
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

**score output** (`ctr_score.json`):
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

**consistency-check output** (`consistency_report.json`):
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

### Strategy Rules (MUST implement -- from Steffen Miro methodology)

These are non-negotiable. They come from 100+ videos of proven methodology.

**The 80/20 Thumbnail Rule:**
- 80% of the thumbnail should match competitor style exactly (colors, layout, contrast, text style)
- 20% should be your original twist (different subject, different angle, different text)
- NEVER copy a competitor thumbnail 1:1 (their audience already saw it -- it won't feel new)
- YouTube trained the audience to click that style. Changing the style = lower CTR.

**The 0.2 Second Rule:**
- A thumbnail must be understood in 0.2 seconds
- One clear subject, one emotion, one message
- If you have to think about what the thumbnail shows, it's too complex

**Thumbnail Consistency = Channel Identity:**
- "Make it look like one channel" -- all thumbnails on a channel should use the same visual pattern
- Same color palette, same font, same layout structure, same contrast level
- Viewers should be able to identify your channel from the thumbnail alone
- Breaking consistency = confusing the audience = lower CTR

**The Blend Test:**
- Take your thumbnail and paste it into the competitor's channel page (Photoshop/editing)
- Show someone the channel page
- If they CANNOT spot which thumbnail doesn't belong, your thumbnail is good
- If it sticks out, adjust to match better

**Exact Thumbnail Prompt (use this, not a paraphrase):**
```
Pretend you are a professional thumbnail designer. Write me a prompt to copy
exactly the style of thumbnails. Target clickbait and CTR for the topic
[TOPIC]. Thumbnail text: [TEXT].
[PASTE SCREENSHOT/DESCRIPTION OF COMPETITOR THUMBNAILS]
```

**Post-Processing Rules (in this order):**
1. Background removal on subject (rembg) -- if the style calls for isolated subjects
2. Bold text overlay -- large, readable at thumbnail size (which is TINY on mobile)
3. Red arrow PNG overlay -- if competitor style uses arrows
4. "Breaking News" banner -- if competitor style uses news-style banners
5. Color grading: brightness +20%, saturation +15%, sharpness/clarity up
6. Match competitor's exact color palette (extracted by style analyzer)
7. Border/glow effects only if competitor style uses them

**Text on Thumbnails:**
- 1-3 words MAXIMUM. Short, punchy, emotional.
- Must be readable at 168x94 pixels (how thumbnails appear in YouTube sidebar)
- High contrast: white or red text on dark backgrounds, or dark text on bright backgrounds
- Power words: same list as titles (exposed, destroyed, vanished, secret, etc.)

**Output Requirements:**
- Exactly 1280x720 pixels (YouTube standard)
- PNG format (lossless for text clarity)
- Generate exactly 3 variants (A, B, C) -- different enough to A/B test but all consistent with channel style
- Variant strategies: vary text position, text color, subject framing, or background emphasis

**Branding Rules (from Steffen's channel setup):**
- Channel name should include a human name: "History with Stefan" not "Mystery Time" (prevents inauthentic content flags)
- Logo: simple, clean, AI-generated is fine
- Banner: channel name + 3-6 word tagline + upload schedule
- Banner dimensions: 2560x1440
- Profile picture: 800x800
- Have matching Instagram/TikTok profiles (linked from channel) to signal legitimacy
- Channel description must include: what the channel provides, upload schedule, contact email, disclaimer for finance/politics/health

### Components to Build

1. **Thumbnail Generator** (`thumbsmith/generator.py`)
   - AI image generation via OpenAI DALL-E 3
   - Prompt construction using Steffen's template
   - Generate 3 variants with different compositions
   - Variant strategies: different text positions, different color emphasis, different subject framing

2. **Image Processor** (`thumbsmith/processor.py`)
   - Pillow-based post-processing pipeline:
     - Background removal (rembg) on subject
     - Bold text overlay with multiple font options (Impact, Oswald, Bebas Neue)
     - Red arrow PNG overlay
     - "Breaking News" banner overlay
     - Color grading: brightness, saturation, contrast, sharpness
     - Border and glow effects
   - Color palette matching (given target palette, adjust image to match)
   - Output at exactly 1280x720

3. **Style Analyzer** (`thumbsmith/style_analyzer.py`)
   - Download and analyze competitor thumbnails
   - Extract dominant colors (PIL color histogram + k-means clustering)
   - Measure brightness, contrast, saturation
   - Detect text via OCR (pytesseract), extract text style (color, size, position)
   - Detect layout pattern (where is subject, where is text, where is negative space)
   - Output: style profile JSON

4. **Brand Engine** (`thumbsmith/branding.py`)
   - Channel logo generation (AI image gen + post-processing)
   - Channel banner generation (2560x1440, text + background)
   - Channel description templates with keyword optimization
   - Social media bio generation
   - Thumbnail template creation (reusable per-channel settings)

5. **CTR Scorer** (`thumbsmith/scorer.py`)
   - Analyze thumbnail image properties:
     - Contrast ratio (foreground vs background)
     - Text readability (size relative to image, contrast against background)
     - Subject clarity (edge detection, not too busy)
     - Color vibrancy (saturation histogram)
     - Emotional intensity (face detection if applicable, color warmth)
   - Compare against niche averages
   - Generate improvement suggestions

6. **Consistency Checker** (`thumbsmith/consistency.py`)
   - Compare new thumbnail against set of existing channel thumbnails
   - Color palette similarity (cosine distance between color histograms)
   - Layout similarity (subject/text position comparison)
   - Font matching (OCR + font detection)
   - Overall visual consistency score
   - Flag thumbnails that "break the pattern"

7. **Template Manager** (`thumbsmith/templates.py`)
   - Save thumbnail settings as reusable templates
   - Apply template to new topics (change text/image, keep style)
   - Template versioning

### Dependencies (this tool only)
```
openai                      # DALL-E 3 image generation
Pillow                      # image processing, text overlay, color grading
rembg                       # background removal
pytesseract                 # OCR for text extraction
scikit-learn                # k-means for color clustering
click                       # CLI framework
requests                    # HTTP client
# Bundled fonts: Impact, Oswald, Bebas Neue (in package data)
```

### Project Structure
```
thumbsmith/
├── thumbsmith/
│   ├── __init__.py
│   ├── cli.py                  # click CLI entry point
│   ├── generator.py            # AI image generation + variant creation
│   ├── processor.py            # Pillow post-processing pipeline
│   ├── style_analyzer.py       # competitor thumbnail analysis
│   ├── branding.py             # logo, banner, descriptions
│   ├── scorer.py               # CTR prediction scoring
│   ├── consistency.py          # channel style consistency check
│   ├── templates.py            # reusable thumbnail templates
│   ├── models.py               # this tool's own dataclasses
│   └── fonts/                  # bundled font files
│       ├── Impact.ttf
│       ├── Oswald-Bold.ttf
│       └── BebasNeue-Regular.ttf
├── tests/
│   ├── test_generator.py
│   ├── test_processor.py
│   ├── test_style_analyzer.py
│   ├── test_branding.py
│   ├── test_scorer.py
│   ├── test_consistency.py
│   └── fixtures/               # sample thumbnails, test images
│       ├── sample_thumb_1280x720.png
│       ├── competitor_thumb_1.jpg
│       └── competitor_thumb_2.jpg
├── pyproject.toml
├── README.md
└── .env.example
```

### Tests
- Unit: text overlay rendering (verify text appears at correct position/color/size)
- Unit: background removal (verify alpha channel created)
- Unit: color grading (verify brightness/saturation/contrast adjustments)
- Unit: color palette extraction from sample images
- Unit: CTR scoring dimensions (contrast, readability, vibrancy)
- Unit: consistency scoring between two sample thumbnails
- Unit: template save/load round-trip
- Integration: full thumbnail generation with mocked DALL-E
- Integration: brand kit generation end-to-end
- Integration: style analysis from sample competitor thumbnails

---

## Integrator: vmachine

### What It Is

The glue layer. Calls all 4 tools in the right order, stores results in a database, provides a web dashboard, handles YouTube upload, and tracks analytics. This is where 4 standalone tools become one product.

The integrator does NOT duplicate any logic from the tools. It calls their CLIs (or imports their Python modules) and passes JSON between them.

### Strategy Rules (MUST implement -- from Steffen Miro methodology)

These are non-negotiable. They come from 100+ videos of proven methodology.

**Upload Process (exact sequence):**
1. Upload video as UNLISTED (never as public)
2. Wait for HD processing to complete (can take 3-24 hours for long videos)
3. ONLY THEN switch to PUBLIC
4. Why: first viewers must see HD quality. SD quality = lower watch time = algorithm penalizes
5. Set country to US (highest RPM audience)
6. Set "Not made for kids" (so people can comment -- comments = engagement signal)
7. Category: closest match to niche
8. Video language: English
9. End screens: ONE only (don't confuse viewers with multiple choices)

**Ad Placement Rules:**
- Place ad breaks every 2 minutes (YouTube decides which to actually show)
- Videos must be 8+ minutes for mid-roll ads
- Videos over 17 minutes get RPM boost
- Enable alcohol and gambling ads for higher RPM

**The 5-Video Test (channel testing framework):**
1. Create 5 cheap test videos for a new niche ($15-25 each, total $100-200)
2. Post in this pattern: post, skip day, post, skip day, post, post, skip day, post
3. Wait 3-10 days for YouTube to distribute
4. If ONE video takes off → continue posting daily
5. If nothing → diagnose using the framework below
6. Post at least 15 videos before making any decisions about abandoning

**Diagnosis Framework (when videos aren't performing):**
- No impressions at all → bad channel (likely shadowbanned) → repost same content to NEW channel
- Impressions but low CTR (<3%) → bad packaging (thumbnails/titles need work)
- Good CTR (>6%) but low AVD (<30%) → bad content (script too boring or video too short)
- If new channel ALSO fails with same content → bad niche → restart niche research

**Shadowban Detection:**
- Shadow bans are "100% real" on YouTube
- Check traffic source distribution:
  - Healthy: 40%+ from browse/suggested
  - Shadowbanned: only search and external traffic
- If < 100 impressions after 15+ videos → shadowbanned
- Fix: delete all videos, post same content to a brand new channel
- Can also try: move channel to different (older) email via YouTube permissions

**Monetization Tracking:**
- Requirements: 1,000 subscribers + 4,000 watch hours
- Average time to monetization with this system: 2-3 weeks
- Track daily progress toward both thresholds
- Alert when within 10% of either threshold

**Channel Health Metrics to Track:**
- CTR: target above 6%
- AVD (Average View Duration): longer = better, track as percentage of video length
- Impressions: should grow week over week
- Traffic sources: browse + suggested should be > 40% of total
- Revenue per video and per day (after monetization)
- Watch hours accumulation rate

**Q4 Revenue Optimization:**
- October-December has highest RPMs across ALL niches (holiday ad spending)
- Strategy: launch new channels in September to be monetized by October
- RPMs can 2-3x during Q4

**Team/Editor Management:**
- Steffen's structure: Manager ($3/video + 5% channel revenue) → 3 Editors each ($15-25/video)
- Communication via WhatsApp groups (one per channel)
- Tracking via Google Sheets
- Editor hiring: Upwork (#1), Fiverr, X/Twitter, Discord
- Editor performance: track turnaround time, revision rate, cost per video

### How It Calls Tools

```python
# Option A: Call as CLI subprocess (most isolated)
result = subprocess.run(["ytscout", "analyze", "--channel", url, "--output-dir", tmp], capture_output=True)
report = json.load(open(f"{tmp}/competitor_blueprint.json"))

# Option B: Import as Python library (faster, same process)
from ytscout import analyze_channel
report = analyze_channel(url)

# The integrator supports both. Default: library import. Fallback: subprocess.
```

### Components to Build

1. **Pipeline Engine** (`vmachine/pipeline.py`)
   - Orchestrates the full video generation flow:
     ```
     1. ytscout.analyze(channel_url)     → blueprint.json
     2. scriptforge.ideate(blueprint)    → topics.json
     3. scriptforge.write(topic, ref)    → script.json
     4. scriptforge.score(script)        → score.json (gate: > 6/10)
     5. scriptforge.metadata(topic)      → metadata.json
     6. mediafactory.produce(script)     → voiceover.mp3, visuals/, video.mp4
     7. thumbsmith.generate(topic, style)→ thumb_A.png, thumb_B.png, thumb_C.png
     8. Package everything → ready for upload
     ```
   - Steps 3+5 run parallel (both need only topic)
   - Steps 6+7 run parallel (independent)
   - Pipeline state machine with status tracking
   - Retry on failure with exponential backoff
   - Cost accumulation across all steps
   - Batch mode: queue 10-50 videos

2. **Database** (`vmachine/database.py`)
   - SQLite via SQLAlchemy
   - Tables: niches, channels, competitors, videos, pipelines, editors, analytics
   - Stores all JSON outputs from tools (as JSON columns or files)
   - The only persistent state in the system

3. **FastAPI Backend** (`vmachine/app.py`)
   - REST API wrapping all tool functionality
   - Pipeline management endpoints
   - CRUD for niches, channels, videos
   - WebSocket for real-time pipeline status
   - File serving for generated assets

4. **Web Dashboard** (`vmachine/frontend/`)
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

5. **YouTube Uploader** (`vmachine/uploader.py`)
   - YouTube Data API v3 upload
   - Upload as unlisted → wait for HD processing → switch to public
   - Set metadata from scriptforge output
   - Add end screens
   - Schedule publishing
   - Multi-channel support (different OAuth tokens per channel)

6. **Channel Health Monitor** (`vmachine/health.py`)
   - YouTube Analytics API integration
   - Daily fetch: impressions, CTR, AVD, revenue, traffic sources
   - Shadowban detection:
     - < 100 impressions after 15+ videos = flagged
     - Traffic only from search/external = flagged
   - Monetization progress tracker (1,000 subs + 4,000 watch hours)
   - Alerts system

7. **Revenue & Cost Analytics** (`vmachine/analytics.py`)
   - Aggregate costs from all tool outputs
   - Aggregate revenue from YouTube Analytics
   - Per-video, per-channel, per-niche profitability
   - RPM tracking over time
   - Projections based on growth rate
   - CSV/PDF export

8. **Team Manager** (`vmachine/team.py`)
   - Editor roster (name, rate, niche specialization)
   - Video assignment with editing brief
   - Review queue (submit → review → approve/reject)
   - Editor performance tracking

### Project Structure
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

### Dependencies
```
fastapi
uvicorn
sqlalchemy
google-api-python-client    # YouTube upload + analytics
websockets                  # real-time pipeline status
# Plus: ytscout, scriptforge, mediafactory, thumbsmith as dependencies
```

---

## Supergod Worker Assignment

| Worker | Builds | Standalone Product | Files |
|--------|--------|--------------------|-------|
| Worker 1 | `ytscout` | YouTube niche research CLI | `ytscout/` (own pyproject.toml, own tests) |
| Worker 2 | `scriptforge` | AI scriptwriting CLI | `scriptforge/` (own pyproject.toml, own tests) |
| Worker 3 | `mediafactory` | Media production CLI | `mediafactory/` (own pyproject.toml, own tests) |
| Worker 4 | `thumbsmith` | Thumbnail & branding CLI | `thumbsmith/` (own pyproject.toml, own tests) |
| Integrator | `vmachine` | Pipeline + Dashboard | `vmachine/` (depends on all 4 tools) |

### Why This Works for Supergod

1. **Zero coordination needed.** Workers never import from each other. No shared models. No merge conflicts. Each builds in their own directory.

2. **Each tool works alone.** Worker can test their tool end-to-end without waiting for anyone else. `ytscout scan --topics "cars"` works on day 1.

3. **Integration is mechanical.** The integrator just reads output JSON from one tool and passes it as input JSON to the next. No API design debates.

4. **Clear success criteria per tool.** Each tool has its own CLI, its own tests, its own README. Either `ytscout scan` returns scored niches, or it doesn't.

5. **Tools can evolve independently.** Want to add a new stock footage provider to mediafactory? Change mediafactory only. The integrator doesn't care -- same JSON output.

---

## Monorepo Layout

All 5 projects live in one git repo but are independently buildable:

```
viral-video-machine/
├── ytscout/                    # Worker 1's project
│   ├── ytscout/
│   ├── tests/
│   └── pyproject.toml
├── scriptforge/                # Worker 2's project
│   ├── scriptforge/
│   ├── tests/
│   └── pyproject.toml
├── mediafactory/               # Worker 3's project
│   ├── mediafactory/
│   ├── tests/
│   └── pyproject.toml
├── thumbsmith/                 # Worker 4's project
│   ├── thumbsmith/
│   ├── tests/
│   └── pyproject.toml
├── vmachine/                   # Integrator's project
│   ├── vmachine/
│   ├── frontend/
│   ├── tests/
│   └── pyproject.toml
├── docs/
│   ├── prd.md
│   └── steffen_miro_strategy.md
└── README.md
```

Each worker works ONLY in their directory. No touching other directories. The integrator is the only one who references all four.

---

## Success Criteria

### Per Tool (worker tests these independently)

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

### Integration (integrator tests these)
- [ ] Full pipeline: topic → video package in < 10 minutes
- [ ] Dashboard renders and can trigger pipelines
- [ ] YouTube upload works (unlisted → HD processing → public)
- [ ] Batch mode: queue and process 10 videos
- [ ] Channel health monitoring detects low-impression channels

---

## Cost Estimates (per video)

| Tool | Cost |
|------|------|
| ytscout (YouTube API) | ~$0 (within free quota) |
| scriptforge (GPT-4o) | ~$0.05 |
| mediafactory voice (ElevenLabs) | ~$0.50-1.00 |
| mediafactory visuals (free stock) | $0 |
| mediafactory visuals (AI fallback) | ~$0.10 |
| thumbsmith (DALL-E 3) | ~$0.12 |
| **Total** | **~$0.77-1.27** |

With human editor instead of auto-assembly: add $15-30.
