/* Bounded qualitative vector scenes. The manifest selects subjects, never code.
 * Every moving part belongs to the paused narration timeline. No wall clock,
 * random layouts, inferred quantities, invented maps or remote assets. */
(() => {
  "use strict";
  const NS = "http://www.w3.org/2000/svg";
  const allowed = new Set(["concept", "census", "census-gap", "people", "mining", "solar", "battery", "circuit", "water", "plant", "network", "trade", "conflict", "document", "orbit", "building", "armour", "firearm"]);
  function svg(tag, attributes = {}, parent) {
    const node = document.createElementNS(NS, tag);
    for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, String(value));
    if (parent) parent.append(node);
    return node;
  }
  function group(parent, x = 0, y = 0, attrs = {}) {
    return svg("g", {transform: `translate(${x} ${y})`, ...attrs}, parent);
  }
  function shape(parent, tag, attrs, style = "object-outline") {
    return svg(tag, {class: style, ...attrs}, parent);
  }
  function person(parent, x, y, ghost = false) {
    const node = group(parent, x, y, {class: ghost ? "object-person object-ghost" : "object-person"});
    shape(node, "circle", {cx: 0, cy: -28, r: 22}, "object-solid");
    shape(node, "path", {d: "M-36 55 L-31 6 Q0-16 31 6 L36 55 M-17 10 V86 M17 10 V86"});
    return node;
  }
  function packageBox(parent, x, y) {
    const node = group(parent, x, y);
    shape(node, "path", {d: "M-31-20 L0-36 L31-20 L31 20 L0 36 L-31 20 Z"}, "object-fill");
    shape(node, "path", {d: "M-31-20 L0-3 L31-20 M0-3 V36 M-15-28 L16-11"});
    return node;
  }
  function wheel(parent, x, y, radius = 18) {
    const node = group(parent, x, y);
    shape(node, "circle", {cx: 0, cy: 0, r: radius}, "object-dark");
    shape(node, "path", {d: `M${-radius + 5} 0 H${radius - 5} M0 ${-radius + 5} V${radius - 5}`});
    return node;
  }

  function drawSubject(parent, symbol, styles = {}) {
    // Shared original silhouettes for legacy objects and composed worlds.
    // A named object does not imply a particular alloy, historical model,
    // loaded weapon, firing action or ballistic result.
    const cls = {body: "object-paper", shade: "object-fill", line: "object-outline",
      fine: "object-subtle", accent: "object-solid", dark: "object-dark", ...styles};
    const node = group(parent, 0, 0, {class: `subject-glyph subject-${symbol}`, "data-subject": symbol});
    const line = (d, style = "line", attrs = {}) => shape(node, "path", {d, ...attrs}, cls[style]);
    if (symbol === "armour") {
      line("M-78-66 L-46-101 Q0-65 46-101 L78-66 L63 76 Q0 112-63 76 Z", "body");
      line("M0-79 Q25-79 46-101 L78-66 L63 76 Q30 96 0 101 Z", "shade", {opacity: .34});
      line("M-70-63 Q-51-30-64 23 M70-63 Q51-30 64 23", "fine");
      line("M0-78 Q-5 11 0 99 M-61 65 Q0 90 61 65", "line");
      line("M-47-94 Q0-59 47-94 M-59 75 Q0 101 59 75", "fine");
      for (const side of [-1, 1]) for (let row = 0; row < 5; row++) {
        shape(node, "circle", {cx: side * (68 - row * 1.9), cy: -49 + row * 28, r: 2.8}, cls.accent);
      }
    } else if (symbol === "firearm") {
      node.setAttribute("transform", "rotate(-16)");
      // A broad barrel-and-stock profile; no operating mechanism is drawn.
      line("M-111-4 L-89-14 L-47-7 L-21 8 L-37 28 L-61 11 L-108 34 Z", "shade");
      line("M-43-16 H-15 L-8-6 V10 H-35 L-47 3 Z", "body");
      line("M-17-15 H112 V-1 H-17 Z", "body");
      line("M-12-12 H110 M-9 0 H103", "fine");
      line("M23-16 V2 M79-16 V2", "line");
      line("M108-16 H116 V1 H108 Z", "dark");
      line("M-32 11 Q-41 33-18 33 Q-2 32-8 12 M-23 11 L-25 22", "line");
      line("M-106-1 L-104 28 M-91-3 L-56 5", "fine");
      line("M91-17 V-22 H96 V-17", "line");
    }
    return node;
  }
  function factory(symbol) {
    const node = svg("svg", {viewBox: "0 0 640 480", role: "img", "aria-label": `Illustrated ${symbol.replaceAll("-", " ")} schematic`, class: "object-art"});
    const title = svg("title", {}, node); title.textContent = `Qualitative ${symbol.replaceAll("-", " ")} diagram`;
    const parts = [];
    const part = (target, motion, a, b, period = 2.4, offset = 0) => {
      // Keep authored SVG placement outside the GSAP-owned transform. This
      // matters when a person at x=178 is animated by +20, not moved to x=20.
      if (target.hasAttribute("transform")) {
        const wrapper = svg("g");
        target.parentNode.insertBefore(wrapper, target); wrapper.append(target); target = wrapper;
      }
      target.dataset.actor = motion;
      parts.push({target, motion, a, b, period, offset});
      return target;
    };
    const line = (d, style = "object-outline", parent = node) => shape(parent, "path", {d}, style);
    const circle = (cx, cy, r, style = "object-solid", parent = node) => shape(parent, "circle", {cx, cy, r}, style);
    const rect = (x, y, width, height, style = "object-fill", parent = node, extra = {}) => shape(parent, "rect", {x, y, width, height, rx: 9, ...extra}, style);

    if (symbol === "armour" || symbol === "firearm") {
      const placement = svg("g", {transform: "translate(320 242) scale(1.8)"}, node);
      const subject = drawSubject(placement, symbol);
      part(subject, "subject-appears", {opacity: .25}, {opacity: 1}, 1.3);
    } else if (symbol === "census" || symbol === "census-gap") {
      // A population is recorded into a ledger; dotted person/row denotes a gap,
      // only selected when the source explicitly says undercount.
      const population = group(node, 0, 0);
      person(population, 102, 126); person(population, 222, 236);
      person(population, 102, 340, symbol === "census-gap");
      rect(360, 48, 224, 382, "object-paper");
      rect(407, 32, 130, 32, "object-fill");
      line("M391 92 H550", "object-subtle");
      [132, 240, 348].forEach((y, index) => {
        const record = group(node, 0, 0, {class: index === 2 && symbol === "census-gap" ? "object-ghost" : ""});
        circle(401, y, 10, "object-solid", record);
        line(`M429 ${y - 6} H551 M429 ${y + 13} H512`, "object-outline", record);
        if (!(index === 2 && symbol === "census-gap")) {
          const connection = line(`M${index === 1 ? 274 : 154} ${y} H342`, "object-route");
          part(connection, "record-entry", {strokeDashoffset: 190}, {strokeDashoffset: 0}, 1.2, index * .45);
          connection.style.strokeDasharray = "190 190";
          part(record, "record-appears", {opacity: .15}, {opacity: 1}, 1.2, index * .45);
        }
      });
      const scan = rect(76, 86, 482, 3, "object-beam", node, {rx: 0});
      part(scan, "census-scan", {y: 0}, {y: 300}, 3.8);
    } else if (symbol === "mining") {
      // A geological cutaway and working drill/conveyor; ore is schematic,
      // never a claim about a particular mine's geography or output.
      line("M30 210 L112 136 L188 193 L260 158 L343 203 L427 158 L514 202 L610 178 L610 432 L30 432 Z", "object-earth");
      line("M30 276 Q160 226 270 284 T610 258 L610 432 L30 432 Z", "object-stratum");
      line("M30 360 Q162 302 295 359 T610 332 L610 432 L30 432 Z", "object-deep");
      line("M220 348 L258 318 L303 335 L339 303 L393 327 L435 305 L481 332", "object-ore");
      const tower = group(node, 272, 0);
      line("M-48 204 L-20 56 H20 L48 204 M-36 150 H36 M-28 102 H28 M-20 56 L36 150 M20 56 L-36 150", "object-outline", tower);
      rect(-32, 41, 64, 25, "object-fill", tower);
      const drill = group(tower, 0, 0);
      line("M0 91 V269", "object-drill", drill);
      line("M-11 257 L0 279 L11 257 M-9 228 L9 239 M-9 207 L9 218", "object-outline", drill);
      part(drill, "drill-into-rock", {y: -15}, {y: 51}, 2.3);
      line("M310 303 L499 212 L592 212", "object-conveyor");
      line("M354 318 L354 372 M465 262 L465 332 M556 225 L556 296", "object-subtle");
      for (let i = 0; i < 3; i++) {
        const ore = shape(node, "path", {d: "M-12-5 L-4-14 L10-9 L14 7 L0 12 L-13 5 Z"}, "object-ore-piece");
        part(ore, "ore-on-conveyor", {x: 317, y: 294}, {x: 498, y: 206}, 3, i * .55);
      }
      rect(501, 300, 93, 52, "object-fill");
      wheel(node, 522, 362); wheel(node, 575, 362);
      line("M503 294 L518 276 L532 285 L547 272 L565 284 L579 277 L592 294", "object-ore");
    } else if (symbol === "solar") {
      circle(115, 92, 43, "object-sun");
      for (let angle = 0; angle < 360; angle += 45) {
        const ray = group(node, 115, 92);
        shape(ray, "path", {d: "M0-59 V-76", transform: `rotate(${angle})`}, "object-sun-line");
      }
      const panel = group(node, 0, 0);
      line("M229 188 L507 188 L562 333 L176 333 Z", "object-panel", panel);
      [224, 260, 297].forEach(y => line(`M${229 - (y - 188) * .366} ${y} H${507 + (y - 188) * .38}`, "object-subtle", panel));
      [276, 323, 370, 417, 464].forEach(x => line(`M${x} 188 L${x + (x - 368) * .38} 333`, "object-subtle", panel));
      line("M236 333 V390 M506 333 V390 M212 394 H531", "object-outline");
      [0, 1, 2].forEach(i => {
        const photon = circle(0, 0, 8, "object-sun");
        part(photon, "sunlight-to-panel", {x: 158 + i * 20, y: 130}, {x: 267 + i * 88, y: 242}, 2.6, i * .35);
      });
      line("M564 311 H593 V407 H407", "object-route");
      const charge = circle(0, 0, 9);
      part(charge, "solar-output", {x: 593, y: 326}, {x: 593, y: 403}, 2.5);
    } else if (symbol === "battery") {
      rect(177, 132, 320, 215, "object-paper"); rect(497, 196, 27, 84, "object-solid");
      line("M202 155 H468 M202 324 H468", "object-subtle");
      const core = rect(199, 176, 272, 125, "object-fill");
      part(core, "charge-transfer", {scaleX: .18, transformOrigin: "left center"}, {scaleX: 1, transformOrigin: "left center"}, 4.8);
      line("M93 239 H169 M534 239 H596", "object-route");
      const charge = circle(0, 0, 11);
      part(charge, "incoming-charge", {x: 64, y: 239}, {x: 163, y: 239}, 1.7);
      line("M310 199 L292 245 H326 L308 282", "object-bright");
    } else if (symbol === "circuit") {
      line("M114 241 V112 H521 V367 H114 V241", "object-route");
      rect(88, 191, 52, 96, "object-paper"); line("M77 218 H150 M92 258 H137", "object-bright");
      rect(271, 89, 133, 47, "object-fill"); line("M291 113 L306 99 L321 125 L336 99 L351 125 L366 99 L384 113");
      circle(521, 241, 48, "object-paper"); line("M498 218 L544 264 M544 218 L498 264", "object-bright");
      for (let i = 0; i < 3; i++) {
        const electron = circle(0, 0, 10);
        part(electron, "electron-through-wire", {x: 141, y: 367}, {x: 497, y: 367}, 2.8, i * .4);
      }
    } else if (symbol === "plant") {
      line("M40 299 Q200 282 320 301 T600 299 L600 440 H40 Z", "object-earth");
      const stem = line("M320 310 Q298 234 322 136 Q336 97 326 56", "object-stem");
      const branches = group(node, 0, 0);
      line("M313 200 Q224 201 202 114 Q278 108 313 200 M320 155 Q398 150 430 75 Q352 74 320 155 M312 249 Q387 259 434 194 Q353 179 312 249", "object-leaf", branches);
      line("M320 307 L301 351 L255 374 M301 351 L324 399 M320 307 L365 345 L405 358 M365 345 L370 393", "object-root");
      part(branches, "leaves-unfold", {scale: .18, transformOrigin: "320px 260px"}, {scale: 1, transformOrigin: "320px 260px"}, 2.1);
      stem.style.strokeDasharray = "300 300";
      part(stem, "stem-grows", {strokeDashoffset: 300}, {strokeDashoffset: 0}, 2.1);
      for (let i = 0; i < 3; i++) {
        const drop = circle(0, 0, 7, "object-water");
        part(drop, "water-up-roots", {x: 320, y: 354}, {x: 320, y: 145}, 3.6, i * .6);
      }
    } else if (symbol === "water") {
      line("M80 254 Q70 206 117 196 Q131 145 188 166 Q225 151 248 187 Q294 186 292 231 Q292 258 260 258 Z", "object-paper");
      line("M35 374 Q150 336 267 372 Q375 410 490 361 Q555 335 608 367 L608 442 H35 Z", "object-water-fill");
      line("M35 402 Q150 364 267 400 Q375 438 490 389 Q555 363 608 395", "object-water-line");
      for (let i = 0; i < 4; i++) {
        const drop = shape(node, "path", {d: "M0-14 Q-16 7 0 12 Q16 7 0-14"}, "object-water");
        part(drop, "rain-to-water", {x: 112 + i * 45, y: 277}, {x: 99 + i * 45, y: 355}, 1.9, i * .3);
      }
      line("M355 339 Q348 240 417 176 Q484 108 557 141", "object-route");
      const flow = circle(0, 0, 10, "object-water");
      part(flow, "water-flow", {x: 361, y: 329}, {x: 425, y: 177}, 3.4);
    } else if (symbol === "network") {
      const locations = [[84, 105], [84, 355], [539, 105], [539, 355]];
      rect(232, 160, 176, 162, "object-chip");
      [185, 214, 244, 274, 303].forEach(y => {line(`M210 ${y} H232 M408 ${y} H430`, "object-subtle");});
      line("M268 240 L294 217 L294 265 Z M332 212 V270 M354 216 L380 240 L354 265", "object-bright");
      locations.forEach(([x, y], i) => {
        rect(x - 37, y - 30, 74, 60, "object-paper");
        line(`M${x} ${y} L${i < 2 ? 210 : 430} ${y} L${i < 2 ? 210 : 430} 241`, "object-route");
        const packet = rect(-7, -7, 14, 14, "object-solid");
        part(packet, "data-packet", {x, y}, {x: i < 2 ? 210 : 430, y}, 2.8, i * .35);
      });
    } else if (symbol === "trade") {
      line("M57 335 H585", "object-route");
      const truck = group(node, 0, 0);
      rect(178, 187, 220, 119, "object-paper", truck);
      line("M398 222 H449 L494 263 V306 H398 Z", "object-fill", truck);
      line("M420 237 H441 L467 262 H420 Z", "object-panel", truck);
      wheel(truck, 223, 315, 24); wheel(truck, 440, 315, 24);
      part(truck, "cargo-in-transit", {x: -43}, {x: 64}, 4.8);
      const cargo = packageBox(node, 0, 0);
      part(cargo, "package-loaded", {x: 87, y: 239}, {x: 277, y: 239}, 2.2);
      line("M82 138 H164 M471 138 H558", "object-subtle");
    } else if (symbol === "conflict") {
      // Symbolic opposing positions and interrupted connection, not a map or
      // fabricated battle. Deliberately no flags, borders or national actors.
      person(node, 129, 248); person(node, 511, 248);
      line("M197 251 H291 M349 251 H443", "object-route");
      const divide = line("M321 97 L300 166 L339 221 L304 281 L333 343 L317 401", "object-warning");
      divide.style.strokeDasharray = "350 350";
      part(divide, "connection-interrupted", {strokeDashoffset: 350}, {strokeDashoffset: 0}, 2);
      const left = circle(0, 0, 9); const right = circle(0, 0, 9, "object-warm");
      part(left, "approach-divider", {x: 204, y: 251}, {x: 284, y: 251}, 2.8);
      part(right, "opposing-position", {x: 438, y: 251}, {x: 355, y: 251}, 2.8);
    } else if (symbol === "orbit") {
      shape(node, "ellipse", {cx: 320, cy: 250, rx: 263, ry: 127}, "object-route");
      circle(320, 250, 102, "object-panel");
      line("M235 218 Q306 164 390 207 M226 260 Q317 311 414 256 M296 151 Q353 254 298 349", "object-subtle");
      const projection = svg("g", {transform: "translate(320 250) scale(1 .5)"}, node);
      const orbit = svg("g", {}, projection);
      const satellite = group(orbit, 248, 0);
      rect(-13, -16, 26, 32, "object-paper", satellite); rect(-55, -12, 34, 24, "object-panel", satellite); rect(21, -12, 34, 24, "object-panel", satellite);
      part(orbit, "satellite-orbit", {rotation: 0, svgOrigin: "0 0"}, {rotation: 360, svgOrigin: "0 0"}, 8);
    } else if (symbol === "building") {
      rect(109, 162, 181, 235, "object-paper"); rect(330, 83, 193, 314, "object-paper");
      [0, 1, 2].forEach(row => [0, 1, 2].forEach(col => rect(136 + col * 49, 194 + row * 55, 22, 30, "object-panel")));
      [0, 1, 2, 3].forEach(row => [0, 1, 2].forEach(col => rect(360 + col * 50, 119 + row * 54, 24, 31, "object-panel")));
      line("M54 403 H583", "object-outline");
      const resident = person(node, 0, 0);
      part(resident, "resident-arrives", {x: 85, y: 331, scale: .54}, {x: 315, y: 331, scale: .54}, 4.2);
    } else if (symbol === "people") {
      line("M57 391 H583", "object-subtle");
      const first = person(node, 178, 275); person(node, 320, 275); const last = person(node, 462, 275);
      part(first, "person-joins", {x: -68, opacity: .25}, {x: 0, opacity: 1}, 2.8);
      part(last, "community-joins", {x: 68, opacity: .25}, {x: 0, opacity: 1}, 2.8);
      line("M175 152 Q319 67 464 152", "object-route");
    } else if (symbol === "document") {
      rect(156, 61, 329, 358, "object-paper");
      line("M194 110 H362", "object-bright");
      [165, 212, 259, 306, 353].forEach((y, i) => {
        const record = line(`M195 ${y} H${i % 2 ? 397 : 446}`, "object-outline");
        record.style.strokeDasharray = "260 260";
        part(record, "written-record", {strokeDashoffset: 260}, {strokeDashoffset: 0}, 1.2, i * .35);
      });
      const tab = rect(472, 129, 42, 83, "object-fill");
      part(tab, "document-tab", {x: -23}, {x: 0}, 1.6);
    } else {
      // A deliberately abstract qualitative relationship for an unknown subject.
      // Do not depict invented machinery merely to make a vague line look busy.
      [2, 1, 0].forEach(i => {
        const solid = group(node, 0, 0);
        const y = 176 + i * 52;
        line(`M138 ${y} L320 ${y - 80} L502 ${y} L320 ${y + 80} Z`, "object-fill", solid);
        line(`M138 ${y} V${y + 28} L320 ${y + 108} L502 ${y + 28} V${y} M320 ${y + 80} V${y + 108}`, "object-outline", solid);
        part(solid, "idea-unfolds", {y: 0}, {y: (i - 1) * 46}, 3);
      });
    }
    return {node, parts};
  }

  function animateActor(actor, timeline, start, duration) {
    for (const part of actor.parts) {
      const oneShot = /appears|entry|grows|unfold|interrupted|record|tab|joins|loaded|arrives|charge-transfer|cargo-in-transit/.test(part.motion);
      const offset = Math.min(part.offset, duration * .2);
      const available = Math.max(.001, duration - offset);
      const period = Math.min(part.period, available);
      const repeat = oneShot ? 0 : Math.max(0, Math.floor(available / period) - 1);
      gsap.set(part.target, part.a);
      timeline.fromTo(part.target, part.a, {...part.b, duration: period,
        repeat, yoyo: part.motion === "drill-into-rock", ease: oneShot ? "power3.out" : "none", immediateRender: false}, start + offset);
    }
  }

  function build(context, api) {
    const {scene, stage, width, height} = context;
    const {portrait, timeline, beatTime} = api;
    const diagram = ["flow", "comparison", "timeline"].includes(scene.template);
    stage.classList.add("object-stage", diagram ? "object-diagram" : "object-sequence");
    const items = scene.items;
    const count = items.length;
    const entries = [];
    const label = (text, cls) => { const node = document.createElement("div"); node.className = cls; node.textContent = text; node.dir = "auto"; return node; };
    const create = (item, index) => {
      const symbol = allowed.has(item.symbol) ? item.symbol : "concept";
      const node = document.createElement("article");
      node.className = "illustrated-item"; node.dataset.item = String(index); node.dataset.symbol = symbol;
      node.id = `${scene.id}-object-${index}`;
      const artwork = factory(symbol);
      node.append(artwork.node, label(item.label, "object-label"));
      stage.append(node);
      const entry = {node, artwork, index}; entries.push(entry); context.items.push({node});
      return entry;
    };
    if (!diagram) {
      // One subject occupies the whole stage. It changes when the narration
      // names the next item, rather than leaving a wall of labels on screen.
      items.forEach(create);
      stage.dataset.sequence = context.composition;
      const ordered = entries.map(entry => ({...entry, at: beatTime(context, entry.index), actionAt: beatTime(context, entry.index)})).sort((a, b) => a.at - b.at || a.index - b.index);
      // A tied/late estimated cue must not make an item vanish instantly. Only
      // distribute ambiguous ties; measured distinct word anchors stay exact.
      for (let i = 0; i < ordered.length; i++) {
        if (i === 0) ordered[i].at = 0;
        else if (ordered[i].at <= ordered[i - 1].at + .08) {
          const next = ordered.slice(i + 1).find(entry => entry.at > ordered[i - 1].at + .08)?.at ?? scene.duration;
          ordered[i].at = ordered[i - 1].at + (next - ordered[i - 1].at) / (ordered.length - i + 1);
        }
      }
      context.objectAnimation = () => {
        ordered.forEach((entry, i) => {
          const from = scene.start + entry.at;
          const until = scene.start + (ordered[i + 1]?.at ?? scene.duration);
          const duration = Math.max(.001, until - from);
          gsap.set(entry.node, {opacity: 0});
          timeline.fromTo(entry.node, {opacity: 0}, {opacity: 1, duration: 0, immediateRender: false}, from);
          timeline.fromTo(entry.node, {opacity: 1}, {opacity: 0, duration: .00001, immediateRender: false}, until - .00001);
          // Establish the first subject on frame zero, but begin its action
          // at the saved word/phrase cue rather than guessing a speech time.
          const actionAt = Math.min(until - .00001, Math.max(from, scene.start + entry.actionAt));
          animateActor(entry.artwork, timeline, actionAt, Math.max(.001, until - actionAt));
        });
      };
    } else {
      const columns = portrait ? (count > 3 ? 2 : 1) : (count > 3 ? 3 : count);
      const rows = Math.ceil(count / columns);
      const cellWidth = width / columns, cellHeight = height / rows;
      const positions = [];
      items.forEach((item, index) => {
        const entry = create(item, index);
        const x = (index % columns) * cellWidth;
        const y = Math.floor(index / columns) * cellHeight;
        Object.assign(entry.node.style, {left: `${x + 12}px`, top: `${y + 8}px`, width: `${cellWidth - 24}px`, height: `${cellHeight - 16}px`});
        positions.push({x: x + cellWidth / 2, y: y + cellHeight * .43});
      });
      const graph = svg("svg", {viewBox: `0 0 ${width} ${height}`, class: "object-connections", "aria-hidden": "true"}, stage);
      const connections = [];
      if (scene.template !== "comparison") positions.slice(1).forEach((end, i) => {
        const start = positions[i];
        const path = shape(graph, "path", {d: `M${start.x} ${start.y} L${end.x} ${end.y}`}, "object-route");
        const length = Math.hypot(end.x - start.x, end.y - start.y);
        path.style.strokeDasharray = `${length} ${length}`; path.style.strokeDashoffset = String(length);
        const packet = shape(graph, "circle", {cx: 0, cy: 0, r: portrait ? 11 : 9}, "object-solid");
        connections.push({path, packet, length, start, end, index: i + 1});
      });
      context.objectAnimation = () => {
        entries.forEach(entry => {
          const at = scene.start + (entry.index ? beatTime(context, entry.index) : 0);
          gsap.set(entry.node, {opacity: 0});
          timeline.fromTo(entry.node, {opacity: 0}, {opacity: 1, duration: entry.index ? Math.min(.4, scene.start + scene.duration - at) : 0, immediateRender: false}, at);
          const actionAt = scene.start + beatTime(context, entry.index);
          animateActor(entry.artwork, timeline, actionAt, Math.max(.001, scene.start + scene.duration - actionAt));
        });
        connections.forEach(({path, packet, length, start, end, index}) => {
          const at = scene.start + beatTime(context, index);
          const duration = Math.min(1.4, scene.start + scene.duration - at);
          gsap.set(packet, {opacity: 0, x: start.x, y: start.y});
          timeline.fromTo(path, {strokeDashoffset: length}, {strokeDashoffset: 0, duration, ease: "power2.inOut", immediateRender: false}, at);
          timeline.fromTo(packet, {x: start.x, y: start.y, opacity: 1}, {x: end.x, y: end.y, opacity: 1, duration, ease: "power2.inOut", immediateRender: false}, at);
          timeline.to(packet, {opacity: 0, duration: .00001}, at + Math.max(0, duration - .00001));
        });
      };
    }
    const note = label("SCHEMATIC · NOT TO SCALE", "object-schematic-note"); stage.append(note);
  }
  window.YAASObjects = {build, drawSubject};
})();
