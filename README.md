# Clipper

Paste a YouTube link, get vertical clips of the moments viewers replay most, edit them with an
instant preview, and export MP4s. Runs entirely on this Mac at http://127.0.0.1:8765.

## Run

```sh
./run.sh
```

1. **Library** (`#/`): paste a link, pick a shape (9:16, 1:1, 16:9), clip count and length, optional direction.
2. **Video page**: progress while it is analysed, then the replay graph and the clips Claude picked. **Export all as zip** renders every clip that is not discarded.
3. **Editor**: click a clip.
   - Transcript: click a word, shift-click a range. **Start here** `[`, **End here** `]`, **Cut words** `Delete`, **Fix word** `Enter`. Space plays.
   - The frame follows whoever is talking. When the camera cuts to a wide shot with no face it can follow, the clip shows the whole frame on a blurred fill until a face is back.
   - Drag the picture sideways to move the frame (this pins it from that point on); **Reset framing** undoes that.
   - Looks: Highlight, Karaoke, Pop, Boxed. Hook headline, @handle (set in **Settings**), channel credit, swear bleep.
   - Every change saves itself; **Export MP4** renders the final file.

## How it works

- Moments are picked by Claude through your Claude Code login (`claude -p` with plugins, hooks and
  MCP servers switched off; roughly $0.05–0.40 of usage per video). The YouTube replay graph is passed
  along, and each clip shows its replay score.
- One render plan (`app/plan.py`) drives both the browser preview and the ffmpeg export, so they match.
- Full videos are deleted 14 days after they were added; each clip keeps ±30 s around it, so clips stay
  editable and exportable afterwards.
- A server restart resumes unfinished analysis without re-downloading.

## Needs

- `brew install ffmpeg-full` (Homebrew's default `ffmpeg` cannot burn captions)
- `uv`, Deno on `PATH` (yt-dlp uses it), and the `claude` CLI logged in
- Optional: `cookies.txt` in the project root, only if YouTube starts demanding sign-in

## Check it works

```sh
uv run pytest                                   # unit and render tests
uv run python -m scripts.smoke [youtube_url]    # real end-to-end run, every caption look, frame-exact check
```

## Not built yet

English YouTube videos only for now. Hindi/Hinglish (Whisper + Roman captions), uploads, the local AI
backup, speaker-cut framing for podcasts, and search are the next phases.

Clipping other creators' videos needs their permission; the credit line helps but does not replace it.
See `VENDORED.md` for third-party code.
