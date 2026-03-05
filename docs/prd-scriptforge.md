# scriptforge -- AI Script & Metadata Generator CLI
## Self-Contained PRD for Supergod Worker 2

---

## Overview

**What it is:** A standalone AI scriptwriting and metadata generation CLI for faceless YouTube channels. Give it a topic and a reference transcript, it produces a retention-optimized script, metadata (titles, description, tags), and a quality score. Think of it as "professional YouTube scriptwriter in a box."

**Why it exists:** This tool implements the exact prompt chain methodology from Steffen Miro's YouTube Automation course, distilled from ~100 video transcripts. The Strategy Rules section below contains hardcoded rules, exact prompts, specific numbers, and non-negotiable requirements from this methodology. These Strategy Rules are the core IP -- they're what makes this system produce videos that actually get views, not just generic content. **Workers MUST implement every Strategy Rule as specified. They are not suggestions.**

**What it produces:**
- Segmented scripts with visual search terms per segment (`script.json`)
- Ranked titles, descriptions, tags, and metadata (`metadata.json`)
- Script quality scores with improvement suggestions (`quality_score.json`)
- Categorized topic ideas from competitor data (`topic_ideas.json`)

Could be sold standalone: "AI script generator optimized for YouTube retention -- $29/month."

---

## Context

- This tool is part of a larger system called "Viral Video Machine" but is built as a **STANDALONE tool**.
- It knows nothing about the other tools (ytscout, mediafactory, thumbsmith). It shares ZERO code with them. It could be published on PyPI independently.
- It communicates via **JSON files only**. It reads a JSON config/input file and produces JSON output files.
- It can optionally receive competitor data (as JSON) from ytscout (e.g., `competitor_blueprint.json` with outlier video titles and view counts), but does NOT import or depend on ytscout. It simply reads that JSON file if provided.
- The **Strategy Rules** section below contains non-negotiable requirements from the Steffen Miro methodology. Every prompt, every rule, every number is intentional and must be implemented exactly as specified.

---

## Standalone CLI Interface

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

---

## Input/Output Contracts

### write input (`script_request.json`)

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

### write output (`script.json`)

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

### metadata output (`metadata.json`)

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

### score output (`quality_score.json`)

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

### ideate output (`topic_ideas.json`)

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

---

## Strategy Rules (MUST implement -- from Steffen Miro methodology)

These are non-negotiable. They come from 100+ videos of proven methodology.

### The Exact 3-Step Prompt Chain (use these exact prompts, not paraphrases)

**Step 1 -- Feed competitor transcript to LLM:**
```
Study and analyze this transcript. Understand the structure, pacing, hooks,
and retention techniques used. Pay attention to how the narrator creates
curiosity, where twists are placed, and how the ending is handled.

[PASTE FULL COMPETITOR TRANSCRIPT]
```

**Step 2 -- Generate meta-prompt:**
```
Here's a faceless YouTube automation niche I want to copy. Pretend you're a
professional script writing genius or guru. These are the best performing
videos so we can replicate their success and virality. Using the script I'm
going to provide as a reference. Write me a prompt to give ChatGPT to write
me a script but for this topic: [TOPIC]. Make sure it's insanely optimized
for AVD and viewer retention and flows the exact same as the reference I'm
about to provide. Make me a prompt and also include the script in the prompt.
```

**Step 3 -- Execute the meta-prompt generated in step 2 against the LLM, with these additions:**
- "Without headings, without music instructions"
- "Make it about [WORD_COUNT] words, ask me to continue after every 1,000 words"
- Continue generating until target word count is reached

### Script Structure Rules

- **Hook:** first 30 seconds MUST create a curiosity gap. Never reveal the answer upfront.
- **Body:** insert a twist, revelation, or "but what they didn't know was..." every 20-30 seconds
- **Ending:** QUICK. No "thanks for watching", no "don't forget to subscribe", no long outro. Just end the story.
- No section headings in the script (they bleed into voiceover)
- No music cues or sound effect instructions

### Word Count / Duration Rules

- 150 words per minute of video (1,500 words = 10 minutes)
- ALWAYS target 1.5x the competitor's average video length
- If competitor averages 8 minutes -> target 12 minutes -> 1,800 words
- If competitor averages 15 minutes -> target 22 minutes -> 3,300 words

### Visual Search Terms

- For every 5-7 seconds of script, generate 3 search terms for stock footage/images
- Terms should be concrete and searchable ("nuclear submarine underwater" not "tension builds")

### Ideation Prompt (exact wording)

```
Pretend you are a YouTube guru and faceless channel expert. Write me a prompt
to copy this video style of ideas. I want perfect ideation model. Write me
video ideas based off these outliers that performed well on their channel.
Target high clickbait and CTR. Write me the expert prompt to give ChatGPT.

[PASTE LIST OF COMPETITOR'S TOP-PERFORMING VIDEO TITLES + VIEW COUNTS]
```

### Topic Split

When generating topics, label 80% as "proven" (directly based on competitor outliers) and 20% as "experimental" (adjacent topics not yet covered).

### Title Rules

- Main keyword FIRST in the title
- Under 60 characters (so it doesn't get cut off on mobile)
- Power words database (hardcode these): "exposed, destroyed, game over, robbing you blind, total ripoff, waste of money, avoid, stop buying, never, worst, secret, hidden, banned, terrifying, vanished, deadly"
- "People respond way more to negativity than to positive things" -- negative framing scores higher
- Use ChatGPT to make titles more clickbaity after initial generation

### Description Rules

- Line 1: exact video title
- Lines 2-6: keyword-rich paragraph summarizing the video
- Then: channel description boilerplate (same on every video for the channel)
- Include: contact email for sponsorship inquiries
- Include: social media links
- Include: relevant hashtags (3-5)

### Tag Rules

- Generate from title keywords + topic keywords
- Include long-tail variations
- 15-30 tags per video

---

## Components to Build

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

---

## Dependencies

```
openai                      # OpenAI API
anthropic                   # Anthropic API (fallback)
tiktoken                    # token counting
click                       # CLI framework
```

---

## Project Structure

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

---

## Tests

- Unit: prompt chain construction (verify correct prompt text)
- Unit: script segmentation (word count -> segment timing)
- Unit: title clickbait scoring (power words, length, structure)
- Unit: tag extraction from sample text
- Unit: script quality scoring dimensions
- Integration: full script generation with mocked LLM (recorded responses)
- Integration: metadata generation end-to-end with mocks

---

## Success Criteria

- [ ] `scriptforge write --topic <topic>` produces segmented script with visual search terms
- [ ] `scriptforge metadata` produces ranked titles, description, and tags
- [ ] `scriptforge ideate` produces categorized topic ideas
- [ ] `scriptforge score` produces quality score with improvement suggestions
- [ ] 3-step prompt chain produces noticeably better scripts than single-prompt
- [ ] All outputs are valid JSON matching documented schemas
