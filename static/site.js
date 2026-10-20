(function () {
  var reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  // Pill nav: a yellow pill slides under whichever link is active.
  var nav = document.querySelector(".nav ul");
  var pill = nav && nav.querySelector(".pill");
  var links = nav ? [].slice.call(nav.querySelectorAll("a[data-sec]")) : [];
  function movePill(a) {
    if (!pill) return;
    links.forEach(function (l) { l.classList.toggle("on", l === a); });
    if (!a) { pill.style.opacity = 0; return; }
    var ul = nav.getBoundingClientRect(), r = a.getBoundingClientRect();
    pill.style.opacity = 1;
    pill.style.width = r.width + "px";
    pill.style.transform = "translateX(" + (r.left - ul.left) + "px)";
  }
  var current = links.filter(function (l) { return l.classList.contains("on"); })[0] || null;
  movePill(current);
  window.addEventListener("resize", function () { movePill(current); });
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(function () { movePill(current); });

  // Scroll spy: the section crossing the middle of the screen wins.
  // Each section says which nav item it belongs to (data-spy), so the collage keeps "Story" lit.
  var sections = [].slice.call(document.querySelectorAll("[data-spy]"));
  if (links.length && sections.length && "IntersectionObserver" in window) {
    var spy = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) {
        if (!en.isIntersecting) return;
        current = links.filter(function (l) { return l.dataset.sec === en.target.dataset.spy; })[0] || null;
        movePill(current);
      });
    }, { rootMargin: "-45% 0px -50% 0px" });
    sections.forEach(function (s) { spy.observe(s); });
  }

  // Reveal on scroll.
  var revealables = [].slice.call(document.querySelectorAll("[data-reveal]"));
  if (reduce || !("IntersectionObserver" in window)) {
    revealables.forEach(function (el) { el.classList.add("in"); });
  } else {
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) {
        if (en.isIntersecting) { en.target.classList.add("in"); io.unobserve(en.target); }
      });
    }, { rootMargin: "0px 0px -8% 0px" });
    revealables.forEach(function (el) { io.observe(el); });
  }

  // Hero name: scale the type so the name fills the content width on one line.
  var h1 = document.querySelector(".name h1");
  function fit() {
    if (!h1) return;
    h1.style.fontSize = "";
    var box = h1.parentElement.clientWidth, w = h1.firstElementChild.getBoundingClientRect().width;
    if (w > 0) {
      var size = parseFloat(getComputedStyle(h1).fontSize) * (box / w) * 0.98;
      h1.style.fontSize = Math.max(40, Math.min(size, 320)) + "px";
    }
  }
  fit();
  window.addEventListener("resize", fit);
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(fit);

  // Doodle: give each stroke its real length so it can draw itself in.
  [].slice.call(document.querySelectorAll(".doodle path, .doodle circle")).forEach(function (p) {
    if (p.getTotalLength) p.style.setProperty("--len", Math.ceil(p.getTotalLength()) + 1);
  });

  // Collage: tiles drift at different speeds while the section is on screen.
  var tiles = [].slice.call(document.querySelectorAll(".ctile[data-speed]"));
  var collage = document.querySelector(".collage");
  var wide = window.matchMedia("(min-width: 821px)");
  if (!reduce && tiles.length && collage) {
    var ticking = false;
    var drift = function () {
      ticking = false;
      if (!wide.matches) { tiles.forEach(function (t) { t.style.removeProperty("--py"); }); return; }
      var r = collage.getBoundingClientRect();
      if (r.bottom < 0 || r.top > innerHeight) return;
      var progress = (r.top + r.height / 2 - innerHeight / 2) / innerHeight;
      tiles.forEach(function (t) { t.style.setProperty("--py", (progress * parseFloat(t.dataset.speed) * 120).toFixed(1) + "px"); });
    };
    window.addEventListener("scroll", function () { if (!ticking) { ticking = true; requestAnimationFrame(drift); } }, { passive: true });
    window.addEventListener("resize", drift);
    drift();
  }

  var yr = document.getElementById("yr");
  if (yr) yr.textContent = new Date().getFullYear();
})();
