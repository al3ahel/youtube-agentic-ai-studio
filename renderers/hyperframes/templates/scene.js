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
    const {scene, stage, height} = context;
    const count = scene.items.length;
    if (!count) return;
    const lines = graph(stage, visualWidth, height);
    const cardWidth = portrait ? 850 : Math.min(380, (visualWidth - 50 * (count - 1)) / count);
    const cardHeight = portrait ? Math.min(180, (height - (count - 1) * 28) / count) : Math.min(260, height - 70);
    const spacing = portrait ? (height - cardHeight) / Math.max(1, count - 1) : (visualWidth - cardWidth) / Math.max(1, count - 1);
    const positions = scene.items.map((item, index) => ({
      x: portrait ? 28 : (count === 1 ? (visualWidth - cardWidth) / 2 : spacing * index),
      y: portrait ? (count === 1 ? (height - cardHeight) / 2 : spacing * index) : (height - cardHeight) / 2,
    }));
    positions.slice(0, -1).forEach((point, index) => {
      const next = positions[index + 1];
      const coordinates = portrait
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
    const columns = portrait ? (count > 3 ? 2 : 1) : Math.min(count, count > 3 ? 3 : count);
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
    const {scene, stage, height} = context;
    const count = scene.items.length;
    const inset = portrait ? 36 : 28;
    const length = portrait ? height - 150 : visualWidth - 230;
    const line = element("div", "timeline-line");
    const draw = element("div", "timeline-draw");
    if (portrait) {
      Object.assign(line.style, {left: "36px", top: "35px", width: "2px", height: `${length}px`});
      Object.assign(draw.style, {left: "36px", top: "35px", width: "2px", height: `${length}px`, transformOrigin: "center top"});
    } else {
      Object.assign(line.style, {left: `${inset}px`, top: `${height / 2}px`, width: `${length}px`, height: "2px"});
      Object.assign(draw.style, {left: `${inset}px`, top: `${height / 2}px`, width: `${length}px`, height: "2px"});
    }
    stage.append(line, draw);
    context.draws.push({node: draw, property: portrait ? "scaleY" : "scaleX"});
    scene.items.forEach((item, index) => {
      const node = element("article", "timeline-node");
      const distance = count <= 1 ? 0 : index * length / (count - 1);
      box(node, portrait ? 36 : inset + distance, portrait ? 35 + distance : height / 2, portrait ? 820 : 240);
      const copy = element("div", "timeline-copy");
      copy.append(element("p", "item-value", item.value !== undefined ? item.value : String(index + 1).padStart(2, "0")), label(item.label));
      if (portrait) Object.assign(copy.style, {left: "60px", top: "-21px"});
      else if (index % 2 === 0) node.classList.add("above");
      node.append(element("span", "timeline-dot"), copy);
      stage.append(registerItem(context, node, index));
    });
  }

  function numericChart(context) {
    const {scene, stage, height} = context;
    stage.classList.add("chart-stage");
    const values = scene.items.map(item => Number(item.value));
    const low = Math.min(0, ...values), high = Math.max(0, ...values);
    const span = high - low || 1;
    const leftInset = 14, plotWidth = portrait ? visualWidth - 155 : visualWidth - 540;
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
    const {scene, stage, height} = context;
    if (!scene.items.length) return;
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

  function kineticText(context) {
    const {scene, stage} = context;
    stage.classList.add("text-stage");
    const headline = element("h2", "kinetic-title", scene.title);
    titleClass(headline, scene.title);
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
    timeline.fromTo(wrapper, {scale: 1.025}, {scale: 1.07, duration: scene.duration, ease: "none", immediateRender: false}, scene.start);
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
    const revealLength = Math.min(.6, scene.duration * .16);
    gsap.set(node, {opacity: 0});
    timeline.fromTo(node, {opacity: 0}, {opacity: 1, duration: .00001, immediateRender: false}, start);
    timeline.fromTo(node, {opacity: 1}, {opacity: 0, duration: .00001, immediateRender: false}, end - .00001);
    gsap.set(title, {y: 22, opacity: 0});
    const titleBeat = scene.beats.find(beat => beat.target === "title" && beat.action === "reveal");
    timeline.fromTo(title, {y: 22, opacity: 0}, {y: 0, opacity: 1, duration: revealLength, ease: "power3.out", immediateRender: false}, start + (titleBeat ? titleBeat.time : .04));
    items.forEach((item, index) => {
      gsap.set(item.node, {opacity: 0, y: 22, scale: .97});
      const at = start + beatTime(context, index);
      timeline.fromTo(item.node, {opacity: 0, y: 22, scale: .97}, {
        opacity: 1, y: 0, scale: 1, duration: revealLength, ease: "power3.out", immediateRender: false,
      }, at);
      if (item.bar) {
        gsap.set(item.bar, {scaleX: 0});
        item.number.textContent = "0";
        timeline.fromTo(item.bar, {scaleX: 0}, {scaleX: 1, duration: Math.min(.9, scene.duration / 3), ease: "power3.out", immediateRender: false}, at);
        if (!scene.beats.some(beat => beat.action === "count" && targetItems(context, beat.target).includes(item))) {
          chartCount(item, at, Math.min(.9, scene.duration / 3));
        }
      }
    });
    context.links.forEach((entry, index) => {
      if (!scene.beats.some(beat => ["draw", "connect"].includes(beat.action) && targetLinks(context, beat).includes(entry))) {
        drawLink(entry, start + Math.min(scene.duration * .75, .6 + index * .6), Math.min(.6, scene.duration / 4));
      }
    });
    context.draws.forEach(entry => {
      gsap.set(entry.node, {[entry.property]: 0});
      if (!scene.beats.some(beat => ["draw", "connect"].includes(beat.action))) {
        timeline.fromTo(entry.node, {[entry.property]: 0}, {
          [entry.property]: 1, duration: Math.min(1.6, scene.duration * .55), ease: "power2.inOut", immediateRender: false,
        }, start + .3);
      }
    });
    for (const beat of scene.beats) {
      const at = start + beat.time;
      const length = Math.min(.75, Math.max(.05, end - at));
      const targets = beat.target === "title" ? [{node: title}] : targetItems(context, beat.target);
      if (["highlight", "emphasize"].includes(beat.action)) {
        targets.forEach(item => {
          timeline.fromTo(item.node, {"--focus": 0}, {"--focus": 1, duration: length / 3, immediateRender: false}, at);
          timeline.to(item.node, {"--focus": 0, duration: length / 2}, at + length / 2);
        });
      } else if (beat.action === "count") {
        targets.forEach(item => chartCount(item, at, length));
      } else if (beat.action === "connect" || beat.action === "draw") {
        targetLinks(context, beat).forEach((entry, index) => drawLink(entry, at + index * .04, Math.max(.05, length - index * .04)));
        context.draws.forEach(entry => timeline.fromTo(entry.node, {[entry.property]: 0}, {[entry.property]: 1, duration: length, ease: "power2.out", immediateRender: false}, at));
        targets.forEach(item => {
          if (item.bar) timeline.fromTo(item.bar, {scaleX: 0}, {scaleX: 1, duration: length, ease: "power2.out", immediateRender: false}, at);
        });
      }
    }
  }

  function makeScene(scene, index) {
    const node = element("section", `scene ${scene.template}-scene`);
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
    return {scene, index, node, header, title, stage, items: [], links: [], draws: [], height: portrait ? 970 : 400};
  }

  function buildCaptions() {
    for (const cue of plan.captions) {
      const node = element("div", "caption-cue", cue.text);
      if (cue.text.length > (portrait ? 100 : 130)) node.classList.add("long-caption");
      captionMount.append(node);
      gsap.set(node, {opacity: 0});
      timeline.fromTo(node, {opacity: 0}, {opacity: 1, duration: .00001, immediateRender: false}, cue.start);
      timeline.fromTo(node, {opacity: 1}, {opacity: 0, duration: .00001, immediateRender: false}, cue.end - .00001);
    }
  }

  async function initialise() {
    const scenes = plan.scenes.map(makeScene);
    const allText = plan.scenes.map(scene => `${scene.title} ${scene.items.map(item => item.label).join(" ")}`).join(" ");
    await document.fonts.load('600 72px "Inter Variable"', allText);
    await document.fonts.ready;
    for (const context of scenes) {
      if (!["text", "footage"].includes(context.scene.template)) {
        const top = Math.max(portrait ? 560 : 390, context.header.offsetTop + context.header.offsetHeight + 38);
        const bottom = portrait ? 1530 : 800;
        context.stage.style.top = `${top}px`;
        context.height = Math.max(portrait ? 500 : 220, bottom - top);
        context.stage.style.height = `${context.height}px`;
      }
      builders[context.scene.template](context);
      animate(context);
    }
    buildCaptions();
    timeline.fromTo(document.getElementById("film-progress-fill"), {scaleX: 0}, {scaleX: 1, duration: plan.duration, ease: "none", immediateRender: false}, 0);
    timeline.fromTo(document.querySelector(".orbit-one"), {x: 0, y: 0}, {x: -65, y: 45, duration: plan.duration, ease: "none", immediateRender: false}, 0);
    // Keep the playhead exactly as long as narration, even when the final visual is still.
    timeline.to({time: 0}, {time: plan.duration, duration: plan.duration, ease: "none"}, 0);
    document.getElementById("chapter-count").textContent = `${String(plan.scenes.length).padStart(2, "0")} SCENES`;
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
