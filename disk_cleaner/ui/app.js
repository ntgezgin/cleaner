/* disk-cleaner report UI. Pure DOM, no network, all data embedded in #report-data. */
(function () {
  "use strict";
  var D = JSON.parse(document.getElementById("report-data").textContent);
  var G = window.DiskCleanerScriptGen;
  var fmt = G.fmtBytes;
  var SEP = D.env.sep || "/";
  var CI = D.env.os !== "linux";
  var items = D.items || [];
  var byId = {};
  items.forEach(function (it) { byId[it.id] = it; });
  var CODE = { s: "safe", r: "review", n: "never", u: "u" };
  var LABEL = { safe: "Safe", review: "Review", never: "Never", u: "Sınıflandırılmamış" };
  var NOW = Date.now() / 1000;
  var LOCK_SVG = '<svg class="lock" viewBox="0 0 10 11" aria-hidden="true"><rect x="1" y="5" width="8" height="6" rx="1.2" fill="currentColor"/><path d="M3 5V3.5a2 2 0 0 1 4 0V5" fill="none" stroke="currentColor" stroke-width="1.4"/></svg>';
  var LOCK_IC = '<svg class="lockic" viewBox="0 0 10 11" aria-label="Seçilemez (Never)"><rect x="1" y="5" width="8" height="6" rx="1.2" fill="currentColor"/><path d="M3 5V3.5a2 2 0 0 1 4 0V5" fill="none" stroke="currentColor" stroke-width="1.4"/></svg>';

  function $(id) { return document.getElementById(id); }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function fmtDate(t) {
    if (!t) return "—";
    try { return new Date(t * 1000).toLocaleDateString("tr-TR", { year: "numeric", month: "short", day: "numeric" }); }
    catch (e) { return new Date(t * 1000).toISOString().slice(0, 10); }
  }
  function ago(t) {
    if (!t) return "";
    var d = Math.floor((NOW - t) / 86400);
    if (d < 1) return "bugün";
    if (d < 60) return d + " gün önce";
    if (d < 730) return Math.round(d / 30) + " ay önce";
    return (d / 365).toFixed(1) + " yıl önce";
  }
  function pct(a, b) { return b ? Math.round(100 * a / b) + "%" : "—"; }
  function badgeCat(cat) {
    var c = cat === "safe" ? "s" : cat === "review" ? "r" : cat === "never" ? "n" : "u";
    return '<span class="badge ' + c + '"><span class="dot ' + c + '"></span>' + esc(LABEL[cat] || LABEL.u) + "</span>";
  }

  // ------------------------------------------------------------------ theme
  function applyTheme(t) {
    if (t === "light" || t === "dark") document.documentElement.setAttribute("data-theme", t);
    else document.documentElement.removeAttribute("data-theme");
    document.querySelectorAll("[data-theme-set]").forEach(function (b) {
      b.setAttribute("aria-pressed", String(b.getAttribute("data-theme-set") === (t || "auto")));
    });
  }
  var theme = "auto";
  try { theme = localStorage.getItem("dc-theme") || "auto"; } catch (e) { /* ignore */ }
  applyTheme(theme);
  document.querySelectorAll("[data-theme-set]").forEach(function (b) {
    b.addEventListener("click", function () {
      theme = b.getAttribute("data-theme-set");
      applyTheme(theme);
      try { localStorage.setItem("dc-theme", theme); } catch (e) { /* ignore */ }
    });
  });

  // -------------------------------------------------------------- selection
  var storeKey = "dc-sel:" + D.env.host + ":" + D.meta.generated;
  var selected = new Set();
  function defaults() {
    selected.clear();
    items.forEach(function (it) { if (it.selectable && it.default) selected.add(it.id); });
  }
  (function loadSel() {
    try {
      var s = localStorage.getItem(storeKey);
      if (s) {
        JSON.parse(s).forEach(function (id) { if (byId[id] && byId[id].selectable) selected.add(id); });
        return;
      }
    } catch (e) { /* ignore */ }
    defaults();
  })();
  function saveSel() { try { localStorage.setItem(storeKey, JSON.stringify(Array.from(selected))); } catch (e) { /* ignore */ } }
  function setSel(id, on) {
    var it = byId[id];
    if (!it || !it.selectable) return;
    if (on) selected.add(id); else selected.delete(id);
    saveSel();
    refreshSelectionViews();
  }

  // ------------------------------------------------------------------ totals
  function countedDisks() {
    return (D.disks || []).filter(function (d) {
      return d.total && ["bind", "network", "cloud", "cdrom"].indexOf(d.type) < 0;
    });
  }
  function totals() {
    var t = { safeFull: 0, safePart: 0, safeUnk: 0, safeN: 0, revFull: 0, revPart: 0, revUnk: 0, revN: 0,
      selFull: 0, selPart: 0, selN: 0, selUnk: 0 };
    items.forEach(function (it) {
      if (!it.selectable) return;
      var full = it.reclaim === "full" ? it.size : 0, part = it.reclaim === "partial" ? it.size : 0;
      if (it.cat === "safe") { t.safeFull += full; t.safePart += part; t.safeN++; if (it.reclaim === "unknown") t.safeUnk++; }
      if (it.cat === "review") { t.revFull += full; t.revPart += part; t.revN++; if (it.reclaim === "unknown") t.revUnk++; }
      if (selected.has(it.id) && G.actionable(it)) {
        t.selFull += full; t.selPart += part; t.selN++; if (it.reclaim === "unknown") t.selUnk++;
      }
    });
    var disks = countedDisks();
    t.total = disks.reduce(function (a, d) { return a + d.total; }, 0);
    t.used = disks.reduce(function (a, d) { return a + d.used; }, 0);
    t.free = disks.reduce(function (a, d) { return a + d.free; }, 0);
    t.unacc = disks.reduce(function (a, d) { return a + (d.unaccounted || 0); }, 0);
    return t;
  }

  function card(label, value, note, cls, dot) {
    return '<div class="card ' + (cls || "") + '"><div class="label">' + (dot ? '<span class="dot ' + dot + '"></span>' : "") +
      esc(label) + '</div><div class="value">' + value + '</div><div class="note">' + note + "</div></div>";
  }
  function renderCards() {
    var t = totals();
    var nd = countedDisks().length;
    var html = "";
    html += card("Toplam disk", esc(fmt(t.total)), esc(nd + " disk · dolu " + fmt(t.used)));
    html += card("Şu an boş", esc(fmt(t.free)), esc(pct(t.free, t.total) + " boş"));
    html += card("Safe to remove", esc(fmt(t.safeFull)), esc(t.safeN + " öğe" +
      (t.safePart ? " · + en fazla " + fmt(t.safePart) + " kısmi" : "") + (t.safeUnk ? " · " + t.safeUnk + " ölçülemeyen" : "")), "", "s");
    html += card("Needs review", esc(fmt(t.revFull)), esc(t.revN + " öğe, varsayılan seçili değil" +
      (t.revPart ? " · + en fazla " + fmt(t.revPart) + " kısmi" : "")), "", "r");
    html += card("Not scanned", "≈ " + esc(fmt(t.unacc)), esc("tahmini · " + D.not_scanned_count + " klasör okunamadı/atlandı (ayrıntı: Taranamayanlar sekmesi)"), "", "u");
    html += card("Temizlik sonrası boş", esc(fmt(t.free + t.selFull)), esc("seçili " + t.selN + " öğe: +" + fmt(t.selFull) +
      (t.selPart ? " (+ en fazla " + fmt(t.selPart) + " kısmi)" : "") + " · Çöp Kutusu boşaltılınca"), "hl");
    $("cards").innerHTML = html;
    var ab = "Seçili <b>" + t.selN + "</b> öğe · <b>" + esc(fmt(t.selFull)) + "</b>" +
      (t.selPart ? " + en fazla " + esc(fmt(t.selPart)) + " kısmi" : "") +
      (t.selUnk ? " · " + t.selUnk + " ölçülemeyen komut" : "") +
      " · temizlik sonrası boş ≈ <b>" + esc(fmt(t.free + t.selFull)) + "</b>";
    $("ab-sum").innerHTML = ab;
    $("gen-btn").disabled = t.selN === 0;
  }

  function renderMeta() {
    var e = D.env, m = D.meta;
    $("meta-line").textContent = e.host + " · " + e.os_detail + " · " + e.user + " · " +
      new Date(m.generated).toLocaleString("tr-TR") + " · " + m.files.toLocaleString("tr-TR") + " dosya, " +
      m.dirs.toLocaleString("tr-TR") + " klasör, " + m.scan_seconds + " sn";
    var rows = (D.disks || []).map(function (d) {
      return "<tr><td class='mono'>" + esc(d.mount) + "</td><td>" + esc([d.label, d.fs, d.type].filter(Boolean).join(" · ")) +
        "</td><td class='num'>" + (d.total ? esc(fmt(d.total)) : "—") + "</td><td class='num'>" + (d.used != null ? esc(fmt(d.used)) : "—") +
        "</td><td class='num'>" + (d.free != null ? esc(fmt(d.free)) : "—") + "</td><td class='num'>" + esc(fmt(d.scanned || 0)) +
        "</td><td class='num'>" + (d.unaccounted != null ? "≈ " + esc(fmt(d.unaccounted)) : "—") + "</td></tr>";
    }).join("");
    $("disks").innerHTML = "<thead><tr><th>Bağlama / sürücü</th><th>Etiket · FS · tür</th><th class='num'>Toplam</th><th class='num'>Dolu</th><th class='num'>Boş</th><th class='num'>Taranan</th><th class='num'>Açıklanamayan</th></tr></thead><tbody>" + rows + "</tbody>";
  }

  // ----------------------------------------------------------------- treemap
  var tm = $("treemap"), tip = $("tip"), details = $("details");
  var stack = [D.tree];
  var tileNodes = [];
  var pinned = null;

  function worst(row, s) {
    var sum = 0, mx = 0, mn = Infinity;
    for (var i = 0; i < row.length; i++) { sum += row[i]; if (row[i] > mx) mx = row[i]; if (row[i] < mn) mn = row[i]; }
    var s2 = s * s, r2 = sum * sum;
    return Math.max(s2 * mx / r2, r2 / (s2 * mn));
  }
  function squarify(vals, x, y, w, h) {
    var total = 0, i;
    for (i = 0; i < vals.length; i++) total += vals[i];
    var out = [];
    if (total <= 0 || w <= 0 || h <= 0) return out;
    var scale = w * h / total, areas = vals.map(function (v) { return v * scale; });
    i = 0;
    while (i < areas.length) {
      var short = Math.min(w, h), row = [areas[i]], j = i + 1, wr = worst(row, short);
      while (j < areas.length) {
        var nr = row.concat(areas[j]), nw = worst(nr, short);
        if (nw > wr) break;
        row = nr; wr = nw; j++;
      }
      var rs = row.reduce(function (a, b) { return a + b; }, 0), k;
      if (w >= h) {
        var cw = rs / h, cy = y;
        for (k = 0; k < row.length; k++) { var rh = row[k] / cw; out.push({ x: x, y: cy, w: cw, h: rh }); cy += rh; }
        x += cw; w -= cw;
      } else {
        var rh2 = rs / w, cx = x;
        for (k = 0; k < row.length; k++) { var rw = row[k] / rh2; out.push({ x: cx, y: y, w: rw, h: rh2 }); cx += rw; }
        y += rh2; h -= rh2;
      }
      i = j;
    }
    return out;
  }

  function nodeEntries(node) {
    var list = (node.k || []).filter(function (c) { return c.s > 0; }).slice();
    if (node.x) list.push({ n: (node.xn ? node.xn + " küçük öğe" : "küçük dosyalar"), s: node.x, rest: true, c: "u" });
    list.sort(function (a, b) { return b.s - a.s; });
    return list;
  }
  function pathOf(chain, child) {
    if (child && child.p) return child.p;
    if (child && child.virtual) return "Bu bilgisayar";
    var all = chain.concat(child ? [child] : []), start = -1, i;
    for (i = 0; i < all.length; i++) if (all[i].p) start = i;
    if (start < 0) return "";
    var p = all[start].p.replace(/[\\/]+$/, "");
    for (i = start + 1; i < all.length; i++) p += SEP + all[i].n;
    return p;
  }
  function lockOf(chain, child) {
    if (child && child.lk) return child.lk;
    for (var i = chain.length - 1; i >= 0; i--) if (chain[i].lk) return chain[i].lk;
    return null;
  }
  function itemOf(chain, child) {
    if (child && child.i != null) return byId[child.i];
    for (var i = chain.length - 1; i >= 0; i--) if (chain[i].i != null) return byId[chain[i].i];
    return null;
  }

  function renderTreemap(flashName) {
    var node = stack[stack.length - 1];
    var W = tm.clientWidth, H = tm.clientHeight;
    var ents = nodeEntries(node);
    var rects = squarify(ents.map(function (e) { return e.s; }), 0, 0, W, H);
    tileNodes = ents;
    var html = [];
    rects.forEach(function (r, idx) {
      var e = ents[idx];
      if (r.w < 2 || r.h < 2) return;
      var x = r.x + 1, y = r.y + 1, w = Math.max(r.w - 2, 1), h = Math.max(r.h - 2, 1);
      var cls = "tile " + (e.rest ? "rest" : (e.c || "u")) + (e.F ? " file" : "") + (flashName && e.n === flashName ? " flash" : "");
      var inner = "";
      if (w > 46 && h > 17) {
        inner += '<div class="tl">' + (e.c === "n" ? LOCK_SVG : "") + "<b>" + esc(e.n) + '</b> <span class="sz">' + esc(fmt(e.s)) + "</span></div>";
      }
      if (!e.rest && !e.F && e.k && w > 60 && h > 50) {
        var sub = (e.k || []).filter(function (c) { return c.s > 0; }).slice(0, 60);
        var area = { x: 3, y: 21, w: w - 6, h: h - 27 };
        if (area.w > 10 && area.h > 10) {
          var svals = sub.map(function (c) { return c.s; });
          var shown = svals.reduce(function (a, v) { return a + v; }, 0);
          if (e.s > shown) svals.push(e.s - shown);  // unlabelled remainder keeps proportions
          var srects = squarify(svals, area.x, area.y, area.w, area.h).slice(0, sub.length);
          srects.forEach(function (sr, si) {
            if (sr.w < 4 || sr.h < 4) return;
            var sc = sub[si].c || "u";
            inner += '<div class="sub" style="--c:var(--' + ({ s: "safe", r: "review", n: "never", u: "unc" }[sc]) + ");left:" + (sr.x + 1).toFixed(1) + "px;top:" + (sr.y + 1).toFixed(1) + "px;width:" + (sr.w - 2).toFixed(1) + "px;height:" + (sr.h - 2).toFixed(1) + 'px"></div>';
          });
        }
      }
      if ((e.rs || e.rr) && w > 8) {
        inner += '<div class="bar">' + (e.rs ? '<i class="s" style="width:' + (100 * e.rs / e.s).toFixed(2) + '%"></i>' : "") +
          (e.rr ? '<i class="r" style="width:' + (100 * e.rr / e.s).toFixed(2) + '%"></i>' : "") + "</div>";
      }
      if (e.i != null && selected.has(e.i)) inner += '<span class="sel" title="seçili"></span>';
      html.push('<div class="' + cls + '" data-idx="' + idx + '" tabindex="0" style="left:' + x.toFixed(1) + "px;top:" + y.toFixed(1) +
        "px;width:" + w.toFixed(1) + "px;height:" + h.toFixed(1) + 'px">' + inner + "</div>");
    });
    if (!rects.length) html.push('<div class="empty" style="margin:20px">Bu klasörde gösterilecek bir şey yok.</div>');
    tm.innerHTML = html.join("");
    renderCrumbs();
  }

  function renderCrumbs() {
    var h = [];
    stack.forEach(function (n, i) {
      var name = n.virtual ? "Bu bilgisayar" : (n.p || n.n);
      if (i === stack.length - 1) h.push('<span class="cur">' + esc(name) + "</span>");
      else h.push('<button type="button" data-crumb="' + i + '">' + esc(name) + '</button><span class="sep">›</span>');
    });
    h.push('<span class="muted" style="margin-left:8px">' + esc(fmt(stack[stack.length - 1].s)) + "</span>");
    $("crumbs").innerHTML = h.join("");
  }

  function describe(e, chain) {
    if (e.rest) {
      return "<h3>" + esc(e.n) + "</h3><dl><dt>Boyut</dt><dd>" + esc(fmt(e.s)) + "</dd></dl><p class='muted'>Ayrı ayrı gösterilemeyecek kadar küçük dosya ve klasörlerin toplamı.</p>";
    }
    var path = pathOf(chain, e);
    var cat = CODE[e.c] || "u";
    var it = itemOf(chain, e);
    var lk = lockOf(chain, e);
    var h = "<h3>" + esc(e.n) + "</h3><div class='path mono'>" + esc(path) + "</div><dl>";
    h += "<dt>Diskte</dt><dd>" + esc(fmt(e.s)) + "</dd>";
    if (e.a) h += "<dt>Görünen</dt><dd>" + esc(fmt(e.a)) + " <span class='muted'>(bulut/sparse)</span></dd>";
    if (!e.F) h += "<dt>Dosya</dt><dd>" + (e.f || 0).toLocaleString("tr-TR") + "</dd>";
    if (e.m) h += "<dt>Değişiklik</dt><dd>" + esc(fmtDate(e.m)) + " <span class='muted'>(" + esc(ago(e.m)) + ")</span></dd>";
    h += "<dt>Kategori</dt><dd>" + badgeCat(cat) + "</dd>";
    if (e.rs) h += "<dt>İçinde Safe</dt><dd>" + esc(fmt(e.rs)) + "</dd>";
    if (e.rr) h += "<dt>İçinde Review</dt><dd>" + esc(fmt(e.rr)) + "</dd>";
    if (e.st) h += "<dt>Durum</dt><dd>" + esc(e.st === "denied" ? "erişim izni yok (içi taranmadı)" : "okunamadı") + "</dd>";
    h += "</dl>";
    if (cat === "n") h += "<p><span class='badge n'>" + LOCK_SVG + "NEVER</span> " + esc(lk ? "Kilit: " + lk : "Dokunulmaz alan.") + " Script'e asla girmez.</p>";
    if (it) {
      var own = e.i === it.id;
      h += "<p>" + (own ? "Öneri: " : "Bu öğe şu önerinin parçası: ") + "<b>" + esc(it.title) + "</b> " + badgeCat(it.cat) + "</p><p class='muted'>" + esc(it.desc) + "</p>";
      if (it.selectable) {
        h += "<label class='chk'><input type='checkbox' data-detail-sel='" + it.id + "'" + (selected.has(it.id) ? " checked" : "") + "> Temizliğe dahil et</label> ";
      }
      h += " <button type='button' class='btn ghost' data-goto-rec='" + it.id + "'>Önerilerde göster</button>";
    } else if (cat === "u") {
      h += "<p class='muted'>Kural eşleşmedi: ne güvenli ne de korumalı olarak tanındı. Script'e girmez; istersen elle incele.</p>";
    }
    return h;
  }

  function showTip(ev, e) {
    var h = "<div class='t'>" + esc(e.n) + "</div><div>" + esc(fmt(e.s)) + " · " + esc(LABEL[CODE[e.c] || "u"]) + "</div>";
    if (!e.rest && !e.F) h += "<div class='k'>Tıkla: içine in</div>";
    tip.innerHTML = h;
    tip.hidden = false;
    var x = ev.clientX + 14, y = ev.clientY + 14, r = tip.getBoundingClientRect();
    if (x + r.width > window.innerWidth - 8) x = ev.clientX - r.width - 14;
    if (y + r.height > window.innerHeight - 8) y = ev.clientY - r.height - 14;
    tip.style.left = Math.max(8, x) + "px";
    tip.style.top = Math.max(8, y) + "px";
  }
  tm.addEventListener("mousemove", function (ev) {
    var t = ev.target.closest(".tile");
    if (!t) { tip.hidden = true; return; }
    var e = tileNodes[+t.getAttribute("data-idx")];
    if (!e) return;
    showTip(ev, e);
    if (!pinned) details.innerHTML = describe(e, stack);
  });
  tm.addEventListener("mouseleave", function () { tip.hidden = true; });
  function openTile(t) {
    var e = tileNodes[+t.getAttribute("data-idx")];
    if (!e || e.rest) return;
    if (!e.F && e.k && e.k.length) {
      stack.push(e);
      pinned = null;
      renderTreemap();
      details.innerHTML = describe(e, stack.slice(0, -1));
    } else {
      pinned = e;
      details.innerHTML = describe(e, stack);
    }
  }
  tm.addEventListener("click", function (ev) {
    var t = ev.target.closest(".tile");
    if (t) openTile(t);
  });
  tm.addEventListener("keydown", function (ev) {
    if (ev.key === "Enter" && ev.target.classList.contains("tile")) openTile(ev.target);
    if (ev.key === "Backspace" && stack.length > 1) { stack.pop(); renderTreemap(); }
  });
  $("crumbs").addEventListener("click", function (ev) {
    var b = ev.target.closest("[data-crumb]");
    if (!b) return;
    stack = stack.slice(0, +b.getAttribute("data-crumb") + 1);
    pinned = null;
    renderTreemap();
  });
  details.addEventListener("change", function (ev) {
    var c = ev.target.closest("[data-detail-sel]");
    if (c) setSel(+c.getAttribute("data-detail-sel"), c.checked);
  });
  details.addEventListener("click", function (ev) {
    var b = ev.target.closest("[data-goto-rec]");
    if (b) gotoRec(+b.getAttribute("data-goto-rec"));
  });
  var rz;
  window.addEventListener("resize", function () { clearTimeout(rz); rz = setTimeout(function () { if (!$("tab-explorer").hidden) renderTreemap(); }, 120); });

  function samePath(a, b) { return CI ? a.toLowerCase() === b.toLowerCase() : a === b; }
  function startsPath(p, root) {
    var r = root.replace(/[\\/]+$/, "");
    if (CI) { p = p.toLowerCase(); r = r.toLowerCase(); }
    return p === r || p.indexOf(r + SEP) === 0;
  }
  function reveal(path) {
    var roots = D.tree.k || [], best = null;
    roots.forEach(function (r) { if (startsPath(path, r.p) && (!best || r.p.length > best.p.length)) best = r; });
    if (!best) return false;
    var chain = [D.tree, best];
    var rest = path.slice(best.p.replace(/[\\/]+$/, "").length).split(/[\\/]+/).filter(Boolean);
    var target = null;
    for (var i = 0; i < rest.length; i++) {
      var cur = chain[chain.length - 1], kids = cur.k || [], found = null;
      for (var j = 0; j < kids.length; j++) if (kids[j].n === rest[i]) { found = kids[j]; break; }
      if (!found) for (j = 0; j < kids.length; j++) if (samePath(kids[j].n, rest[i])) { found = kids[j]; break; }
      if (!found) break;
      if (i === rest.length - 1 || !found.k) { target = found; break; }
      chain.push(found);
    }
    stack = chain;
    showTab("explorer");
    renderTreemap(target ? target.n : null);
    if (target) { pinned = target; details.innerHTML = describe(target, stack); }
    return true;
  }

  function renderLegend() {
    $("legend").innerHTML = [
      ["s", "Safe: yeniden oluşturulabilir cache/temp"], ["r", "Review: sana sorulur, varsayılan seçili değil"],
      ["n", "Never: kilitli, seçilemez"], ["u", "Sınıflandırılmamış: kural yok, dokunulmaz"]
    ].map(function (p) { return '<span><span class="dot ' + p[0] + '"></span>' + esc(p[1]) + "</span>"; }).join("");
  }

  // --------------------------------------------------------- recommendations
  var recState = { cat: "all", q: "", only: false, sort: "size", limit: { safe: 300, review: 300, never: 300 } };
  var KIND = {
    trash: "Çöp Kutusu'na taşı (klasörün tamamı)", trash_children: "İçeriğini Çöp Kutusu'na taşı",
    trash_old: "Eski içeriği Çöp Kutusu'na taşı", command: "Resmi temizlik komutu",
    advice: "Sadece öneri (script'e girmez)", report: "Sadece bilgi (dokunulmaz)"
  };
  function recRow(it) {
    var on = selected.has(it.id);
    var cb = it.selectable ? '<input type="checkbox" data-sel="' + it.id + '"' + (on ? " checked" : "") + ' aria-label="' + esc(it.title) + ' seç">'
      : (it.cat === "never" ? LOCK_IC : '<span class="muted" title="Sadece öneri">i</span>');
    var b = [badgeCat(it.cat)];
    b.push('<span class="badge info">' + esc(it.kind === "trash_old" ? (it.older || 7) + "+ gün eski içerik → Çöp Kutusu" : it.is_file && it.kind === "trash" ? "Dosyayı Çöp Kutusu'na taşı" : KIND[it.kind] || it.kind) + "</span>");
    if (it.reclaim === "partial") b.push('<span class="badge info">kısmi kazanç</span>');
    if (it.reclaim === "unknown") b.push('<span class="badge info">boyut ölçülemedi</span>');
    if (it.admin) b.push('<span class="badge warn">yönetici gerekir</span>');
    if (it.stale) b.push('<span class="badge r">' + esc(ago(it.mtime)) + " değişmemiş</span>");
    if (it.procs && it.procs.length) b.push('<span class="badge info">açıksa atlanır: ' + esc(it.procs.join(", ")) + "</span>");
    if (it.path && it.path !== "docker://") b.push('<button type="button" class="link" data-reveal="' + it.id + '">Ağaçta göster</button>');
    var ev = (it.evidence || []).map(function (x) { return "<li>" + esc(x) + "</li>"; }).join("") +
      (it.warnings || []).map(function (x) { return '<li class="w">' + esc(x) + "</li>"; }).join("");
    if (it.cmd) ev = "<li>Komut: <code class='cmd mono'>" + esc(it.cmd) + "</code>" + (it.fallback ? " (araç yoksa: " + esc(KIND[it.fallback] || it.fallback) + ")" : " (araç yoksa atlanır)") + "</li>" + ev;
    if (it.mtime) ev += "<li>Son değişiklik: " + esc(fmtDate(it.mtime)) + " (" + esc(ago(it.mtime)) + ")" + (it.files ? " · " + it.files.toLocaleString("tr-TR") + " dosya" : "") + "</li>";
    var warnInline = (it.warnings || []).length ? ' <span class="badge warn">' + (it.warnings.length) + " uyarı</span>" : "";
    return '<div class="rec' + (on ? " on" : "") + '" id="rec-' + it.id + '"><div class="cb">' + cb + '</div><div class="title">' + esc(it.title) + warnInline +
      '</div><div class="size">' + esc(fmt(it.size)) + (it.reclaim === "partial" ? "<small>en fazla</small>" : it.reclaim === "unknown" ? "<small>ölçülemedi</small>" : "") +
      '</div><div class="path mono">' + esc(it.path === "docker://" ? "Docker" : it.path) + '</div><div class="desc">' + esc(it.desc) +
      '</div><div class="meta">' + b.join("") + "</div>" + (ev ? "<details" + ((it.warnings || []).length ? " open" : "") + "><summary>Kanıt ve ayrıntılar</summary><ul>" + ev + "</ul></details>" : "") + "</div>";
  }
  function renderRecs() {
    var q = recState.q.toLowerCase();
    var list = items.filter(function (it) {
      if (recState.cat !== "all" && it.cat !== recState.cat) return false;
      if (recState.only && !selected.has(it.id)) return false;
      if (q && (it.title + " " + it.path).toLowerCase().indexOf(q) < 0) return false;
      return true;
    });
    var sorters = {
      size: function (a, b) { return (b.size || 0) - (a.size || 0); },
      age: function (a, b) { return (a.mtime || Infinity) - (b.mtime || Infinity); },
      name: function (a, b) { return a.title.localeCompare(b.title, "tr"); }
    };
    list.sort(sorters[recState.sort]);
    var groups = { safe: [], review: [], never: [] };
    list.forEach(function (it) { (groups[it.cat] || groups.review).push(it); });
    var heads = { safe: "Safe — yeniden oluşturulabilir", review: "Review — karar senin", never: "Never — bilgi amaçlı, seçilemez" };
    var html = "";
    ["safe", "review", "never"].forEach(function (cat) {
      var g = groups[cat];
      if (!g.length) return;
      var sum = 0, selSum = 0, selN = 0;
      g.forEach(function (it) { if (it.reclaim === "full") sum += it.size || 0; if (selected.has(it.id)) { selN++; if (it.reclaim === "full") selSum += it.size || 0; } });
      var c = cat === "safe" ? "s" : cat === "review" ? "r" : "n";
      html += '<div class="group"><div class="group-head"><h2><span class="dot ' + c + '"></span>' + esc(heads[cat]) + '</h2><span class="gsum">' +
        g.length + " öğe · " + esc(fmt(sum)) + (cat !== "never" ? " · seçili " + selN + " (" + esc(fmt(selSum)) + ")" : "") + "</span></div>";
      html += g.slice(0, recState.limit[cat]).map(recRow).join("");
      if (g.length > recState.limit[cat]) html += '<button type="button" class="btn" data-more="' + cat + '">' + (g.length - recState.limit[cat]) + " öğe daha göster</button>";
      html += "</div>";
    });
    $("recs").innerHTML = html || '<div class="empty">Filtreye uyan öneri yok.</div>';
    $("recs-count").textContent = items.filter(function (it) { return it.cat !== "never"; }).length;
  }
  $("recs").addEventListener("change", function (ev) {
    var c = ev.target.closest("[data-sel]");
    if (!c) return;
    setSel(+c.getAttribute("data-sel"), c.checked);
    var row = $("rec-" + c.getAttribute("data-sel"));
    if (row) row.classList.toggle("on", c.checked);
  });
  $("recs").addEventListener("click", function (ev) {
    var r = ev.target.closest("[data-reveal]");
    if (r) { var it = byId[+r.getAttribute("data-reveal")]; if (it && !reveal(it.path)) alert("Bu yol ağaçta gösterilemiyor (çok küçük ya da taranmamış)."); return; }
    var m = ev.target.closest("[data-more]");
    if (m) { recState.limit[m.getAttribute("data-more")] += 1000; renderRecs(); }
  });
  $("cat-filter").addEventListener("click", function (ev) {
    var b = ev.target.closest("[data-cat]");
    if (!b) return;
    recState.cat = b.getAttribute("data-cat");
    this.querySelectorAll("button").forEach(function (x) { x.setAttribute("aria-pressed", String(x === b)); });
    renderRecs();
  });
  var st;
  $("rec-search").addEventListener("input", function () { var v = this.value; clearTimeout(st); st = setTimeout(function () { recState.q = v; renderRecs(); }, 120); });
  $("only-selected").addEventListener("change", function () { recState.only = this.checked; renderRecs(); });
  $("rec-sort").addEventListener("change", function () { recState.sort = this.value; renderRecs(); });
  $("sel-safe").addEventListener("click", function () { items.forEach(function (it) { if (it.selectable && it.cat === "safe") selected.add(it.id); }); saveSel(); refreshSelectionViews(true); });
  $("sel-none").addEventListener("click", function () { selected.clear(); saveSel(); refreshSelectionViews(true); });
  $("sel-reset").addEventListener("click", function () { defaults(); saveSel(); refreshSelectionViews(true); });
  function gotoRec(id) {
    var it = byId[id];
    if (!it) return;
    recState.cat = "all"; recState.q = ""; recState.only = false;
    $("rec-search").value = ""; $("only-selected").checked = false;
    document.querySelectorAll("#cat-filter button").forEach(function (x) { x.setAttribute("aria-pressed", String(x.getAttribute("data-cat") === "all")); });
    recState.limit[it.cat] = Infinity;
    showTab("recs");
    renderRecs();
    var row = $("rec-" + id);
    if (row) { row.scrollIntoView({ block: "center" }); row.classList.add("on"); setTimeout(function () { row.classList.toggle("on", selected.has(id)); }, 1200); }
  }
  function refreshSelectionViews(full) {
    renderCards();
    if (full || !$("tab-recs").hidden) renderRecs();
    if (!$("tab-explorer").hidden) renderTreemap();
  }

  // ------------------------------------------------------------------ tables
  function sortableTable(el, cols, rows, sortKey, desc) {
    var state = { key: sortKey, desc: desc !== false };
    function draw() {
      var col = cols.filter(function (c) { return c.key === state.key; })[0];
      var r = rows.slice().sort(function (a, b) {
        var va = col.val(a), vb = col.val(b);
        if (va == null) va = col.num ? -Infinity : "";
        if (vb == null) vb = col.num ? -Infinity : "";
        var c = col.num ? va - vb : String(va).localeCompare(String(vb), "tr");
        return state.desc ? -c : c;
      });
      var h = "<thead><tr>" + cols.map(function (c) {
        return '<th data-k="' + c.key + '"' + (c.num ? ' class="num"' : "") + (c.key === state.key ? ' aria-sort="' + (state.desc ? "descending" : "ascending") + '"' : "") + ">" + esc(c.label) + "</th>";
      }).join("") + "</tr></thead><tbody>";
      h += r.map(function (row, i) {
        return '<tr class="click" data-row="' + rows.indexOf(row) + '">' + cols.map(function (c) {
          return "<td" + (c.num ? ' class="num"' : c.cls ? ' class="' + c.cls + '"' : "") + ">" + c.html(row) + "</td>";
        }).join("") + "</tr>";
      }).join("") + "</tbody>";
      el.innerHTML = h;
    }
    el.addEventListener("click", function (ev) {
      var th = ev.target.closest("th[data-k]");
      if (th) {
        var k = th.getAttribute("data-k");
        if (state.key === k) state.desc = !state.desc; else { state.key = k; state.desc = cols.filter(function (c) { return c.key === k; })[0].num; }
        draw();
        return;
      }
      var tr = ev.target.closest("tr[data-row]");
      if (tr && el._onRow) el._onRow(rows[+tr.getAttribute("data-row")]);
    });
    el._redraw = function (newRows) { rows = newRows; draw(); };
    draw();
  }
  function dirname(p) { var i = Math.max(p.lastIndexOf("/"), p.lastIndexOf("\\")); return i > 0 ? p.slice(0, i) : p; }
  function basename(p) { var i = Math.max(p.lastIndexOf("/"), p.lastIndexOf("\\")); return p.slice(i + 1); }
  function catOf(code) { return CODE[code] || code || "u"; }

  function renderFiles() {
    var el = $("files");
    sortableTable(el, [
      { key: "name", label: "Dosya", val: function (r) { return basename(r.path); }, html: function (r) { return "<b>" + esc(basename(r.path)) + "</b>"; }, cls: "p" },
      { key: "dir", label: "Klasör", val: function (r) { return dirname(r.path); }, html: function (r) { return '<span class="mono">' + esc(dirname(r.path)) + "</span>"; }, cls: "p" },
      { key: "size", label: "Boyut", num: true, val: function (r) { return r.size; }, html: function (r) { return esc(fmt(r.size)) + (r.apparent && r.apparent - r.size > 1048576 ? "<br><small class='muted'>görünen " + esc(fmt(r.apparent)) + "</small>" : ""); } },
      { key: "mtime", label: "Değiştirilme", num: true, val: function (r) { return r.mtime; }, html: function (r) { return esc(fmtDate(r.mtime)); } },
      { key: "atime", label: "Son erişim*", num: true, val: function (r) { return r.atime; }, html: function (r) { return esc(fmtDate(r.atime)); } },
      { key: "cat", label: "Kategori", val: function (r) { return catOf(r.cat); }, html: function (r) { return badgeCat(catOf(r.cat)); } }
    ], D.top_files || [], "size");
    el._onRow = function (r) { reveal(r.path); };
  }
  function renderDirs() {
    var el = $("dirs");
    function rows() { return (D.top_dirs || []).filter(function (d) { return !$("hide-wrap").checked || !d.wrap; }).slice(0, 50); }
    sortableTable(el, [
      { key: "path", label: "Klasör", val: function (r) { return r.path; }, html: function (r) { return '<span class="mono">' + esc(r.path) + "</span>"; }, cls: "p" },
      { key: "size", label: "Boyut", num: true, val: function (r) { return r.size; }, html: function (r) { return esc(fmt(r.size)); } },
      { key: "files", label: "Dosya", num: true, val: function (r) { return r.files; }, html: function (r) { return (r.files || 0).toLocaleString("tr-TR"); } },
      { key: "mtime", label: "Son değişiklik", num: true, val: function (r) { return r.mtime; }, html: function (r) { return esc(fmtDate(r.mtime)); } },
      { key: "cat", label: "Kategori", val: function (r) { return catOf(r.cat); }, html: function (r) { return badgeCat(catOf(r.cat)); } }
    ], rows(), "size");
    el._onRow = function (r) { reveal(r.path); };
    $("hide-wrap").addEventListener("change", function () { el._redraw(rows()); });
  }
  function renderRepos() {
    var el = $("repos");
    var reps = D.repos || [];
    $("repos-count").textContent = reps.length;
    if (!reps.length) { el.innerHTML = "<tbody><tr><td class='muted'>Git deposu bulunamadı.</td></tr></tbody>"; return; }
    sortableTable(el, [
      { key: "path", label: "Depo (NEVER)", val: function (r) { return r.path; }, html: function (r) { return '<span class="mono">' + esc(r.path) + "</span>"; }, cls: "p" },
      { key: "size", label: "Boyut", num: true, val: function (r) { return r.size; }, html: function (r) { return esc(fmt(r.size)); } },
      { key: "dirty", label: "Commit edilmemiş", num: true, val: function (r) { return r.dirty; }, html: function (r) { return r.dirty == null ? "?" : r.dirty ? '<span class="badge warn">' + r.dirty + " değişiklik</span>" : "temiz"; } },
      { key: "ahead", label: "Push edilmemiş", num: true, val: function (r) { return r.ahead; }, html: function (r) { return r.upstream === false ? '<span class="badge r">upstream yok</span>' : r.ahead == null ? "?" : r.ahead ? '<span class="badge warn">' + r.ahead + " commit</span>" : "0"; } },
      { key: "branch", label: "Branch", val: function (r) { return r.branch; }, html: function (r) { return esc(r.branch || "—"); } },
      { key: "age", label: "Son değişiklik", num: true, val: function (r) { return r.age_days == null ? null : -r.age_days; }, html: function (r) { return r.age_days == null ? "—" : esc(r.age_days + " gün önce"); } },
      { key: "error", label: "Not", val: function (r) { return r.error; }, html: function (r) { return esc(r.error || ""); } }
    ], reps, "size");
    el._onRow = function (r) { reveal(r.path); };
  }
  function renderInfo() {
    $("ns-count").textContent = D.not_scanned_count || 0;
    var h = "";
    h += '<div class="info-block"><h3>Taranamayan / atlanan (' + (D.not_scanned_count || 0) + ")</h3>";
    if ((D.not_scanned || []).length) {
      h += "<ul>" + D.not_scanned.slice(0, 400).map(function (n) { return '<li><span class="mono">' + esc(n.path) + "</span> — " + esc(n.reason) + "</li>"; }).join("") + "</ul>";
      if (D.not_scanned_count > 400) h += "<p class='muted'>… ve " + (D.not_scanned_count - 400) + " tane daha.</p>";
    } else h += "<p class='muted'>Yok.</p>";
    h += "</div>";
    h += '<div class="info-block"><h3>Notlar ve tahminler</h3><ul>' + (D.notes || []).map(function (n) { return "<li>" + esc(n) + "</li>"; }).join("") +
      "<li>* Son erişim sütunu bilgi amaçlıdır; birçok sistemde güncellenmez.</li><li>“Not scanned” kartındaki değer, disklerdeki dolu alan ile taranan toplam arasındaki farktır; sistem dosyaları, diğer kullanıcılar, izinsiz klasörler, dosya sistemi metadata'sı ve snapshot'ları kapsar. Tahmindir.</li></ul></div>";
    if ((D.link_samples || []).length || (D.reparse_samples || []).length) {
      h += '<div class="info-block"><h3>Takip edilmeyen bağlantılar</h3><p class="muted">' + D.links_skipped + " sembolik bağlantı, " + D.reparse_skipped + " junction/reparse point. Örnekler:</p><ul>" +
        (D.link_samples || []).concat(D.reparse_samples || []).slice(0, 30).map(function (p) { return '<li class="mono">' + esc(p) + "</li>"; }).join("") + "</ul></div>";
    }
    var o = D.meta.options || {};
    h += '<div class="info-block"><h3>Tarama ayarları</h3><ul>' +
      "<li>Kökler: " + (o.roots || []).map(function (r) { return '<span class="mono">' + esc(r) + "</span>"; }).join(", ") + "</li>" +
      "<li>Ayar dosyası: " + (D.meta.config ? '<span class="mono">' + esc(D.meta.config) + "</span>" : "yok") + "</li>" +
      "<li>Senin NEVER klasörlerin: " + ((o.never_paths || []).length ? o.never_paths.map(function (r) { return '<span class="mono">' + esc(r) + "</span>"; }).join(", ") : "yok") + "</li>" +
      "<li>Hariç tutulanlar: " + ((o.excludes || []).length ? o.excludes.map(function (r) { return '<span class="mono">' + esc(r) + "</span>"; }).join(", ") : "yok") + "</li>" +
      "<li>Bulut senkron klasörleri (NEVER): " + ((D.env.cloud_roots || []).length ? D.env.cloud_roots.map(function (c) { return '<span class="mono">' + esc(c.path) + "</span> (" + esc(c.provider) + ")"; }).join(", ") : "bulunamadı") + "</li>" +
      "<li>Script koruma listesi: " + D.guard.hard.length + " korumalı kök + " + D.guard.exact.length + " birebir yol. Script çalışırken her yolu bu listeye göre tekrar kontrol eder.</li>" +
      "<li>Treemap'te " + esc(fmt(D.meta.tree_threshold)) + " altındaki öğeler “küçük öğeler” olarak gruplandı.</li>" +
      "<li>disk-cleaner " + esc(D.meta.version) + ", Python " + esc(D.env.python) + (D.env.is_admin ? ", yönetici olarak çalıştı" : ", normal yetkiyle çalıştı") + "</li></ul></div>";
    $("info").innerHTML = h;
  }

  // -------------------------------------------------------------------- tabs
  function showTab(name) {
    document.querySelectorAll("#tabs [data-tab]").forEach(function (b) { b.setAttribute("aria-selected", String(b.getAttribute("data-tab") === name)); });
    document.querySelectorAll(".tab").forEach(function (s) { s.hidden = s.id !== "tab-" + name; });
    if (name === "explorer") renderTreemap();
    if (name === "recs") renderRecs();
  }
  $("tabs").addEventListener("click", function (ev) {
    var b = ev.target.closest("[data-tab]");
    if (b) showTab(b.getAttribute("data-tab"));
  });

  // ------------------------------------------------------------ script modal
  var lastScript = null;
  $("gen-btn").addEventListener("click", function () {
    var chosen = items.filter(function (it) { return selected.has(it.id); });
    lastScript = G.generate(D, chosen);
    $("script-out").value = lastScript.content;
    var f = lastScript.filename, ps = lastScript.shell === "powershell";
    var dl = ps ? "$HOME\\Downloads\\" + f : "~/Downloads/" + f;
    var steps = "<b>" + lastScript.count + " öğe</b> · tahmini " + esc(fmt(lastScript.totals.full)) +
      (lastScript.totals.partial ? " + en fazla " + esc(fmt(lastScript.totals.partial)) + " kısmi" : "") +
      ". Script varsayılan olarak <b>hiçbir şeyi değiştirmez</b> (dry-run).<ol>" +
      "<li>İndir ve bir metin editöründe <b>baştan sona oku</b>.</li>" +
      (ps ? "<li>Önizleme (dry-run): <code>powershell -ExecutionPolicy Bypass -File \"" + esc(dl) + "\"</code></li>" +
        "<li>Uygula: aynı komutun sonuna <code>-Apply</code> ekle. EVET yazmanı ister. Yönetici gerektiren öğeler için PowerShell'i “Yönetici olarak çalıştır” ile aç.</li>" +
        "<li>Geri Dönüşüm Kutusu'nu kontrol et; emin olunca boşalt. Yer o zaman açılır.</li>"
        : "<li>Önizleme (dry-run): <code>bash " + esc(dl) + "</code></li>" +
        "<li>Uygula: <code>bash " + esc(dl) + " --apply</code> (EVET yazmanı ister).</li>" +
        "<li>Çöp Kutusu'nu kontrol et; emin olunca boşalt. Yer o zaman açılır.</li>") +
      "</ol>";
    $("script-steps").innerHTML = steps;
    $("copy-status").textContent = "";
    $("modal").hidden = false;
    $("dl-btn").focus();
  });
  function closeModal() { $("modal").hidden = true; }
  $("modal-close").addEventListener("click", closeModal);
  $("modal").addEventListener("click", function (ev) { if (ev.target === this) closeModal(); });
  document.addEventListener("keydown", function (ev) { if (ev.key === "Escape" && !$("modal").hidden) closeModal(); });
  $("dl-btn").addEventListener("click", function () {
    if (!lastScript) return;
    var ps = lastScript.shell === "powershell";
    // UTF-8 BOM so Windows PowerShell 5.1 reads non-ASCII paths correctly
    var blob = new Blob([(ps ? "﻿" : "") + lastScript.content], { type: "text/plain;charset=utf-8" });
    var a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = lastScript.filename;
    document.body.appendChild(a);
    a.click();
    setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
  });
  $("copy-btn").addEventListener("click", function () {
    var ta = $("script-out"), done = function () { $("copy-status").textContent = "Kopyalandı."; };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(ta.value).then(done, function () { ta.select(); document.execCommand("copy"); done(); });
    } else { ta.select(); document.execCommand("copy"); done(); }
  });

  // -------------------------------------------------------------------- init
  renderMeta();
  renderLegend();
  renderCards();
  renderFiles();
  renderDirs();
  renderRepos();
  renderInfo();
  renderRecs();
  if ((D.tree.k || []).length === 1) stack = [D.tree, D.tree.k[0]];
  renderTreemap();
  var cur = stack[stack.length - 1];
  details.innerHTML = describe(cur, stack.slice(0, -1));
})();
