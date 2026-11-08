/* YAAS composed scene worlds, version 2.
 * Original bounded SVG geometry. The saved plan supplies actors and intent,
 * never markup, code, quantities or a new clock. Every pose is a GSAP tween
 * on the registered narration timeline, including camera and consequences.
 */
(() => {
  "use strict";
  const NS = "http://www.w3.org/2000/svg";
  function svg(tag, attrs = {}, parent) {
    const node = document.createElementNS(NS, tag);
    Object.entries(attrs).forEach(([name, value]) => node.setAttribute(name, String(value)));
    if (parent) parent.append(node);
    return node;
  }
  const path = (parent, d, cls = "world-line", extra = {}) => svg("path", {d, class: cls, ...extra}, parent);
  const rect = (parent, x, y, width, height, cls = "world-solid", extra = {}) => svg("rect", {x, y, width, height, rx: 5, class: cls, ...extra}, parent);
  const circle = (parent, cx, cy, r, cls = "world-solid", extra = {}) => svg("circle", {cx, cy, r, class: cls, ...extra}, parent);
  const group = (parent, attrs = {}) => svg("g", attrs, parent);
  const at = (parent, x, y, scale = 1, attrs = {}) => group(parent, {transform: `translate(${x} ${y}) scale(${scale})`, ...attrs});
  const clamp = (value, min, max) => Math.max(min, Math.min(max, value));

  // Individual actors, not miniature scenes inside boxes. A world places
  // these actors in one shared space and supplies their causal interactions.
  function actor(parent, symbol, x, y, size, role = "support") {
    const placement = at(parent, x, y, size / 200, {class: `world-actor world-role-${role}`, "data-symbol": symbol, "data-role": role});
    const node = group(placement);
    const parts = {};
    if (symbol === "people" || symbol === "census" || symbol === "census-gap") {
      const positions = symbol === "people" ? [-62, 2, 65] : [-57, 47];
      positions.forEach((px, index) => {
        const person = at(node, px, index % 2 ? 10 : -15, .83, {class: index === positions.length - 1 && symbol === "census-gap" ? "world-missing" : ""});
        circle(person, 0, -62, 20, "world-ink-fill");
        path(person, "M-28 18 L-24-29 Q0-45 24-29 L28 18 Z", "world-solid");
        path(person, "M-13 13 L-18 70 M13 13 L18 70 M-24-21 L-38 22 M24-21 L38 22", "world-limb");
        if (!index) parts.hand = path(person, "M24-21 L48-5 L69-24", "world-limb");
      });
    } else if (symbol === "solar") {
      path(node, "M-82-63 L68-63 L96 55 L-108 55 Z", "world-panel");
      [-35, 5, 44].forEach(y0 => path(node, `M${-82 - (y0 + 63) * .22} ${y0} H${68 + (y0 + 63) * .24}`, "world-panel-grid"));
      [-45, 0, 45].forEach(x0 => path(node, `M${x0}-63 L${x0 * 1.3} 55`, "world-panel-grid"));
      path(node, "M-61 58 L-70 89 M53 58 L62 89 M-84 89 H78", "world-line");
      parts.energy = path(node, "M8-47 L-14-7 H7 L-9 31 L34-16 H12 Z", "world-energy");
    } else if (symbol === "battery") {
      rect(node, -72, -84, 144, 168, "world-body", {rx: 15});
      rect(node, -25, -98, 50, 14, "world-ink-fill");
      parts.charge = rect(node, -53, -57, 106, 121, "world-energy", {rx: 4});
      path(node, "M8-43 L-18 4 H4 L-5 40 L30-9 H9 Z", "world-cutout");
      path(node, "M-54 64 H54", "world-line");
    } else if (symbol === "plant") {
      path(node, "M-79 75 Q0 50 81 75 L63 96 H-61 Z", "world-earth-fill");
      parts.stem = group(node);
      path(parts.stem, "M0 75 Q-8 3 5-80", "world-stem");
      path(parts.stem, "M-2 27 Q-82 29-81-42 Q-24-50-2 27 Z", "world-leaf");
      path(parts.stem, "M1-6 Q78-4 79-69 Q24-76 1-6 Z", "world-leaf-secondary");
      path(parts.stem, "M-2 27 L-61-27 M1-6 L59-53", "world-fine-line");
    } else if (symbol === "building") {
      path(node, "M-85-53 L0-105 L86-53 Z", "world-roof");
      rect(node, -73, -53, 146, 146, "world-body", {rx: 0});
      path(node, "M73-53 L97-36 V93 H73 Z", "world-side");
      parts.windows = group(node, {"data-action-part": "powered-windows"});
      [-45, 17].forEach(xx => [-25, 21].forEach(yy => rect(parts.windows, xx, yy, 28, 26, "world-window", {rx: 1})));
      rect(node, -12, 52, 30, 41, "world-dark", {rx: 0});
      path(node, "M-95 96 H111", "world-line");
    } else if (symbol === "water") {
      parts.water = path(node, "M0-98 C-17-66-66-8-66 28 A66 66 0 0 0 66 28 C66-8 17-66 0-98 Z", "world-water-fill");
      path(node, "M-39 19 Q-51 55-13 67", "world-water-glint");
    } else if (symbol === "document") {
      path(node, "M-70-97 H33 L78-53 V97 H-70 Z", "world-paper");
      path(node, "M33-97 V-53 H78", "world-line");
      parts.records = group(node);
      [-28, 8, 44].forEach(yy => { rect(parts.records, -46, yy - 8, 13, 13, "world-solid", {rx: 1}); path(parts.records, `M-17 ${yy - 2} H48 M-17 ${yy + 10} H26`, "world-fine-line"); });
    } else if (symbol === "mining") {
      path(node, "M-93 85 L-36-91 H27 L80 85 M-73 27 H62 M-56-31 H46 M-36-91 L62 27 M27-91 L-73 27", "world-gantry");
      rect(node, -43, -104, 79, 23, "world-solid");
      parts.drill = group(node);
      path(parts.drill, "M-5-79 V74 M-18 57 L-5 88 L8 57 M-14 24 L4 35 M-14-1 L4 10", "world-drill");
    } else if (symbol === "circuit" || symbol === "network") {
      if (symbol === "circuit") {
        rect(node, -55, -55, 110, 110, "world-body", {rx: 9});
        rect(node, -28, -28, 56, 56, "world-solid", {rx: 3});
        [-32, 0, 32].forEach(p => path(node, `M${p}-55 V-88 M${p} 55 V88 M-55 ${p} H-88 M55 ${p} H88`, "world-line"));
      } else {
        const pts = [[-75, -65], [73, -44], [-53, 69], [81, 73], [0, 0]];
        [[0, 4], [1, 4], [2, 4], [3, 4], [0, 1], [2, 3]].forEach(([a, b]) => path(node, `M${pts[a]} L${pts[b]}`, "world-network-line"));
        pts.forEach(([xx, yy], index) => circle(node, xx, yy, index === 4 ? 24 : 18, index === 4 ? "world-solid" : "world-body"));
      }
      parts.signal = circle(node, 0, 0, symbol === "circuit" ? 12 : 9, "world-energy");
    } else if (symbol === "trade") {
      path(node, "M-84-31 L0-80 L84-31 V55 L0 100 L-84 55 Z", "world-body");
      path(node, "M0 15 L84-31 V55 L0 100 Z", "world-side");
      path(node, "M-84-31 L0 15 L84-31 M0 15 V100 M-40-57 L43-9 V26", "world-line");
    } else if (symbol === "conflict") {
      path(node, "M-100-54 L-19-16 L-55 7 L-6 48", "world-conflict-a");
      path(node, "M100-54 L19-16 L55 7 L6 48", "world-conflict-b");
      path(node, "M-27-53 L-5-82 L8-51 L33-75", "world-line");
    } else if (symbol === "orbit") {
      svg("ellipse", {cx: 0, cy: 0, rx: 108, ry: 42, transform: "rotate(-24)", class: "world-orbit"}, node);
      circle(node, 0, 0, 54, "world-body");
      path(node, "M-43-19 Q-9 9 46-11 M-38 27 Q2 3 48 20", "world-fine-line");
      parts.satellite = circle(node, 91, -36, 17, "world-solid");
    } else {
      // An explicitly abstract subject stays abstract: connected modules,
      // with no invented real-world device, statistic or scientific claim.
      path(node, "M-66-30 L0-70 L66-30 L66 40 L0 81 L-66 40 Z", "world-body");
      path(node, "M-66-30 L0 11 L66-30 M0 11 V81", "world-line");
      parts.module = path(node, "M-36-69 L0-91 L36-69 L0-47 Z", "world-solid");
    }
    return {node, placement, parts, x, y, size, symbol};
  }

  function build(context, api) {
    const {scene, stage, width: W, height: H} = context;
    const {portrait, timeline, beatTime, palette} = api;
    const staging = scene.staging;
    const start = scene.start, duration = scene.duration;
    stage.classList.add("world-stage");
    stage.dataset.world = staging.world;
    stage.dataset.action = staging.action;
    stage.dataset.camera = staging.camera;
    for (const [key, value] of Object.entries(palette || {})) stage.style.setProperty(`--world-${key}`, value);
    const art = svg("svg", {viewBox: `0 0 ${W} ${H}`, class: "world-art", role: "img", "aria-label": scene.visual_goal || scene.title}, stage);
    const title = svg("title", {}, art); title.textContent = scene.visual_goal || scene.title;
    const defs = svg("defs", {}, art);
    const clipId = `world-clip-${context.index}`;
    svg("rect", {x: 0, y: 0, width: W, height: H, rx: 0}, svg("clipPath", {id: clipId}, defs));
    const clipped = group(art, {"clip-path": `url(#${clipId})`});
    const camera = group(clipped, {class: "world-camera"});
    const backdrop = group(camera, {class: "world-environment"});
    const mechanisms = group(camera, {class: "world-mechanisms"});
    const cast = group(camera, {class: "world-cast"});
    const annotations = group(camera, {class: "world-annotations"});
    const tracks = [];
    const subjectIndex = scene.items.findIndex(item => item.role === "subject");
    const subject = scene.items[subjectIndex];
    const actors = [];
    const helpers = scene.items.map((item, index) => ({...item, index})).filter(item => item.index !== subjectIndex);
    // Existing measured reveal/action cues choose phase starts. They are not
    // replaced with fabricated word times or a separate animation clock.
    const cues = [...new Set(scene.beats.map(beat => beat.time).filter(value => value > .05 && value < duration * .9))].sort((a, b) => a - b);
    const firstCue = clamp(Math.min(...scene.items.map((_, index) => beatTime(context, index))), 0, duration - .00001);
    const secondCue = cues.find(value => value > firstCue + duration * .12) ?? firstCue + (duration - firstCue) * .48;
    const actionStart = firstCue;
    const resultStart = Math.min(duration, Math.max(actionStart, secondCue));
    const actionEnd = Math.max(resultStart, actionStart + (duration - actionStart) * .85);

    function tween(node, from, to, atTime, length, ease = "power2.inOut") {
      gsap.set(node, from);
      tracks.push(() => timeline.fromTo(node, from, {...to, duration: Math.max(0, Math.min(length, duration - atTime)), ease, immediateRender: false}, start + atTime));
    }
    function reveal(node, atTime, length = .4) { tween(node, {opacity: 0}, {opacity: 1}, atTime, Math.min(length, duration * .12), "power2.out"); }
    function draw(node, atTime, length, ease = "power2.inOut") {
      const total = node.getTotalLength();
      node.style.strokeDasharray = `${total} ${total}`;
      tween(node, {strokeDashoffset: total}, {strokeDashoffset: 0}, atTime, length, ease);
    }
    function caption(item, x, y, maxWidth, align = "middle") {
      maxWidth = Math.min(maxWidth, W * .8);
      const labelX = clamp(align === "middle" ? x - maxWidth / 2 : x, W * .1, W * .9 - maxWidth);
      const foreign = svg("foreignObject", {x: labelX, y, width: maxWidth, height: portrait ? 150 : 112, class: "world-label-box", "data-item": item.index ?? subjectIndex}, annotations);
      const label = document.createElement("div"); label.className = `world-label world-label-${item.role}`;
      label.dir = "auto"; label.textContent = item.label;
      foreign.append(label);
      // Protect the camera's complete bounded crop, using the rendered text
      // height rather than assuming every label occupies one line.
      foreign.setAttribute("y", String(clamp(y, H * .1, Math.max(H * .1, H * .92 - label.offsetHeight))));
      foreign.setAttribute("height", String(label.offsetHeight));
      return foreign;
    }
    function addActor(item, x, y, size) {
      const figure = actor(cast, item.symbol, x, y, size, item.role);
      figure.placement.dataset.item = String(item.index ?? subjectIndex);
      actors.push(figure); return figure;
    }
    function channel(x1, y1, x2, y2, delay = actionStart, result) {
      const bend = portrait ? `C${x1} ${(y1 + y2) / 2} ${x2} ${(y1 + y2) / 2}` : `C${(x1 + x2) / 2} ${y1} ${(x1 + x2) / 2} ${y2}`;
      const d = `M${x1} ${y1} ${bend} ${x2} ${y2}`;
      path(mechanisms, d, "world-channel-under");
      const route = path(mechanisms, d, "world-channel");
      draw(route, delay, Math.min(duration * .28, duration - delay), "none");
      const packet = circle(mechanisms, 0, 0, portrait ? 13 : 11, "world-energy");
      tween(packet, {x: x1, y: y1, opacity: 0}, {x: x1, y: y1, opacity: 1}, delay, .08);
      const travelStart = Math.min(duration, delay + .08);
      const settle = Math.min(.3, duration * .1, Math.max(0, duration - travelStart) * .25);
      const travelLength = Math.max(0, Math.min(duration * .28, duration - travelStart - settle));
      const arrival = travelStart + travelLength;
      // Sample the actual authored channel, so the packet follows the route
      // rather than cutting diagonally across a curved relationship.
      tracks.push(() => {
        const total = route.getTotalLength(), segments = 16;
        for (let i = 1; i <= segments; i++) {
          const point = route.getPointAtLength(total * i / segments);
          timeline.to(packet, {x: point.x, y: point.y, duration: travelLength / segments, ease: "none"},
            start + travelStart + (i - 1) * travelLength / segments);
        }
        timeline.to(packet, {opacity: 0, duration: Math.min(.05, duration - arrival)}, start + arrival);
      });
      if (result) result(arrival);
      return route;
    }
    function quietGrid() {
      const gap = portrait ? 105 : 120;
      for (let x = W * .07; x <= W * .94; x += gap) path(backdrop, `M${x} ${H * .08} V${H * .9}`, "world-grid");
      for (let y = H * .08; y <= H * .9; y += gap) path(backdrop, `M${W * .07} ${y} H${W * .94}`, "world-grid");
      path(backdrop, `M${W * .07} ${H * .92} H${W * .93}`, "world-baseline");
    }

    function generic() {
      quietGrid();
      const count = scene.items.length;
      const stacked = !portrait && context.composition === "stack";
      const mainSize = Math.min(W * (portrait ? .54 : .3), H * (portrait ? .36 : stacked ? .42 : .56));
      // One persistent workspace. Subject occupies the foreground; related
      // actors have named roles and share a route, assembly or comparison.
      const main = {x: W * (portrait || stacked ? .5 : count > 1 ? .36 : .5), y: H * (portrait ? .28 : stacked ? .27 : .4)};
      const primary = addActor({...subject, index: subjectIndex}, main.x, main.y, mainSize);
      caption({...subject, index: subjectIndex}, main.x, main.y + mainSize * .6, Math.min(W * (portrait ? .72 : .35), 640));
      const locations = portrait || stacked
        ? (helpers.length === 1 ? [[.5, .75]] : helpers.length === 2 ? [[.26, .75], [.74, .75]] : [[.21, .74], [.5, .74], [.79, .74]])
        : (helpers.length === 1 ? [[.77, .45]] : helpers.length === 2 ? [[.75, .23], [.75, .65]] : [[.76, .17], [.76, .44], [.76, .71]]);
      let previousSupport = primary;
      helpers.forEach((item, i) => {
        const [u, v] = locations[i];
        const size = Math.min(mainSize * (item.role === "context" ? .47 : .64), helpers.length > 2 ? W * (portrait ? .23 : .14) : W * .34, !portrait && helpers.length > 2 ? H * .2 : H);
        const figure = addActor(item, W * u, H * v, size);
        const label = caption(item, W * u, H * v + size * .57, W * (portrait || stacked ? (helpers.length === 1 ? .66 : helpers.length === 2 ? .42 : .28) : .29));
        const cue = clamp(beatTime(context, item.index), 0, duration - .00001);
        if (staging.action === "assemble") {
          const dx = (W * u - main.x) * .16, dy = (H * v - main.y) * .16;
          tween(figure.node, {x: dx, y: dy, opacity: .2}, {x: 0, y: 0, opacity: 1}, cue, duration * .22);
          reveal(label, cue);
        } else if (staging.action === "reveal") {
          tween(figure.node, {opacity: .18}, {opacity: 1}, cue, duration * .2);
        }
        if (staging.action !== "compare" && item.role !== "context") {
          // Connect follows the ordered support actors: solar → battery →
          // home, rather than silently replacing that chain with a star.
          const source = staging.action === "connect" ? previousSupport : primary;
          const dx = figure.x - source.x, dy = figure.y - source.y, length = Math.hypot(dx, dy);
          const origin = {x: source.x + dx / length * source.size * .55, y: source.y + dy / length * source.size * .55};
          const destination = {x: figure.x - dx / length * size * .56, y: figure.y - dy / length * size * .56};
          channel(origin.x, origin.y, destination.x, destination.y, cue, arrived => {
            if (figure.parts.charge) tween(figure.parts.charge, {scaleY: .12, transformOrigin: "50% 100%"}, {scaleY: 1}, arrived, duration * .15);
            if (figure.parts.signal) tween(figure.parts.signal, {scale: .3, transformOrigin: "50% 50%", opacity: .4}, {scale: 1, opacity: 1}, arrived, duration * .1);
            if (figure.parts.windows) tween(figure.parts.windows, {opacity: .15}, {opacity: 1}, arrived, duration * .1);
          });
          previousSupport = figure;
        }
      });
      if (staging.action === "compare" && helpers.length) {
        const divider = portrait ? `M${W * .14} ${H * .6} H${W * .86}` : `M${W * .58} ${H * .12} V${H * .88}`;
        const rule = path(mechanisms, divider, "world-comparison-rule"); draw(rule, actionStart, duration * .2);
      }
      if (primary.parts.module) tween(primary.parts.module, {y: -30, opacity: .3}, {y: 0, opacity: 1}, actionStart, duration * .4);
      if (primary.parts.stem) tween(primary.parts.stem, {scaleY: .35, transformOrigin: "50% 100%"}, {scaleY: 1}, actionStart, actionEnd - actionStart);
      if (primary.parts.charge) tween(primary.parts.charge, {scaleY: .1, transformOrigin: "50% 100%"}, {scaleY: 1}, actionStart, actionEnd - actionStart);
      if (primary.parts.drill) tween(primary.parts.drill, {y: -30}, {y: 12}, actionStart, actionEnd - actionStart);
      if (primary.parts.records) reveal(primary.parts.records, resultStart, duration * .2);
      if (primary.parts.energy) tween(primary.parts.energy, {opacity: .15}, {opacity: 1}, actionStart, duration * .22);
      if (primary.parts.satellite) tween(primary.parts.satellite, {x: 0, y: 0}, {x: -80, y: 54}, actionStart, actionEnd - actionStart);
      if (primary.parts.signal) tween(primary.parts.signal, {scale: .3, transformOrigin: "50% 50%", opacity: .3}, {scale: 1, opacity: 1}, actionStart, duration * .3);
      // With one actor, reveal/assemble exposes its actual component structure
      // instead of adding unrelated actors just to fill an empty scene.
      if (count === 1 && !Object.keys(primary.parts).length) tween(primary.node, {opacity: .25, scale: .86, transformOrigin: "50% 50%"}, {opacity: 1, scale: 1}, actionStart, duration * .45);
      return {focus: {x: main.x, y: main.y}, destination: helpers.length ? actors[actors.length - 1] : primary};
    }

    function flood() {
      const ground = H * .78;
      // A qualitative environmental cross-section, never a geographic map or
      // a prediction of flood depth. Water physically changes the scene.
      path(backdrop, `M0 ${ground} Q${W * .28} ${ground - 12} ${W * .53} ${ground - 10} L${W * .9} ${H * .47} L${W} ${H * .48} V${H} H0 Z`, "world-land");
      path(backdrop, `M0 ${ground + H * .1} Q${W * .38} ${ground + H * .06} ${W} ${H * .7}`, "world-stratum-line");
      path(backdrop, `M0 ${ground + H * .18} Q${W * .48} ${ground + H * .11} ${W} ${H * .81}`, "world-stratum-line");
      const water = group(mechanisms, {"data-action-part": "water-level"});
      path(water, `M-${W * .05} 0 Q${W * .08}-18 ${W * .21} 0 T${W * .48} 0 T${W * .75} 0 T${W * 1.02} 0 V${H} H-${W * .05} Z`, "world-water-mass");
      [32, 74, 120].forEach(y => path(water, `M${W * .03} ${y} Q${W * .16} ${y - 12} ${W * .31} ${y} T${W * .61} ${y} T${W * .94} ${y}`, "world-water-contour"));
      tween(water, {y: H * .85}, {y: H * .59}, actionStart, actionEnd - actionStart);
      const mainSize = Math.min(W * .48, H * (portrait ? .46 : .42));
      const floating = staging.action === "float" && subject.symbol === "building";
      const mainX = W * .39, mainY = floating ? H * .85 - mainSize * .48 : H * .43;
      let primary;
      if (subject.symbol === "water") {
        water.classList.add("world-actor", "world-role-subject");
        water.dataset.symbol = "water"; water.dataset.role = "subject"; water.dataset.item = String(subjectIndex);
        primary = {node: water, parts: {}, x: mainX, y: H * .7, size: mainSize};
      } else primary = addActor({...subject, index: subjectIndex}, mainX, mainY, mainSize);
      caption({...subject, index: subjectIndex}, W * .38, H * .09, W * .61);
      if (floating) {
        // Only the explicitly grounded float action lifts a building. Water
        // and home use the same displacement, so their contact stays visible.
        primary.node.dataset.actionPart = "floating-home";
        tween(primary.node, {y: 0}, {y: -H * .26 * 200 / mainSize}, actionStart, actionEnd - actionStart);
        const base = path(cast, `M${mainX - mainSize * .56} ${H * .85} H${mainX + mainSize * .56} L${mainX + mainSize * .47} ${H * .85 + 16} H${mainX - mainSize * .47} Z`, "world-floating-base");
        tween(base, {y: 0}, {y: -H * .26}, actionStart, actionEnd - actionStart);
      }
      const helperLocations = portrait ? [[.78, .56], [.74, .26], [.2, .82]] : [[.76, .32], [.73, .68], [.15, .73]];
      helpers.forEach((item, i) => {
        const [x, y] = helperLocations[i];
        if (item.symbol === "water" && item.role === "context") {
          const label = caption(item, W * .25, H * .82, W * .43); reveal(label, resultStart);
          return;
        }
        const size = Math.min(mainSize * (subject.symbol === "water" && item.symbol === "building" ? .85 : .56), W * .36);
        const helperY = ["building", "people"].includes(item.symbol) ? H * .57 - size * .46 : H * y;
        const figure = addActor(item, W * x, helperY, size);
        caption(item, W * x, helperY + size * .56, W * .34);
        if (staging.action === "adapt" && item.role === "support") {
          // Only named supporting technology is assembled. There is no
          // automatic dam, floating house, rescue boat or invented solution.
          tween(figure.node, {opacity: .22, y: -28}, {opacity: 1, y: 0}, resultStart, duration * .2);
          channel(mainX + mainSize * .4, mainY, figure.x - size * .54, figure.y, resultStart);
        }
      });
      if (staging.action === "adapt") {
        const revision = path(mechanisms, `M${mainX - mainSize * .63} ${mainY + mainSize * .56} V${mainY - mainSize * .58} H${mainX + mainSize * .59}`, "world-engineering-line");
        draw(revision, resultStart, duration * .2);
        if (primary.parts.module) tween(primary.parts.module, {y: -35, opacity: .2}, {y: 0, opacity: 1}, resultStart, duration * .2);
        if (primary.parts.hand) tween(primary.parts.hand, {rotation: 12, svgOrigin: "24 -21"}, {rotation: -15}, resultStart, duration * .2);
      }
      return {focus: {x: mainX, y: H * .5}, destination: {x: W * .68, y: H * .57}};
    }

    function census() {
      quietGrid();
      const portraitLayout = portrait;
      const peopleX = W * (portraitLayout ? .3 : .23), peopleY = H * (portraitLayout ? .27 : .43);
      const paperX = W * (portraitLayout ? .58 : .7), paperY = H * (portraitLayout ? .66 : .47);
      const size = Math.min(W * .46, H * (portraitLayout ? .32 : .68));
      const peopleItem = scene.items.find(item => ["people", "census", "census-gap"].includes(item.symbol));
      const paperItem = scene.items.find(item => item.symbol === "document");
      // Census itself entails people and records; these are schematic actors,
      // not a literal count or a claim about a named population's size.
      const population = actor(cast, staging.action === "gap" ? "census-gap" : "people", peopleX, peopleY, size, peopleItem?.role || "support");
      const ledger = actor(cast, "document", paperX, paperY, size * 1.02, paperItem?.role || "support");
      population.placement.dataset.item = String(peopleItem ? scene.items.indexOf(peopleItem) : subjectIndex);
      ledger.placement.dataset.item = String(paperItem ? scene.items.indexOf(paperItem) : subjectIndex);
      actors.push(population, ledger);
      caption({...subject, index: subjectIndex}, portraitLayout ? W * .5 : W * .46, H * .06, W * .75);
      scene.items.forEach((item, index) => {
        if (index === subjectIndex) return;
        if (item === peopleItem) caption({...item, index}, peopleX, peopleY + size * .48, W * (portrait ? .5 : .3));
        else if (item === paperItem) caption({...item, index}, paperX, paperY + size * .57, W * (portrait ? .6 : .36));
        else {
          const figure = addActor({...item, index}, W * (portrait ? .82 : .47), H * (portrait ? .32 : .76), size * .36);
          caption({...item, index}, figure.x, figure.y + size * .21, W * .3);
        }
      });
      const paths = [];
      [0, 1, 2].forEach(index => {
        const px = peopleX + (index - 1) * size * .22;
        const py = peopleY + size * .29;
        const tx = paperX - size * .27, ty = paperY + (index - 1) * size * .18;
        const d = portraitLayout ? `M${px} ${py} V${paperY - size * .59} Q${px} ${ty} ${tx} ${ty}` : `M${peopleX + size * .48} ${peopleY + (index - 1) * size * .2} H${tx}`;
        const line = path(mechanisms, d, index === 2 && staging.action === "gap" ? "world-unrecorded-route" : "world-channel");
        if (index === 2 && staging.action === "gap") { line.style.strokeDasharray = "9 12"; reveal(line, resultStart); }
        else { draw(line, actionStart + index * duration * .14, duration * .19); paths.push(line); }
      });
      const rows = [...ledger.parts.records.children];
      rows.forEach((row, index) => {
        const rowIndex = Math.floor(index / 2);
        if (rowIndex === 2 && staging.action === "gap") {
          row.classList.add("world-missing"); tween(row, {opacity: 0}, {opacity: .55}, resultStart, duration * .17);
        } else reveal(row, actionStart + rowIndex * duration * .14 + duration * .12, duration * .12);
      });
      return {focus: {x: paperX, y: paperY}, destination: ledger};
    }

    function mining() {
      const horizon = H * .46, mineX = W * (portrait ? .34 : .27);
      path(backdrop, `M0 ${horizon + H * .03} L${W * .13} ${horizon - H * .06} L${W * .25} ${horizon} L${W * .43} ${horizon - H * .03} L${W * .58} ${horizon + H * .02} L${W * .8} ${horizon - H * .02} L${W} ${horizon} V${H * .94} H0 Z`, "world-land");
      path(backdrop, `M0 ${H * .65} Q${W * .23} ${H * .58} ${W * .45} ${H * .68} T${W} ${H * .61} V${H * .94} H0 Z`, "world-stratum-fill");
      path(backdrop, `M0 ${H * .82} Q${W * .24} ${H * .73} ${W * .5} ${H * .82} T${W} ${H * .78} V${H * .94} H0 Z`, "world-deep-fill");
      const size = Math.min(W * .42, H * .42);
      const rig = actor(cast, "mining", mineX, horizon - size * .35, size, subject.symbol === "mining" ? subject.role : "context");
      actors.push(rig);
      if (subject.symbol !== "mining") {
        const figure = addActor({...subject, index: subjectIndex}, W * .7, H * .22, size * .62);
        caption({...subject, index: subjectIndex}, figure.x, figure.y + size * .36, W * .48);
      } else caption({...subject, index: subjectIndex}, W * .7, H * .13, W * .39);
      const shaft = path(mechanisms, `M${mineX} ${horizon} V${H * .73}`, "world-shaft");
      if (staging.action === "extract") draw(shaft, actionStart, duration * .33);
      const seam = path(mechanisms, `M${W * .09} ${H * .75} L${W * .23} ${H * .7} L${W * .36} ${H * .75} L${W * .53} ${H * .7} L${W * .66} ${H * .75}`, "world-ore-seam");
      if (staging.action === "extract") {
        draw(seam, actionStart, duration * .28);
        const depth = (H * .73 - (horizon - size * .35)) * 200 / size - 88;
        tween(rig.parts.drill, {y: -25}, {y: depth}, actionStart, duration * .35);
      }
      const destination = {x: W * .78, y: H * .53};
      const conveyor = path(mechanisms, `M${mineX + W * .02} ${H * .71} L${destination.x} ${destination.y}`, "world-conveyor-line");
      reveal(conveyor, resultStart, duration * .1);
      const rock = path(cast, "M-20-7 L-8-23 L16-16 L24 9 L3 23 L-21 10 Z", "world-ore-piece", {"data-action-part": "extracted-ore"});
      tween(rock, {x: mineX + W * .02, y: H * .71, opacity: 0}, {x: mineX + W * .02, y: H * .71, opacity: 1}, resultStart, .1);
      tracks.push(() => timeline.to(rock, {x: destination.x, y: destination.y, rotation: 100, duration: Math.max(0, Math.min(duration * .3, duration - resultStart - .1)), ease: "power1.inOut"}, start + Math.min(duration, resultStart + .1)));
      const destinationSupport = helpers.find(item => item.symbol === "trade" || item.symbol === "building");
      if (destinationSupport) {
        const figure = addActor(destinationSupport, destination.x, H * .35, size * .47);
        caption(destinationSupport, figure.x, H * .63, W * .34);
      }
      helpers.filter(item => item !== destinationSupport).forEach((item, index) => {
        const x = W * (index ? .77 : .3), y = H * (index ? .22 : .8);
        if (item.symbol === "mining") caption(item, mineX, H * .05, W * .55);
        else {
          const figure = addActor(item, x, y, size * .28);
          caption(item, figure.x, y + size * .16, W * .34);
        }
      });
      return {focus: {x: mineX, y: H * .62}, destination};
    }

    const frame = ({flood, census, mining, generic}[staging.world] || generic)();
    // Named camera windows are derived from the interaction's real location,
    // with a bounded crop. Labels stay in the protected interior. Camera
    // movement explains a transfer/reveal; it never extends the voice clock.
    const maxZoom = context.rhythm === "calm" ? 1.04 : 1.08;
    const focus = frame.focus;
    const cameraPose = (point, zoom) => ({scale: zoom, x: clamp(W / 2 - point.x * zoom, W * (1 - zoom), 0), y: clamp(H / 2 - point.y * zoom, H * (1 - zoom), 0), transformOrigin: "0 0"});
    if (staging.camera === "detail") tween(camera, {scale: 1, x: 0, y: 0, transformOrigin: "0 0"}, cameraPose(focus, maxZoom), actionStart, Math.max(duration * .25, resultStart - actionStart));
    else if (staging.camera === "reveal") tween(camera, cameraPose(focus, maxZoom), {scale: 1, x: 0, y: 0}, actionStart, actionEnd - actionStart);
    else if (staging.camera === "follow") tween(camera, cameraPose(focus, maxZoom), cameraPose(frame.destination, maxZoom), resultStart, Math.max(.1, actionEnd - resultStart));
    // locked and wide deliberately retain a stable full-world view.
    const note = document.createElement("div"); note.className = "world-note"; note.textContent = "ILLUSTRATED MECHANISM · NOT TO SCALE"; stage.append(note);
    context.objectAnimation = () => tracks.forEach(schedule => schedule());
    context.worldActors = actors;
  }
  window.YAASWorlds = {build};
})();
