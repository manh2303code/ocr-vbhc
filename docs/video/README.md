# README videos (Remotion source)

The explainer videos and GIFs in the main README are rendered from this
folder with [Remotion](https://www.remotion.dev/) (React components rendered
frame by frame in a headless browser). The rendered files live in
`docs/media/video/`; this folder only holds their source.

| Composition | Length | What it shows |
|---|---|---|
| `Pipeline` | 30 s, 1920 x 1080, 30 fps | A sample document read by Chandra OCR 2, the page check (a looping page is read again), the 12 Nghị định 30 fields, chunking and bge-m3 vectors, FTS + vector + accent-free search fused with RRF and re-ranked by ViRanker, then Qwen3 answering with sources and the answer check |
| `Memory` | 12 s, 1920 x 1080, 30 fps | A three-turn chat where "tờ trình đó" and "ở đầu đoạn chat" are resolved from the conversation state |

Both take two props: `lang` (`"vi"` or `"en"`) and `theme` (`"light"`, the
one used in the README, or `"dark"`). The interface copy is in
`src/strings.ts`. The sample document and the chat stay in Vietnamese in both
languages, as in the app (the assistant answers in Vietnamese); the English
variant adds a muted translation under the chat.

The document, its agency ("tỉnh Mẫu" means "sample province"), numbers and
people are invented. No real document, name or address appears. The measured
numbers in the captions come from the developer's own test runs (RTX 5090
Laptop 24 GB, Qwen3-8B, local test sets) and are quoted as measured.

## Render

Requirements: Node 20 or newer, `ffmpeg` on `PATH` (GIFs and posters), and a
Chromium browser. Microsoft Edge or Google Chrome is found automatically at
its default Windows path (or set `REMOTION_BROWSER` to an executable);
without one, Remotion downloads its own Chrome Headless Shell.

```powershell
cd docs\video
npm ci                                   # exact versions from package-lock.json
npm run render                           # all MP4s, then GIFs, then posters
```

Single steps and variants:

```powershell
npm run render:mp4                       # pipeline_{vi,en}.mp4, memory_{vi,en}.mp4
npm run render:gif                       # GIFs from the MP4s (render those first)
npm run render:poster                    # pipeline_{vi,en}.png
node scripts/render.mjs --comp=Memory --lang=en --only=mp4,gif
node scripts/render.mjs --only=stills --comp=Pipeline --lang=vi --frames=150,420,808
npm run studio                           # live preview in the browser
```

`scripts/render.mjs` bundles once and renders:

- MP4: H.264, CRF 18, preset slow, yuv420p (about 5 to 6 MB for 30 s).
- GIF: 960 px wide, 12 fps, a 256-colour palette made with ffmpeg
  `palettegen` and applied with `paletteuse` (Bayer dither, changed
  rectangles only).
- Poster: one frame of `Pipeline` (frame 808, the answer with its sources and
  checks), 1280 px wide, 256-colour PNG.

Stills and temporary files go to `docs/video/out/` (ignored by Git).

## Typefaces, icons, images

- Be Vietnam Pro (SIL Open Font License 1.1, `public/fonts/OFL-BeVietnamPro.txt`)
  is loaded from `public/fonts/`, the same files the app bundles.
- Document text uses Times New Roman from the system (it is on every Windows
  installation and is the typeface Nghị định 30/2020 prescribes); Tinos or
  Liberation Serif are used where it is missing.
- Icons are path data from the app's Fluent UI System Icons (Microsoft, MIT).
- `public/app_mark.png` is the app icon (`assets/app_icon_256.png`) cropped to
  the badge.

## Licences

Remotion is source-available under the Remotion License, not an open-source
licence (`node_modules/remotion/LICENSE.md`): it is free, also for commercial
use, for individuals, for-profit companies with up to three employees and
non-profits; larger companies need a company licence. Whoever renders the
videos for an organisation should check that licence against the
organisation first. The rendered MP4, GIF and PNG files are ordinary media
files. Remotion and React are development tools here;
nothing from `node_modules` is shipped with the app or committed to the
repository.

Versions are pinned exactly in `package.json` and locked in
`package-lock.json`: remotion, @remotion/cli and @remotion/fonts 4.0.533,
react and react-dom 19.2.8, typescript 5.9.3, @types/react 19.2.18.
