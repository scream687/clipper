# Clipper

Paste a YouTube link (or upload a video), get vertical clips of the moments viewers replay most, edit them
with an instant preview, and export MP4s. English, Hindi and Hinglish (captions in Roman Hinglish).
Runs entirely on this Mac at http://127.0.0.1:8765.

## Run

```sh
./run.sh
```

1. **Library** (`#/`): paste a link (shape, clip count, length and direction are under the link box).
2. **Video page, the review feed**: progress while it is analysed, then a player and the clips the AI picked, each
   already finished. `J`/`K` move between clips, `Y` keeps, `N` drops (and plays the next one), `E` opens the editor,
   `Space` plays. **Export** zips the clips you kept (or every clip not dropped, before you keep any).
3. **Editor** (`E`, or double-click a clip), for the rare fix. Its settings are in tabs: Style, Sound & extras, Post text.
   **Make this my default** saves the clip's look for every new clip; **Apply to all clips** copies it across the video.
   - Transcript: click a word, shift-click a range. **Start here** `[`, **End here** `]`, **Cut words** `Delete`, **Fix word** `Enter`. Space plays.
   - `I` / `O` trim the start / end to the word at the playhead.
   - The frame follows whoever is talking. When the camera cuts to a wide shot with no face it can follow, the clip shows the whole frame on a blurred fill until a face is back.
   - Drag the picture sideways to move the frame (this pins it from that point on); **Reset framing** undoes that.
   - Framing: **Follow face**, **Whole frame**, or **Split**: a streamer's facecam on top, the gameplay or screen below, captions on the seam. The facecam is found automatically (a small face in a corner, inside a fixed border); gameplay clips without a big face start in Split. Drag the top to move the facecam crop, scroll over it to zoom, drag the bottom sideways. With no facecam, the top shows the face the framing followed.
   - Canvas behind the whole frame and wide shots: blur, colour or gradient, with padding and rounded corners.
   - Template: **X post** (light or dark): the clip under a post with your logo, display name, verified tick,
     @handle and the hook as the post text.
   - Captions: Highlight, Karaoke, Pop, Build up, Full line, Boxed. Hook headline, @handle, channel credit,
     swear bleep, emojis, a **Follow badge** over the last 3 seconds, your **logo** in a corner.
   - **Music** from your library under the speech, with a riser to a peak volume over the last seconds.
   - Rate a clip 1-10: later picks lean towards what you rate high.
   - Every change saves itself; **Export MP4** renders the final file.

## Settings

@handle, display name (X post template), logo and music library, export quality (1080p or 4K) and frame
rate (source, 30 or 60 fps), watched channels, trends, OpusClip key, and the AI provider.

## Live streams

Paste the link of a stream that is still running: Clipper captures the **last 30 minutes** from YouTube's rewind
buffer (set "Live streams: last minutes" under the link box, 5 to 240) and finds clips in it right away, instead of
waiting for the stream to end. Add the link again later to clip the next stretch. A running stream has no captions
yet, so it is transcribed with Whisper.

**Chat hype.** With "keep watching the chat" ticked (the default), Clipper also follows the stream's live chat.
When the chat explodes (many different people reacting at once, at least twice its usual pace, with extra weight
for 😂 / LOL / W / F and "clip it"), it waits 45 seconds for the reaction to play out, captures the last 2-5
minutes, and clips that moment as its own video ("… · chat hype 21:42 (3.8x)"). It keeps watching until the
stream ends or you press **Stop watching**; **Watch chat** on a live video starts it again. YouTube only serves a
running stream's chat from now on, so the stretch captured when you paste the link has no chat signal; a
finished stream's chat replay is read in full and its spikes lead the moment picking.

## What each clip gets without touching it

- **Framing by kind of video**: the AI tells a talk, gameplay, show, vlog or stream apart. Gameplay never follows the
  game's characters' faces: a facecam becomes Split, no facecam shows the whole frame.
- **Pacing**: ums and stutters cut (Hindi doubling like "jaldi jaldi" is kept), pauses over 0.35 s shortened to a
  breath, and a punch-in zoom on alternate shots so the picture changes every few seconds. Loudness -14 LUFS.
- **Sound design**: a whoosh into every cutaway and punch-in, a pop on emojis, a meme sound on the punchline, music
  ducked under the voice. Built-in sounds are generated (no licence questions); add your own in Settings by pasting a
  myinstants.com link (those can be copyright-claimed; **Safe sounds only** skips them).
- **Captions and graphics** (`app/ass.py` in the export, `web/overlay/` in the editor preview): caption groups
  blur-pop in, the AI's key words pop in colour, the hook settles in, emojis, a progress bar along the top edge.
- **B-roll and cards** (Remotion, `motion/`): a number or a name gets a card over the speaker's own frame; a concrete
  thing gets Pexels footage (free key in Settings; without one it becomes a card).

## YouTube uploads (daily, automatic)

With **Settings > YouTube channel** connected and *Post the AI's best clips every day* on, Clipper picks the two best
new clips (AI score 7+, one per video, never one you discarded) and uploads them as Shorts, rendered and scheduled to
go public at **1 PM and 7 PM IST**. It checks every 30 minutes and uploads each slot's clip up to 20 hours ahead.
Titles get #Shorts, the description carries the hashtags and a credit line. Uploads are listed under the panel.

One-time setup (about 10 minutes, your own Google Cloud project, free):

1. console.cloud.google.com: create a project, then **APIs & Services > Library > YouTube Data API v3 > Enable**.
2. **OAuth consent screen**: External; add your Google account under test users. Then **Publish app** (In
   production). Without that, Google ends the sign-in every 7 days and the daily uploads stop. Verification is not
   needed for your own use: at sign-in, click *Advanced > Go to (your app)* past the "unverified app" warning.
3. **Credentials > Create credentials > OAuth client ID > Desktop app**, download the JSON and save it in the Clipper
   folder as `client_secret.json` (kept out of git).
4. Clipper: **Settings > YouTube channel > Connect**, pick the account that owns the channel, allow uploads. Tick the
   daily posting box and **Save**.

Limits: until Google audits your API project (YouTube API Services audit form), YouTube keeps API uploads private,
so they will not go public at the slot by themselves: publish them in YouTube Studio with one tap. The free API quota
allows about six uploads a day. Clips are reposted footage of other channels' streams: post only what you have the
right to, or expect claims.



- Transcript: YouTube's word-timed captions in the video's own language (never a dub's track); uploads
  and videos without them go through Whisper (large-v3-turbo) on this Mac.
- Moments are picked by the AI chosen in **Settings** (default: Claude through your Claude Code login,
  `claude -p` with plugins, hooks and MCP servers off). Long videos are read in 30-minute parts and the
  candidates ranked against each other. Signals passed along: live chat spikes (streams; each spike is asked
  for its own clip and ranks higher), the replay graph, comments that quote or timestamp a line, loud seconds,
  what India is searching, and your own 1-10 ratings of earlier clips. The on-screen hook is the best of three
  the AI writes, scored by the master-virality skill's `hookscore.py`.
- A podcast's cold-open teaser (lines replayed later in the episode) is skipped, edges are snapped to whole
  sentences, and a clip that answers a question starts on the question. With no AI, clips fall back to
  the loudest moments.
- **Search**: find a word, or describe a moment and the AI adds it as a clip. **Also ask OpusClip** imports
  OpusClip's picks (needs its API key).
- One render plan (`app/plan.py`) drives both the browser preview and the export, so they match.
- **Exports are rendered by ffmpeg** (`app/render.py`), one command per clip, in seconds: frame-exact cuts, the
  framing (face follow, whole frame on a canvas, facecam split) with punch-ins and a small settle on every cut as one
  per-frame crop, a light grade and vignette, Remotion's inserts, the post header, ASS captions (`app/ass.py`),
  emojis, logo, progress bar, and every sound (speech levelled to -14 LUFS, bleeps, music ducked under the voice by a
  sidechain with its riser, effects), encoded on the Mac's hardware H.264 encoder.
- Full videos are deleted 14 days after they were added; each clip keeps ±30 s around it, so clips stay
  editable and exportable afterwards.
- A server restart resumes unfinished analysis without re-downloading.

## Needs

- `brew install ffmpeg-full` (analysis: proxies, loudness, face tracking, posters) and `brew install whisper-cpp`
- `uv`, Deno on `PATH` (yt-dlp uses it), and the `claude` CLI logged in
- Optional: Node 22+ and `cd motion && npm install` once, for the Remotion B-roll inserts (without them, clips
  simply have no inserts).
- Optional: `cookies.txt` in the project root, only if YouTube starts demanding sign-in ("Sign in to confirm
  you're not a bot"). Make it from the browser you are signed in to YouTube with (Chrome here; also `safari`,
  `firefox`, `brave`), then press **Watch chat** again:
  `uv run yt-dlp --cookies-from-browser chrome --cookies cookies.txt --skip-download "https://youtu.be/dQw4w9WgXcQ"`

## Check it works

```sh
uv run pytest                                   # unit and render tests
uv run python -m scripts.smoke [youtube_url]    # real end-to-end run, every caption look, frame-exact check
uv run python -m scripts.bench --urls URL ...   # score picks against the Shorts the creator cut themselves
uv run python -m scripts.score OUT VIDEO_ID ...  # rebuild and render top clips, score them on the 9/10 rubric
```

## Not built yet

Chapters, three-person layouts, more post templates (news ticker), a light app theme.

Clipping other creators' videos needs their permission; the credit line helps but does not replace it.
See `VENDORED.md` for third-party code.

## License

MIT, see `LICENSE`. Contributions welcome: open an issue or a pull request.
