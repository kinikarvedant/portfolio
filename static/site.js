  // Theme: remembers the visitor's choice, otherwise follows the OS setting.
  (function () {
    var root = document.documentElement, KEY = "vk-theme", saved = null;
    try { saved = localStorage.getItem(KEY); } catch (e) {}
    if (saved) root.dataset.theme = saved;
    else if (window.matchMedia("(prefers-color-scheme: light)").matches) root.dataset.theme = "light";
    var tb = document.getElementById("theme"); if (tb) tb.addEventListener("click", function () {
      root.dataset.theme = root.dataset.theme === "light" ? "dark" : "light";
      try { localStorage.setItem(KEY, root.dataset.theme); } catch (e) {}
    });
  })();

  // Highlight whichever section is in view.
  (function () {
    var links = [].slice.call(document.querySelectorAll('.nav a[href^="#"]')), map = {};
    links.forEach(function (a) {
      var el = document.getElementById(a.hash.slice(1));
      if (el) map[a.hash.slice(1)] = a;
    });
    if (!window.IntersectionObserver) return;
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) {
        if (!en.isIntersecting) return;
        links.forEach(function (a) { a.classList.remove("on"); });
        map[en.target.id].classList.add("on");
      });
    }, { rootMargin: "-45% 0px -50% 0px" });
    Object.keys(map).forEach(function (id) { io.observe(document.getElementById(id)); });
  })();

  var yr = document.getElementById("yr"); if (yr) yr.textContent = new Date().getFullYear();
