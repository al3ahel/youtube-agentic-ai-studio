# Renderer verification samples

These checked-in MP4s are copies of the final landscape and portrait verification
renders. **Each contains exactly 168 video frames: 14 seconds at 12 fps.**

| File | Dimensions | Duration | Frame rate | Video frames | Streams |
| --- | --- | --- | --- | --- | --- |
| [motion-landscape.mp4](motion-landscape.mp4) | 640 × 360 | 14 seconds | 12 fps | 168 | H.264 + AAC |
| [motion-portrait.mp4](motion-portrait.mp4) | 360 × 640 | 14 seconds | 12 fps | 168 | H.264 + AAC |

Seven two-second scenes cover `text`, `flow`, `comparison`, `timeline`, `chart`,
`explain` and `footage`. The picture is a locally generated raster, the footage is
an FFmpeg test pattern, and the chart uses explicitly synthetic values.
**Both clips contain audible synthetic test tones.** They verify encoding, media
handling and audio presence; they do not demonstrate narration quality or alignment
to spoken words. No NASA photograph appears in these two clips.

[The landscape poster](../screenshots/motion-photo-demo.png) is extracted from the
same landscape MP4 at 7.25 seconds.

## Reproduce and verify

Use the supported Python environment, Node.js 22+, FFmpeg/ffprobe and an installed
Chrome/Chromium. Install the pinned renderer dependencies:

```bash
npm ci --prefix renderers/hyperframes
```

From the repository root, reproduce both 168-frame outputs:

```bash
python renderers/hyperframes/smoke.py --seconds-per-scene 2 --output renderers/hyperframes/.smoke/release-landscape
python renderers/hyperframes/smoke.py --portrait --seconds-per-scene 2 --output renderers/hyperframes/.smoke/release-portrait
```

Each command writes `motion_preview.mp4` in its output directory, alongside the
scene plan, generated assets and local HTML project. The checked-in samples were
copied directly from those outputs. Add `--prepare-only` to build the project
without encoding. No API keys, network image search or speech service are used.
`HYPERFRAMES_BROWSER_PATH` can select an existing compatible browser.

Count decoded video frames in each checked-in sample:

```bash
ffprobe -v error -count_frames -select_streams v:0 -show_entries stream=width,height,r_frame_rate,nb_read_frames -of json docs/samples/motion-landscape.mp4
ffprobe -v error -count_frames -select_streams v:0 -show_entries stream=width,height,r_frame_rate,nb_read_frames -of json docs/samples/motion-portrait.mp4
```

Both should report `r_frame_rate: "12/1"` and `nb_read_frames: "168"`.

## Separate optional photo demonstration

`renderers/hyperframes/demo.py` still uses the bundled NASA photograph to create
a separate 18-second, 24 fps silent photo demonstration. Running it replaces the
sample filenames and poster above with that different demonstration. Use
`smoke.py` above to reproduce the checked-in 168-frame verification clips.

The retained photograph's attribution is:

- **Photograph:** NASA / Johnson Space Center (JSC), Skylab above Earth.
- **NASA asset:** `sl4-143-4707` — [original source record](https://images.nasa.gov/details/sl4-143-4707).
- **Bundled file:** [skylab.jpg](skylab.jpg), originally retrieved through YAAS's NASA image adapter.
- **Usage information:** [NASA Images and Media Usage Guidelines](https://www.nasa.gov/nasa-brand-center/images-and-media/).
