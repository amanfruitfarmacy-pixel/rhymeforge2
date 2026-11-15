# RhymeForge

One command that turns a week of research into four ~3-minute educational
toddler songs on YouTube, using free tools only:

```bash
rhymeforge week --count 4
```

| Step | What happens | Free tool |
|---|---|---|
| 1. Scout | Ranks kids-song videos by **how far each beat its own channel's median** (views ÷ channel median), not by channel size | YouTube Data API (free quota) |
| 2. Niches | Scores topics by demand (outlier strength) vs supply (number of uploads); avoids topics you did recently | local |
| 3. Ideas | Original angles in the best gaps, rejected if too close to existing titles | Gemini API free tier |
| 4. Script + hook | Lyrics in verse/chorus form; hooks drafted from **21 toddler hook formulas** and scored; rewritten until ≥ 75 | Gemini + heuristic scorer |
| 5. Title + thumbnail | Written as **one pair**; pairs where the thumbnail text repeats the title are rejected | Gemini |
| 6. Storyboard | One shot per lyric line. ~12 AI clips per video, chorus repeats reuse footage, the rest are animated stills; old clips reused from the clip library | local |
| 7. Generate | Keyframes, image-to-video clips, the song with vocals, and word timestamps, all in one batch on a free GPU | Kaggle GPU: SDXL, LTX-Video, ACE-Step, faster-whisper |
| 8. Assemble | Shots cut to the sung words, karaoke captions, loudness -14 LUFS, thumbnail | ffmpeg, Pillow |
| 9. QA | Duration, loudness, black frames, strobe check, hook and lyric safety | ffmpeg |
| 10. Upload | Private + scheduled (Mon/Wed/Fri/Sun by default, at least 24h ahead so you can review), `madeForKids`, AI disclosure, captions | YouTube Data API |
| 11. Review | 48h after a video goes public: **retention autopsy** (the second people left + the lyric and shot on screen), lessons fed into next week's songwriting, and **Shorts** cut from the best moments | YouTube Analytics API |

Re-running the command resumes: finished stages are loaded from `runs/<week>/`.

## Standalone tools

```bash
rhymeforge scout                       # outlier videos
rhymeforge niches                      # under-served topics
rhymeforge formulas                    # the 21 hook formulas
rhymeforge hook "Uh-oh! Where did Coco's ball go?"
rhymeforge autopsy --video-dir runs/2026-W46/<video> --csv retention.csv   # or --video-id ID
rhymeforge edl transcript.srt --retention retention.csv --duration 600 -o my_edit   # CMX3600 .edl + .json
rhymeforge shorts --video long.mp4 --transcript long.srt --retention retention.csv --cut
rhymeforge review                      # autopsy + Shorts for everything public 48h+
```

`edl` and `shorts` work on any video's transcript (.srt, .vtt or JSON), not just ones RhymeForge made.

## Try it with no accounts

```bash
pip install -e ".[dev]"
rhymeforge week --offline --count 2 --run-name demo
```

Offline mode uses **sample** scouting data, built-in lyric templates and placeholder visuals/tones, so you can see every
stage and the folder layout without any keys or GPU. Requires `ffmpeg`.

## Real setup (all free)

1. **Install**: `pip install -e ".[youtube,kaggle]"` and ffmpeg.
2. **Gemini key**: https://aistudio.google.com/apikey → put it in `.env` as `GEMINI_API_KEY` (see `.env.example`).
3. **Kaggle**: create an account, **verify your phone number** (needed for GPU + internet in notebooks),
   then Settings → API → "Create New Token" and save `kaggle.json` to `~/.kaggle/`.
   You get roughly 30 GPU hours a week; one week of 4 videos is designed to fit within a single run.
4. **YouTube (Google Cloud project)**:
   - Enable *YouTube Data API v3* and *YouTube Analytics API*.
   - Create an **API key** → `YOUTUBE_API_KEY` in `.env` (used for scouting).
   - Create an **OAuth client ID (Desktop app)** → download it to `.secrets/client_secret.json` (used for upload + analytics).
     The first upload opens a browser to sign in.
   - **Apply for the API audit early.** Until Google approves your project, videos uploaded through the API are
     locked to private. You can still publish them by hand in YouTube Studio meanwhile.
   - Verify your channel's phone number so custom thumbnails work.
5. **Config**: `cp config.example.yaml config.yaml`, then set your characters, schedule and timezone, plus some seed
   channel IDs to watch.
6. Run `rhymeforge week --count 4`.

No Kaggle? Use `--backend manual` to run the generated `generate.py` in Google Colab (free GPU) and drop the results
back in, or `--backend local` if you have an NVIDIA GPU.

## Output layout

```
runs/2026-W46/
  scout.json  niches.json  ideas.json  job.json  summary.md
  gpu_output/{images,clips,songs,transcripts}/  errors.json
  <video-slug>/
    script.json  package.json  storyboard.json  timing.json  shots_timed.json
    final.mp4  thumbnail.jpg  captions.ass  captions.srt  qa.json  upload.json
    autopsy.md  shorts/short_1.mp4 ...      (after review)
data/history.json  data/learnings.json      (what you've published and what you've learned)
library/index.json  library/clips/          (reusable clips)
```

If the GPU job fails to make a song for a video, put your own `song.wav` in that video's folder and re-run.

## Things to know

- **Not yet tested on a real GPU.** The GPU script (`rhymeforge/kaggle_kernel/generate.py`) was written against the
  diffusers LTX-Video and ACE-Step APIs and checked for syntax, but this repo was built without GPU access. Expect to
  tune it on the first Kaggle run (model versions, memory, step counts). Every item is wrapped so partial results come
  back, and failures are listed in `errors.json`.
- **Check licences before monetising.** SDXL base (CreativeML Open RAIL++-M), LTX-Video and ACE-Step all publish
  their own licence terms. Read the current terms of the exact model versions you use.
- **A person must watch every video before it goes public.** Uploads are private and scheduled at least
  `min_lead_hours` ahead; if something's wrong, unschedule it in Studio. YouTube demonetises repetitive,
  mass-produced content and has strict rules for kids' content. Recurring characters, real teaching value and your
  own review are what keep a channel safe.
- **Retention data is approximate.** The YouTube Analytics API gives 100 points per video (~1.8 s apart on a
  3-minute video), so "the exact second" is accurate to about 2 seconds.

## Tests

```bash
pytest
```
