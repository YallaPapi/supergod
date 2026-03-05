# thumbsmith -- AI Thumbnail & Brand Identity Generator CLI
## Self-Contained PRD for Supergod Worker 4

---

## Context

This tool is part of a larger system called "Viral Video Machine" but is built as a **STANDALONE tool**. Key facts:

- **thumbsmith knows nothing about the other tools** (ytscout, scriptforge, mediafactory). It does not import them, depend on them, or reference their code.
- **Communication is via JSON files only.** thumbsmith reads a JSON input file and produces JSON + image files as output. An external integrator (vmachine) may chain tools together, but thumbsmith neither knows nor cares about that.
- **It can optionally receive style analysis data** (as JSON fields in its input) but does NOT import or depend on ytscout or any other tool to obtain that data. If style data is provided, it uses it. If not, it can analyze competitor thumbnails itself.
- **The Strategy Rules section below contains non-negotiable requirements** from Steffen Miro's YouTube Automation methodology (distilled from ~100 video transcripts). These rules are the core IP -- they are what makes this system produce thumbnails that actually get clicks, not just generic images. **Workers MUST implement every Strategy Rule as specified. They are not suggestions.**

---

## Overview

### What It Is

A standalone AI thumbnail and branding CLI. Give it a topic and reference thumbnails, it produces 3 click-optimized thumbnail variants plus full channel branding. Think of it as "professional thumbnail designer + brand agency in a box."

Could be sold standalone: "AI YouTube thumbnail generator -- 3 A/B variants in 30 seconds."

### What It Produces

- **3 thumbnail variants** (A, B, C) at 1280x720 PNG -- different enough to A/B test but all consistent with channel style
- **Full channel brand kit** -- logo (800x800), banner (2560x1440), channel description, social media bios, thumbnail template
- **CTR score predictions** with actionable improvement suggestions
- **Consistency reports** comparing new thumbnails against existing channel style
- **Reusable templates** that can be applied to new topics

---

## Standalone CLI Interface

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

---

## Input/Output Contracts

### generate input (`thumbnail_request.json`)
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

### generate output (`manifest.json`)
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

### brand output (`brand_kit.json`)
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

### score output (`ctr_score.json`)
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

### consistency-check output (`consistency_report.json`)
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

These are non-negotiable. They come from 100+ videos of proven methodology.

### The 80/20 Thumbnail Rule
- 80% of the thumbnail should match competitor style exactly (colors, layout, contrast, text style)
- 20% should be your original twist (different subject, different angle, different text)
- NEVER copy a competitor thumbnail 1:1 (their audience already saw it -- it won't feel new)
- YouTube trained the audience to click that style. Changing the style = lower CTR.

### The 0.2 Second Rule
- A thumbnail must be understood in 0.2 seconds
- One clear subject, one emotion, one message
- If you have to think about what the thumbnail shows, it's too complex

### Thumbnail Consistency = Channel Identity
- "Make it look like one channel" -- all thumbnails on a channel should use the same visual pattern
- Same color palette, same font, same layout structure, same contrast level
- Viewers should be able to identify your channel from the thumbnail alone
- Breaking consistency = confusing the audience = lower CTR

### The Blend Test
- Take your thumbnail and paste it into the competitor's channel page (Photoshop/editing)
- Show someone the channel page
- If they CANNOT spot which thumbnail doesn't belong, your thumbnail is good
- If it sticks out, adjust to match better

### Exact Thumbnail Prompt (use this, not a paraphrase)
```
Pretend you are a professional thumbnail designer. Write me a prompt to copy
exactly the style of thumbnails. Target clickbait and CTR for the topic
[TOPIC]. Thumbnail text: [TEXT].
[PASTE SCREENSHOT/DESCRIPTION OF COMPETITOR THUMBNAILS]
```

### Post-Processing Rules (in this exact order)
1. Background removal on subject (rembg) -- if the style calls for isolated subjects
2. Bold text overlay -- large, readable at thumbnail size (which is TINY on mobile)
3. Red arrow PNG overlay -- if competitor style uses arrows
4. "Breaking News" banner -- if competitor style uses news-style banners
5. Color grading: brightness +20%, saturation +15%, sharpness/clarity up
6. Match competitor's exact color palette (extracted by style analyzer)
7. Border/glow effects only if competitor style uses them

### Text on Thumbnails
- 1-3 words MAXIMUM. Short, punchy, emotional.
- Must be readable at 168x94 pixels (how thumbnails appear in YouTube sidebar)
- High contrast: white or red text on dark backgrounds, or dark text on bright backgrounds
- Power words: same list as titles (exposed, destroyed, vanished, secret, etc.)

### Output Requirements
- Exactly 1280x720 pixels (YouTube standard)
- PNG format (lossless for text clarity)
- Generate exactly 3 variants (A, B, C) -- different enough to A/B test but all consistent with channel style
- Variant strategies: vary text position, text color, subject framing, or background emphasis

### Branding Rules (from Steffen's channel setup)
- Channel name should include a human name: "History with Stefan" not "Mystery Time" (prevents inauthentic content flags)
- Logo: simple, clean, AI-generated is fine
- Banner: channel name + 3-6 word tagline + upload schedule
- Banner dimensions: 2560x1440
- Profile picture: 800x800
- Have matching Instagram/TikTok profiles (linked from channel) to signal legitimacy
- Channel description must include: what the channel provides, upload schedule, contact email, disclaimer for finance/politics/health

---

## Components to Build

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

---

## Dependencies

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

---

## Project Structure

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

---

## Tests

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

## Success Criteria

- [ ] `thumbsmith generate` produces 3 distinct thumbnail variants at 1280x720
- [ ] `thumbsmith brand` produces logo (800x800), banner (2560x1440), descriptions
- [ ] `thumbsmith score` returns CTR prediction with improvement suggestions
- [ ] `thumbsmith consistency-check` detects style mismatches
- [ ] Post-processing pipeline applies text overlay, color grading, sharpening
- [ ] Templates can be saved and re-applied to new topics
- [ ] All outputs are valid JSON matching the documented schemas above
- [ ] Tests pass with mocked AI API calls (no live API needed for CI)
