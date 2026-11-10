/* Original YAAS shot renderer.
 * Every pose is a pure function of the saved narration clock. Three.js is a
 * local, pinned dependency. There is no model-authored code, external asset
 * request, simulation accumulator, requestAnimationFrame loop or randomness.
 * Material interactions are qualitative schematics, not ballistic predictions.
 */
(() => {
  "use strict";
  const clamp = (x, a = 0, b = 1) => Math.max(a, Math.min(b, x));
  const lerp = (a, b, t) => a + (b - a) * t;
  const smooth = x => { x = clamp(x); return x * x * (3 - 2 * x); };
  const between = (t, a, b) => smooth((t - a) / Math.max(.0001, b - a));
  const views = [];
  let THREE, renderer, environment, outputWidth, outputHeight;
  let scenePlan;

  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = String(text);
    return node;
  }
  function svg(tag, attrs, parent) {
    const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
    for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, String(value));
    if (parent) parent.append(node);
    return node;
  }

  async function prepare(plan) {
    scenePlan = plan;
    outputWidth = plan.width;
    outputHeight = plan.height;
    if (!plan.scenes.some(scene => scene.template !== "chart" && scene.shot?.technique === "diagram3d")) return;
    THREE = await import("./vendor/three.module.js");
    try {
      renderer = new THREE.WebGLRenderer({alpha: true, antialias: true, preserveDrawingBuffer: true, powerPreference: "high-performance"});
      renderer.setPixelRatio(1);
      renderer.setSize(outputWidth, outputHeight, false);
      renderer.setClearColor(0x000000, 0);
      renderer.outputColorSpace = THREE.SRGBColorSpace;
      renderer.toneMapping = THREE.ACESFilmicToneMapping;
      renderer.toneMappingExposure = 1.2;
      // A small original studio environment gives metal a readable silhouette.
      // The lights are geometry, not a downloaded image or a hidden HDRI.
      const room = new THREE.Scene();
      room.background = new THREE.Color(0x34424d);
      const white = new THREE.MeshBasicMaterial({color: 0xffffff});
      for (const [x, y, z, sx, sy, rotation] of [[-4, 3, 2, 3, 6, 1.0], [4, 1, 0, 1, 5, -1.2], [0, 5, -2, 5, 2, 0]]) {
        const panel = new THREE.Mesh(new THREE.PlaneGeometry(sx, sy), white);
        panel.position.set(x, y, z); panel.rotation.y = rotation; panel.lookAt(0, 0, 0);
        room.add(panel);
      }
      const pmrem = new THREE.PMREMGenerator(renderer);
      environment = pmrem.fromScene(room, .04).texture;
      pmrem.dispose();
      room.traverse(node => node.geometry?.dispose());
      white.dispose();
    } catch (error) {
      throw new Error(`This shot needs WebGL 2 for its 3D cutaway: ${error.message}`);
    }
  }

  function contact(scene) {
    const cue = scene.beats.find(beat => beat.target === "scene" && beat.action === "highlight");
    return cue ? clamp(cue.time / scene.duration) : .49;
  }
  function mechanismPhase(scene, t) {
    const hit = contact(scene), p = clamp(t / scene.duration);
    // This flag is derived by the application from an explicit result statement
    // following the same supported interaction. A detail of its surviving dent
    // starts with that consequence; it must not manufacture another projectile.
    if (scene.mechanism_result_view === true) {
      return {p, hit: -1, incoming: 1, result: 1, impact: 0, shock: 1,
        active: true, settled: true};
    }
    return {p, hit, incoming: between(p, Math.max(0, hit - .34), hit),
      result: between(p, hit, Math.min(1, hit + .36)),
      impact: Math.max(0, 1 - Math.abs(p - hit) / .07),
      shock: clamp((p - hit) / .25),
      active: p >= hit, settled: false};
  }

  function canvasSurface(context, type) {
    const wrap = element("div", "shot-surface");
    const surface = element("canvas", `shot-${type}-canvas`);
    surface.width = outputWidth; surface.height = outputHeight;
    surface.setAttribute("role", "img");
    surface.setAttribute("aria-label", context.scene.visual_goal || context.scene.title);
    surface.dataset.mechanism = context.scene.mechanism.kind;
    surface.dataset.material = context.scene.mechanism.material;
    surface.dataset.action = context.scene.mechanism.action;
    wrap.append(surface); context.stage.append(wrap);
    return surface;
  }

  function line(ctx, points, color, width = 2, close = false) {
    ctx.beginPath(); points.forEach(([x, y], i) => i ? ctx.lineTo(x, y) : ctx.moveTo(x, y));
    if (close) ctx.closePath();
    ctx.strokeStyle = color; ctx.lineWidth = width; ctx.stroke();
  }

  function arrow(ctx, from, to, color, width = 6) {
    const dx = to[0] - from[0], dy = to[1] - from[1], length = Math.hypot(dx, dy);
    if (length < 2) return;
    const ux = dx / length, uy = dy / length, head = Math.min(width * 3.8, length * .36);
    ctx.save(); ctx.lineCap = "round";
    line(ctx, [from, [to[0] - ux * head * .45, to[1] - uy * head * .45]], color, width);
    ctx.fillStyle = color; ctx.beginPath(); ctx.moveTo(...to);
    ctx.lineTo(to[0] - ux * head - uy * head * .45, to[1] - uy * head + ux * head * .45);
    ctx.lineTo(to[0] - ux * head + uy * head * .45, to[1] - uy * head - ux * head * .45);
    ctx.closePath(); ctx.fill(); ctx.restore();
  }

  function buildMechanism2D(context, api) {
    const {scene} = context, model = scene.mechanism;
    const surface = canvasSurface(context, "2d"), ctx = surface.getContext("2d");
    const portrait = api.portrait;
    const W = portrait ? 1080 : 1920, H = portrait ? 1920 : 1080;
    const palette = api.palette || {};
    const blue = palette.accent || "#345b64", dark = palette.ink || "#19333b", pale = "#f9f4e9", copper = "#b5653e", gold = "#d7a354";
    const framingScale = {wide: .85, medium: .93, close: 1.02, macro: 1.12}[scene.shot.framing] || .93;
    const metric = () => ({x: W * .52, y: H * (portrait ? .51 : .53), size: Math.min(W * .83, H * (portrait ? .67 : .5)) * framingScale});
    function bullet(x, y, scale = 1, rotation = 0) {
      ctx.save(); ctx.translate(x, y); ctx.rotate(rotation); ctx.scale(scale, scale);
      const fill = ctx.createLinearGradient(0, -14, 0, 14);
      fill.addColorStop(0, "#f5d68b"); fill.addColorStop(.45, "#c5853a"); fill.addColorStop(1, "#754529");
      ctx.fillStyle = fill; ctx.strokeStyle = "#5a3f24"; ctx.lineWidth = 2;
      ctx.beginPath(); ctx.moveTo(-47, -12); ctx.lineTo(15, -12); ctx.quadraticCurveTo(42, -8, 52, 0);
      ctx.quadraticCurveTo(42, 8, 15, 12); ctx.lineTo(-47, 12); ctx.closePath(); ctx.fill(); ctx.stroke();
      ctx.fillStyle = "#cba97c"; ctx.fillRect(-48, -13, 8, 26);
      ctx.restore();
    }
    function pulse(x, y, s, phase, color = copper) {
      if (!phase.active || phase.shock >= 1) return;
      ctx.save(); ctx.strokeStyle = color; ctx.globalAlpha = (1 - phase.shock) * .55;
      for (let i = 0; i < 3; i++) {
        const radius = s * (.07 + phase.shock * .36 + i * .035);
        ctx.beginPath(); ctx.ellipse(x, y, radius * .35, radius, 0, -.95, .95); ctx.lineWidth = 4 - i; ctx.stroke();
      }
      ctx.restore();
    }
    function cloth(x, y, w, h, color) {
      ctx.save(); ctx.beginPath(); ctx.rect(x, y, w, h); ctx.clip();
      ctx.strokeStyle = color; ctx.lineWidth = 2;
      for (let i = -h; i <= w + h; i += 15) {
        line(ctx, [[x + i, y], [x + i + h, y + h]], color, 2);
        line(ctx, [[x + i, y], [x + i - h, y + h]], color, 1.2);
      }
      ctx.restore();
    }
    function sheet(x, y, w, h, depth, fill, phase, woven = false) {
      const dent = phase.active && ["impact", "absorb"].includes(model.action) ? Math.sin(phase.result * Math.PI / 2) * w * .075 : 0;
      ctx.save();
      ctx.fillStyle = "#29414b"; ctx.globalAlpha = .12;
      ctx.beginPath(); ctx.ellipse(x + w * .5, y + h + 40, w * .65, 28, 0, 0, Math.PI * 2); ctx.fill(); ctx.globalAlpha = 1;
      const gradient = ctx.createLinearGradient(x, y, x + w, y + h);
      gradient.addColorStop(0, fill); gradient.addColorStop(.5, pale); gradient.addColorStop(1, fill);
      ctx.fillStyle = gradient; ctx.strokeStyle = dark; ctx.lineWidth = 3;
      ctx.beginPath(); ctx.moveTo(x, y); ctx.lineTo(x + w, y + 30); ctx.lineTo(x + w, y + h * .3);
      ctx.quadraticCurveTo(x + w + dent, y + h * .5, x + w, y + h * .7);
      ctx.lineTo(x + w, y + h); ctx.lineTo(x, y + h - 30); ctx.closePath(); ctx.fill(); ctx.stroke();
      ctx.fillStyle = fill; ctx.beginPath(); ctx.moveTo(x + w, y + 30); ctx.lineTo(x + w + depth, y + 15);
      ctx.lineTo(x + w + depth, y + h - 15); ctx.lineTo(x + w, y + h); ctx.closePath(); ctx.fill(); ctx.stroke();
      if (woven) cloth(x + 8, y + 35, w - 16, h - 72, "#48636d70");
      ctx.restore();
    }
    function armour(phase) {
      const {x, y, size: s} = metric(), framing = scene.shot.framing;
      const interacting = ["approach", "impact", "deflect", "glance", "penetrate", "absorb"].includes(model.action);
      if (model.material === "mail") {
        const radius = s * (framing === "macro" ? .065 : .045), cols = framing === "macro" ? 6 : 9, rows = framing === "macro" ? 7 : 11;
        const step = radius * 1.5, left = x - (cols - 1) * step / 2, top = y - (rows - 1) * step / 2;
        for (let row = 0; row < rows; row++) for (let col = 0; col < cols; col++) {
          const bx = left + col * step + row % 2 * step * .5, by = top + row * step;
          const distance = Math.hypot(bx - x, by - y) / (radius * 3);
          const deformation = (interacting && model.action !== "glance") || model.action === "separate" ? phase.result : 0;
          const spread = deformation * Math.max(0, 1 - distance) * radius * (["penetrate", "separate"].includes(model.action) ? 2.2 : .4);
          const angle = Math.atan2(by - y || .1, bx - x || .1);
          // Reveal a linked construction, not a fixed texture with a moving
          // caption. The saved clock places every ring; reverse seeks rebuild
          // exactly the same arrangement without a physics accumulator.
          const rank = (row + col * .55) / (rows - 1 + (cols - 1) * .55);
          const joined = model.action === "reveal" ? between(phase.p, rank * .42, rank * .42 + .25) : 1;
          const xx = bx + Math.cos(angle) * spread + (1 - joined) * radius * .6;
          const yy = by + Math.sin(angle) * spread - (1 - joined) * radius * 1.5;
          ctx.save(); ctx.globalAlpha = .18 + .82 * joined;
          ctx.beginPath(); ctx.ellipse(xx, yy, radius, radius * .82, row % 2 ? -.23 : .23, 0, Math.PI * 2);
          ctx.lineWidth = radius * .28; ctx.strokeStyle = dark; ctx.stroke();
          ctx.lineWidth = radius * .15; ctx.strokeStyle = row % 2 ? "#81949a" : "#b3c0bc"; ctx.stroke();
          ctx.beginPath(); ctx.ellipse(xx, yy - 1, radius, radius * .82, row % 2 ? -.23 : .23, 3.5, 5.7);
          ctx.lineWidth = radius * .07; ctx.strokeStyle = "#f7f4e6"; ctx.stroke();
          ctx.restore();
        }
        if (interacting && model.action !== "glance" && !phase.settled) {
          const px = !phase.active ? lerp(x - s * .62, x - s * .07, phase.incoming)
            : model.action === "penetrate" ? lerp(x - s * .07, x + s * .6, phase.result)
            : model.action === "deflect" ? x - s * .07 - s * .37 * phase.result : x - s * .07;
          const py = model.action === "deflect" ? y - s * .37 * phase.result : y;
          bullet(px, py, s / 800, model.action === "deflect" ? -phase.result * 2 : 0);
          if (model.action !== "approach") pulse(x, y, s, phase);
        }
        if (model.action === "glance") {
          const face = x - s * .08;
          arrow(ctx, [x - s * .55, y], [lerp(x - s * .5, face, phase.incoming), y], copper, s * .009);
          if (phase.active) arrow(ctx, [face, y], [face - s * .32 * phase.result, y - s * .35 * phase.result], blue, s * .009);
        }
      } else if (interacting) {
        // A profile cutaway makes contact, bending and passage readable.
        const width = s * .13, top = y - s * .4, height = s * .8;
        const redirects = ["deflect", "glance"].includes(model.action);
        const tilt = redirects ? .42 : -.12;
        // The tip reaches the actual front face at the narration contact beat.
        // A glancing blow changes direction at that face instead of travelling
        // through the plate and then reversing somewhere behind it.
        const contactX = x - width / (2 * Math.cos(tilt)), tip = s * 52 / 800;
        const penetration = model.action === "penetrate" && phase.active;
        ctx.save(); ctx.translate(x, y); ctx.rotate(tilt); ctx.translate(-x, -y);
        const metal = ctx.createLinearGradient(x - width, 0, x + width, 0);
        metal.addColorStop(0, "#3b5965"); metal.addColorStop(.4, "#c1cecc"); metal.addColorStop(.65, "#f5f0dd"); metal.addColorStop(1, "#57717a");
        ctx.fillStyle = metal; ctx.strokeStyle = dark; ctx.lineWidth = 4;
        if (penetration) {
          const halfGap = phase.result * s * .06;
          for (const [a, b] of [[top, y - halfGap], [y + halfGap, top + height]]) {
            ctx.beginPath(); ctx.moveTo(x - width / 2, a); ctx.lineTo(x + width / 2, a);
            ctx.lineTo(x + width / 2, b); ctx.lineTo(x - width / 2, b); ctx.closePath(); ctx.fill(); ctx.stroke();
          }
        } else {
          const bend = ["impact", "absorb", "deflect"].includes(model.action) ? phase.result * s * (model.action === "deflect" ? .025 : .07) : 0;
          ctx.beginPath(); ctx.moveTo(x - width / 2, top); ctx.lineTo(x + width / 2, top);
          ctx.lineTo(x + width / 2, y - s * .17); ctx.quadraticCurveTo(x + width / 2 + bend, y, x + width / 2, y + s * .17);
          ctx.lineTo(x + width / 2, top + height); ctx.lineTo(x - width / 2, top + height);
          ctx.lineTo(x - width / 2, y + s * .17); ctx.quadraticCurveTo(x - width / 2 + bend, y, x - width / 2, y - s * .17);
          ctx.closePath(); ctx.fill(); ctx.stroke();
        }
        ctx.restore();
        const reflected = [-Math.cos(2 * tilt), -Math.sin(2 * tilt)];
        const px = phase.active ? redirects ? contactX - tip + reflected[0] * s * .48 * phase.result
          : model.action === "penetrate" ? lerp(contactX - tip, x + s * .49, phase.result)
          : contactX - tip + s * .035 * phase.result
          : lerp(x - s * .55, contactX - tip, phase.incoming);
        const py = redirects && phase.active ? y + reflected[1] * s * .48 * phase.result : y;
        if (model.action === "glance") {
          // The narration names a blow, not a particular weapon/projectile.
          // Arrows explain the direction change without inventing either one.
          const front = contactX - 8;
          arrow(ctx, [x - s * .55, y], [lerp(x - s * .5, front, phase.incoming), y], copper, s * .009);
          if (phase.active) arrow(ctx, [front, y],
            [front + reflected[0] * s * .48 * phase.result, y + reflected[1] * s * .48 * phase.result], blue, s * .009);
        } else if (!phase.settled) {
          line(ctx, [[x - s * .6, y], [phase.active ? contactX : px - tip, y]], "#b56f4555", 3);
          if (model.action === "deflect" && phase.active) {
            ctx.save(); ctx.setLineDash([9, 9]);
            line(ctx, [[contactX, y], [px, py]], "#b56f4577", 3); ctx.restore();
          }
          bullet(px, py, s / 800, model.action === "deflect" && phase.active
            ? Math.atan2(reflected[1], reflected[0]) * between(phase.result, 0, .16) : 0);
          if (model.action !== "approach") pulse(contactX, y, s, phase);
        }
      } else {
        // Original curved breastplate silhouette, with a neck and a waist.
        ctx.save(); ctx.translate(x, y); const scale = s / 800; ctx.scale(scale, scale);
        const metal = ctx.createLinearGradient(-290, -300, 310, 280);
        metal.addColorStop(0, "#35545f"); metal.addColorStop(.28, "#9bafae"); metal.addColorStop(.48, "#e7e4d3");
        metal.addColorStop(.55, "#81979b"); metal.addColorStop(1, "#294852");
        ctx.fillStyle = metal; ctx.strokeStyle = dark; ctx.lineWidth = 5;
        ctx.beginPath(); ctx.moveTo(-247, -245); ctx.lineTo(-147, -340); ctx.quadraticCurveTo(0, -205, 147, -340);
        ctx.lineTo(247, -245); ctx.lineTo(204, 248); ctx.quadraticCurveTo(0, 376, -204, 248); ctx.closePath(); ctx.fill(); ctx.stroke();
        line(ctx, [[0, -245], [0, 297]], "#f5f0d5", 5);
        for (const side of [-1, 1]) for (let row = 0; row < 6; row++) {
          const yy = -211 + row * 88, xx = side * (218 - row * 5);
          ctx.beginPath(); ctx.arc(xx, yy, 7, 0, Math.PI * 2); ctx.fillStyle = gold; ctx.fill(); ctx.strokeStyle = "#634625"; ctx.lineWidth = 1; ctx.stroke();
        }
        ctx.restore();
      }
    }
    function layers(phase) {
      const {x, y, size: s} = metric();
      const count = model.kind !== "layers" ? 1 : model.material === "mixed" ? 3 : 2;
      const spread = model.action === "separate" ? between(phase.p, .05, .76) : .7;
      const gap = s * (.05 + spread * .19), w = s * .33, h = s * .76;
      const start = x - (w + (count - 1) * gap) / 2;
      const color = {ceramic: "#d2c8af", fiber: "#a8b7b0", steel: "#7f989f"}[model.material] || "#7f989f";
      const colors = model.material === "mixed" ? ["#d2c8af", "#6b8d93", "#a8b7b0"] : Array(count).fill(color);
      for (let i = count - 1; i >= 0; i--) sheet(start + i * gap, y - h / 2 - i * 27, w, h, s * .045, colors[i], phase,
        model.material === "fiber" || model.material === "mixed" && i === 2);
      if (["impact", "absorb", "penetrate", "approach"].includes(model.action) && !phase.settled) {
        const destination = start - s * .025;
        const px = !phase.active ? lerp(x - s * .58, destination, phase.incoming)
          : model.action === "penetrate" ? lerp(destination, x + s * .56, phase.result) : destination + s * .08 * phase.result;
        bullet(px, y, s / 800);
        pulse(destination + s * .03, y, s, phase, blue);
      }
      // The drawing represents layers only. No resistance numbers are inferred.
      const labels = scene.items.slice(0, count).map(item => item.label);
      ctx.font = `600 ${portrait ? 30 : 27}px 'Inter Variable', sans-serif`;
      ctx.fillStyle = dark; ctx.textAlign = "center";
      labels.forEach((text, i) => {
        if (text.length > 34) return;
        const xx = start + i * gap + w / 2;
        const maxWidth = (count > 1 ? gap : w) * .87, lines = [];
        let current = "";
        for (const word of text.split(/\s+/)) {
          const next = current ? `${current} ${word}` : word;
          if (current && ctx.measureText(next).width > maxWidth) { lines.push(current); current = word; }
          else current = next;
        }
        if (current) lines.push(current);
        if (lines.length > 3 || lines.some(value => ctx.measureText(value).width > maxWidth)) return;
        const baseline = y - h / 2 - 31, lineHeight = portrait ? 36 : 32;
        line(ctx, [[xx, y - h / 2 - 12], [xx, baseline + 9]], `${dark}80`, 2);
        lines.forEach((value, row) => ctx.fillText(value, xx, baseline - (lines.length - row - 1) * lineHeight));
      });
    }
    function draw(time) {
      const phase = mechanismPhase(scene, time);
      ctx.setTransform(outputWidth / W, 0, 0, outputHeight / H, 0, 0);
      ctx.clearRect(0, 0, W, H);
      const {x, y, size: s} = metric();
      const bg = ctx.createRadialGradient(x, y, 0, x, y, s * .8);
      bg.addColorStop(0, palette.surface || "#fffaf1"); bg.addColorStop(1, `${palette.background || "#e6dece"}00`);
      ctx.fillStyle = bg; ctx.fillRect(0, 0, W, H);
      ctx.strokeStyle = "#6c7d7914"; ctx.lineWidth = 1;
      for (let gx = W * .08; gx < W * .95; gx += 78) line(ctx, [[gx, H * .27], [gx, H * .79]], `${dark}14`, 1);
      for (let gy = H * .28; gy < H * .82; gy += 78) line(ctx, [[W * .07, gy], [W * .94, gy]], `${dark}14`, 1);
      ctx.save();
      const zoom = scene.shot.camera === "push" ? 1 + phase.p * .07 : 1;
      const pan = scene.shot.camera === "track" ? (phase.p - .5) * s * .09 : 0;
      ctx.translate(pan, 0);
      ctx.translate(x, y); ctx.scale(zoom, zoom); ctx.translate(-x, -y);
      const layered = model.kind === "layers" || ["ceramic", "fiber", "mixed"].includes(model.material);
      if (layered) layers(phase); else armour(phase);
      ctx.restore();
      surface.dataset.phase = phase.p.toFixed(6); surface.dataset.contact = phase.active ? "after" : "before";
      surface.dataset.resultView = String(phase.settled);
      surface.dataset.projectileVisible = String(!phase.settled && (layered
        ? ["approach", "impact", "penetrate", "absorb"] : ["approach", "impact", "deflect", "penetrate", "absorb"]).includes(model.action));
      surface.dataset.forceDirectionVisible = String(model.action === "glance");
    }
    views.push({scene, draw, surface});
  }

  function metal(color = 0x9aaeb6, roughness = .32, metalness = .72) {
    return new THREE.MeshStandardMaterial({color, roughness, metalness});
  }
  function mesh(geometry, material, parent, position = [0, 0, 0]) {
    const node = new THREE.Mesh(geometry, material); node.position.set(...position); parent.add(node); return node;
  }
  function tube(points, radius, material, parent) {
    return mesh(new THREE.TubeGeometry(new THREE.CatmullRomCurve3(points.map(p => new THREE.Vector3(...p))), 24, radius, 6, false), material, parent);
  }
  function weaveTexture() {
    const canvas = document.createElement("canvas"); canvas.width = canvas.height = 128;
    const ctx = canvas.getContext("2d"); ctx.fillStyle = "#597076"; ctx.fillRect(0, 0, 128, 128);
    for (let i = 0; i < 16; i++) for (let j = 0; j < 16; j++) {
      ctx.fillStyle = (i + j) % 2 ? "#819798" : "#405a63";
      ctx.fillRect(i * 8 + 1, j * 8 + 1, (i + j) % 2 ? 6 : 3, (i + j) % 2 ? 3 : 6);
      ctx.fillStyle = "#a1b0a7"; ctx.fillRect(i * 8 + 1, j * 8 + 1, 1, 3);
    }
    const texture = new THREE.CanvasTexture(canvas); texture.colorSpace = THREE.SRGBColorSpace;
    texture.wrapS = texture.wrapT = THREE.RepeatWrapping; texture.repeat.set(3, 4);
    return texture;
  }

  function plateModel(parent, perforated, colour = 0x97a8b0) {
    const group = new THREE.Group(); parent.add(group);
    const shape = new THREE.Shape();
    shape.moveTo(-.96, .9); shape.lineTo(-.62, 1.29); shape.quadraticCurveTo(-.3, .96, 0, .97);
    shape.quadraticCurveTo(.3, .96, .62, 1.29); shape.lineTo(.96, .9);
    shape.lineTo(.78, -.99); shape.quadraticCurveTo(0, -1.43, -.78, -.99); shape.closePath();
    if (perforated) { const hole = new THREE.Path(); hole.absarc(0, 0, .17, 0, Math.PI * 2, true); shape.holes.push(hole); }
    const outline = new THREE.ExtrudeGeometry(shape, {depth: .095, bevelEnabled: true, bevelThickness: .018, bevelSize: .018, bevelSegments: 2, curveSegments: 24});
    // ShapeGeometry triangulates only the outline. Add interior vertices so a
    // contact actually bends the plate surface, instead of floating a mark on
    // an unchanged flat triangle. Only broad front/back faces need subdivision.
    const points = outline.attributes.position, sourceNormals = outline.attributes.normal;
    const subdivided = [], subdividedNormals = [];
    const distance2 = (a, b) => (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2;
    function split(a, b, c, na, nb, nc, depth = 0) {
      const lengths = [distance2(a, b), distance2(b, c), distance2(c, a)];
      const maximum = Math.max(...lengths), flat = Math.max(a[2], b[2], c[2]) - Math.min(a[2], b[2], c[2]) < .00001;
      if (flat && maximum > .22 ** 2 && depth < 9) {
        const index = lengths.indexOf(maximum), triangle = [a, b, c], normals = [na, nb, nc];
        const u = triangle[index], v = triangle[(index + 1) % 3], opposite = triangle[(index + 2) % 3];
        const nu = normals[index], nv = normals[(index + 1) % 3], no = normals[(index + 2) % 3];
        const middle = u.map((value, i) => (value + v[i]) / 2);
        const normal = nu.map((value, i) => (value + nv[i]) / 2);
        split(u, middle, opposite, nu, normal, no, depth + 1);
        split(middle, v, opposite, normal, nv, no, depth + 1);
      } else { subdivided.push(...a, ...b, ...c); subdividedNormals.push(...na, ...nb, ...nc); }
    }
    for (let i = 0; i < points.count; i += 3) split(
      [points.getX(i), points.getY(i), points.getZ(i)],
      [points.getX(i + 1), points.getY(i + 1), points.getZ(i + 1)],
      [points.getX(i + 2), points.getY(i + 2), points.getZ(i + 2)],
      [sourceNormals.getX(i), sourceNormals.getY(i), sourceNormals.getZ(i)],
      [sourceNormals.getX(i + 1), sourceNormals.getY(i + 1), sourceNormals.getZ(i + 1)],
      [sourceNormals.getX(i + 2), sourceNormals.getY(i + 2), sourceNormals.getZ(i + 2)]);
    const geometry = new THREE.BufferGeometry(); geometry.setAttribute("position", new THREE.Float32BufferAttribute(subdivided, 3));
    geometry.setAttribute("normal", new THREE.Float32BufferAttribute(subdividedNormals, 3));
    outline.dispose();
    const coordinates = geometry.attributes.position;
    for (let i = 0; i < coordinates.count; i++) coordinates.setZ(i, coordinates.getZ(i) + .37 * (1 - Math.pow(coordinates.getX(i) / 1.05, 2)));
    // The outline triangulator duplicates vertices at triangle boundaries.
    // Recomputing normals on that non-indexed mesh produces a faceted surface.
    // Transform the extrusion normals with the actual curvature derivative,
    // preserving the rolled edge while keeping broad steel faces continuous.
    function smoothPlateNormals(depth = 0) {
      const normals = geometry.attributes.normal;
      for (let i = 0; i < coordinates.count; i++) {
        const x = subdivided[i * 3], y = subdivided[i * 3 + 1];
        const depression = depth * Math.exp(-(x * x + y * y) / .075);
        const dx = -.74 * x / 1.05 ** 2 + depression * 2 * x / .075;
        const dy = depression * 2 * y / .075;
        const nz = subdividedNormals[i * 3 + 2];
        const nx = subdividedNormals[i * 3] - dx * nz, ny = subdividedNormals[i * 3 + 1] - dy * nz;
        const length = Math.hypot(nx, ny, nz) || 1;
        normals.setXYZ(i, nx / length, ny / length, nz / length);
      }
      normals.needsUpdate = true;
    }
    smoothPlateNormals();
    const steel = metal(colour, .33, .8); const plate = mesh(geometry, steel, group);
    const edge = new THREE.LineSegments(new THREE.EdgesGeometry(geometry, 32), new THREE.LineBasicMaterial({color: 0x576a72, transparent: true, opacity: .44})); group.add(edge);
    const detail = metal(0xe3bd76, .33, .8);
    for (const side of [-1, 1]) for (let row = 0; row < 7; row++) {
      const x = side * (.87 - row * .019), y = .78 - row * .282;
      const rivet = mesh(new THREE.SphereGeometry(.025, 10, 8), detail, group, [x, y, .37 * (1 - (x / 1.05) ** 2) + .112]);
      rivet.scale.z = .55;
    }
    const spine = tube([[0, -.96, .485], [0, -.5, .5], [0, 0, .49], [0, .5, .48], [0, .9, .48]], .012, metal(0xd4d7c9, .25, .7), group);
    const plug = perforated ? mesh(new THREE.CylinderGeometry(.168, .17, .12, 36), steel, group, [0, 0, .43]) : null;
    if (plug) plug.rotation.x = Math.PI / 2;
    const dent = mesh(new THREE.TorusGeometry(.2, .018, 8, 40), steel, group, [0, 0, .487]); dent.visible = false;
    const surfaces = [geometry, spine.geometry, edge.geometry].map(surface => ({surface, original: Float32Array.from(surface.attributes.position.array)}));
    function pose(phase, action) {
      const depth = ["impact", "absorb", "deflect"].includes(action) ? phase.result * (action === "deflect" ? .065 : .17) : 0;
      for (const {surface, original} of surfaces) {
        const vertices = surface.attributes.position;
        for (let i = 0; i < vertices.count; i++) {
          const x = original[i * 3], y = original[i * 3 + 1], z = original[i * 3 + 2];
          vertices.setXYZ(i, x, y, z - depth * Math.exp(-(x * x + y * y) / .075));
        }
        vertices.needsUpdate = true;
        if (surface === geometry) smoothPlateNormals(depth);
        else if (surface.attributes.normal) surface.computeVertexNormals();
        surface.computeBoundingBox();
      }
      dent.position.z = .487 - depth * .5;
    }
    return {group, plate, plug, dent, pose};
  }

  function mailModel(parent, detail = false) {
    const group = new THREE.Group(); parent.add(group);
    // A macro view is an explicitly cropped fabric sample. Its larger links
    // reveal the opening mechanism; it is not a new kind of armour.
    const positions = [], columns = detail ? 7 : 10, rows = detail ? 8 : 13;
    for (let row = 0; row < rows; row++) for (let col = 0; col < columns; col++) {
      const x = (col - (columns - 1) / 2) * .177 + (row % 2) * .088, y = (row - (rows - 1) / 2) * .171;
      positions.push({x, y, z: .22 * (1 - (x / 1.1) ** 2), tilt: (row % 2 ? 1 : -1) * .37, spin: (col % 2 ? 1 : -1) * .06});
    }
    const rings = new THREE.InstancedMesh(new THREE.TorusGeometry(.118, .022, 8, 20), metal(0xb5c4c5, .31, .88), positions.length);
    rings.instanceMatrix.setUsage(THREE.DynamicDrawUsage); group.add(rings);
    const object = new THREE.Object3D();
    function pose(phase, action) {
      const deformation = ["impact", "absorb", "deflect", "penetrate", "separate"].includes(action) ? phase.result : 0;
      positions.forEach((p, i) => {
        const distance = Math.hypot(p.x, p.y), affect = Math.max(0, 1 - distance / .55);
        const open = ["penetrate", "separate"].includes(action) ? deformation * affect * .35 : 0;
        const theta = Math.atan2(p.y + .001, p.x + .001);
        object.position.set(p.x + Math.cos(theta) * open, p.y + Math.sin(theta) * open,
          p.z - deformation * affect * .25);
        object.rotation.set(p.tilt + deformation * affect * .9, p.spin + deformation * affect * .55, theta * open * .5);
        object.scale.set(1, 1 + deformation * affect * .25, 1); object.updateMatrix(); rings.setMatrixAt(i, object.matrix);
      });
      rings.instanceMatrix.needsUpdate = true; rings.computeBoundingSphere();
    }
    pose({result: 0}, "reveal");
    return {group, pose};
  }

  function layersModel(parent, materialKind, layered = true) {
    const group = new THREE.Group(); parent.add(group);
    const layers = [], fragments = [], frame = new THREE.Group(); group.add(frame);
    const fabric = new THREE.MeshStandardMaterial({color: 0x82968f, map: weaveTexture(), roughness: .86, metalness: .06});
    const core = new THREE.MeshStandardMaterial({color: 0x526c75, roughness: .56, metalness: .18});
    const ceramic = new THREE.MeshStandardMaterial({color: 0xdcd4be, roughness: .46, metalness: .05});
    const steel = metal(0x97a8b0, .29, .8);
    const single = {fiber: fabric, ceramic, steel}[materialKind] || steel;
    // Do not silently turn a steel/ceramic/fiber source into a composite. A
    // single armour material gets one panel; an explicit layers mechanism gets
    // repeated layers of that stated material. Only "mixed" adds other types.
    const materials = materialKind === "mixed" ? [ceramic, core, fabric] : Array(layered ? 2 : 1).fill(single);
    const breastplates = materialKind === "steel" && layered;
    for (let layer = 0; layer < materials.length; layer++) {
      const plane = new THREE.Group(); group.add(plane); layers.push(plane);
      if (breastplates) {
        // The base and reinforcement remain the same stated steel material.
        // A second curved shell, with a small presentation tint, makes the
        // relationship legible instead of two indistinguishable flat boxes.
        const shell = plateModel(plane, false, layer === 0 ? 0x9baeb3 : 0x658791);
        shell.group.scale.setScalar(layer === 0 ? 1 : .91);
        plane.userData.shell = shell;
      } else if (layer === 0 && materialKind === "mixed") {
        for (let row = 0; row < 8; row++) for (let col = 0; col < 6; col++) {
          const x = (col - 2.5) * .303 + (row % 2 ? .076 : -.076), y = (row - 3.5) * .265;
          const tile = mesh(new THREE.CylinderGeometry(.173, .173, .09, 6), ceramic, plane, [x, y, 0]);
          tile.rotation.x = Math.PI / 2;
          fragments.push({tile, x, y, near: Math.max(0, 1 - Math.hypot(x, y) / .55)});
        }
      } else {
        const geometry = new THREE.BoxGeometry(1.94, 2.35, .09, 24, 30, 1);
        const sheet = mesh(geometry, materials[layer], plane);
        plane.userData.sheet = sheet; plane.userData.original = Float32Array.from(geometry.attributes.position.array);
      }
      if (!breastplates) {
        const edge = mesh(new THREE.BoxGeometry(2.0, .025, .12), metal(0x9baba5, .4, .3), plane, [0, -1.18, 0]);
        edge.name = "layer-edge";
      }
    }
    function pose(phase, action) {
      const separation = action === "separate" ? between(phase.p, .08, .78) : action === "compare" ? .88 : .15;
      layers.forEach((layer, i) => {
        if (breastplates) {
          // Index zero is the original breastplate. The reinforcing shell is
          // in front of it and separates far enough in screen space for both
          // silhouettes to remain visible, even with a locked portrait camera.
          layer.position.set((i - .5) * separation * 2.7,
            (i - .5) * separation * -.25, i * (.17 + separation * .5));
          layer.userData.shell.pose(phase, action);
        } else {
          layer.position.z = .4 - i * (.13 + separation * .62);
          layer.position.x = i * separation * .12;
        }
        const sheet = layer.userData.sheet;
        if (!sheet) return;
        const original = layer.userData.original, attributes = sheet.geometry.attributes.position;
        for (let v = 0; v < attributes.count; v++) {
          const x = original[v * 3], y = original[v * 3 + 1], z = original[v * 3 + 2];
          const depression = ["absorb", "impact"].includes(action) ? phase.result * .24 * Math.exp(-(x * x + y * y) / (.07 + i * .23)) : 0;
          attributes.setXYZ(v, x, y, z - depression);
        }
        attributes.needsUpdate = true; sheet.geometry.computeVertexNormals();
      });
      for (const {tile, x, y, near} of fragments) {
        const broken = ["impact", "absorb", "penetrate"].includes(action) ? phase.result * near : 0;
        tile.position.set(x + Math.sign(x || .1) * broken * .2, y + Math.sign(y || .1) * broken * .21, -broken * .12);
        tile.rotation.set(Math.PI / 2 + broken * .3, broken * .4, broken * Math.sign(x) * .6);
      }
    }
    pose({p: 0, result: 0}, "reveal");
    return {group, layers, pose, breastplates};
  }

  function projectile(parent) {
    const group = new THREE.Group(); parent.add(group);
    mesh(new THREE.CylinderGeometry(.085, .085, .27, 20), metal(0xc7894a, .28, .82), group, [0, -.04, 0]);
    mesh(new THREE.ConeGeometry(.085, .2, 20), metal(0xe1b474, .28, .72), group, [0, .195, 0]);
    mesh(new THREE.CylinderGeometry(.091, .091, .035, 20), metal(0xb99b66, .34, .62), group, [0, -.185, 0]);
    group.rotation.x = -Math.PI / 2;
    return group;
  }

  function forceArrow(parent, direction, origin, colour) {
    const group = new THREE.Group(); parent.add(group);
    group.position.copy(origin);
    group.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), direction);
    const paint = new THREE.MeshBasicMaterial({color: colour});
    const shaft = mesh(new THREE.CylinderGeometry(.022, .022, 1, 12), paint, group);
    const tip = mesh(new THREE.ConeGeometry(.074, .19, 16), paint, group);
    group.setLength = length => {
      const head = Math.min(.19, length * .35), stem = Math.max(.002, length - head);
      shaft.scale.y = stem; shaft.position.y = stem / 2;
      tip.scale.set(head / .19, head / .19, head / .19); tip.position.y = stem + head / 2;
    };
    group.setLength(.01);
    return group;
  }

  function buildMechanism3D(context, api) {
    if (!THREE || !renderer) throw new Error("The local 3D renderer was not prepared");
    const {scene} = context, model = scene.mechanism, portrait = api.portrait;
    const surface = canvasSurface(context, "3d"), pixels = surface.getContext("2d", {alpha: true});
    const world = new THREE.Scene(); world.environment = environment;
    world.add(new THREE.HemisphereLight(0xd8e8ed, 0x364149, 2.15));
    const key = new THREE.DirectionalLight(0xffe5be, 4.5); key.position.set(-3, 5, 5); world.add(key);
    const edge = new THREE.DirectionalLight(0x9fd8f1, 3.1); edge.position.set(4, 1, -2); world.add(edge);
    const fill = new THREE.DirectionalLight(0xffffff, 1.2); fill.position.set(1, -2, 4); world.add(fill);
    const assembly = new THREE.Group(); world.add(assembly);
    const material = model.kind === "layers" || ["ceramic", "fiber", "mixed"].includes(model.material)
      ? layersModel(assembly, model.material, model.kind === "layers") : model.material === "mail" ? mailModel(assembly, scene.shot.framing === "macro") : plateModel(assembly, model.action === "penetrate");
    const moving = projectile(assembly);
    const forceContact = new THREE.Vector3(-.65, 0, .095 + .37 * (1 - (.65 / 1.05) ** 2));
    const forceStart = new THREE.Vector3(-1.8, -.42, 1.3);
    const forceNormal = new THREE.Vector3(.74 * forceContact.x / 1.05 ** 2, 0, 1).normalize();
    const forceDirection = forceContact.clone().sub(forceStart).normalize().reflect(forceNormal);
    const forceIn = forceArrow(assembly, forceContact.clone().sub(forceStart).normalize(), forceStart, 0xe39e70);
    const forceOut = forceArrow(assembly, forceDirection, forceContact, 0x78b7bf);
    forceIn.visible = forceOut.visible = false;
    const sparks = [];
    for (let i = 0; i < 13; i++) {
      const spark = mesh(new THREE.OctahedronGeometry(.018 + (i % 3) * .006), new THREE.MeshBasicMaterial({color: i % 3 ? 0xe6bd77 : 0xffedd4}), assembly);
      sparks.push(spark);
    }
    const shock = mesh(new THREE.TorusGeometry(.19, .008, 5, 64), new THREE.MeshBasicMaterial({color: 0xecc58d, transparent: true, opacity: 0}), assembly, [0, 0, .55]);
    const ground = mesh(new THREE.CircleGeometry(4, 64), new THREE.MeshBasicMaterial({color: 0x20303a, transparent: true, opacity: .34, depthWrite: false}), world, [0, -1.53, 0]);
    ground.rotation.x = -Math.PI / 2;
    // A simple original stand establishes scale without an invented human.
    const stand = new THREE.Group(); assembly.add(stand);
    mesh(new THREE.CylinderGeometry(.045, .045, 1.0, 16), metal(0x455761, .56, .4), stand, [0, -1.1, -.4]);
    mesh(new THREE.CylinderGeometry(.68, .8, .08, 48), metal(0x293b46, .5, .45), stand, [0, -1.56, -.4]);
    stand.visible = scene.shot.role === "establish" && model.kind === "armour";
    // A horizon-sized grey disc obscured the bottom half of detail shots.
    // Only the establishing stand needs a ground; exploded diagrams float in
    // their existing neutral studio field so all layers stay visible.
    ground.visible = stand.visible;
    ground.scale.setScalar(.42);
    const camera = new THREE.PerspectiveCamera(portrait ? 35 : 31, outputWidth / outputHeight, .05, 80);
    // Fit the actual mechanism at both ends of its action, not an assumed
    // icon-sized box. This keeps exploded backing layers and mail visible in
    // portrait while leaving a safe band for the title and measured captions.
    const bounds = new THREE.Box3().setFromObject(material.group);
    material.pose?.({p: 1, result: 1, active: true, shock: 1}, model.action);
    bounds.union(new THREE.Box3().setFromObject(material.group));
    if (model.action === "glance") {
      bounds.expandByPoint(forceStart);
      bounds.expandByPoint(forceContact.clone().addScaledVector(forceDirection, 1.7));
    }
    if (model.material === "mail") bounds.expandByScalar(.1);
    material.pose?.({p: 0, result: 0, active: false, shock: 0}, model.action);
    const centre = bounds.getCenter(new THREE.Vector3());
    const corners = [];
    for (const x of [bounds.min.x, bounds.max.x]) for (const y of [bounds.min.y, bounds.max.y]) for (const z of [bounds.min.z, bounds.max.z]) corners.push(new THREE.Vector3(x, y, z).sub(centre));
    const coverage = {wide: [.71, .51], medium: [.78, .55], close: [.86, .61], macro: [.91, .65]}[scene.shot.framing] || [.78, .55];
    const labels = [];
    let leaders;
    if (material.layers && model.action === "separate") {
      leaders = svg("svg", {viewBox: portrait ? "0 0 1080 1920" : "0 0 1920 1080", class: "shot-layer-leaders", "aria-hidden": "true"}, context.stage);
      scene.items.slice(0, material.layers.length).forEach((item, i) => {
        const label = element("div", "shot-layer-label", item.label.length <= 64 ? item.label : "");
        const connector = svg("polyline", {fill: "none", "stroke-width": 2.5}, leaders);
        const endpoint = svg("circle", {r: 5}, leaders);
        context.stage.append(label); labels.push({label, connector, endpoint, layer: material.layers[i], index: i});
      });
    }
    function draw(time) {
      const phase = mechanismPhase(scene, time), action = model.action;
      const interacting = ["approach", "impact", "deflect", "glance", "penetrate", "absorb"].includes(action);
      const orbit = scene.shot.camera === "orbit" ? lerp(-.3, .34, smooth(phase.p)) : 0;
      const side = action === "glance" ? .78 : interacting ? 1.0 : model.action === "separate" ? .74 : .55;
      const theta = side + orbit;
      const forward = new THREE.Vector3(Math.sin(theta), .14, Math.cos(theta)).normalize();
      const right = new THREE.Vector3().crossVectors(new THREE.Vector3(0, 1, 0), forward).normalize();
      const up = new THREE.Vector3().crossVectors(forward, right);
      const tanY = Math.tan(camera.fov * Math.PI / 360), tanX = tanY * camera.aspect;
      let radius = 0;
      for (const corner of corners) radius = Math.max(radius, corner.dot(forward) + Math.max(Math.abs(corner.dot(right)) / (tanX * coverage[0]), Math.abs(corner.dot(up)) / (tanY * coverage[1])));
      radius *= scene.shot.camera === "push" ? lerp(1.04, 1, phase.p) : 1.02;
      const target = centre.clone().add(new THREE.Vector3(0, radius * tanY * (portrait ? .06 : .08), 0));
      camera.position.copy(target).addScaledVector(forward, radius);
      camera.lookAt(target);
      // Framing remains centred on the causal relationship, not a spinning logo.
      assembly.rotation.set(-.03, scene.shot.camera === "locked" ? 0 : Math.sin(phase.p * Math.PI) * .025, 0);
      material.pose?.(phase, action);
      if (material.plug) material.plug.visible = !(phase.active && action === "penetrate");
      if (material.dent) {
        material.dent.visible = phase.active && (["impact", "deflect", "penetrate"].includes(action)
          || phase.settled && action === "absorb");
        material.dent.scale.set(1 + phase.result * .4, 1 + phase.result * .22, 1);
      }
      moving.visible = interacting && action !== "glance" && !phase.settled;
      moving.position.set(0, 0, lerp(3.0, .7, phase.incoming));
      moving.rotation.set(-Math.PI / 2, 0, 0);
      moving.scale.set(1, 1, 1);
      if (phase.active) {
        if (action === "penetrate") moving.position.z = lerp(.7, -2.2, phase.result);
        else if (action === "deflect") {
          moving.position.set(phase.result * 1.4, phase.result * .85, .7 + phase.result * 1.6);
          moving.rotation.set(lerp(-Math.PI / 2, .8, phase.result), 0, lerp(0, -1.1, phase.result));
        } else if (action === "absorb" || action === "impact") {
          moving.position.z = .7 - phase.result * .23;
          moving.scale.set(1 + phase.result * .48, 1 - phase.result * .32, 1 + phase.result * .48);
        }
      }
      forceIn.visible = action === "glance";
      forceOut.visible = action === "glance" && phase.active && phase.result > .001;
      if (forceIn.visible) forceIn.setLength(Math.max(.01, forceStart.distanceTo(forceContact) * phase.incoming));
      if (forceOut.visible) forceOut.setLength(Math.max(.01, 1.7 * phase.result));
      // Small deterministic fragments indicate contact, never a gun explosion.
      sparks.forEach((spark, i) => {
        const theta = i * 2.39996, age = clamp((phase.p - phase.hit) / .2);
        spark.visible = interacting && !phase.settled && phase.active && age < 1 && !["approach", "glance"].includes(action);
        spark.position.set(Math.cos(theta) * age * .65, Math.sin(theta) * age * .65 - age * age * .12, .53 + age * (.13 + i % 4 * .08));
        spark.scale.setScalar(Math.max(.01, 1 - age)); spark.rotation.set(i * .3 + age, i * .2, age);
      });
      const age = clamp((phase.p - phase.hit) / .27);
      shock.visible = interacting && !phase.settled && phase.active && !["approach", "glance"].includes(action);
      shock.scale.setScalar(1 + age * 4.2); shock.material.opacity = (1 - age) * .38;
      world.updateMatrixWorld(true); camera.updateMatrixWorld(true);
      renderer.render(world, camera);
      pixels.clearRect(0, 0, outputWidth, outputHeight); pixels.drawImage(renderer.domElement, 0, 0);
      for (const {label, connector, endpoint, layer, index} of labels) {
        const anchor = material.breastplates ? new THREE.Vector3(index === 0 ? -.52 : .56, index === 0 ? .65 : -.65, .37)
          : new THREE.Vector3(index === 0 ? -.98 : .98, index === 0 ? .8 : -.55 - index * .2, 0);
        const point = layer.localToWorld(anchor).project(camera);
        const dw = portrait ? 1080 : 1920, dh = portrait ? 1920 : 1080;
        const width = portrait ? 355 : 390;
        const x = clamp((point.x * .5 + .5) * dw + (index === 0 ? -width - 15 : 12), 66, dw - width - 66);
        const projectedY = (-point.y * .5 + .5) * dh;
        const y = material.breastplates ? (index === 0 ? portrait ? 490 : 275 : portrait ? 1380 : 800)
          : index > 0 ? layerLabelY(index) : clamp(projectedY, portrait ? 440 : 265, portrait ? 1450 : 825);
        label.style.left = `${x}px`; label.style.top = `${y}px`;
        const opacity = between(phase.p, .2, .44);
        label.style.opacity = String(opacity);
        const endX = (point.x * .5 + .5) * dw;
        const labelWidth = label.offsetWidth, fromX = endX > x + labelWidth / 2 ? x + labelWidth : x;
        connector.setAttribute("points", `${fromX},${y} ${endX},${y} ${endX},${projectedY}`);
        endpoint.setAttribute("cx", String(endX)); endpoint.setAttribute("cy", String(projectedY));
        connector.style.opacity = endpoint.style.opacity = label.textContent ? String(opacity * .82) : "0";
      }
      surface.dataset.phase = phase.p.toFixed(6); surface.dataset.contact = phase.active ? "after" : "before";
      surface.dataset.resultView = String(phase.settled);
      surface.dataset.projectileVisible = String(moving.visible);
      surface.dataset.forceDirectionVisible = String(forceIn.visible);
      surface.dataset.meshes = String(renderer.info.render.calls);
      surface.dataset.projectileZ = moving.position.z.toFixed(6);
    }
    function layerLabelY(index) {
      const firstHeight = labels[1]?.label.offsetHeight || 70, lastHeight = labels[2]?.label.offsetHeight || 70;
      const bottom = portrait ? 1450 : 825;
      return index === 1 ? bottom - lastHeight - 28 - firstHeight / 2 : bottom - lastHeight / 2;
    }
    views.push({scene, draw, surface});
  }

  function animateMedia(timeline, image, scene, direction) {
    // Framing controls the actual crop. Camera is an independent operation:
    // locked keeps that crop fixed, push zooms, and track pans at a fixed scale.
    // Extra track coverage keeps image edges outside the visible viewport.
    const framing = {wide: 1.02, medium: 1.1, close: 1.22, macro: 1.4}[scene.shot.framing] || 1.1;
    const track = scene.shot.camera === "track", locked = scene.shot.camera === "locked";
    const scale = framing + (track ? .06 : 0), pan = track ? direction * 2.1 : 0;
    const from = {scale, xPercent: -pan, yPercent: 0};
    const to = {scale: scale + (track || locked ? 0 : .08), xPercent: pan, yPercent: 0};
    gsap.set(image, from);
    timeline.fromTo(image, from, {...to, duration: scene.duration, ease: "none", immediateRender: false}, scene.start);
  }

  function buildCollage(context, api) {
    const {scene, stage, index} = context, {portrait, timeline} = api;
    // The establishing collage shows the collection; a close/detail shot
    // selects two larger views with independent, readable identity strips.
    // A different crop alone should not replay the same three-photo layout.
    const detail = ["close", "macro"].includes(scene.shot.framing);
    const descriptors = (scene.media_layers || []).slice(0, detail ? 2 : 4);
    const images = descriptors.map((_, i) => document.getElementById(`layer-${index}-${i}`));
    if (images.length < 2 || images.some(image => !image)) throw new Error("Collage needs at least two different, verified local pictures");
    stage.append(element("div", "collage-paper"), element("div", "collage-rule"));
    const W = portrait ? 1080 : 1920, H = portrait ? 1920 : 1080;
    stage.dataset.collageLayout = detail ? "detail-pair" : "collection";
    const layout = detail
      ? (portrait ? [[.065, .255, .56, .49, -3], [.59, .35, .35, .36, 4]]
        : [[.07, .25, .51, .56, -3], [.61, .31, .31, .45, 3]])
      : (portrait
        ? [[.055, .25, .78, .32, -7], [.38, .48, .59, .27, 8], [.025, .54, .51, .22, -4], [.57, .27, .39, .2, 5]]
        : [[.055, .28, .43, .49, -7], [.48, .34, .39, .50, 7], [.30, .55, .27, .27, -3], [.75, .22, .21, .30, 4]]);
    const drawing = svg("svg", {viewBox: `0 0 ${W} ${H}`, class: "collage-stroke", "aria-hidden": "true"}, stage);
    const route = svg("path", {d: portrait ? `M${W * .16} ${H * .55} Q${W * .08} ${H * .74} ${W * .67} ${H * .71}`
      : `M${W * .24} ${H * .77} Q${W * .48} ${H * .95} ${W * .81} ${H * .79}`,
    fill: "none", stroke: "#ac533f", "stroke-width": portrait ? 6 : 5, "stroke-linecap": "round"}, drawing);
    const routeLength = route.getTotalLength();
    route.style.strokeDasharray = String(routeLength); route.style.strokeDashoffset = String(routeLength);
    timeline.fromTo(route, {strokeDashoffset: routeLength}, {strokeDashoffset: 0, duration: scene.duration * .28, ease: "power2.inOut", immediateRender: false}, scene.start + scene.duration * .47);
    const frames = [], entranceLength = Math.min(.64, scene.duration * .2);
    const entranceEase = gsap.parseEase("power3.out");
    images.forEach((image, i) => {
      const [u, v, w, h, rotation] = layout[i];
      const frame = element("figure", "collage-layer"); const viewport = element("div", "collage-viewport");
      Object.assign(frame.style, {left: `${u * W}px`, top: `${v * H}px`, width: `${w * W}px`, height: `${h * H}px`});
      viewport.append(image); frame.append(viewport, element("div", "collage-tape"));
      // A photograph's own trusted identity belongs on its frame. Narration
      // fragments and model actor labels are not descriptions of this asset.
      const description = String(descriptors[i].credit || "").trim() || String(descriptors[i].source || "").trim();
      const copy = [...description.replace(/\s+/g, " ").split(" · ")[0]].slice(0, 120).join("");
      const label = element("figcaption", "collage-label", copy);
      frame.append(label);
      stage.append(frame);
      const range = document.createRange(); range.selectNodeContents(label);
      const displayScale = frame.getBoundingClientRect().width / (w * W);
      const textWidth = Math.min(label.clientWidth, range.getBoundingClientRect().width / displayScale);
      const delay = i ? Math.min(scene.duration * .42, .18 + i * scene.duration * .14) : 0;
      const finalRotation = scene.shot.role === "payoff" ? rotation * .45 : rotation;
      const startingOpacity = scene.start === 0 && i === 0 ? 1 : 0;
      frames.push({label, x: u * W, y: v * H, width: w * W, height: h * H, delay, rotation: finalRotation,
        startingOpacity, direction: i % 2 ? 1 : -1,
        text: [label.offsetLeft - 1, label.offsetTop - 1, textWidth + 2, label.offsetHeight + 2]});
      gsap.set(frame, {rotation: finalRotation - 7, x: i % 2 ? 80 : -80, y: 70, opacity: startingOpacity, scale: 1.025});
      timeline.fromTo(frame, {rotation: finalRotation - 7, x: i % 2 ? 80 : -80, y: 70, opacity: startingOpacity, scale: 1.025},
        {rotation: finalRotation, x: 0, y: 0, opacity: 1, scale: 1, duration: entranceLength, ease: "power3.out", immediateRender: false}, scene.start + delay);
      animateMedia(timeline, image, scene, i % 2 ? -1 : 1);
    });
    function polygon(frame, box, progress) {
      const scale = lerp(1.025, 1, progress), angle = (frame.rotation - 7 * (1 - progress)) * Math.PI / 180;
      const cx = frame.width / 2, cy = frame.height / 2, [x, y, width, height] = box;
      return [[x, y], [x + width, y], [x + width, y + height], [x, y + height]].map(([px, py]) => ({
        x: frame.x + cx + frame.direction * 80 * (1 - progress) + scale * ((px - cx) * Math.cos(angle) - (py - cy) * Math.sin(angle)),
        y: frame.y + cy + 70 * (1 - progress) + scale * ((px - cx) * Math.sin(angle) + (py - cy) * Math.cos(angle)),
      }));
    }
    function overlaps(a, b) {
      return [a, b].every(points => points.every((point, i) => {
        const next = points[(i + 1) % points.length], nx = point.y - next.y, ny = next.x - point.x;
        const project = polygon => polygon.map(p => p.x * nx + p.y * ny);
        const aa = project(a), bb = project(b);
        return Math.max(...aa) > Math.min(...bb) && Math.max(...bb) > Math.min(...aa);
      }));
    }
    // Later photographs cover earlier ones. Hide the whole identity caption
    // while any of its text is covered, instead of leaving a misleading tail.
    // Derive poses from time, not DOM/tween order: backwards and suppressed
    // seeks must restore the same labels even during the layered entrance.
    function captionsAt(time) {
      const poses = frames.map(frame => {
        const progress = entranceEase(clamp((time - frame.delay) / entranceLength));
        return {paper: polygon(frame, [0, 0, frame.width, frame.height], progress),
          text: polygon(frame, frame.text, progress), opacity: lerp(frame.startingOpacity, 1, progress)};
      });
      frames.forEach((frame, i) => {
        const covered = poses.some((pose, j) => j > i && pose.opacity > .001 && overlaps(poses[i].text, pose.paper));
        frame.label.style.visibility = covered ? "hidden" : "visible";
      });
    }
    const clock = {}; let captionTime = 0;
    Object.defineProperty(clock, "time", {get: () => captionTime, set: value => { captionTime = Number(value); captionsAt(captionTime); }});
    timeline.fromTo(clock, {time: 0}, {time: scene.duration, duration: scene.duration, ease: "none", immediateRender: false}, scene.start);
    captionsAt(0);
  }

  function buildFootage(context, api) {
    const {scene, stage, index} = context, {timeline} = api;
    const video = document.getElementById(`footage-${index}`);
    if (!video) throw new Error("Footage shot is missing its verified clip");
    const wrapper = element("div", "shot-footage"); wrapper.append(video); stage.append(wrapper, element("div", "shot-footage-shade"));
    const zoom = {wide: 1.025, medium: 1.08, close: 1.16, macro: 1.25}[scene.shot.framing] || 1.025;
    const move = scene.shot.camera === "track" ? 3 : scene.shot.camera === "push" ? 1 : 0;
    gsap.set(wrapper, {scale: zoom, xPercent: -move * .5});
    timeline.fromTo(wrapper, {scale: zoom, xPercent: -move * .5}, {scale: zoom + (scene.shot.camera === "locked" ? 0 : .035), xPercent: move * .5, duration: scene.duration, ease: "none", immediateRender: false}, scene.start);
    const copy = scene.items.find(item => item.label && item.label !== scene.title)?.label;
    if (copy && copy.length <= 92) {
      const label = element("div", "shot-footage-copy", copy); stage.append(label);
      timeline.fromTo(label, {y: 16, opacity: 0}, {y: 0, opacity: 1, duration: Math.min(.5, scene.duration * .2), ease: "power2.out", immediateRender: false}, scene.start + scene.duration * .21);
    }
    if (scene.image || scene.media_layers?.length) {
      const image = document.getElementById(scene.image ? `image-${index}` : `layer-${index}-0`);
      if (image) {
        const inset = element("figure", "shot-footage-inset"); inset.append(image); stage.append(inset);
        stage.querySelector(".shot-footage-copy")?.remove();
        timeline.fromTo(inset, {y: 75, rotation: -5, opacity: 0}, {y: 0, rotation: 3, opacity: 1, duration: Math.min(.65, scene.duration * .2), immediateRender: false, ease: "power3.out"}, scene.start + scene.duration * .48);
      }
    }
    if (scene.footage.credit) stage.append(element("div", "shot-source-credit", scene.footage.credit));
  }

  function buildStill(context, api) {
    const {scene, stage, index, node} = context;
    const image = document.getElementById(`image-${index}`);
    if (!image) throw new Error("The shot is missing its saved picture");
    const wrapper = element("div", "shot-footage"); wrapper.append(image);
    stage.append(wrapper, element("div", "shot-footage-shade"));
    node.style.setProperty("--shot-ink", "#f5f0e6"); node.style.setProperty("--shot-accent", "#e8b86c");
    animateMedia(api.timeline, image, scene, index % 2 ? -1 : 1);
    if (scene.image.credit) stage.append(element("div", "shot-source-credit", scene.image.credit));
  }

  function buildWorld(context, api) {
    const {stage, scene} = context, {portrait, timeline} = api;
    const viewport = element("div", "shot-world-window"), inner = element("div", "shot-world-inner");
    viewport.append(inner); stage.append(viewport);
    const child = {...context, stage: inner, width: portrait ? 1024 : 1770, height: portrait ? 1120 : 710};
    const palette = api.palette || {background: "#e7e0d2", surface: "#f8f3e5", ink: "#19333b", accent: "#497a79", secondary: "#bd8c53"};
    if (scene.graphics_version === 2 && scene.staging) window.YAASWorlds.build(child, {...api, palette});
    else window.YAASObjects.build(child, api);
    // Preserve all existing mechanism motion; the shot camera frames the shared
    // stage around it instead of replacing an action with a different heading.
    context.objectAnimation = child.objectAnimation;
    const scale = {wide: .91, medium: 1, close: 1.12, macro: 1.24}[scene.shot.framing] || 1;
    const move = scene.shot.camera === "track" ? (portrait ? 32 : 60) : 0;
    gsap.set(inner, {scale, x: -move / 2, transformOrigin: "50% 42%"});
    timeline.fromTo(inner, {scale, x: -move / 2}, {scale: scale + (scene.shot.camera === "push" ? .07 : 0), x: move / 2, duration: scene.duration, ease: "none", immediateRender: false}, scene.start);
  }

  function build(context, api) {
    const {scene, node, stage, header} = context, shot = scene.shot;
    node.dataset.shotRole = shot.role; node.dataset.technique = shot.technique; node.dataset.framing = shot.framing;
    node.classList.add("shot-scene");
    for (const [field, color] of Object.entries(api.palette || {})) node.style.setProperty(`--shot-${field}`, color);
    context.width = api.portrait ? 1080 : 1920; context.height = api.portrait ? 1920 : 1080;
    Object.assign(stage.style, {left: "0px", top: "0px", width: `${context.width}px`, height: `${context.height}px`});
    const kicker = element("div", "shot-kicker", shot.role === "establish" ? scenePlan.title : "");
    const titleWords = text => new Set(String(text).normalize("NFKC").toLocaleLowerCase().match(/[\p{L}\p{N}]+/gu) || []);
    const filmWords = titleWords(kicker.textContent), headingWords = titleWords(scene.title);
    const sharedWords = [...filmWords].filter(word => headingWords.has(word)).length;
    const repeatsHeading = Math.min(filmWords.size, headingWords.size) >= 3
      && sharedWords / Math.max(filmWords.size, headingWords.size) >= .8;
    if (kicker.textContent.length > 58 || kicker.textContent === scene.title || repeatsHeading) kicker.textContent = "";
    header.prepend(kicker);
    if (shot.technique === "diagram3d") {
      if (!scene.mechanism) throw new Error("A 3D shot needs an explicit, source-grounded mechanism");
      buildMechanism3D(context, api);
    } else if (shot.technique === "collage") buildCollage(context, api);
    else if (shot.technique === "footage") buildFootage(context, api);
    else if (scene.mechanism) buildMechanism2D(context, api);
    else if (scene.image) buildStill(context, api);
    else buildWorld(context, api);
    if (scene.mechanism) stage.append(element("div", "shot-mechanism-note", "Illustrative mechanism · not to scale"));
    // Custom builders own their cues. The legacy item-card entrance is not used.
    if (!context.objectAnimation) context.objectAnimation = () => {};
  }

  function renderAt(time) {
    for (const view of views) {
      const end = view.scene.start + view.scene.duration;
      // Render a short outgoing hold when a following shot cross-dissolves.
      const index = scenePlan.scenes.indexOf(view.scene), next = scenePlan.scenes[index + 1];
      const hold = next?.shot?.transition === "dissolve" ? Math.min(.22, next.duration * .1) : 0;
      if (time >= view.scene.start && time <= end + hold) {
        if (view.surface.width !== outputWidth || view.surface.height !== outputHeight) {
          view.surface.width = outputWidth; view.surface.height = outputHeight;
        }
        view.draw(clamp(time - view.scene.start, 0, view.scene.duration));
      } else if (view.surface.width > 1) {
        // A long film should not retain a full-resolution bitmap for every
        // hidden cutaway. Re-entering a shot reconstructs its exact saved pose.
        view.surface.width = view.surface.height = 1;
      }
    }
  }

  function bind(timeline, plan) {
    if (!views.length) return;
    let current = 0;
    // GSAP renders property setters even for seek(t, true). A callback would
    // silently freeze WebGL when capture suppresses timeline callback events.
    const clock = {};
    Object.defineProperty(clock, "value", {get: () => current, set: value => { current = Number(value); renderAt(current); }});
    timeline.fromTo(clock, {value: 0}, {value: plan.duration, duration: plan.duration, ease: "none", immediateRender: false}, 0);
    renderAt(0);
  }
  window.YAASShots = {prepare, build, bind, renderAt};
})();
