/* Coordn8 page runtime — progressive enhancement over a page that already reads without it.
   Every behaviour here hangs off a data-c8-* hook written by build_page.py. Nothing is fetched;
   nothing runs on a timer except the 800 ms contents hold and the tooltip close delay. */
(function () {
  "use strict";
  var root = document.documentElement;
  root.classList.add("c8-js");

  var HOLD_MS = 800;
  var reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
  function nowMs() { return Date.now(); }
  function scrollMode() { return reduceMotion.matches ? "auto" : "smooth"; }
  function each(list, fn) { Array.prototype.forEach.call(list, fn); }
  function setFlag(el, attr, on) {
    if (on) { el.setAttribute(attr, ""); } else { el.removeAttribute(attr); }
  }

  /* things that only make sense with this script running start hidden and are revealed here,
     so no control is ever shown that cannot act */
  each(document.querySelectorAll("[data-c8-js-only]"), function (el) { el.hidden = false; });

  /* ------------------------------------------------------------------ theme */
  var themeBtn = document.querySelector("[data-c8-theme]");
  var darkQuery = window.matchMedia("(prefers-color-scheme: dark)");
  function storedTheme() {
    try { return window.localStorage.getItem("c8-theme"); } catch (e) { return null; }
  }
  function effectiveTheme() {
    var pinned = root.getAttribute("data-theme");
    if (pinned === "dark" || pinned === "light") { return pinned; }
    return darkQuery.matches ? "dark" : "light";
  }
  function paintThemeButton() {
    if (themeBtn) { themeBtn.setAttribute("aria-pressed", effectiveTheme() === "dark" ? "true" : "false"); }
  }
  var saved = storedTheme();
  if (saved === "dark" || saved === "light") { root.setAttribute("data-theme", saved); }
  if (themeBtn) {
    themeBtn.hidden = false;
    paintThemeButton();
    themeBtn.addEventListener("click", function () {
      var next = effectiveTheme() === "dark" ? "light" : "dark";
      root.setAttribute("data-theme", next);
      try { window.localStorage.setItem("c8-theme", next); } catch (e) { /* storage may be off */ }
      paintThemeButton();
    });
    var onScheme = function () { if (!storedTheme()) { paintThemeButton(); } };
    if (darkQuery.addEventListener) { darkQuery.addEventListener("change", onScheme); }
  }

  /* --------------------------------------------- contents scroll tracking */
  var tocLinks = Array.prototype.slice.call(document.querySelectorAll("[data-c8-toc] a[href^='#']"));
  var tracked = [];
  tocLinks.forEach(function (a) {
    var target = document.getElementById(a.getAttribute("href").slice(1));
    if (target) { tracked.push({ link: a, el: target, id: target.id }); }
  });
  var activeId = null;
  var holdUntil = 0;
  var bar = document.querySelector("[data-c8-progress]");

  function setActive(id) {
    if (id === activeId) { return; }
    activeId = id;
    tracked.forEach(function (t) {
      if (t.id === id) { t.link.setAttribute("aria-current", "true"); }
      else { t.link.removeAttribute("aria-current"); }
    });
  }
  function headerOffset() {
    var h = document.querySelector(".c8-header");
    return (h ? h.getBoundingClientRect().height : 0) + 24;
  }
  function atBottom() {
    var doc = document.documentElement;
    return window.innerHeight + window.scrollY >= doc.scrollHeight - 2;
  }
  function computeActive() {
    if (!tracked.length) { return; }
    if (nowMs() < holdUntil) { return; }
    if (atBottom() && window.scrollY > 0) { setActive(tracked[tracked.length - 1].id); return; }
    var line = headerOffset() + Math.round(window.innerHeight * 0.18);
    var current = tracked[0].id;
    for (var i = 0; i < tracked.length; i++) {
      if (tracked[i].el.getBoundingClientRect().top <= line) { current = tracked[i].id; }
    }
    setActive(current);
  }
  function paintProgress() {
    if (!bar) { return; }
    var doc = document.documentElement;
    var span = doc.scrollHeight - window.innerHeight;
    var p = span > 0 ? Math.min(1, Math.max(0, window.scrollY / span)) : 1;
    bar.style.transform = "scaleX(" + p.toFixed(4) + ")";
  }
  var ticking = false;
  function onScroll() {
    if (ticking) { return; }
    ticking = true;
    window.requestAnimationFrame(function () { ticking = false; paintProgress(); computeActive(); });
  }
  window.addEventListener("scroll", onScroll, { passive: true });
  window.addEventListener("resize", onScroll);
  if ("IntersectionObserver" in window && tracked.length) {
    var thresholds = [0, 0.25, 0.5, 0.75, 1];
    var watcher = new IntersectionObserver(function () { computeActive(); }, { threshold: thresholds });
    tracked.forEach(function (t) { watcher.observe(t.el); });
  }
  tracked.forEach(function (t) {
    t.link.addEventListener("click", function (ev) {
      if (ev.button !== 0 || ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.altKey) { return; }
      ev.preventDefault();
      showPanel("read", false);
      setActive(t.id);
      holdUntil = nowMs() + HOLD_MS;
      t.el.scrollIntoView({ behavior: scrollMode(), block: "start" });
      if (window.history && window.history.pushState) { window.history.pushState(null, "", "#" + t.id); }
      var heading = t.el.querySelector("h2, h3") || t.el;
      if (!heading.hasAttribute("tabindex")) { heading.setAttribute("tabindex", "-1"); }
      heading.focus({ preventScroll: true });
      window.setTimeout(function () { holdUntil = 0; computeActive(); }, HOLD_MS + 20);
    });
  });

  /* ------------------------------------------------------- glossary tooltips */
  var openTip = null;
  var MARGIN = 8;
  function placeTip(term, tip) {
    var r = term.getBoundingClientRect();
    var vw = document.documentElement.clientWidth;
    var vh = window.innerHeight;
    tip.style.left = "0px";
    tip.style.top = "0px";
    tip.style.maxWidth = Math.max(160, Math.min(352, vw - 2 * MARGIN)) + "px";
    var w = tip.offsetWidth;
    var h = tip.offsetHeight;
    var left = r.left + r.width / 2 - w / 2;
    left = Math.max(MARGIN, Math.min(left, vw - w - MARGIN));
    var top = r.bottom + 6;
    var placement = "below";
    if (top + h > vh - MARGIN) {
      top = r.top - 6 - h;
      placement = "above";
    }
    if (top < MARGIN) { top = Math.max(MARGIN, Math.min(r.bottom + 6, vh - h - MARGIN)); placement = "clamped"; }
    tip.style.left = Math.round(left) + "px";
    tip.style.top = Math.round(top) + "px";
    tip.setAttribute("data-placement", placement);
  }
  function tipFor(term) { return document.getElementById(term.getAttribute("aria-describedby")); }
  function closeTip(term) {
    var tip = tipFor(term);
    if (tip) { tip.removeAttribute("data-open"); }
    term.removeAttribute("data-open");
    if (openTip === term) { openTip = null; }
  }
  function openTerm(term) {
    if (openTip && openTip !== term) { closeTip(openTip); }
    var tip = tipFor(term);
    if (!tip) { return; }
    tip.setAttribute("data-open", "");
    term.setAttribute("data-open", "");
    term._c8OpenedAt = nowMs();
    openTip = term;
    placeTip(term, tip);
  }
  each(document.querySelectorAll("button.c8-term[aria-describedby]"), function (term) {
    var wrap = term.parentNode;
    var leaveTimer = 0;
    wrap.addEventListener("pointerenter", function (ev) {
      if (ev.pointerType !== "mouse") { return; }
      window.clearTimeout(leaveTimer);
      openTerm(term);
    });
    wrap.addEventListener("pointerleave", function (ev) {
      if (ev.pointerType !== "mouse") { return; }
      leaveTimer = window.setTimeout(function () {
        if (document.activeElement !== term) { closeTip(term); }
      }, 120);
    });
    term.addEventListener("focus", function () { openTerm(term); });
    term.addEventListener("blur", function () { closeTip(term); });
    term.addEventListener("click", function (ev) {
      ev.preventDefault();
      var isOpen = term.hasAttribute("data-open");
      var fresh = isOpen && nowMs() - (term._c8OpenedAt || 0) < 450;
      if (isOpen && !fresh) { closeTip(term); } else { openTerm(term); }
    });
  });
  document.addEventListener("keydown", function (ev) {
    if (ev.key === "Escape" && openTip) { closeTip(openTip); }
  });
  document.addEventListener("pointerdown", function (ev) {
    if (openTip && !openTip.parentNode.contains(ev.target)) { closeTip(openTip); }
  });
  window.addEventListener("scroll", function () { if (openTip) { placeTip(openTip, tipFor(openTip)); } }, { passive: true });
  window.addEventListener("resize", function () { if (openTip) { placeTip(openTip, tipFor(openTip)); } });

  /* ----------------------------------------------------------- example stepper */
  function highlight(stepper, ids) {
    var scope = stepper.closest("[data-c8-section]") || document;
    each(scope.querySelectorAll(".c8-hl"), function (n) { n.classList.remove("c8-hl"); });
    ids.forEach(function (id) {
      if (!id) { return; }
      var sel = "[data-node-id='" + id.replace(/'/g, "") + "']";
      each(scope.querySelectorAll(sel), function (n) { n.classList.add("c8-hl"); });
    });
  }
  each(document.querySelectorAll("[data-c8-stepper]"), function (stepper) {
    var steps = Array.prototype.slice.call(stepper.querySelectorAll("[data-c8-step]"));
    var controls = stepper.querySelector("[data-c8-controls]");
    if (!steps.length || !controls) { return; }
    var prev = controls.querySelector("[data-c8-prev]");
    var next = controls.querySelector("[data-c8-next]");
    var count = controls.querySelector("[data-c8-count]");
    if (!prev || !next || !count) { return; }
    var index = 0;
    function render() {
      steps.forEach(function (s, i) { setFlag(s, "data-current", i === index); });
      prev.setAttribute("aria-disabled", index === 0 ? "true" : "false");
      next.setAttribute("aria-disabled", index === steps.length - 1 ? "true" : "false");
      count.textContent = "Step " + (index + 1) + " of " + steps.length;
      var hl = (steps[index].getAttribute("data-highlight") || "").split(/\s+/);
      highlight(stepper, hl);
    }
    function go(delta) {
      var target = Math.max(0, Math.min(steps.length - 1, index + delta));
      if (target === index) { return; }
      index = target;
      render();
    }
    prev.addEventListener("click", function () { go(-1); });
    next.addEventListener("click", function () { go(1); });
    stepper.addEventListener("keydown", function (ev) {
      if (ev.key === "ArrowRight") { go(1); ev.preventDefault(); }
      else if (ev.key === "ArrowLeft") { go(-1); ev.preventDefault(); }
      else if (ev.key === "Home") { index = 0; render(); ev.preventDefault(); }
      else if (ev.key === "End") { index = steps.length - 1; render(); ev.preventDefault(); }
    });
    controls.hidden = false;
    stepper.setAttribute("data-c8-ready", "");
    render();
  });

  /* ------------------------------------------------------------ flow zoom */
  each(document.querySelectorAll("[data-c8-zoom]"), function (btn) {
    var fig = btn.closest("[data-c8-flow]");
    btn.hidden = false;
    btn.addEventListener("click", function () {
      var on = !fig.hasAttribute("data-zoomed");
      setFlag(fig, "data-zoomed", on);
      btn.setAttribute("aria-pressed", on ? "true" : "false");
    });
  });

  /* ------------------------------------------------------- read / watch tabs */
  var tabs = Array.prototype.slice.call(document.querySelectorAll("[data-c8-tab]"));
  var panels = Array.prototype.slice.call(document.querySelectorAll("[data-c8-panel]"));
  function showPanel(name, moveFocus) {
    if (!tabs.length) { return; }
    tabs.forEach(function (t) {
      var on = t.getAttribute("data-c8-tab") === name;
      t.setAttribute("aria-selected", on ? "true" : "false");
      t.tabIndex = on ? 0 : -1;
      if (on && moveFocus) { t.focus(); }
    });
    panels.forEach(function (p) { p.hidden = p.getAttribute("data-c8-panel") !== name; });
    if (name !== "watch") {
      each(document.querySelectorAll("[data-c8-panel='watch'] video"), function (v) { if (!v.paused) { v.pause(); } });
    }
    onScroll();
  }
  if (tabs.length) {
    var tablist = tabs[0].parentNode;
    tablist.hidden = false;
    tabs.forEach(function (t, i) {
      t.addEventListener("click", function () { showPanel(t.getAttribute("data-c8-tab"), false); });
      t.addEventListener("keydown", function (ev) {
        var j = -1;
        if (ev.key === "ArrowRight") { j = (i + 1) % tabs.length; }
        else if (ev.key === "ArrowLeft") { j = (i - 1 + tabs.length) % tabs.length; }
        else if (ev.key === "Home") { j = 0; }
        else if (ev.key === "End") { j = tabs.length - 1; }
        if (j >= 0) { ev.preventDefault(); showPanel(tabs[j].getAttribute("data-c8-tab"), true); }
      });
    });
    showPanel(location.hash === "#watch" ? "watch" : "read", false);
  }

  /* ------------------------------------------------------------ video speed */
  each(document.querySelectorAll("[data-c8-speed-group]"), function (group) {
    var video = document.getElementById(group.getAttribute("data-c8-speed-group"));
    var buttons = Array.prototype.slice.call(group.querySelectorAll("[data-c8-speed]"));
    group.hidden = false;
    function paint(rate) {
      buttons.forEach(function (b) {
        b.setAttribute("aria-pressed", parseFloat(b.getAttribute("data-c8-speed")) === rate ? "true" : "false");
      });
    }
    buttons.forEach(function (b) {
      b.addEventListener("click", function () {
        var rate = parseFloat(b.getAttribute("data-c8-speed"));
        if (video) { video.playbackRate = rate; }
        paint(rate);
      });
    });
    if (video) { video.addEventListener("ratechange", function () { paint(video.playbackRate); }); }
    paint(video ? video.playbackRate : 1);
  });

  /* ---------------------------------- print: unfold every folded block, then restore */
  var unfolded = [];
  window.addEventListener("beforeprint", function () {
    each(document.querySelectorAll("details:not([open])"), function (d) { d.open = true; unfolded.push(d); });
  });
  window.addEventListener("afterprint", function () {
    unfolded.forEach(function (d) { d.open = false; });
    unfolded = [];
  });

  /* ----------------------- extension point for components added to the library later */
  var registry = {};
  window.C8 = {
    register: function (name, init) {
      registry[name] = init;
      each(document.querySelectorAll("[data-c8-widget='" + name + "']"), function (el) {
        if (!el.hasAttribute("data-c8-ready")) { init(el); el.setAttribute("data-c8-ready", ""); }
      });
    },
    widgets: registry
  };

  paintProgress();
  computeActive();
  if (location.hash) {
    var initial = tracked.filter(function (t) { return "#" + t.id === location.hash; })[0];
    if (initial) { setActive(initial.id); }
  }
})();
