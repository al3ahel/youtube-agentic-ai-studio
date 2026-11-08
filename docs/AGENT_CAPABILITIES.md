# Agent capabilities and visual development contract

YAAS uses bounded specialist agents around a deterministic media pipeline. A model chooses content and supported creative options; Python validators and the local renderer decide what can run. This document is a repository guide for maintainers, not a separately installed agent skill.

## Runtime responsibilities

| Component | Inputs and authority | Output | Validation and fallback |
| --- | --- | --- | --- |
| Researcher — `agents/researcher.py` | Selected channel description/preset, requested topic or topic-selection context, retrieved background excerpts. May select an angle and supported key points. | Research JSON, actual source references, and the distinction between dated news evidence and background material. | Requires a nonempty topic/title and typed key-point/tag lists. Missing source evidence stays missing; the built-in Wikipedia/arXiv adapter is not a live newsroom. If no text provider succeeds, research fails visibly. |
| Scriptwriter — `agents/scriptwriter.py` | Research, channel tone, format, target length. May write spoken sections and descriptive image queries. | Script JSON with stable normalized section IDs, narration and estimated durations. | Checks section count and narration size/content; computes duration estimates from words. Estimates are never labeled measured. Provider failure stops writing rather than inventing a completed script. |
| Script editor — `agents/script_editor.py` | Original sections, source material, profile and local editorial checks. May revise general narration within the original subject and length; news receives suggestions while its narration stays unchanged. | Script plus `quality_review`, including whether a rewrite was accepted or only suggestions were supplied. | Preserves IDs/order; rejects new numbers or quotations, changed negation/uncertainty terms, invalid structure, excessive length changes and a lower local rubric score. On unavailable/invalid AI output, retains the original. These checks cannot prove semantic equivalence or factual truth and do not predict views. |
| Visual director — `agents/visual_director.py`, `agents/art_direction.py` | Script, measured timeline, mode, photo policy, trusted media and an optional edit request. May choose supported templates, labels, composition, look and rhythm. | Validated `scene_plan.json`: scene identities, measured boundaries, beats, media references and creative direction. | Strict JSON/schema and plan validation; numbers must come from source material and charts need actual data. Missing/invalid AI uses deterministic local direction/templates. It cannot invent local files, rewrite narration timing, or execute generated HTML/JS. |
| Sound director — `agents/audio_director.py` | Script, measured sections, channel preset/intensity, compatible included recordings and the selected profile's audio library. May choose a music bed or ambience and a bounded number of purposeful accents. | Sound-design plan, privately resolved local recordings and exact attribution metadata. | Validates IDs and event count; the app resolves section IDs to measured times. Library access remains profile-bound. Included assets are hash/size/duration/license checked; other requests use bounded licensed search. Missing recordings are omitted with a warning; conservative local choices remain available when AI fails. |
| Optional frame critic — `apis/openrouter.py:review_frames` | Bounded samples of the rendered video and a visual-review request. May report visible readability, overlap, clipping or contrast issues. | Review status, issue list and nullable `passed`. | Validates image bounds and a strict response schema. Disabled, unavailable and Local-only results are not passes. It does not hear sound, verify facts, inspect every frame or rewrite the project. |

The pipeline worker in `studio/runner.py` owns execution, cancellation, project ownership and reuse. Narration/timeline code owns the measured clock. `video/renderers/html.py` validates and prepares local assets, invokes the pinned renderer and verifies the resulting media. Mixers and `video/audio_master.py` apply and measure sound. These are executable capabilities, not model instructions.

## What visual direction can change

The renderer supports `flow`, `comparison`, `timeline`, `chart`, `explain`, `text` and `footage` scenes. The bounded art-direction catalogue in `agents/art_direction.py` defines:

- Looks: `editorial`, `blueprint`, `studio`, `botanical`, `poster`.
- Rhythms: `calm`, `measured`, `brisk`.
- Compositions: `default`, `split`, `stack`, `focus`, where the scene template supports them.

The saved `visual_direction` has its own version and rationale. Scene-level choices are validated against the same catalogue. A change must reach the renderer payload and the actual HTML/CSS/GSAP behavior to count as a capability. A rationale or `visual_goal` alone does not animate anything.

`agents/object_direction.py` grounds each new illustrated subject in its source
section. Scenes opt in with `graphics_version: 1` and allowlisted item `symbol`
values; `templates/objects.js` implements the actual vector actors and movement.
Supported subjects include census/undercount, people, mining, solar, battery,
circuit, water, plant, network, trade, conflict, document, orbit and buildings.
Unknown subjects remain labeled abstract diagrams. These are qualitative drawings,
not inferred statistics, accurate maps or arbitrary generated character scenes.
New plans and explicit rebuilds use them; old plans preserve their saved layout.

Motion defaults to graphics without project photos; Hybrid and the explicit photo-animation workflow can use trusted media. Existing photo-based projects retain their saved policy. A motion instruction is not permission to fetch arbitrary URLs, create a fabricated photograph or replace the source narration.

An edit's success means the requested supported fields changed, while its protected fields stayed intact. A different template is insufficient evidence that an unrelated request was carried out. Unsupported requests need an actionable explanation. Motion-only changes must be included in visible-change detection, and the regenerated preview must correspond to the new plan revision.

## Which skills help

The repository pins **Hyperframes 0.9.19**, GSAP 3.15.1 and local dependencies in `renderers/hyperframes/package.json` / `package-lock.json`. After `npm ci --prefix renderers/hyperframes`, the pinned package already includes the following guidance. These are exact repository-relative installed paths; do not commit `node_modules` or install a second skill bundle merely to read them.

| Guidance | Bundled source path |
| --- | --- |
| Core render contract | `renderers/hyperframes/node_modules/hyperframes/dist/skills/hyperframes/SKILL.md` |
| CLI and verification loop | `renderers/hyperframes/node_modules/hyperframes/dist/skills/hyperframes-cli/SKILL.md` |
| Motion craft and deterministic mechanisms | `renderers/hyperframes/node_modules/hyperframes/dist/skills/hyperframes/references/motion.md` |
| Explanatory scenes | `renderers/hyperframes/node_modules/hyperframes/dist/skills/hyperframes/references/types/explainer.md` |
| Frame and motion review | `renderers/hyperframes/node_modules/hyperframes/dist/skills/hyperframes/references/review.md` |
| Media sources and placement | `renderers/hyperframes/node_modules/hyperframes/dist/skills/media-use/SKILL.md` |
| Skill/package lifecycle | `renderers/hyperframes/node_modules/hyperframes/dist/skills/hyperframes/references/skill-lifecycle.md` |

Useful guidance for this pipeline is concrete:

- Build a diagram progressively when narration names its parts; ground labels and numbers in the supplied source.
- Choose a visual relationship that explains the content. Repeated bullet slides, identical card rows and identical fade/slide arrivals do not become explanatory animation just because they move.
- Vary motion by purpose. Let text settle, preserve readable holds, and use movement to direct attention. Calm/ASMR material should not inherit promotional-film speed or sound defaults.
- Keep each rendered frame a deterministic function of timeline time. Use the registered paused timeline, stable IDs and local assets; avoid wall-clock timers, unseeded randomness and render-time network fetches.
- Do not animate clip visibility or let CSS and GSAP compete for the same transform. Check the exact mechanism against the pinned render contract before extending a template.
- Check structure, inspect scene/cut frames and inspect motion between those frames. A passing schema or static screenshot cannot establish motion quality. Measured loudness is not a listening review.

These are development references. The bundled media skill also describes HeyGen sign-in, credits and optional services; that does not enable or authorize them in YAAS. The user's free/local requirement and YAAS's own provider adapters govern acquisition. Runtime models do not execute the CLI workflows, install skills, run package updates or ingest the whole third-party skill as authority.

Versioned, repository-owned director prompts are useful for examples, profile tone and edit intent. Keep them paired with their schema, renderer behavior and regression cases. Installing a personal coding-agent skill does not automatically load it into `gemini_client.generate`, add a JSON field, or teach the renderer a new primitive.

## Free and local inference boundaries

`agents/gemini_client.py` is the shared text entry point; `apis/openrouter.py:plan_json` is the strict visual-JSON boundary.

- **Local-only (`LLM_ROUTE=local`):** text and visual planning use the validated loopback Ollama adapter. It requires evidence of an installed local model, rejects cloud aliases, does not pull models and bounds request size, time and cancellation. Remote frame review is unavailable. A local failure never triggers hosted inference.
- **Auto:** an explicitly configured local model is tried first. Other text routes use configured Gemini/Groq accounts and verified free OpenRouter choices. Provider limits remain real; quota failure does not upgrade a plan or select a paid OpenRouter model.
- **OpenRouter:** free selection, schema support where needed, conservative local request budgets and response validation apply at the adapter boundary. Direct calls cannot bypass Local-only. The retired unauthenticated Pollinations text endpoint is not a fallback.
- **Groq compatibility:** legacy `llama-3.3-70b-versatile` / `llama-3.1-8b-instant` preferences resolve to `openai/gpt-oss-120b` / `openai/gpt-oss-20b`. Other explicit choices are preserved. The adapter logs a migration without rewriting the user's saved settings.

Hosted free tiers require eligible accounts and available quota; the app cannot infer an account's billing arrangement from its key. Local-only describes the inference boundary, not an offline switch for research, licensed-media search, narration services or YouTube. Automatic audio first checks compatible included CC BY 4.0 recordings, then uses the bounded keyless Wikimedia adapter for other requests. Its source matcher preserves specific semantic constraints and all applied credits. It does not scrape Pixabay music or presume that an unrelated service's audio API is free for commercial use.

## Adding a visual capability end to end

1. **Define the actual behavior.** Name the supported operation, its parameters and its meaning for each format/profile. Decide what is protected: section identity, narration audio, measured start/duration, other scenes, media ownership and publication metadata. An unsupported cinematic request needs a truthful response, not an unrelated fallback change.
2. **Extend the contract.** Add bounded enums/fields in the director schema and `validate_plan`; add normalization/validation in `video/renderers/html.py`. Preserve old saved plans with explicit defaults. Extend the saved direction version if semantics change incompatibly.
3. **Implement the renderer.** Update `renderers/hyperframes/templates/scene.js` and `scene.css` using the paused, seekable timeline. Implement landscape and portrait behavior, meaningful timing, readable holds and captions. Model output stays data; file paths still come from trusted adapters.
4. **Teach both planning paths.** Update the AI prompt/examples and deterministic fallback to choose the capability for a reason. Keep variation stable across rerenders. Add explicit edit parsing where a supported instruction must override an optional model response.
5. **Prove the requested edit.** Include the new renderable fields in visible-change comparison. Preserve untouched scenes/timing, report no change honestly, invalidate stale QA/media as appropriate and bind the new preview to the plan revision. A render-only review operation must reuse its edited plan and existing narration.
6. **Verify the concrete risk.** Add focused cases in `tests/test_visual_director.py` and `tests/test_html_renderer.py`; exercise seeks and appearance in `renderers/hyperframes/tests/render.test.mjs`. Check one representative landscape and portrait render, their decoded frames, frame rate/duration and the changed motion. Add a lifecycle test only when the feature changes edit/restart/reuse behavior.
7. **Document the supported request.** Update this capability map and `docs/MOTION_STUDIO.md` with a real example and its limits. Keep the pinned dependency version unless a deliberate, tested update is necessary. Do not make external skill installation, a paid provider or a model download an implicit requirement.

The detailed renderer contract is in `renderers/hyperframes/README.md`. Public API boundaries remain in `studio/visuals.py`, `studio/pipeline.py` and the profile-bound audio routes; prompt changes must not weaken those checks.
