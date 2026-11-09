# Claude Code Latest

Source for the weekly **Claude Code Latest** issue, published at
https://aiattheedge.github.io/claude-code-weekly/.

Current issue: Nov 2 to 9, 2026 (`og-cover-v7.png`, images in `images/2026-11-09/`).

| Path | What it is |
| --- | --- |
| `index.html`, `og-cover-v*.png` | The current issue page and its social images |
| `.github/workflows/render-images.yml` | Renders cover and banner PNGs from `render/<date>/jobs.json` |
| `.github/workflows/render-video.yml` | Turns a spoken script into an AI video of Ian (below) |
| `video/` | Video config, weekly scripts and a sample |
| `scripts/` | Code for both workflows |
| `tests/` | Unit and end-to-end tests for the video step |

## Weekly video

Each week's issue can ship with a short video of Ian presenting it. The video is made by
[HeyGen](https://developers.heygen.com) from Ian's own digital twin and cloned voice, so it is
**AI-generated** and is labelled that way on screen, in the opening line and on the end card.

### How a video gets made

1. A spoken script is committed to `main` as `video/script-YYYY-MM-DD.md`.
2. The push starts **Render video**. It checks the script, finds Ian's twin, and asks HeyGen
   for an Avatar V video (`POST /v3/videos`).
3. It polls `GET /v3/videos/{id}` until the render is done, downloads the MP4 and HeyGen's SRT
   captions, then uses ffmpeg to burn in the captions, add an "AI-generated" label and append
   a 5 second end card.
4. The finished MP4, captions and `render.json` are uploaded as assets to that issue's
   GitHub release, tagged with the issue date (for example `2026-11-02`). Videos are never
   committed to git. The release is created as a **draft** (only repo members can see it)
   until Ian publishes it. Re-rendering a date replaces the release's assets. A copy is also
   kept as a run artifact for 30 days.
5. Ian downloads it, watches it, and posts it himself with a first line saying it's AI-generated.

The workflow never runs on a schedule. It runs only on a script push to `main` or when started
by hand from **Actions > Render video > Run workflow**. A hand start defaults to **dry-run**,
which is free: it checks the script, lists the twin looks and prints the cost estimate without
creating a video. Choose **render** to spend credits.

### One-time setup (Ian)

1. Create a HeyGen account and a digital twin in the HeyGen web app (2 to 5 minutes of
   continuous 1080p footage plus the consent clip HeyGen asks for). HeyGen clones the voice
   from the same footage.
2. Buy API credits (pay as you go) and create an API key.
3. In this repo: **Settings > Secrets and variables > Actions**
   - Secret `HEYGEN_API_KEY` (required).
   - Secret `VIDEO_BLOCKED_TERMS` (optional): comma-separated words a script must never contain.
   - Variables `HEYGEN_AVATAR_ID` and `HEYGEN_VOICE_ID` (optional): pin a look or voice.
     Without them the workflow uses the first finished private twin look that supports the
     chosen engine, and that look's default voice.
4. Run the workflow by hand in dry-run mode once. The run summary shows which look and voice
   it found.

### Script format

```markdown
---
title: How I'm using Claude Duet
date: 2026-11-02
---
<!-- Notes like this are not spoken. -->
# Section labels like this are not spoken

Hi, I'm Ian. I gave an AI agent the night shift, and here's what it did.

# Disclosure

Quick note: this is an AI-generated video. ...
```

- File name: `video/script-YYYY-MM-DD.md`, using the issue's Monday date.
- Front matter: `title` and `date` are required. `engine` (`avatar_iii`, `avatar_iv`,
  `avatar_v`), `aspect_ratio`, `resolution`, `avatar_id` and `voice_id` optionally override
  `video/config.json` for one video.
- Body: plain spoken sentences, written the way Ian talks. Blank lines separate paragraphs.
- Order: open with the hook (one or two sentences), then the spoken AI line. The on-screen
  AI-generated label stays up the whole video.
- Rules the workflow enforces: "AI-generated", "AI twin" or "digital twin" must appear in the
  first 50 spoken words (about 15 seconds),
  at most 900 words (about 6 minutes), and none of the blocked terms.
- Content rules (not machine-checked): no employer, no job search, no church or personal names.
- See `video/samples/script-2026-11-02-duet.md` for a full example. Files under
  `video/samples/` never start a render.

### Slides beside the twin

When `video/slides/YYYY-MM-DD/slides.json` exists for the script's date, the video puts the
twin small in the bottom-right corner and fills the rest of the screen with slides:

- `slides.json` lists one slide per script section, in order:
  `{"section": "Hook", "html": "slide-01.html", "png": "out/slides/YYYY-MM-DD/slide-01.png"}`.
  `section` must match a `# Label` in the script, and the first slide's section must be
  where speaking starts.
- `jobs.json` in the same folder tells `scripts/render.js` to draw each HTML slide at 1920x1080.
  The workflow renders them before calling HeyGen, and the run stops before paying if any
  slide image is missing or a slide doesn't match the script.
- A slide switches on when the twin starts its section, timed from HeyGen's captions.
- Keep slide content clear of the bottom-right corner (about 640x380 px) and the bottom
  strip, where the twin and captions sit.

HeyGen still renders only the twin, so slides don't change the cost. Without a
`slides.json` the twin fills the frame as before.

### Cost

HeyGen API pricing at the time of writing is per rendered minute: Avatar III $0.60,
Avatar IV $4.83, Avatar V $7.20 (`video/config.json` holds these for the estimate).
A 600 word script runs about 4 minutes, so about $29 on Avatar V. Each run's summary shows
the estimate before rendering.

Editing and re-pushing a script renders it again and is charged again. A re-run of the
same script within 24 hours reuses the same HeyGen video (the request carries an
`Idempotency-Key`), so retrying a run that timed out does not pay twice.

### Settings

`video/config.json`:

| Key | Default | Meaning |
| --- | --- | --- |
| `heygen.engine` | `avatar_v` | HeyGen model. The run fails if the twin look doesn't support it. |
| `heygen.resolution` | `1080p` | `720p`, `1080p` or `4k` (4k only on some avatars) |
| `heygen.aspect_ratio` | `auto` | `auto` follows the twin footage; or `16:9`, `9:16`, `1:1`, ... |
| `heygen.avatar_id` / `voice_id` / `avatar_name` | blank | Pin a look, voice, or pick a look by name |
| `heygen.motion_prompt` | podcast-host direction | Avatar V body motion and gestures, in plain English (blank = none) |
| `heygen.more_blinking` | `true` | Avatar V extra blinking |
| `disclosure.require_ai_in_first_words` | 50 | The spoken AI line must come within this many words |
| `limits.max_words` | 900 | Script length cap |
| `limits.poll_timeout_minutes` | 100 | How long to wait for HeyGen |
| `disclosure.label_text` | `AI-generated` | On-screen label |
| `end_card.lines` | see file | End card text; first line is the title |
| `layout.twin_width` / `twin_max_height` / `margin_px` | `0.30` / `0.50` / `40` | Twin size and corner margin when a video has slides |

### Running locally

```bash
python3 -m unittest discover -s tests -v
python3 scripts/video/make_video.py video/samples/script-2026-11-02-duet.md --dry-run
```

The tests need ffmpeg and run the whole pipeline against a local fake of the HeyGen API, so
they never call HeyGen or spend credits.
