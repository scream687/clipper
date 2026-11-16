# Clipper

Paste a YouTube link, get vertical clips of the moments viewers replay most.
Runs entirely on this Mac at http://127.0.0.1:8765.

## Run

```sh
./run.sh
```

Open http://127.0.0.1:8765, add your AI endpoint, model and key under **AI settings**
(any OpenAI-compatible provider), paste a link and press **Find moments**.
Tick the moments you want, choose the framing, press **Make clips**.

## Needs

- `brew install ffmpeg-full` (Homebrew's default `ffmpeg` cannot burn captions)
- `uv` (installs the Python dependencies on first run)
- Deno on `PATH` (yt-dlp uses it for YouTube's JavaScript challenges)

## Optional files

- `data/logo.png`: enables the **Logo** watermark toggle.
- `cookies.txt` (project root): only if YouTube starts demanding sign-in.

## Check it works

```sh
uv run pytest                  # unit tests
uv run python -m scripts.smoke # downloads and renders one clip in all three framings, no AI key needed
```

## How it picks moments

YouTube's "Most replayed" graph is fetched for the video. Its peaks are passed to the AI
next to the transcript, and every pick gets a replay score (1.0 = the video's average).
Videos without a graph are picked from the transcript alone.

## Framing

- **Follow face**: crops to the speaker. Best for talking-head and podcast footage; wide
  shots and slides fall back to a centre crop.
- **Blurred fill** / **Black bars**: keep the whole frame. Much faster, and better for slides.

## Vendored code

`yt_short_clipper_core/` comes from [jipraks/yt-short-clipper](https://github.com/jipraks/yt-short-clipper)
(MIT). See `VENDORED.md`; `git log -- yt_short_clipper_core` lists every local change.

Clipping other people's videos for reposting needs their permission. This tool does not check.
