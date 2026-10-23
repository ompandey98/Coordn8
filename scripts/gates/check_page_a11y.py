#!/usr/bin/env python3
"""
Coordn8 gate — PAGE ACCESSIBILITY & HONEST AFFORDANCE (advisory; C1/C2).

Opens the built single-file page in a headless browser and MEASURES it:

  contrast_light / contrast_dark   every visible text run vs the background it actually sits on
                                   (computed styles, alpha-composited) >= 4.5:1, large text >= 3:1,
                                   with the OS set to light and to dark
  focus_order                      Tab reaches every focusable control, in DOM order, never jumping
                                   backwards on screen
  focus_visible                    each focused control shows a ring (outline >= 2 px or a shadow)
                                   whose colour is >= 3:1 against its surroundings
  toc_aria_current                 after scrolling, exactly one contents link carries
                                   aria-current="true" and it names the section in view; the last
                                   section is current at the page bottom; a clicked link holds for
                                   800 ms against scroll and then follows the scroll again
  no_external_requests             nothing outside file:/data:/blob:/about: is requested
                                   (the page is also run fully offline)
  no_script_errors                 no uncaught exception
  affordance_honest                anything that looks clickable (pointer cursor, link underline) is
                                   a real control, and clicking each visible control changes
                                   something (DOM, scroll, location, form state, media rate)
  phone_no_hscroll                 no horizontal page scroll at 320 / 390 / 420 px
  tooltip_*                        glossary terms open on keyboard focus, mouse hover and touch tap,
                                   close on Escape, have the term as accessible name and the
                                   definition as accessible description, and stay on screen
                                   (flip above near the bottom edge; clamp at phone width)
  print_unfolds                    print media: every folded How block is shown, navigation is
                                   hidden, linked code chips print their target
  reduced_motion                   smooth scrolling and kit durations are off under reduce
  theme_toggle                     the theme button flips the page between the two skins
  stepper_keyboard / video_speed   the example stepper answers buttons and arrow keys; the video
                                   tab's speed buttons set the playback rate

Screenshots (light, dark, print-emulated, phone) are written to --shots DIR when given.

Elements that are not rendered when the page opens (a term inside a folded How block, a stepper step
that is not current, a hidden panel) are revealed DELIBERATELY before they are checked — through the
page's own controls where it has them — and the report says so. No browser action waits longer than
5 s on one element, and a probe error fails only the checks of its group that did not finish (each
reported with the error); the gate itself errors (exit 3) only when the browser cannot run at all.

ADVISORY like every Coordn8 gate: REVIEW (exit 1) lists measured findings to look at. If no
browser can be started here, the static checks still run and the gate exits SKIPPED (2) saying the
live checks did not run — never a pass.

    python3 scripts/gates/check_page_a11y.py page.html [--shots DIR] [--json OUT]
        [--only contrast,focus,...] [--phone-widths 320,390,420]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib.common import EXIT_ERROR, EXIT_PASS, EXIT_REVIEW, EXIT_SKIPPED, emit, find_chrome  # noqa: E402

GROUPS = ("static", "network", "contrast", "focus", "toc", "affordance", "phone", "tooltip", "print",
          "motion", "theme", "stepper", "video", "shots")
LOCAL_SCHEMES = ("file:", "data:", "blob:", "about:")
LOAD_RE = re.compile(r"""(?:\b(?:src|href|poster|action|srcset)\s*=\s*["']?\s*(?:[a-z][a-z0-9+.-]*:)?//)"""
                     r"""|url\(\s*['"]?\s*(?:[a-z][a-z0-9+.-]*:)?//|@import\b""", re.I)
URL_TEXT_RE = re.compile(r"\b(?:https?|ftp)://", re.I)
ACTION_TIMEOUT_MS = 5000
# every check name a live group reports; a group that errors still reports each of its names
# (failed, with the error), so one probe error never removes or hides another check
GROUP_CHECKS = {
    "contrast": ("contrast_light", "contrast_dark"),
    "focus": ("focus_order", "focus_visible"),
    "toc": ("toc_aria_current",),
    "affordance": ("affordance_honest",),
    "phone": ("phone_no_hscroll",),
    "tooltip": ("tooltip_present", "tooltip_keyboard_focus_opens", "tooltip_escape_closes", "tooltip_hover_opens",
                "tooltip_accessible_name", "tooltip_role_and_link", "tooltip_flips_on_screen",
                "tooltip_touch_tap_opens", "tooltip_clamped_at_phone_width"),
    "print": ("print_unfolds",),
    "motion": ("reduced_motion",),
    "theme": ("theme_toggle",),
    "stepper": ("stepper_keyboard",),
    "video": ("video_speed",),
    "shots": ("screenshots",),
}

# ------------------------------------------------------------------ in-page measurement code
COLOR_JS = r"""
window.__c8c = (function(){
  function parse(c){
    c=(c||'').trim(); var m;
    if(c==='transparent'||!c) return [0,0,0,0];
    if((m=c.match(/^rgba?\(([^)]+)\)/))){var p=m[1].split(/[\s,\/]+/).filter(Boolean);
      var a=p.length>3?(p[3].slice(-1)==='%'?parseFloat(p[3])/100:parseFloat(p[3])):1; return [+p[0],+p[1],+p[2],a];}
    if((m=c.match(/^color\(srgb\s+([^)]+)\)/))){var q=m[1].split(/[\s\/]+/).filter(Boolean);
      return [q[0]*255,q[1]*255,q[2]*255,q.length>3?parseFloat(q[3]):1];}
    return null;
  }
  function over(t,b){var a=t[3]+b[3]*(1-t[3]); if(a<=0) return [0,0,0,0];
    var o=[0,1,2].map(function(i){return (t[i]*t[3]+b[i]*b[3]*(1-t[3]))/a;}); o.push(a); return o;}
  function lum(c){function f(v){v/=255; return v<=0.03928?v/12.92:Math.pow((v+0.055)/1.055,2.4);}
    return 0.2126*f(c[0])+0.7152*f(c[1])+0.0722*f(c[2]);}
  function ratio(a,b){var l1=lum(a),l2=lum(b); return (Math.max(l1,l2)+0.05)/(Math.min(l1,l2)+0.05);}
  function bgOf(el){var layers=[],unknown=false;
    for(var e=el;e;e=e.parentElement){var cs=getComputedStyle(e);
      if(cs.backgroundImage&&cs.backgroundImage!=='none') unknown=true;
      var c=parse(cs.backgroundColor); if(c&&c[3]>0){layers.push(c); if(c[3]>=0.999) break;}}
    var base=[255,255,255,1]; for(var i=layers.length-1;i>=0;i--) base=over(layers[i],base);
    return {bg:base,unknown:unknown};}
  function opac(el){var o=1; for(var e=el;e;e=e.parentElement){o*=parseFloat(getComputedStyle(e).opacity||'1');} return o;}
  return {parse:parse,over:over,ratio:ratio,bgOf:bgOf,opac:opac};
})();
"""

CONTRAST_JS = r"""
() => {
  const C = window.__c8c; const fails = {}; let checked = 0, unknown = 0;
  const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  let n;
  while ((n = w.nextNode())) {
    if (!n.nodeValue.trim()) continue;
    const el = n.parentElement; if (!el) continue;
    if (el.closest('svg,script,style,video,[aria-disabled="true"],:disabled,noscript')) continue;
    if (!el.checkVisibility({opacityProperty: true, visibilityProperty: true})) continue;
    const r = document.createRange(); r.selectNodeContents(n);
    const rects = Array.from(r.getClientRects()).filter(q => q.width > 1.5 && q.height > 1.5);
    if (!rects.length) continue;
    const cs = getComputedStyle(el);
    const fg0 = C.parse(cs.color); if (!fg0) continue;
    const b = C.bgOf(el); if (b.unknown) { unknown++; continue; }
    const fg = fg0.slice(); fg[3] = fg[3] * C.opac(el);
    const ratio = C.ratio(C.over(fg, b.bg), b.bg);
    const px = parseFloat(cs.fontSize), wt = parseInt(cs.fontWeight, 10) || 400;
    const large = px >= 24 || (px >= 18.66 && wt >= 700);
    const need = large ? 3 : 4.5; checked++;
    if (ratio + 1e-6 < need) {
      const key = el.tagName.toLowerCase() + '.' + (el.className && el.className.baseVal === undefined ? el.className : '') + '|' + cs.color + '|' + b.bg.map(v=>Math.round(v)).join(',');
      if (!fails[key] || fails[key].ratio > ratio)
        fails[key] = {text: n.nodeValue.trim().slice(0, 60), el: key.split('|')[0], color: cs.color,
                      bg: 'rgb(' + b.bg.slice(0,3).map(v=>Math.round(v)).join(',') + ')',
                      ratio: Math.round(ratio * 100) / 100, need: need, px: px};
    }
  }
  return {checked: checked, unknown_bg: unknown, failures: Object.values(fails).sort((a,b)=>a.ratio-b.ratio).slice(0, 40)};
}
"""

TABBABLE_JS = r"""
() => {
  const sel = 'a[href],button,input:not([type=hidden]),select,textarea,summary,[tabindex],video[controls],audio[controls],[contenteditable=""],[contenteditable="true"]';
  const list = Array.from(document.querySelectorAll(sel)).filter(el =>
    el.tabIndex >= 0 && !el.disabled && !el.closest('[inert]') &&
    el.checkVisibility({visibilityProperty: true}));
  document.querySelectorAll('[data-c8i]').forEach(e => e.removeAttribute('data-c8i'));
  list.forEach((el, i) => el.setAttribute('data-c8i', String(i)));
  return list.map(el => ({tag: el.tagName.toLowerCase(), label: (el.getAttribute('aria-label') || el.textContent || '').trim().slice(0, 40)}));
}
"""

ACTIVE_JS = r"""
() => {
  const el = document.activeElement;
  if (!el || el === document.body || !el.hasAttribute('data-c8i')) return null;
  const r = el.getBoundingClientRect(); const cs = getComputedStyle(el);
  const C = window.__c8c; const host = el.parentElement || el;
  const b = C.bgOf(host); const oc = C.parse(cs.outlineColor) || [0,0,0,0];
  const ringRatio = oc[3] > 0 ? C.ratio(C.over(oc, b.bg), b.bg) : 0;
  const land = el.closest('header,nav,main,footer,[data-c8-panel]');
  return {i: +el.getAttribute('data-c8i'), top: r.top + scrollY, left: r.left + scrollX,
          w: r.width, h: r.height, landmark: land ? (land.tagName.toLowerCase() + (land.id ? '#' + land.id : '')) : '',
          outlineStyle: cs.outlineStyle, outlineWidth: parseFloat(cs.outlineWidth) || 0,
          boxShadow: cs.boxShadow, ringRatio: Math.round(ringRatio * 100) / 100};
}
"""

CANDIDATES_JS = r"""
() => {
  const native = 'a[href],button,summary,label,input,select,textarea,[role=button],[role=tab],[role=link],[role=checkbox],[role=switch],[role=menuitem]';
  const looks = [];
  document.querySelectorAll('[data-c8-cand]').forEach(e => e.removeAttribute('data-c8-cand'));
  const all = document.body.querySelectorAll('*');
  for (const el of all) {
    if (el.closest('svg') && el.tagName.toLowerCase() !== 'svg') continue;
    if (!el.checkVisibility({visibilityProperty: true, opacityProperty: true})) continue;
    const cs = getComputedStyle(el);
    const pointer = cs.cursor === 'pointer';
    const underline = (cs.textDecorationLine || '').indexOf('underline') >= 0 &&
                      !el.closest('a,button,abbr,ins,u,.c8-term');
    if ((pointer || underline) && !el.closest(native)) {
      looks.push({tag: el.tagName.toLowerCase(), cls: String(el.className || ''), why: pointer ? 'pointer cursor' : 'link-style underline',
                  text: (el.textContent || '').trim().slice(0, 50)});
    }
  }
  const controls = Array.from(document.querySelectorAll('a[href],button,summary,input,[role=button],[role=tab]'))
    .filter(el => el.checkVisibility({visibilityProperty: true}) && !el.closest('[inert]'));
  const out = [];
  controls.forEach((el, i) => {
    el.setAttribute('data-c8-cand', String(i));
    out.push({i: i, tag: el.tagName.toLowerCase(), disabled: el.disabled || el.getAttribute('aria-disabled') === 'true',
              label: (el.getAttribute('aria-label') || el.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 50)});
  });
  return {looks_clickable_but_inert: looks.slice(0, 40), controls: out};
}
"""

WATCH_JS = r"""
() => {
  window.__c8m = 0;
  const mo = new MutationObserver(recs => { for (const r of recs) {
    const t = r.target.nodeType === 1 ? r.target : r.target.parentElement;
    if (t && t.closest && t.closest('.c8-progress')) continue;
    if (r.type === 'attributes' && (r.attributeName === 'data-c8-cand' || r.attributeName === 'data-c8i')) continue;
    window.__c8m++; } });
  mo.observe(document.documentElement, {attributes: true, childList: true, subtree: true, characterData: true});
  const form = Array.from(document.querySelectorAll('input')).map(i => i.checked + ':' + i.value).join('|');
  const rates = Array.from(document.querySelectorAll('video,audio')).map(v => v.playbackRate + ':' + v.paused).join('|');
  window.__c8s = JSON.stringify([scrollX, scrollY, location.href, form, rates]);
  return true;
}
"""

CHANGED_JS = r"""
() => {
  const form = Array.from(document.querySelectorAll('input')).map(i => i.checked + ':' + i.value).join('|');
  const rates = Array.from(document.querySelectorAll('video,audio')).map(v => v.playbackRate + ':' + v.paused).join('|');
  const now = JSON.stringify([scrollX, scrollY, location.href, form, rates]);
  return {mutations: window.__c8m || 0, state_changed: now !== window.__c8s};
}
"""


def static_checks(raw: str) -> dict:
    out = {}
    loads = sorted({m.group(0) for m in LOAD_RE.finditer(raw)})
    out["static_no_external_refs"] = {"ok": not loads, "detail": loads[:10]}
    texts = len(URL_TEXT_RE.findall(raw))
    out["static_no_url_text"] = {"ok": texts == 0, "detail": f"{texts} URL string(s) in the file"}
    inline = re.findall(r"<[a-z][^>]*\son[a-z]+\s*=", raw, re.I)
    out["static_no_inline_handlers"] = {"ok": not inline, "detail": [s[:80] for s in inline[:5]]}
    return out


class Probe:
    def __init__(self, pw, browser, url: str, shots: Path | None):
        self.pw, self.browser, self.url, self.shots = pw, browser, url, shots
        self.external: list = []
        self.errors: list = []

    def ctx(self, **opts):
        opts.setdefault("viewport", {"width": 1280, "height": 900})
        c = self.browser.new_context(offline=True, **opts)

        def route(r):
            u = r.request.url
            if u.startswith(LOCAL_SCHEMES):
                r.continue_()
            else:
                self.external.append(u)
                r.abort()
        c.route("**/*", route)
        return c

    def page(self, c):
        p = c.new_page()
        # no single element may hold the gate for Playwright's 30 s default: an element that is not
        # rendered fails its own check quickly and the rest of the probe carries on
        p.set_default_timeout(ACTION_TIMEOUT_MS)
        p.on("pageerror", lambda e: self.errors.append(str(e)[:200]))
        return p

    def open(self, c, wait: int = 250):
        p = self.page(c)
        p.goto(self.url, wait_until="load")
        p.evaluate(COLOR_JS)
        p.wait_for_timeout(wait)
        return p


def run_contrast(pr: Probe, scheme: str) -> dict:
    c = pr.ctx(color_scheme=scheme)
    try:
        p = pr.open(c)
        p.evaluate("""() => { document.querySelectorAll('details').forEach(d => d.open = true);
            document.querySelectorAll('[data-c8-panel]').forEach(x => x.hidden = false);
            document.querySelectorAll('.c8-tip').forEach(t => t.setAttribute('data-open', ''));
            document.querySelectorAll('[data-c8-step]').forEach(s => s.setAttribute('data-current', '')); }""")
        p.wait_for_timeout(50)
        res = p.evaluate(CONTRAST_JS)
    finally:
        c.close()
    return {"ok": not res["failures"] and res["checked"] > 0, "detail": res}


def run_focus(pr: Probe) -> tuple:
    c = pr.ctx(color_scheme="light")
    try:
        p = pr.open(c)
        tabbables = p.evaluate(TABBABLE_JS)
        n = len(tabbables)
        p.evaluate("() => { window.scrollTo(0, 0); if (document.activeElement) document.activeElement.blur(); }")
        seq, info = [], []
        last = None
        for _ in range(n * 2 + 10):
            p.keyboard.press("Tab")
            a = p.evaluate(ACTIVE_JS)
            if a is None:
                if seq:
                    break
                continue
            if a["i"] == last:
                continue
            if seq and a["i"] == seq[0]:
                break
            last = a["i"]
            seq.append(a["i"])
            info.append(a)
            if len(seq) >= n:
                break
        # Escape any open tooltip state from tabbing over terms
        p.keyboard.press("Escape")
    finally:
        c.close()
    missing = sorted(set(range(n)) - set(seq))
    in_dom_order = seq == sorted(seq)
    backwards = []
    for prev, cur in zip(info, info[1:]):
        if prev["landmark"] == cur["landmark"] and cur["top"] < prev["top"] - 8 and cur["left"] < prev["left"] - 1:
            backwards.append({"from": tabbables[prev["i"]]["label"], "to": tabbables[cur["i"]]["label"]})
    order = {"ok": n > 0 and not missing and in_dom_order and not backwards,
             "detail": {"focusable": n, "reached": len(seq), "missing": [tabbables[i] for i in missing][:15],
                        "dom_order": in_dom_order, "visual_backjumps": backwards[:10]}}
    no_ring = []
    for a in info:
        ring = (a["outlineStyle"] not in ("none", "") and a["outlineWidth"] >= 2) or (a["boxShadow"] not in ("none", ""))
        if not ring or (a["outlineStyle"] not in ("none", "") and a["ringRatio"] < 3):
            no_ring.append({"control": tabbables[a["i"]]["label"], "outline": f"{a['outlineStyle']} {a['outlineWidth']}px",
                            "ring_contrast": a["ringRatio"]})
    visible = {"ok": bool(info) and not no_ring, "detail": {"checked": len(info), "without_visible_ring": no_ring[:15]}}
    return order, visible


def run_toc(pr: Probe) -> dict:
    c = pr.ctx(reduced_motion="reduce", viewport={"width": 1280, "height": 800})
    detail = {}
    ok = True
    try:
        p = pr.open(c)
        ids = p.evaluate("() => Array.from(document.querySelectorAll('[data-c8-toc] a[href^=\"#\"]')).map(a => a.getAttribute('href').slice(1))")
        if len(ids) < 2:
            return {"ok": False, "detail": "fewer than two contents links"}

        def current():
            return p.evaluate("() => Array.from(document.querySelectorAll('[data-c8-toc] a[aria-current=\"true\"]')).map(a => a.getAttribute('href').slice(1))")
        scrolled = {}
        for sid in ids[:-1]:
            p.evaluate("(id) => { const e = document.getElementById(id); window.scrollTo(0, e.getBoundingClientRect().top + scrollY - 70); }", sid)
            p.wait_for_timeout(260)
            cur = current()
            scrolled[sid] = cur
            # a short page cannot bring the last sections to the top: they then hit the bottom rule
            at_bottom = p.evaluate("() => innerHeight + scrollY >= document.documentElement.scrollHeight - 2")
            if not at_bottom and cur != [sid]:
                ok = False
        detail["after_scroll"] = scrolled
        p.evaluate("() => window.scrollTo(0, document.documentElement.scrollHeight)")
        p.wait_for_timeout(260)
        detail["at_bottom"] = current()
        if detail["at_bottom"] != [ids[-1]]:
            ok = False
        p.evaluate("() => window.scrollTo(0, 0)")
        p.wait_for_timeout(260)
        target = ids[min(2, len(ids) - 1)]
        p.click(f"[data-c8-toc] a[href='#{target}']")
        p.evaluate("() => { window.scrollTo(0, 0); window.dispatchEvent(new Event('scroll')); }")
        p.wait_for_timeout(200)
        held = current()
        p.wait_for_timeout(900)
        p.evaluate("() => { window.scrollBy(0, 1); window.dispatchEvent(new Event('scroll')); }")
        p.wait_for_timeout(260)
        released = current()
        detail["click_hold"] = {"clicked": target, "within_800ms": held, "after_hold": released}
        if held != [target] or released != [ids[0]]:
            ok = False
    finally:
        c.close()
    return {"ok": ok, "detail": detail}


def run_affordance(pr: Probe) -> dict:
    states = ["read"]
    c = pr.ctx()
    try:
        p = pr.open(c)
        if p.evaluate("() => !!document.querySelector('[data-c8-tab=watch]')"):
            states.append("watch")
    finally:
        c.close()
    inert, dead, clicked, skipped = [], [], 0, []
    for state in states:
        c = pr.ctx(color_scheme="light")
        try:
            p = pr.open(c)
            if state != "read":
                p.click(f"[data-c8-tab={state}]")
                p.wait_for_timeout(150)
            base = p.evaluate(CANDIDATES_JS)
            inert += [dict(x, state=state) for x in base["looks_clickable_but_inert"]]
            controls = base["controls"]
            for ctl in controls:
                if state != "read" and ctl["label"] in ("Read", "Watch"):
                    continue
                if ctl["disabled"]:
                    skipped.append(ctl["label"])
                    continue
                p.goto(pr.url, wait_until="load")
                p.evaluate(COLOR_JS)
                p.evaluate("() => { try { localStorage.clear(); } catch (e) {} }")
                if state != "read":
                    p.click(f"[data-c8-tab={state}]")
                p.evaluate(CANDIDATES_JS)
                loc = p.locator(f"[data-c8-cand='{ctl['i']}']")
                try:
                    loc.focus(timeout=2000)   # keyboard-only controls (a skip link) appear on focus
                    loc.scroll_into_view_if_needed(timeout=2000)
                    p.wait_for_timeout(130)
                    p.evaluate(WATCH_JS)
                    url_before = p.url
                    loc.click(timeout=3000)
                    p.wait_for_timeout(130)
                    try:
                        ch = p.evaluate(CHANGED_JS)
                        changed = ch["mutations"] > 0 or ch["state_changed"]
                    except Exception:  # noqa: BLE001 - the click navigated away: that is a response
                        changed = True
                    if p.url != url_before:
                        changed = True
                except Exception as ex:  # noqa: BLE001
                    dead.append({"control": ctl["label"], "tag": ctl["tag"], "state": state,
                                 "why": f"could not be clicked: {str(ex).splitlines()[0][:120]}"})
                    continue
                clicked += 1
                if not changed:
                    dead.append({"control": ctl["label"], "tag": ctl["tag"], "state": state,
                                 "why": "clicking it changed nothing"})
        finally:
            c.close()
    return {"ok": clicked > 0 and not inert and not dead,
            "detail": {"controls_clicked": clicked, "looks_clickable_but_not_a_control": inert[:20],
                       "controls_that_do_nothing": dead[:20], "skipped_disabled": skipped[:20]}}


def run_phone(pr: Probe, widths: list) -> dict:
    res = {}
    for w in widths:
        c = pr.ctx(viewport={"width": w, "height": 800}, is_mobile=True, has_touch=True)
        try:
            p = pr.open(c)
            # with mobile emulation the layout viewport grows to fit wide content, so innerWidth is not
            # the screen: compare against the width the page was asked to fit (clientWidth = device width)
            res[str(w)] = p.evaluate("() => ({scrollWidth: document.documentElement.scrollWidth, innerWidth: innerWidth, clientWidth: document.documentElement.clientWidth})")
            res[str(w)]["device"] = w
        finally:
            c.close()
    ok = all(v["scrollWidth"] <= min(v["device"], v["clientWidth"]) + 1 for v in res.values())
    return {"ok": ok, "detail": res}


TIP_STATE_JS = r"""
(id) => { const t = document.getElementById(id); if (!t) return null; const r = t.getBoundingClientRect();
  return {visible: t.checkVisibility(), open: t.hasAttribute('data-open'), top: r.top, bottom: r.bottom, left: r.left,
          right: r.right, vw: document.documentElement.clientWidth, vh: innerHeight, placement: t.getAttribute('data-placement')}; }
"""

# Where each glossary term is and whether it is rendered right now (a term can sit in a folded How
# block, a stepper step that is not current, or a hidden panel).
TERMS_JS = r"""
() => Array.from(document.querySelectorAll('button.c8-term')).map(b => {
  const r = b.getBoundingClientRect();
  return {tip: b.getAttribute('aria-describedby'), text: b.textContent,
          visible: b.checkVisibility({visibilityProperty: true}) && r.width > 0 && r.height > 0,
          doc_top: r.top + scrollY, doc_bottom: r.bottom + scrollY}; })
"""

# Make one term rendered DELIBERATELY, through the page's own controls where it has them: open the
# folded block it sits in, step the example stepper forward until its step is current, unhide its
# panel. Returns what was done, so the report says the term was revealed before it was checked.
REVEAL_JS = r"""
(id) => {
  const b = document.querySelector("button.c8-term[aria-describedby='" + id + "']");
  if (!b) return {visible: false, done: ['no such term']};
  const done = [];
  for (let e = b.parentElement; e; e = e.parentElement) {
    if (e.tagName === 'DETAILS' && !e.open) { e.open = true; done.push('opened its folded block'); }
    if (e.hasAttribute && e.hasAttribute('data-c8-panel') && e.hidden) { e.hidden = false; done.push('unhid its panel'); }
  }
  const step = b.closest('[data-c8-step]');
  if (step && !b.checkVisibility()) {
    const root = step.closest('[data-c8-stepper]');
    const steps = root ? Array.from(root.querySelectorAll('[data-c8-step]')) : [];
    const prev = root && root.querySelector('[data-c8-prev]');
    const next = root && root.querySelector('[data-c8-next]');
    for (let i = 0; prev && i < steps.length && !prev.disabled && prev.getAttribute('aria-disabled') !== 'true'; i++) prev.click();
    for (let i = 0; next && i < steps.length && !b.checkVisibility(); i++) next.click();
    if (b.checkVisibility()) done.push('stepped its stepper to the step that holds it');
    else { steps.forEach(s => s.removeAttribute('data-current')); step.setAttribute('data-current', '');
           done.push('marked its stepper step current'); }
  }
  return {visible: b.checkVisibility(), done: done};
}
"""


def _term(p, tip: str):
    return p.locator(f"button.c8-term[aria-describedby='{tip}']")


def _ensure_visible(p, tip: str) -> list:
    """Reveal one term on purpose if it is not rendered; returns what was done ([] if it already was)."""
    info = next((t for t in p.evaluate(TERMS_JS) if t["tip"] == tip), None)
    if info and info["visible"]:
        return []
    r = p.evaluate(REVEAL_JS, tip)
    p.wait_for_timeout(40)
    if not r["visible"]:
        raise RuntimeError(f"term {tip!r} could not be made visible ({', '.join(r['done']) or 'nothing to open'})")
    return r["done"]


def run_tooltips(pr: Probe, out: dict) -> None:
    """Fills `out` in place, so a probe error part-way keeps every result already measured."""
    c = pr.ctx(color_scheme="light", viewport={"width": 1280, "height": 800}, reduced_motion="reduce")
    try:
        p = pr.open(c)
        terms = p.evaluate("""() => Array.from(document.querySelectorAll('button.c8-term')).map(b => ({
            text: b.textContent, tip: b.getAttribute('aria-describedby'), key: b.getAttribute('data-term'),
            role: (document.getElementById(b.getAttribute('aria-describedby')) || {getAttribute: () => null}).getAttribute('role'),
            def: (document.getElementById(b.getAttribute('aria-describedby')) || {textContent: ''}).textContent.trim()}))""")
        if not terms:
            out["tooltip_present"] = {"ok": False, "detail": "no glossary terms on the page"}
            return
        out["tooltip_present"] = {"ok": True, "detail": f"{len(terms)} marked term(s)"}
        where = {t["tip"]: t for t in p.evaluate(TERMS_JS)}
        shown = [t for t in terms if where.get(t["tip"], {}).get("visible")]
        revealed = {}
        if not shown:
            # every term is inside something folded: reveal the first one on purpose and say so
            revealed[terms[0]["tip"]] = _ensure_visible(p, terms[0]["tip"])
            shown = [terms[0]]
        first = shown[0]
        # keyboard: Tab until a term has focus
        p.evaluate("() => { window.scrollTo(0, 0); if (document.activeElement) document.activeElement.blur(); }")
        reached = None
        for _ in range(80):
            p.keyboard.press("Tab")
            k = p.evaluate("() => document.activeElement && document.activeElement.classList.contains('c8-term') ? document.activeElement.getAttribute('aria-describedby') : null")
            if k:
                reached = k
                break
        st = p.evaluate(TIP_STATE_JS, reached) if reached else None
        p.keyboard.press("Escape")
        p.wait_for_timeout(60)
        st2 = p.evaluate(TIP_STATE_JS, reached) if reached else None
        out["tooltip_keyboard_focus_opens"] = {"ok": bool(st and st["visible"]),
                                               "detail": st if st else "Tab never reached a rendered term"}
        out["tooltip_escape_closes"] = {"ok": bool(st2 and not st2["visible"]), "detail": st2}
        # hover (only a rendered term can be hovered)
        p.keyboard.press("Shift+Tab")
        p.evaluate("() => document.activeElement && document.activeElement.blur()")
        btn = _term(p, first["tip"])
        btn.scroll_into_view_if_needed()
        btn.hover()
        p.wait_for_timeout(80)
        hv = p.evaluate(TIP_STATE_JS, first["tip"])
        p.mouse.move(2, 2)
        p.wait_for_timeout(300)
        hv2 = p.evaluate(TIP_STATE_JS, first["tip"])
        out["tooltip_hover_opens"] = {"ok": bool(hv["visible"] and not hv2["visible"]),
                                      "detail": {"term": first["text"], "hovered": hv["visible"],
                                                 "after_leave": hv2["visible"],
                                                 "revealed_first": revealed.get(first["tip"], [])}}
        # accessible name and description, from the browser's own accessibility tree
        names = []
        try:
            cdp = c.new_cdp_session(p)
            cdp.send("Accessibility.enable")
            doc = cdp.send("DOM.getDocument", {"depth": 0})
            for t in terms:
                q = cdp.send("DOM.querySelector", {"nodeId": doc["root"]["nodeId"],
                                                    "selector": f"button[aria-describedby='{t['tip']}']"})
                ax = cdp.send("Accessibility.getPartialAXTree", {"nodeId": q["nodeId"], "fetchRelatives": False})
                node = next((n for n in ax.get("nodes", []) if not n.get("ignored")), {})
                names.append({"term": t["text"], "role": (node.get("role") or {}).get("value"),
                              "name": (node.get("name") or {}).get("value"),
                              "description": (node.get("description") or {}).get("value", "")})
            # a term inside a folded block is ignored by the tree until it is shown: reveal and re-read it
            for i, t in enumerate(terms):
                if names[i]["role"] is None:
                    revealed.setdefault(t["tip"], _ensure_visible(p, t["tip"]))
                    q = cdp.send("DOM.querySelector", {"nodeId": doc["root"]["nodeId"],
                                                        "selector": f"button[aria-describedby='{t['tip']}']"})
                    ax = cdp.send("Accessibility.getPartialAXTree", {"nodeId": q["nodeId"], "fetchRelatives": False})
                    node = next((n for n in ax.get("nodes", []) if not n.get("ignored")), {})
                    names[i] = {"term": t["text"], "role": (node.get("role") or {}).get("value"),
                                "name": (node.get("name") or {}).get("value"),
                                "description": (node.get("description") or {}).get("value", ""),
                                "revealed_first": revealed[t["tip"]]}
            source = "accessibility tree"
        except Exception as ex:  # noqa: BLE001
            source = f"role query fallback ({str(ex)[:60]})"
            names = []
            for t in terms:
                cnt = p.get_by_role("button", name=t["text"], exact=True).count()
                names.append({"term": t["text"], "role": "button" if cnt else None, "name": t["text"] if cnt else None,
                              "description": t["def"]})
        bad = [n for n in names if n["role"] != "button" or (n["name"] or "").strip() != n["term"].strip()
               or not n["description"]]
        out["tooltip_accessible_name"] = {"ok": not bad, "detail": {"source": source, "terms": names[:12], "bad": bad}}
        out["tooltip_role_and_link"] = {"ok": all(t["role"] == "tooltip" and t["def"] for t in terms),
                                        "detail": [{"term": t["text"], "role": t["role"]} for t in terms][:12]}
        # flip near the bottom edge: the RENDERED term furthest down the page, scrolled so it sits 12 px
        # above the bottom edge. A term in the first screen of a page cannot be scrolled down there, so
        # the window is made shorter instead (a short window is a real reader's case).
        p.evaluate("() => { window.scrollTo(0, 0); if (document.activeElement) document.activeElement.blur(); }")
        vis_now = [t for t in p.evaluate(TERMS_JS) if t["visible"]]
        if not vis_now:
            revealed.setdefault(terms[-1]["tip"], _ensure_visible(p, terms[-1]["tip"]))
            vis_now = [t for t in p.evaluate(TERMS_JS) if t["visible"]]
        deep = max(vis_now, key=lambda t: t["doc_top"])["tip"]
        to_edge = """(id) => { const b = document.querySelector("button.c8-term[aria-describedby='" + id + "']");
            const r = b.getBoundingClientRect(); window.scrollBy({top: r.bottom - (innerHeight - 12), behavior: 'instant'});
            return b.getBoundingClientRect().bottom; }"""
        bottom = p.evaluate(to_edge, deep)
        p.wait_for_timeout(120)
        vh0 = p.evaluate("() => innerHeight")
        shrunk = None
        if bottom < vh0 - 24:
            shrunk = int(bottom + 12 + 0.999)
            p.set_viewport_size({"width": 1280, "height": max(shrunk, 120)})
            p.wait_for_timeout(80)
            p.evaluate(to_edge, deep)
            p.wait_for_timeout(80)
        p.evaluate("(id) => document.querySelector(\"button.c8-term[aria-describedby='\" + id + \"']\").focus()", deep)
        p.wait_for_timeout(60)
        fl = p.evaluate(TIP_STATE_JS, deep)
        term_top = p.evaluate("(id) => document.querySelector(\"button.c8-term[aria-describedby='\" + id + \"']\").getBoundingClientRect().top", deep)
        out["tooltip_flips_on_screen"] = {"ok": bool(fl["visible"] and fl["bottom"] <= fl["vh"] and fl["top"] >= 0
                                                     and fl["bottom"] <= term_top + 1 and fl["placement"] == "above"),
                                          "detail": dict(fl, term_top=term_top, window_shortened_to=shrunk)}
        first = dict(first, tip=deep)
    finally:
        c.close()
    # touch tap, and every tooltip clamped on a 320 px phone
    c = pr.ctx(viewport={"width": 320, "height": 640}, is_mobile=True, has_touch=True, reduced_motion="reduce")
    try:
        p = pr.open(c)
        _ensure_visible(p, first["tip"])
        btn = _term(p, first["tip"])
        btn.scroll_into_view_if_needed()
        btn.tap()
        p.wait_for_timeout(120)
        tp = p.evaluate(TIP_STATE_JS, first["tip"])
        p.locator("h1").tap()
        p.wait_for_timeout(120)
        tp2 = p.evaluate(TIP_STATE_JS, first["tip"])
        out["tooltip_touch_tap_opens"] = {"ok": bool(tp["visible"] and not tp2["visible"]),
                                          "detail": {"after_tap": tp, "after_tap_elsewhere_visible": tp2["visible"]}}
        off, revealed_phone = [], {}
        for t in terms:
            try:
                done = _ensure_visible(p, t["tip"])
                if done:
                    revealed_phone[t["text"]] = done
                b = _term(p, t["tip"])
                b.scroll_into_view_if_needed()
                b.focus()
                p.wait_for_timeout(40)
                s = p.evaluate(TIP_STATE_JS, t["tip"])
            except Exception as ex:  # noqa: BLE001 - one term that cannot be reached fails this check, not the gate
                off.append({"term": t["text"], "probe_error": str(ex).splitlines()[0][:160]})
                continue
            if not (s["visible"] and s["left"] >= 0 and s["right"] <= s["vw"] + 0.5 and s["top"] >= 0 and s["bottom"] <= s["vh"] + 0.5):
                off.append(dict(s, term=t["text"]))
        out["tooltip_clamped_at_phone_width"] = {"ok": not off, "detail": {"checked": len(terms), "off_screen": off[:10],
                                                                           "revealed_first": revealed_phone}}
    finally:
        c.close()


def run_print(pr: Probe) -> dict:
    c = pr.ctx()
    try:
        p = pr.open(c)
        p.emulate_media(media="print")
        p.wait_for_timeout(150)
        res = p.evaluate("""() => {
          const how = Array.from(document.querySelectorAll('.c8-how-body'));
          const shown = how.filter(e => e.checkVisibility() && e.getBoundingClientRect().height > 0).length;
          const steps = Array.from(document.querySelectorAll('[data-c8-step]'));
          const stepsShown = steps.filter(e => e.checkVisibility()).length;
          const nav = document.querySelector('[data-c8-toc]');
          const chips = Array.from(document.querySelectorAll('a.c8-chip[href]'));
          const printed = chips.filter(a => (getComputedStyle(a, '::after').content || '').indexOf(a.getAttribute('href')) >= 0).length;
          return {how_blocks: how.length, how_shown: shown, steps: steps.length, steps_shown: stepsShown,
                  nav_hidden: nav ? !nav.checkVisibility() : true, linked_chips: chips.length, chips_print_target: printed};
        }""")
    finally:
        c.close()
    ok = (res["how_shown"] == res["how_blocks"] and res["steps_shown"] == res["steps"] and res["nav_hidden"]
          and res["chips_print_target"] == res["linked_chips"])
    return {"ok": ok, "detail": res}


def run_motion(pr: Probe) -> dict:
    vals = {}
    for mode in ("reduce", "no-preference"):
        c = pr.ctx(reduced_motion=mode)
        try:
            p = pr.open(c)
            vals[mode] = p.evaluate("""() => ({scroll: getComputedStyle(document.documentElement).scrollBehavior,
                enter: getComputedStyle(document.documentElement).getPropertyValue('--c8-enter-ms').trim()})""")
        finally:
            c.close()
    ok = vals["reduce"]["scroll"] == "auto" and vals["reduce"]["enter"] in ("0ms", "0s", "") and vals["no-preference"]["scroll"] == "smooth"
    return {"ok": ok, "detail": vals}


def run_theme(pr: Probe) -> dict:
    res = {}
    for scheme in ("light", "dark"):
        c = pr.ctx(color_scheme=scheme)
        try:
            p = pr.open(c)
            before = p.evaluate("() => ({bg: getComputedStyle(document.documentElement).backgroundColor, pressed: document.querySelector('[data-c8-theme]') && document.querySelector('[data-c8-theme]').getAttribute('aria-pressed')})")
            p.click("[data-c8-theme]")
            p.wait_for_timeout(80)
            after = p.evaluate("() => ({bg: getComputedStyle(document.documentElement).backgroundColor, pressed: document.querySelector('[data-c8-theme]').getAttribute('aria-pressed'), theme: document.documentElement.getAttribute('data-theme')})")
            res[scheme] = {"before": before, "after": after}
        finally:
            c.close()
    ok = (res["light"]["before"]["pressed"] == "false" and res["light"]["after"]["pressed"] == "true"
          and res["light"]["after"]["theme"] == "dark" and res["light"]["before"]["bg"] != res["light"]["after"]["bg"]
          and res["dark"]["before"]["pressed"] == "true" and res["dark"]["after"]["theme"] == "light"
          and res["dark"]["before"]["bg"] != res["dark"]["after"]["bg"])
    return {"ok": ok, "detail": res}


def run_stepper(pr: Probe) -> dict:
    c = pr.ctx(reduced_motion="reduce")
    try:
        p = pr.open(c)
        if not p.evaluate("() => !!document.querySelector('[data-c8-stepper][data-c8-ready]')"):
            return {"ok": True, "detail": "no stepper on this page"}

        def state():
            return p.evaluate("""() => { const s = document.querySelector('[data-c8-stepper]');
              const vis = Array.from(s.querySelectorAll('[data-c8-step]')).filter(e => e.checkVisibility()).length;
              return {count: s.querySelector('[data-c8-count]').textContent.trim(), visible_steps: vis,
                      highlighted: document.querySelectorAll('.c8-hl').length}; }""")
        s0 = state()
        nxt = p.locator("[data-c8-stepper] [data-c8-next]").first
        nxt.click()
        s1 = state()
        nxt.focus()
        p.keyboard.press("ArrowRight")
        s2 = state()
        p.keyboard.press("End")
        s3 = state()
        p.keyboard.press("ArrowLeft")
        s4 = state()
    finally:
        c.close()
    ok = (s0["visible_steps"] == 1 and s0["count"].startswith("Step 1 ") and s1["count"].startswith("Step 2 ")
          and s2["count"].startswith("Step 3 ") and s3["count"] != s2["count"] and s4["count"] != s3["count"]
          and s1["visible_steps"] == 1)
    return {"ok": ok, "detail": [s0, s1, s2, s3, s4]}


def run_video(pr: Probe) -> dict:
    c = pr.ctx()
    try:
        p = pr.open(c)
        if not p.evaluate("() => !!document.querySelector('[data-c8-tab=watch]')"):
            return {"ok": True, "detail": "no video tab on this page"}
        p.click("[data-c8-tab=watch]")
        p.wait_for_timeout(100)
        vis = p.evaluate("() => ({watch: document.querySelector('[data-c8-panel=watch]').checkVisibility(), read: document.querySelector('[data-c8-panel=read]').checkVisibility()})")
        rates = p.evaluate("() => Array.from(document.querySelectorAll('[data-c8-speed]')).map(b => parseFloat(b.getAttribute('data-c8-speed')))")
        pick = 1.5 if 1.5 in rates else rates[-1]
        p.click(f"[data-c8-speed='{pick:g}']")
        p.wait_for_timeout(60)
        got = p.evaluate("() => document.getElementById('c8-video').playbackRate")
        pressed = p.evaluate(f"() => document.querySelector(\"[data-c8-speed='{pick:g}']\").getAttribute('aria-pressed')")
        p.focus("[data-c8-tab=watch]")
        p.keyboard.press("ArrowLeft")
        back = p.evaluate("() => document.querySelector('[data-c8-panel=read]').checkVisibility()")
        cues = p.evaluate("() => { const t = document.getElementById('c8-video').textTracks; return t.length ? (t[0].cues ? t[0].cues.length : -1) : 0; }")
    finally:
        c.close()
    ok = (vis["watch"] and not vis["read"] and abs(got - pick) < 1e-6 and pressed == "true" and back
          and min(rates) >= 0.75 and max(rates) <= 2.0)
    return {"ok": ok, "detail": {"panels_after_watch": vis, "rates": rates, "clicked": pick, "playbackRate": got,
                                 "aria_pressed": pressed, "arrow_back_to_read": back, "caption_cues": cues}}


def run_shots(pr: Probe, shots: Path) -> dict:
    shots.mkdir(parents=True, exist_ok=True)
    made = {}
    plans = (("light", {"color_scheme": "light"}, None), ("dark", {"color_scheme": "dark"}, None),
             ("print", {"color_scheme": "light"}, "print"),
             ("phone", {"color_scheme": "light", "viewport": {"width": 390, "height": 844}, "is_mobile": True,
                        "has_touch": True}, None))
    for name, opts, media in plans:
        c = pr.ctx(**opts)
        try:
            p = pr.open(c)
            if media:
                p.emulate_media(media=media)
                p.wait_for_timeout(120)
            out = shots / f"{name}.png"
            p.screenshot(path=str(out), full_page=True)
            made[name] = str(out)
        finally:
            c.close()
    return {"ok": len(made) == 4, "detail": made}


def run_group(checks: dict, probe_errors: list, group: str, fn) -> None:
    """Run one live group. A probe error (a timeout, a missing element, a closed page) fails exactly the
    checks of that group that did not finish — each reported with the error — and the gate goes on."""
    part: dict = {}
    try:
        fn(part)
    except Exception as ex:  # noqa: BLE001
        msg = f"probe error: {type(ex).__name__}: {str(ex).splitlines()[0] if str(ex) else ''}"[:300]
        probe_errors.append({"group": group, "error": msg})
        for name in GROUP_CHECKS.get(group, (group,)):
            if name not in part:
                part[name] = {"ok": False, "probe_error": True, "detail": msg}
    checks.update(part)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("html")
    ap.add_argument("--shots", help="directory for light/dark/print/phone screenshots")
    ap.add_argument("--json", help="also write the full result here")
    ap.add_argument("--only", help="comma list of: " + ",".join(GROUPS))
    ap.add_argument("--phone-widths", default="320,390,420")
    a = ap.parse_args()
    path = Path(a.html).resolve()
    if not path.exists():
        emit({"gate": "check_page_a11y", "status": "SKIPPED", "summary": f"no HTML at {path}"})
        return EXIT_SKIPPED
    want = set(GROUPS) if not a.only else {g.strip() for g in a.only.split(",") if g.strip()}
    raw = path.read_text(encoding="utf-8", errors="replace")
    checks: dict = static_checks(raw) if "static" in want else {}
    started = time.time()
    status_note = ""
    probe_errors: list = []
    try:
        from playwright.sync_api import sync_playwright  # type: ignore
    except Exception as ex:  # noqa: BLE001
        sync_playwright = None
        status_note = f"Playwright for Python is not importable ({type(ex).__name__}); live checks did not run."
    if sync_playwright is not None:
        try:
            with sync_playwright() as pw:
                browser = None
                errs = []
                for kw in ({}, {"executable_path": find_chrome()}):
                    if "executable_path" in kw and not kw["executable_path"]:
                        continue
                    try:
                        browser = pw.chromium.launch(**kw)
                        break
                    except Exception as ex:  # noqa: BLE001
                        errs.append(str(ex).splitlines()[0][:160])
                if browser is None:
                    status_note = "no headless browser could be started: " + " | ".join(errs)
                else:
                    pr = Probe(pw, browser, path.as_uri(), Path(a.shots) if a.shots else None)
                    try:
                        phone_widths = [int(w) for w in a.phone_widths.split(",") if w.strip()]
                        plan = (
                            ("contrast", lambda o: o.update(contrast_light=run_contrast(pr, "light"),
                                                            contrast_dark=run_contrast(pr, "dark"))),
                            ("focus", lambda o: o.update(zip(("focus_order", "focus_visible"), run_focus(pr)))),
                            ("toc", lambda o: o.update(toc_aria_current=run_toc(pr))),
                            ("affordance", lambda o: o.update(affordance_honest=run_affordance(pr))),
                            ("phone", lambda o: o.update(phone_no_hscroll=run_phone(pr, phone_widths))),
                            ("tooltip", lambda o: run_tooltips(pr, o)),
                            ("print", lambda o: o.update(print_unfolds=run_print(pr))),
                            ("motion", lambda o: o.update(reduced_motion=run_motion(pr))),
                            ("theme", lambda o: o.update(theme_toggle=run_theme(pr))),
                            ("stepper", lambda o: o.update(stepper_keyboard=run_stepper(pr))),
                            ("video", lambda o: o.update(video_speed=run_video(pr))),
                        )
                        for group, fn in plan:
                            if group in want:
                                run_group(checks, probe_errors, group, fn)
                        if "shots" in want and a.shots:
                            run_group(checks, probe_errors, "shots",
                                      lambda o: o.update(screenshots=run_shots(pr, Path(a.shots))))
                        checks["no_external_requests"] = {"ok": not pr.external, "detail": sorted(set(pr.external))[:10]}
                        checks["no_script_errors"] = {"ok": not pr.errors, "detail": sorted(set(pr.errors))[:10]}
                    finally:
                        browser.close()
        except Exception as ex:  # noqa: BLE001 - the browser itself failed (not one check): nothing was judged
            emit({"gate": "check_page_a11y", "status": "ERROR", "summary": f"probe crashed: {type(ex).__name__}: {ex}"[:400],
                  "checks": checks, "probe_errors": probe_errors})
            return EXIT_ERROR
    live = any(k for k in checks if not k.startswith("static_"))
    failed = [k for k, v in checks.items() if not v["ok"]]
    if failed:
        status = "REVIEW"
    elif not live:
        status = "SKIPPED"
    else:
        status = "PASS"
    result = {"gate": "check_page_a11y", "status": status, "html": str(path), "live_checks_ran": live,
              "seconds": round(time.time() - started, 1), "failed": failed, "checks": checks,
              "summary": (f"{len(failed)} check(s) to review: {', '.join(failed)}" if failed else
                          (f"all {len(checks)} checks pass" if live else
                           "static checks only — " + (status_note or "live checks were not requested"))),
              "advisory_note": "Measured in a real browser; a pass means these properties hold, not that the "
                               "page reads well. Look at the screenshots."}
    if status_note:
        result["note"] = status_note
    if probe_errors:
        result["probe_errors"] = probe_errors
    if a.json:
        Path(a.json).write_text(json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8")
    emit(result)
    return {"PASS": EXIT_PASS, "REVIEW": EXIT_REVIEW, "SKIPPED": EXIT_SKIPPED}[status]


if __name__ == "__main__":
    sys.exit(main())
