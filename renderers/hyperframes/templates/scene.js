/* Fixed YAAS scene templates. Manifest text is data, never HTML or JavaScript. */
(() => {
  "use strict";
  const plan = JSON.parse(document.getElementById("scene-plan").textContent);
  const root = document.getElementById("composition");
  const canvas = document.getElementById("design-canvas");
  const portrait = plan.height > plan.width;
  const designWidth = portrait ? 1080 : 1920;
  const designHeight = portrait ? 1920 : 1080;
  const visualWidth = portrait ? 916 : 1720;
  const looks = new Set(["editorial", "blueprint", "studio", "botanical", "poster"]);
  const rhythms = new Set(["calm", "measured", "brisk"]);
  const compositions = new Set(["default", "split", "stack", "focus"]);
  const planLook = looks.has(plan.visual_direction?.look) ? plan.visual_direction.look : "legacy";
  const planRhythm = rhythms.has(plan.visual_direction?.rhythm) ? plan.visual_direction.rhythm : "measured";
  root.dataset.look = planLook;
  for (const [key, value] of Object.entries(plan.palette_colors || {})) root.style.setProperty(`--film-world-${key}`, value);
  root.classList.toggle("portrait", portrait);
  canvas.style.width = `${designWidth}px`;
  canvas.style.height = `${designHeight}px`;
  canvas.style.transform = `scale(${plan.width / designWidth})`;
  document.getElementById("film-title").textContent = plan.title;
  const mount = document.getElementById("scene-mount");
  const captionMount = document.getElementById("caption-mount");
  // Avoid automatic 2D/3D layer switches when a timeline is scrubbed backwards.
  gsap.config({force3D: false});
  const timeline = gsap.timeline({paused: true});
  const types = {
    flow: "How it works", comparison: "Two ways to see it", timeline: "The sequence",
    chart: "The numbers", explain: "Inside the idea", text: "The key idea", footage: "In the real world",
  };

  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function svgElement(tag, attrs = {}) {
    const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
    for (const [name, value] of Object.entries(attrs)) node.setAttribute(name, String(value));
    return node;
  }

  function box(node, x, y, width, height) {
    Object.assign(node.style, {left: `${x}px`, top: `${y}px`, width: `${width}px`});
    if (height !== undefined) node.style.height = `${height}px`;
    return node;
  }

  function label(text, tag = "h3") {
    const node = element(tag, "item-label", text);
    if (text.length > 55) node.classList.add("long-label");
    return node;
  }

  function titleClass(node, text) {
    if (text.length > 145) node.classList.add("very-long-title");
    else if (text.length > 78) node.classList.add("long-title");
  }

  function numberLabel(index) {
    return element("span", "node-index", String(index + 1).padStart(2, "0"));
  }

  function graph(stage, width, height) {
    const node = svgElement("svg", {viewBox: `0 0 ${width} ${height}`, "aria-hidden": "true"});
    node.classList.add("graph-links");
    stage.append(node);
    return node;
  }

  function link(graphNode, coordinates) {
    graphNode.append(svgElement("path", {d: coordinates, class: "graph-link-under"}));
    const path = svgElement("path", {d: coordinates, class: "graph-link"});
    graphNode.append(path);
    const length = path.getTotalLength();
    path.style.strokeDasharray = `${length} ${length}`;
    path.style.strokeDashoffset = String(length);
    return {node: path, length};
  }

  function registerItem(context, node, index, extra = {}) {
    node.dataset.item = String(index);
    context.items.push({node, ...extra});
    return node;
  }

  function flow(context) {
    if (context.composition !== "default") return arrangedCards(context, true);
    const {scene, stage, height, width} = context;
    const count = scene.items.length;
    if (!count) return;
    const vertical = portrait || context.imageLayout;
    const grid = context.imageLayout && count > 4;
    const lines = graph(stage, width, height);
    const rows = grid ? Math.ceil(count / 2) : count;
    const cardWidth = grid ? (width - 46) / 2 : vertical ? width - 40 : Math.min(380, (width - 50 * (count - 1)) / count);
    const cardHeight = vertical ? Math.min(180, (height - (rows - 1) * 22) / rows) : Math.min(260, height - 70);
    const spacing = vertical ? (height - cardHeight) / Math.max(1, rows - 1) : (width - cardWidth) / Math.max(1, count - 1);
    stage.classList.toggle("flow-grid", Boolean(grid));
    const positions = scene.items.map((item, index) => ({
      x: grid ? 8 + (Math.floor(index / 2) % 2 ? 1 - index % 2 : index % 2) * (cardWidth + 30)
        : vertical ? 20 : (count === 1 ? (width - cardWidth) / 2 : spacing * index),
      y: vertical ? (count === 1 ? (height - cardHeight) / 2 : spacing * (grid ? Math.floor(index / 2) : index)) : (height - cardHeight) / 2,
    }));
    positions.slice(0, -1).forEach((point, index) => {
      const next = positions[index + 1];
      const coordinates = grid && point.y === next.y
        ? (next.x > point.x ? `M${point.x + cardWidth} ${point.y + cardHeight / 2} L${next.x} ${next.y + cardHeight / 2}`
          : `M${point.x} ${point.y + cardHeight / 2} L${next.x + cardWidth} ${next.y + cardHeight / 2}`)
        : vertical
        ? `M${point.x + cardWidth / 2} ${point.y + cardHeight} L${next.x + cardWidth / 2} ${next.y}`
        : `M${point.x + cardWidth} ${point.y + cardHeight / 2} L${next.x} ${next.y + cardHeight / 2}`;
      context.links.push(link(lines, coordinates));
    });
    scene.items.forEach((item, index) => {
      const node = box(element("article", "node-card"), positions[index].x, positions[index].y, cardWidth, cardHeight);
      node.append(numberLabel(index), label(item.label));
      stage.append(registerItem(context, node, index));
    });
  }

  function comparison(context) {
    const {scene, stage} = context;
    const count = scene.items.length;
    stage.classList.add("comparison-stage");
    const columns = context.imageLayout ? Math.min(2, count) : portrait ? (count > 3 ? 2 : 1) : Math.min(count, count > 3 ? 3 : count);
    stage.style.gridTemplateColumns = `repeat(${Math.max(1, columns)}, minmax(0, 1fr))`;
    stage.style.gridTemplateRows = `repeat(${Math.max(1, Math.ceil(count / Math.max(1, columns)))}, minmax(0, 1fr))`;
    scene.items.forEach((item, index) => {
      const node = element("article", "comparison-card");
      node.append(element("div", "comparison-symbol"), numberLabel(index));
      if (item.value !== undefined) node.append(element("div", "comparison-value", item.value));
      node.append(label(item.label));
      if (count > 3 && !portrait) node.querySelector(".item-label").style.fontSize = "26px";
      stage.append(registerItem(context, node, index));
    });
  }

  function chronology(context) {
    const {scene, stage, height, width} = context;
    const count = scene.items.length;
    const vertical = portrait || context.imageLayout;
    const inset = vertical ? 36 : 28;
    const length = vertical ? height - 120 : width - 230;
    const line = element("div", "timeline-line");
    const draw = element("div", "timeline-draw");
    if (vertical) {
      Object.assign(line.style, {left: "36px", top: "35px", width: "2px", height: `${length}px`});
      Object.assign(draw.style, {left: "36px", top: "35px", width: "2px", height: `${length}px`, transformOrigin: "center top"});
    } else {
      Object.assign(line.style, {left: `${inset}px`, top: `${height / 2}px`, width: `${length}px`, height: "2px"});
      Object.assign(draw.style, {left: `${inset}px`, top: `${height / 2}px`, width: `${length}px`, height: "2px"});
    }
    stage.append(line, draw);
    context.draws.push({node: draw, property: vertical ? "scaleY" : "scaleX"});
    scene.items.forEach((item, index) => {
      const node = element("article", "timeline-node");
      const distance = count <= 1 ? 0 : index * length / (count - 1);
      box(node, vertical ? 36 : inset + distance, vertical ? 35 + distance : height / 2, vertical ? width - 80 : 240);
      const copy = element("div", "timeline-copy");
      if (!context.imageLayout || item.value !== undefined) copy.append(element("p", "item-value", item.value !== undefined ? item.value : String(index + 1).padStart(2, "0")));
      copy.append(label(item.label));
      if (vertical) Object.assign(copy.style, {left: "60px", top: "-21px"});
      else if (index % 2 === 0) node.classList.add("above");
      node.append(element("span", "timeline-dot"), copy);
      stage.append(registerItem(context, node, index));
    });
  }

  function numericChart(context) {
    const {scene, stage, height} = context;
    stage.classList.add("chart-stage");
    const values = scene.items.map(item => Number(item.value));
    const dense = values.length > 4;
    stage.classList.toggle("dense-chart", dense);
    const low = Math.min(0, ...values), high = Math.max(0, ...values);
    const span = high - low || 1;
    const leftInset = 14, plotWidth = portrait ? visualWidth - 155 : visualWidth - (dense ? 660 : 540);
    const zero = leftInset + (0 - low) / span * plotWidth;
    const step = Math.min(portrait ? 160 : 100, (height - 25) / Math.max(1, values.length));
    scene.items.forEach((item, index) => {
      const row = element("article", "chart-row");
      row.style.top = `${index * step}px`;
      row.append(label(item.label));
      const track = element("div", "bar-track");
      const grid = element("div", "bar-grid");
      grid.style.width = `${plotWidth + leftInset}px`;
      const baseline = element("div", "bar-zero");
      baseline.style.left = `${zero}px`;
      const end = leftInset + (Number(item.value) - low) / span * plotWidth;
      const bar = element("div", `bar-fill${item.value < 0 ? " negative" : ""}`);
      Object.assign(bar.style, {left: `${Math.min(end, zero)}px`, width: `${Math.abs(end - zero)}px`});
      const number = element("span", "bar-value", item.value);
      number.style.left = `${Math.max(end, zero) + 14}px`;
      track.append(grid, baseline, bar, number);
      row.append(track);
      stage.append(registerItem(context, row, index, {bar, number, value: Number(item.value)}));
    });
    const source = scene.source || [...new Set(scene.items.map(item => item.source).filter(Boolean))].join(" · ");
    if (source) stage.append(element("div", "chart-source", `Source: ${source}`));
  }

  function explanation(context) {
    if (context.composition !== "default") return arrangedCards(context, false);
    const {scene, stage, height} = context;
    if (!scene.items.length) return;
    if (context.imageLayout) {
      stage.classList.add("photo-facts");
      stage.style.gridTemplateColumns = scene.items.length > 4 ? "repeat(2, minmax(0, 1fr))" : "minmax(0, 1fr)";
      scene.items.forEach((item, index) => {
        const row = element("article", "photo-fact");
        row.append(numberLabel(index), label(item.label));
        stage.append(registerItem(context, row, index));
      });
      return;
    }
    const lines = graph(stage, visualWidth, height);
    const size = portrait ? Math.min(330, height * .35) : Math.min(300, height - 50);
    const hubX = portrait ? (visualWidth - size) / 2 : (visualWidth - size) / 2;
    const hubY = portrait ? 0 : (height - size) / 2;
    const hub = box(element("article", "orbit-hub"), hubX, hubY, size, size);
    hub.append(label(scene.items[0].label));
    stage.append(registerItem(context, hub, 0));
    const rest = scene.items.slice(1);
    rest.forEach((item, index) => {
      let x, y, width, nodeHeight;
      if (portrait) {
        width = visualWidth - 80;
        nodeHeight = Math.min(130, (height - size - 45) / Math.max(1, rest.length) - 12);
        x = 40;
        y = size + 40 + index * (nodeHeight + 12);
      } else {
        width = 515;
        nodeHeight = Math.min(122, (height - 20) / Math.ceil(rest.length / 2) - 16);
        const column = index % 2;
        const rows = Math.ceil((rest.length - column) / 2);
        x = column ? visualWidth - width : 0;
        y = (height - rows * (nodeHeight + 18) + 18) / 2 + Math.floor(index / 2) * (nodeHeight + 18);
      }
      const node = box(element("article", "orbit-card"), x, y, width, nodeHeight);
      node.append(numberLabel(index + 1), label(item.label));
      stage.append(registerItem(context, node, index + 1));
      const x1 = hubX + size / 2, y1 = hubY + size / 2;
      const x2 = x + width / 2, y2 = y + nodeHeight / 2;
      context.links.push(link(lines, `M${x1} ${y1} L${x2} ${y2}`));
    });
  }

  function arrangedCards(context, sequential) {
    const {scene, stage, width, height, composition} = context;
    const count = scene.items.length;
    if (!count) return;
    stage.classList.add("arranged-stage", `arranged-${composition}`);
    const gap = portrait ? 24 : 26;
    const positions = [];
    if (composition === "focus" && count > 1) {
      const rest = count - 1;
      if (portrait || context.imageLayout) {
        const heroHeight = height * (count > 4 ? .27 : .36);
        positions.push({x: 0, y: 0, width, height: heroHeight});
        const columns = rest > 3 ? 2 : 1, rows = Math.ceil(rest / columns);
        const cardWidth = (width - gap * (columns - 1)) / columns;
        const cardHeight = (height - heroHeight - gap * rows) / rows;
        for (let index = 0; index < rest; index++) positions.push({
          x: index % columns * (cardWidth + gap), y: heroHeight + gap + Math.floor(index / columns) * (cardHeight + gap), width: cardWidth, height: cardHeight,
        });
      } else {
        const heroWidth = width * .42;
        positions.push({x: 0, y: 0, width: heroWidth, height});
        const columns = rest > 3 ? 2 : 1, rows = Math.ceil(rest / columns);
        const cardWidth = (width - heroWidth - gap * columns) / columns;
        const cardHeight = (height - gap * (rows - 1)) / rows;
        for (let index = 0; index < rest; index++) positions.push({
          x: heroWidth + gap + index % columns * (cardWidth + gap), y: Math.floor(index / columns) * (cardHeight + gap), width: cardWidth, height: cardHeight,
        });
      }
    } else {
      const columns = composition === "split" ? Math.min(!portrait && !context.imageLayout && count > 4 ? 3 : 2, count)
        : (!portrait && count > 3 ? 2 : 1);
      const rows = Math.ceil(count / columns);
      const cardWidth = (width - gap * (columns - 1)) / columns;
      const cardHeight = (height - gap * (rows - 1)) / rows;
      for (let index = 0; index < count; index++) positions.push({
        x: index % columns * (cardWidth + gap), y: Math.floor(index / columns) * (cardHeight + gap), width: cardWidth, height: cardHeight,
      });
    }
    const lines = sequential ? graph(stage, width, height) : null;
    if (sequential) positions.slice(0, -1).forEach((point, index) => {
      const next = positions[index + 1];
      const sameRow = Math.abs(point.y - next.y) < 1;
      const x1 = sameRow ? point.x + point.width : point.x + point.width / 2;
      const y1 = sameRow ? point.y + point.height / 2 : point.y + point.height;
      const x2 = sameRow ? next.x : next.x + next.width / 2;
      const y2 = sameRow ? next.y + next.height / 2 : next.y;
      context.links.push(link(lines, `M${x1} ${y1} L${x2} ${y2}`));
    });
    scene.items.forEach((item, index) => {
      const position = positions[index];
      const node = box(element("article", `node-card arranged-card${composition === "focus" && index === 0 ? " focus-card" : ""}`), position.x, position.y, position.width, position.height);
      node.append(numberLabel(index), label(item.label));
      stage.append(registerItem(context, node, index));
    });
  }

  function kineticText(context) {
    const {scene, stage} = context;
    stage.classList.add("text-stage");
    const headline = element("h2", "kinetic-title", scene.title);
    titleClass(headline, scene.title);
    if (scene.title.length > 52) headline.classList.add("long-title");
    context.title = headline;
    const underline = element("div", "text-underline");
    context.draws.push({node: underline, property: "scaleX"});
    const keywords = element("div", "keyword-row");
    scene.items.forEach((item, index) => keywords.append(registerItem(context, element("div", "keyword", item.label), index)));
    stage.append(headline, underline, keywords);
  }

  function footage(context) {
    const {scene, node, stage, index} = context;
    const video = document.getElementById(`footage-${index}`);
    if (!video) throw new Error("Footage asset missing from the local composition");
    const wrapper = element("div", "footage-backdrop");
    wrapper.append(video);
    node.prepend(wrapper, element("div", "footage-shade"));
    stage.classList.add("footage-stage");
    scene.items.forEach((item, itemIndex) => stage.append(registerItem(context, element("div", "keyword", item.label), itemIndex)));
    if (scene.footage.credit) node.append(element("div", "footage-credit", scene.footage.credit));
    // Transform the wrapper only. The HyperFrames runtime owns video playback.
    timeline.fromTo(wrapper, {scale: 1.02}, {scale: 1.045, duration: scene.duration, ease: "none", immediateRender: false}, scene.start);
  }

  function picture(context) {
    const {scene, node, stage, index} = context;
    const image = document.getElementById(`image-${index}`);
    if (!image) throw new Error("Picture asset missing from the local composition");
    const figure = element("figure", "scene-photo");
    const viewport = element("div", "photo-viewport");
    viewport.append(image);
    figure.append(viewport);
    if (scene.image.credit) figure.append(element("figcaption", "photo-credit", scene.image.credit));
    node.prepend(figure);
    const hero = scene.template === "text";
    node.classList.add("image-scene");
    node.classList.toggle("photo-hero", hero);
    node.classList.toggle("dense-items", scene.items.length > 4);
    if (hero) {
      node.prepend(element("div", "photo-shade"));
      // Keep the picture behind the shade and all semantic text.
      node.prepend(figure);
    } else {
      context.imageLayout = true;
      const top = Math.max(portrait ? 510 : 355, context.header.offsetTop + context.header.offsetHeight + 34);
      const bottom = portrait ? 1530 : 818;
      if (portrait) {
        const photoHeight = Math.min(420, Math.max(300, (bottom - top - 36) * .43));
        box(figure, 72, top, visualWidth, photoHeight);
        box(stage, 72, top + photoHeight + 38, visualWidth, bottom - top - photoHeight - 38);
        context.width = visualWidth;
        context.height = bottom - top - photoHeight - 38;
      } else {
        box(figure, 100, top, 794, bottom - top);
        box(stage, 958, top, 862, bottom - top);
        context.width = 862;
        context.height = bottom - top;
      }
    }
    // A restrained camera move runs on the same seekable narration clock.
    // No GIF/WebP playback, CSS animation, or wall-clock randomness is involved.
    const direction = index % 2 ? 1 : -1;
    gsap.set(image, {scale: 1.035, xPercent: direction * .5});
    timeline.fromTo(image, {scale: 1.035, xPercent: direction * .5}, {
      scale: 1.07, xPercent: -direction * .5, duration: scene.duration,
      ease: "none", immediateRender: false,
    }, scene.start);
  }

  const builders = {flow, comparison, timeline: chronology, chart: numericChart, explain: explanation, text: kineticText, footage};

  function targetItems(context, target) {
    if (target === "scene" || target === "all" || target === "items") return context.items;
    const explicit = /^item[-_:](\d+)$/.exec(target);
    if (explicit) return context.items[Number(explicit[1])] ? [context.items[Number(explicit[1])]] : [];
    const found = context.scene.items.findIndex(item => item.id === target || item.label === target);
    return found >= 0 ? [context.items[found]] : [];
  }

  function beatTime(context, index) {
    const item = context.scene.items[index];
    const beat = context.scene.beats.find(candidate =>
      candidate.action === "reveal" && (["scene", "all", "items", `item-${index}`].includes(candidate.target) || candidate.target === item.id || candidate.target === item.label));
    if (beat) return beat.time;
    return Math.min(context.scene.duration * .65, .45 + index * Math.min(.65, context.scene.duration / Math.max(4, context.items.length + 2)));
  }

  function drawLink(entry, time, duration = .65) {
    timeline.fromTo(entry.node, {strokeDashoffset: entry.length},
      {strokeDashoffset: 0, duration, ease: "power2.inOut", immediateRender: false}, time);
  }

  function targetLinks(context, beat) {
    if (["scene", "all", "items"].includes(beat.target)) return context.links;
    const indices = targetItems(context, beat.target).map(item => context.items.indexOf(item) - 1);
    return context.links.filter((entry, index) => indices.includes(index));
  }

  function chartCount(item, time, duration) {
    if (!item.number) return;
    const decimals = Number.isInteger(item.value) ? 0 : Math.min(4, (String(item.value).split(".")[1] || "").length);
    timeline.fromTo(item.number, {textContent: 0}, {
      textContent: item.value, duration, ease: "power2.out", immediateRender: false,
      modifiers: {textContent: value => Number(value).toLocaleString("en-US", {minimumFractionDigits: 0, maximumFractionDigits: decimals})},
    }, time);
  }

  function animate(context) {
    const {scene, node, title, items} = context;
    const start = scene.start, end = start + scene.duration;
    const pace = context.rhythm === "calm" ? 1.45 : context.rhythm === "brisk" ? .7 : 1;
    const lift = context.rhythm === "calm" ? 5 : context.rhythm === "brisk" ? 20 : 14;
    const itemLift = context.rhythm === "measured" ? 12 : lift;
    const revealLength = Math.min(.6 * pace, scene.duration * .16 * pace);
    // A word/cue near the section boundary may leave only part of a frame.
    // Shorten its effect, never extend the narration clock or the next scene.
    const remaining = at => Math.max(0, end - at);
    const previous = context.index ? plan.scenes[context.index - 1] : scene;
    const previousLook = looks.has(previous.look) ? previous.look : planLook;
    const fullMedia = candidate => candidate.template === "footage" || (candidate.template === "text" && candidate.image) ? "full" : "none";
    // Bounded, zero-time theme changes share the same seekable clock as scenes.
    // fromTo also restores the previous scene's theme when seeking backwards.
    const isShot = candidate => Boolean(candidate.shot) && candidate.template !== "chart";
    timeline.fromTo(root, {attr: {"data-look": previousLook, "data-media": fullMedia(previous), "data-world": previous.graphics_version === 2 ? "2" : "0", "data-shot": isShot(previous) ? "1" : "0"}},
      {attr: {"data-look": context.look, "data-media": fullMedia(scene), "data-world": context.worlds ? "2" : "0", "data-shot": context.cinematic ? "1" : "0"}, duration: 0, immediateRender: false}, start);
    if (plan.palette_colors) {
      const colors = source => Object.fromEntries(Object.entries(source.palette_colors || plan.palette_colors).map(([key, value]) => [`--film-world-${key}`, value]));
      timeline.fromTo(root, colors(previous), {...colors(scene), duration: 0, immediateRender: false}, start);
    }
    const visibleAtStart = context.index === 0 && context.cinematic;
    gsap.set(node, {opacity: visibleAtStart ? 1 : 0});
    const dissolve = context.cinematic && scene.shot.transition === "dissolve" && context.index > 0
      ? Math.min(.22, scene.duration * .1) : 0;
    const next = plan.scenes[context.index + 1];
    const hold = next?.shot?.transition === "dissolve" ? Math.min(.22, next.duration * .1) : 0;
    if (!visibleAtStart) timeline.fromTo(node, {opacity: 0}, {opacity: 1, duration: dissolve || (context.objects ? 0 : .00001), immediateRender: false}, start);
    // An editorial dissolve overlaps the preceding final pose. The voice,
    // captions, shot boundaries and final duration keep their saved timings.
    timeline.fromTo(node, {opacity: 1}, {opacity: 0, duration: .00001, immediateRender: false}, end + hold - .00001);
    gsap.set(title, {y: lift, opacity: 0});
    const titleBeat = scene.beats.find(beat => beat.target === "title" && beat.action === "reveal");
    const titleAt = Math.min(end, start + (titleBeat ? titleBeat.time : .04));
    timeline.fromTo(title, {y: lift, opacity: 0}, {y: 0, opacity: 1, duration: Math.min(revealLength, remaining(titleAt)), ease: "power2.out", immediateRender: false}, titleAt);
    if (context.objectAnimation) {
      context.objectAnimation();
      return;
    }
    items.forEach((item, index) => {
      gsap.set(item.node, {opacity: 0, y: itemLift, scale: 1});
      const at = start + beatTime(context, index);
      timeline.fromTo(item.node, {opacity: 0, y: itemLift, scale: 1}, {
        opacity: 1, y: 0, scale: 1, duration: Math.min(revealLength, remaining(at)), ease: "power2.out", immediateRender: false,
      }, at);
      if (item.bar) {
        gsap.set(item.bar, {scaleX: 0});
        item.number.textContent = "0";
        const barDuration = Math.min(.9 * pace, scene.duration / 3, remaining(at));
        timeline.fromTo(item.bar, {scaleX: 0}, {scaleX: 1, duration: barDuration, ease: "power3.out", immediateRender: false}, at);
        if (!scene.beats.some(beat => beat.action === "count" && targetItems(context, beat.target).includes(item))) {
          chartCount(item, at, barDuration);
        }
      }
    });
    context.links.forEach((entry, index) => {
      if (!scene.beats.some(beat => ["draw", "connect"].includes(beat.action) && targetLinks(context, beat).includes(entry))) {
        const at = start + Math.min(scene.duration * .75, .6 + index * .6);
        drawLink(entry, at, Math.min(.6 * pace, scene.duration / 4, remaining(at)));
      }
    });
    context.draws.forEach(entry => {
      gsap.set(entry.node, {[entry.property]: 0});
      if (!scene.beats.some(beat => ["draw", "connect"].includes(beat.action))) {
        const at = start + Math.min(.3, scene.duration * .15);
        timeline.fromTo(entry.node, {[entry.property]: 0}, {
          [entry.property]: 1, duration: Math.min(1.6 * pace, scene.duration * .55, remaining(at)), ease: "power2.inOut", immediateRender: false,
        }, at);
      }
    });
    for (const beat of scene.beats) {
      const at = start + beat.time;
      const length = Math.min(.75 * pace, remaining(at));
      const targets = beat.target === "title" ? [{node: title}] : targetItems(context, beat.target);
      if (["highlight", "emphasize"].includes(beat.action)) {
        targets.forEach(item => {
          timeline.fromTo(item.node, {"--focus": 0}, {"--focus": 1, duration: length / 3, immediateRender: false}, at);
          timeline.to(item.node, {"--focus": 0, duration: length / 2}, at + length / 2);
        });
      } else if (beat.action === "count") {
        targets.forEach(item => chartCount(item, at, length));
      } else if (beat.action === "connect" || beat.action === "draw") {
        targetLinks(context, beat).forEach((entry, index) => {
          const linkAt = Math.min(end, at + index * .04);
          drawLink(entry, linkAt, Math.min(Math.max(0, length - index * .04), remaining(linkAt)));
        });
        context.draws.forEach(entry => timeline.fromTo(entry.node, {[entry.property]: 0}, {[entry.property]: 1, duration: length, ease: "power2.out", immediateRender: false}, at));
        targets.forEach(item => {
          if (item.bar) timeline.fromTo(item.bar, {scaleX: 0}, {scaleX: 1, duration: length, ease: "power2.out", immediateRender: false}, at);
        });
      }
    }
  }

  function makeScene(scene, index) {
    const node = element("section", `scene ${scene.template}-scene`);
    const look = looks.has(scene.look) ? scene.look : planLook;
    const rhythm = rhythms.has(scene.rhythm) ? scene.rhythm : planRhythm;
    const composition = compositions.has(scene.composition) ? scene.composition : "default";
    // Exact numerical charts retain their established chart builder. All
    // cinematic scene geometry is selected by the bounded shot contract.
    const cinematic = Boolean(scene.shot) && scene.template !== "chart";
    const worlds = !cinematic && scene.graphics_version === 2 && !scene.image && !["footage", "chart"].includes(scene.template);
    const objects = cinematic || [1, 2].includes(scene.graphics_version) && !scene.image && !["footage", "chart"].includes(scene.template);
    node.classList.toggle("object-scene", objects);
    node.classList.toggle("world-scene", worlds);
    node.classList.add(`composition-${composition}`);
    node.classList.toggle("dense-items", scene.items.length > 4);
    node.id = `scene-${index}`;
    // These are animation groups, not HyperFrames media clips. Their visibility
    // belongs to the registered timeline; media alone uses data-start/duration.
    node.dataset.visualStart = String(scene.start);
    node.dataset.visualDuration = String(scene.duration);
    node.dataset.sceneId = scene.id;
    const header = element("header", "scene-header");
    const title = element("h1", "scene-title", scene.title);
    titleClass(title, scene.title);
    header.append(element("div", "eyebrow", types[scene.template]), title);
    const stage = element("div", "scene-visual");
    node.append(header, element("div", "scene-number", `${String(index + 1).padStart(2, "0")} / ${String(plan.scenes.length).padStart(2, "0")}`), stage);
    mount.append(node);
    return {scene, index, node, header, title, stage, look, rhythm, composition, objects, worlds, cinematic, items: [], links: [], draws: [], width: visualWidth, height: portrait ? 970 : 400};
  }

  function buildCaptions() {
    if (plan.caption_style === "off") return;
    const words = plan.caption_style === "highlight" && plan.timing_source === "measured_sections"
      ? (plan.words || []).filter(word => {
        const section = plan.sections.find(item => String(item.id) === String(word.section_id));
        return section?.caption_timing === "word" && Number.isFinite(word.start) && Number.isFinite(word.end)
          && word.start >= section.start && word.end <= section.start + section.duration + 1 / plan.fps
          && word.end > word.start && typeof word.word === "string" && word.word.length;
      }).sort((left, right) => left.start - right.start || left.end - right.end) : [];
    let firstWord = 0;
    for (const cue of plan.captions) {
      const node = element("div", "caption-cue", cue.text);
      node.dir = "auto";
      while (firstWord < words.length && words[firstWord].end <= cue.start) firstWord++;
      const events = [];
      for (let index = firstWord; index < words.length && words[index].start < cue.end; index++) {
        if (words[index].end > cue.start) events.push(words[index]);
      }
      let cursor = 0;
      const matches = [];
      for (const word of events) {
        const index = cue.text.indexOf(word.word, cursor);
        if (index < cursor || !/^[\s\p{P}\p{S}]*$/u.test(cue.text.slice(cursor, index))) { matches.length = 0; break; }
        matches.push({word, index}); cursor = index + word.word.length;
      }
      // Match actual cue text, including its punctuation and whitespace. If a
      // word event is missing or the text differs, keep a plain phrase instead
      // of guessing karaoke timing or changing the displayed narration.
      if (matches.length && /^[\s\p{P}\p{S}]*$/u.test(cue.text.slice(cursor))) {
        node.replaceChildren(); cursor = 0;
        for (const {word, index} of matches) {
          node.append(document.createTextNode(cue.text.slice(cursor, index)));
          const span = element("span", "caption-word", word.word);
          node.append(span); cursor = index + word.word.length;
          const start = Math.max(cue.start, word.start), end = Math.min(cue.end, word.end);
          const plain = {color: "#ffffff", backgroundColor: "rgba(255, 213, 128, 0)"};
          const active = {color: "#ffd580", backgroundColor: "rgba(255, 213, 128, .14)"};
          gsap.set(span, plain);
          timeline.fromTo(span, plain, {...active, duration: 0, immediateRender: false}, start);
          timeline.fromTo(span, active, {...plain, duration: 0, immediateRender: false}, end);
        }
        node.append(document.createTextNode(cue.text.slice(cursor)));
      }
      if (cue.text.length > (portrait ? 100 : 130)) node.classList.add("long-caption");
      captionMount.append(node);
      gsap.set(node, {opacity: 0});
      timeline.fromTo(node, {opacity: 0}, {opacity: 1, duration: .00001, immediateRender: false}, cue.start);
      timeline.fromTo(node, {opacity: 1}, {opacity: 0, duration: .00001, immediateRender: false}, cue.end - .00001);
    }
  }

  function fitArrangedContent(context) {
    if (context.composition === "default") return;
    for (const card of context.stage.querySelectorAll(".arranged-card")) {
      if (card.clientHeight < 180) {
        card.style.padding = "12px 18px";
        card.style.gap = "8px";
      }
      const text = card.querySelector(".item-label");
      let size = parseFloat(getComputedStyle(text).fontSize);
      const minimum = portrait ? 23 : 20;
      const fits = () => {
        const style = getComputedStyle(card), rect = card.getBoundingClientRect();
        const children = [...card.children].map(node => node.getBoundingClientRect());
        const scale = plan.width / designWidth;
        return children.every(child => child.top >= rect.top - .1 && child.bottom <= rect.bottom - parseFloat(style.paddingBottom) * scale + .5
          && child.right <= rect.right - parseFloat(style.paddingRight) * scale + .5);
      };
      while (size > minimum && !fits()) { size -= 1; text.style.fontSize = `${size}px`; }
    }
    if (context.scene.template === "text" && !context.node.classList.contains("photo-hero")) {
      const stage = context.stage, headline = stage.querySelector(".kinetic-title");
      const overflows = () => {
        const bounds = stage.getBoundingClientRect();
        const nodes = [...stage.children, ...stage.querySelectorAll(".kinetic-title,.keyword")];
        return nodes.some(node => {
          const rect = node.getBoundingClientRect();
          const range = document.createRange(); range.selectNodeContents(node);
          const text = node.textContent.trim() ? range.getBoundingClientRect() : null;
          return rect.top < bounds.top - .1 || rect.bottom > bounds.bottom + .1 || rect.right > bounds.right + .1
            || (text && (text.top < bounds.top - .1 || text.bottom > bounds.bottom + .1 || text.right > bounds.right + .1));
        });
      };
      let size = parseFloat(getComputedStyle(headline).fontSize);
      while (size > (portrait ? 54 : 42) && overflows()) {
        size -= 2; headline.style.fontSize = `${size}px`;
      }
      const keywords = [...stage.querySelectorAll(".keyword")];
      let wordSize = keywords.length ? parseFloat(getComputedStyle(keywords[0]).fontSize) : 0;
      while (wordSize > (portrait ? 24 : 20) && overflows()) {
        wordSize -= 1; keywords.forEach(keyword => { keyword.style.fontSize = `${wordSize}px`; });
      }
    }
  }

  async function initialise() {
    const scenes = plan.scenes.map(makeScene);
    const allText = plan.scenes.map(scene => `${scene.title} ${scene.items.map(item => item.label).join(" ")}`).join(" ");
    await document.fonts.load('600 72px "Inter Variable"', allText);
    await document.fonts.ready;
    await Promise.all([...document.querySelectorAll("#media-bank img")].map(image => image.decode()));
    if (plan.scenes.some(scene => scene.shot)) await window.YAASShots.prepare(plan);
    for (const context of scenes) {
      root.dataset.look = context.look;
      if (context.objects) {
        // Bound the new heading to two lines, leaving the stage to its subject.
        let size = parseFloat(getComputedStyle(context.title).fontSize);
        while (size > (portrait ? 48 : 42) && context.title.offsetHeight > size * 2.32) {
          size -= 2; context.title.style.fontSize = `${size}px`;
        }
      }
      if (!context.cinematic && (context.objects || !["text", "footage"].includes(context.scene.template))) {
        const top = Math.max(context.worlds ? (portrait ? 350 : 255) : context.objects ? (portrait ? 410 : 300) : (portrait ? 560 : 390), context.header.offsetTop + context.header.offsetHeight + (context.worlds ? 10 : context.objects ? 22 : 38));
        const bottom = context.worlds ? (portrait ? 1510 : 855) : context.objects ? (portrait ? 1490 : 835) : (portrait ? 1530 : 800);
        context.stage.style.top = `${top}px`;
        context.height = Math.max(portrait ? 500 : 220, bottom - top);
        context.stage.style.height = `${context.height}px`;
      }
      if (context.cinematic) window.YAASShots.build(context, {portrait, timeline, beatTime, palette: context.scene.palette_colors || plan.palette_colors});
      else if (context.scene.image && !["chart", "footage"].includes(context.scene.template)) picture(context);
      if (context.cinematic) { /* The shot builder owns its media and geometry. */ }
      else if (context.worlds) window.YAASWorlds.build(context, {portrait, timeline, beatTime, palette: context.scene.palette_colors || plan.palette_colors});
      else if (context.objects) window.YAASObjects.build(context, {portrait, timeline, beatTime});
      else { builders[context.scene.template](context); fitArrangedContent(context); }
      animate(context);
    }
    buildCaptions();
    timeline.fromTo(document.getElementById("film-progress-fill"), {scaleX: 0}, {scaleX: 1, duration: plan.duration, ease: "none", immediateRender: false}, 0);
    // Keep the playhead exactly as long as narration, even when the final visual is still.
    timeline.to({time: 0}, {time: plan.duration, duration: plan.duration, ease: "none"}, 0);
    if (plan.scenes.some(scene => scene.shot)) window.YAASShots.bind(timeline, plan);
    document.getElementById("chapter-count").textContent = `${String(plan.scenes.length).padStart(2, "0")} SCENES`;
    root.dataset.look = scenes[0].look;
    root.dataset.world = scenes[0].worlds ? "2" : "0";
    root.dataset.shot = scenes[0].cinematic ? "1" : "0";
    root.dataset.media = scenes[0].scene.template === "footage" || (scenes[0].scene.template === "text" && scenes[0].scene.image) ? "full" : "none";
    timeline.pause(0);
    window.__timelines = window.__timelines || {};
    window.__timelines["yaas-motion"] = timeline;
    window.dispatchEvent(new CustomEvent("yaas-motion-ready"));
    return {timeline, plan};
  }

  let previewPromise;
  function loadPreview() {
    if (!previewPromise) previewPromise = new Promise((resolve, reject) => {
      const script = document.createElement("script");
      script.src = "preview.js";
      script.onload = () => resolve(window.YAASMotionPreview);
      script.onerror = () => reject(new Error("Could not load local preview controls"));
      document.head.append(script);
    });
    return previewPromise;
  }

  const ready = initialise();
  window.YAASMotionReady = ready;
  window.YAASMotion = {
    ready, duration: plan.duration, fps: plan.fps,
    seek: async seconds => {
      await ready;
      const value = Number(seconds);
      if (!Number.isFinite(value)) throw new TypeError("Seek time must be finite");
      const time = Math.max(0, Math.min(plan.duration, value));
      if (window.YAASMotionPreview) return window.YAASMotionPreview.seek(time);
      timeline.pause().seek(time, false);
      return time;
    },
    play: async () => { await ready; return (await loadPreview()).play(); },
    pause: () => { timeline.pause(); if (window.YAASMotionPreview) window.YAASMotionPreview.pause(); },
    get currentTime() { return timeline.time(); },
  };
  if (new URLSearchParams(window.location.search).get("preview") === "1") ready.then(loadPreview);
  ready.catch(error => { document.body.dataset.motionError = error.message; console.error("YAAS motion:", error); });
})();
