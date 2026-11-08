# Bundled audio: source, license and reproduction notes

These four recordings provide a small, offline source for compatible Automatic
sound requests. They are real third-party recordings. They retain **Creative
Commons Attribution 4.0 International** licensing; the application code's license
does not replace the audio licenses. Preserve the credits when redistributing
these files, and include the selected recording's attribution in a published
video description. No artist or licensor endorsement is implied.

Full license: [CC-BY-4.0.txt](CC-BY-4.0.txt), also available at
<https://creativecommons.org/licenses/by/4.0/>. The license permits commercial
reuse, redistribution and adaptation with attribution, a license link and an
indication of changes.

`catalog.json` records the exact final bytes, duration and SHA-256; the fetched
pre-edit source SHA-256; pinned retrieval URLs and Git blob identifiers; primary
license evidence; selection tags; and edit-aware attribution for each recording.
Its `original_sha256` identifies the source bytes used for the transformation,
not an independently certified publisher checksum. The catalogue records
`content_id: "unknown"`; a Creative Commons license is not a promise that a
platform will never issue a claim.

## Included recordings

| Local file | Creator and recording | Duration | Audio bytes |
| --- | --- | ---: | ---: |
| `music/cylinder-five-45s.ogg` | Chris Zabriskie, “Cylinder Five” from *Cylinders* | 45.000 s | 486,690 |
| `music/cylinder-one-45s.ogg` | Chris Zabriskie, “Cylinder One” from *Cylinders* | 45.000 s | 537,411 |
| `sfx/book-page-turn.ogg` | Oculus VR, LLC, *Oculus Audio Pack 01*: book page turns | 1.740667 s | 21,961 |
| `sfx/small-whoosh.ogg` | Oculus VR, LLC, *Oculus Audio Pack 01*: small whoosh | 0.189333 s | 6,221 |

Total audio size: **1,052,283 bytes**. All four files are Ogg Vorbis at 48 kHz;
music is stereo and effects are mono. The music files have no detected leading
silence at −45 dBFS. Natural internal phrase gaps remain. The page-turn excerpt
starts at the original recording's first audible paper movement. The whoosh is
the upstream short recording, with approximately 28 ms of quiet onset.

The artist's track pages identify *Cylinders* as ambient, electronic,
instrumental music. Tags such as `calm`, `minimal` and `sparse` are catalogue
selection descriptions, not claims about a particular acoustic instrument.
The catalogue does not claim verified piano or orchestral instrumentation, and
does not supply rain, alarm or vocal recordings. Specific requests outside these
recorded families require another matching source; they must not be silently
relabeled as one of these files.

## Chris Zabriskie music

Primary album and licensing statements, checked 2026-11-08:

- <https://chriszabriskie.com/cylinders/>
- <https://chriszabriskie.com/use/>
- <https://chriszabriskie.bandcamp.com/track/cylinder-five>
- <https://chriszabriskie.bandcamp.com/track/cylinder-one>

The composer grants CC BY 4.0 use of his music, including commercial video use,
and supplies a credit format. Both track pages link to CC BY 4.0 and label the
album instrumental. The license basis is these statements from the composer,
not the license of a repository that redistributes a copy.

### Credits

“Cylinder Five” by Chris Zabriskie, from *Cylinders* (2014), © 2014 Chris Zabriskie.
<https://chriszabriskie.com/cylinders/>. Licensed under Creative Commons
Attribution 4.0: <https://creativecommons.org/licenses/by/4.0/>. Excerpted,
crossfaded into a loop, level reduced and transcoded for use in the soundtrack.

“Cylinder One” by Chris Zabriskie, from *Cylinders* (2014), © 2014 Chris Zabriskie.
<https://chriszabriskie.com/cylinders/>. Licensed under Creative Commons
Attribution 4.0: <https://creativecommons.org/licenses/by/4.0/>. Excerpted,
crossfaded into a loop, level reduced and transcoded for use in the soundtrack.

### Acquisition provenance

Direct HTTPS downloads from the artist's host timed out in the build
environment. The recordings were obtained through public, pinned Git
redistributions; only the required audio blobs were fetched. No paid download,
login, private endpoint or scraping bypass was used.

- **Cylinder Five:** repository `ManuelR-T/paperplay`, commit
  `1195c8d6bd3c545ee1ac5ffd1262c74e984eaf05`, file
  `demo-video/public/audio/cylinder-five.m4a`, Git blob
  `b5ecaaf6e065dad785d074017791c1d393e24996`. The accompanying
  [recording-specific credits](https://github.com/ManuelR-T/paperplay/blob/1195c8d6bd3c545ee1ac5ffd1262c74e984eaf05/demo-video/public/audio/CREDITS.md)
  identify the artist's original download as
  <https://audio.chriszabriskie.com/cylinders/cylinders-05.m4a>.
  The received file is 3,529,100 bytes and 173.592381 seconds; source SHA-256
  `6bfc484b9c8e60a2b162112086224ce052e97265f77de540d3a5587c9eff54a1`.
- **Cylinder One:** repository `psyinf/berserker_assets`, commit
  `f682f9bface246c4e20d28c1f1b14e7b7b8bb3af`, file
  `sound/music/Chris Zabriskie - Cylinders/Chris Zabriskie - Cylinders - 01 Cylinder One.mp3`,
  Git blob `c8a3a330fbd50f0f63cf4b5560da208343a8f441`. The recording's ID3
  metadata names Chris Zabriskie, *Cylinders*, track 1, “Cylinder One”, and the
  artist's Bandcamp site. The received file is 7,186,894 bytes and 176.143673
  seconds; source SHA-256
  `75fc66b45b0934aeff6fe2d7d01b8826a5c6045824570982b491e65ce0d3b3b7`.

The original recordings remain available under their own license. Their raw
files are not needed at runtime; only the selected excerpts ship here.

### Reproducing the music edits

With the exact source bytes above, FFmpeg and NumPy:

1. Decode source seconds 12–58 to 46 seconds of 48 kHz stereo float32 PCM:
   `ffmpeg -ss 12 -t 46 -i SOURCE -map 0:a:0 -ar 48000 -ac 2 -f f32le pipe:1`.
2. Let `samples` be the resulting `(2208000, 2)` float32 array. Build a cyclic
   join from the final second to the first second, followed by the unchanged
   middle 44 seconds:

   ```python
   fade = numpy.linspace(0, 1, 48000, dtype=numpy.float32)[:, None]
   seam = samples[-48000:] * (1 - fade) + samples[:48000] * fade
   loop = numpy.concatenate((seam, samples[48000:-48000]))
   loop *= numpy.float32(10 ** (-3 / 20))
   ```

3. Encode the 45-second loop using
   `ffmpeg -f f32le -ar 48000 -ac 2 -i pipe:0 -map_metadata -1 -c:a libvorbis -q:a 4 OUTPUT.ogg`.

The edits use no synthesis or added instruments. FFmpeg/libvorbis versions or
Ogg serial numbers can change encoded file hashes when rebuilding, so update
the manifest deliberately after verifying regenerated audio. The committed
manifest verifies the exact shipped bytes, not every possible re-encoding.

## Oculus Audio Pack effects

Both effects come directly from the original licensor's
[Meta Open Sound repository](https://github.com/facebookincubator/Meta-open-sound),
commit `251c0be57d5d1b3242b91f53e0cde62ab1cb42d9`. The collection has its own
[CC BY 4.0 license](https://github.com/facebookincubator/Meta-open-sound/blob/251c0be57d5d1b3242b91f53e0cde62ab1cb42d9/corpora/oculus-audio-pack/LICENSE)
and [attribution statement](https://github.com/facebookincubator/Meta-open-sound/blob/251c0be57d5d1b3242b91f53e0cde62ab1cb42d9/corpora/oculus-audio-pack/ATTRIBUTION.md).
The repository's general CC0 license does **not** replace this collection's
CC BY 4.0 license. Its exact upstream credit is retained in
[META_ATTRIBUTION.md](META_ATTRIBUTION.md).

The indexed descriptions identify actual book page turns in `index/PAPR.json`
and a small, subtle whoosh in `index/WHSH.json`; there is no alarm, voice or
musical sting substituted for those effects.

### Credits and changes

“Oculus Audio Pack 01” by Oculus VR, LLC
(<https://developers.meta.com/vr/downloads/package/oculus-audio-pack-1/>), licensed
under CC BY 4.0 (<https://creativecommons.org/licenses/by/4.0/>). Files renamed
to the UCS naming convention; OGG previews transcoded from the original WAVs.

- **Book page turn:** source
  `corpora/oculus-audio-pack/ogg/PAPRHndl_BookPageTurns_OS_01.ogg`, Git blob
  `6d1b1cc552c7e0e79006c6aaefcf5d406b13ba00`, SHA-256
  `5fb5bdd48b9544728fa7599e1c3f9ead148fee2bc1a8f4e541c2baf06c842cd1`.
  The 3.967625-second original was excerpted with
  `atrim=start=0.10:end=1.85,asetpts=PTS-STARTPTS,afade=t=in:d=0.005,afade=t=out:st=1.73:d=0.02`
  and re-encoded using `-map_metadata -1 -c:a libvorbis -q:a 4 -ac 1`.
- **Small whoosh:** source
  `corpora/oculus-audio-pack/ogg/WHSH_WhooshSmall_OS_01.ogg`, Git blob
  `fc831db894c08fb5d543759da1d4c4e0b1101679`, SHA-256
  `606f8d0b2b022f1470466e250613bd067ccff7697d58a2518661d1e5eaa8f153`.
  Its Ogg bytes were copied unchanged; only the local filename differs.

The soundtrack mixer can further adjust each recording's level and timing.
The selected recording's manifest credit accompanies those uses.
