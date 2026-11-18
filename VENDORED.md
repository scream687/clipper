# Third-party code

`app/roman.py` is PeakCut's Hinglish speller ([haziqahmedqasmi/PeakCut](https://github.com/haziqahmedqasmi/PeakCut), MIT); the notice is in that file.

`app/youtube.py` adapts `parse_json3_words` and `find_orig_caption_code` from
[jipraks/yt-short-clipper](https://github.com/jipraks/yt-short-clipper) (commit `f6d6d98`),
MIT License, Copyright (c) Aji Prakoso. The notice is kept in that file's docstring.

`web/overlay/gsap.min.js` is GSAP 3.14.2 (GreenSock, free under the [GSAP standard license](https://gsap.com/standard-license)),
used by the captions and graphics layer. `motion/` is a Remotion project (`npx create-video --blank`); Remotion is free
for individuals and teams of up to 3 ([licence](https://www.remotion.dev/docs/license)).

Earlier versions of this project vendored that whole core in `yt_short_clipper_core/`;
it was removed once the new render plan and renderer replaced it (see `git log`).
