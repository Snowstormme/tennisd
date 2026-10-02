document.addEventListener("DOMContentLoaded", () => {
  const connection = navigator.connection || navigator.mozConnection || navigator.webkitConnection;
  const canPrefetch = !connection?.saveData && !/2g/.test(connection?.effectiveType || "");
  const prefetched = new Set();
  if (canPrefetch) {
    document.querySelectorAll(".main-nav a, .brand").forEach((anchor) => {
      const prefetch = () => {
        const url = new URL(anchor.href, window.location.href);
        if (url.origin !== window.location.origin || url.href === window.location.href || prefetched.has(url.href)) return;
        prefetched.add(url.href);
        const hint = document.createElement("link");
        hint.rel = "prefetch";
        hint.href = url.href;
        hint.as = "document";
        document.head.appendChild(hint);
      };
      anchor.addEventListener("pointerenter", prefetch, { once: true, passive: true });
      anchor.addEventListener("touchstart", prefetch, { once: true, passive: true });
      anchor.addEventListener("focus", prefetch, { once: true, passive: true });
    });
  }

  document.querySelectorAll("[data-news-card-image]").forEach((image) => {
    image.addEventListener("error", () => image.remove());
  });
  document.querySelectorAll("[data-password-toggle]").forEach((button) => {
    const input = document.getElementById(button.getAttribute("aria-controls"));
    if (!input) return;

    button.addEventListener("click", () => {
      const isVisible = input.type === "text";
      input.type = isVisible ? "password" : "text";
      button.setAttribute("aria-pressed", String(!isVisible));
      button.setAttribute("aria-label", isVisible ? "Show password" : "Hide password");
      input.focus({ preventScroll: true });
    });
  });

  document.querySelectorAll("[data-featured-carousel]").forEach((carousel) => {
    const slides = Array.from(carousel.querySelectorAll("[data-featured-slide]"));
    const dots = Array.from(carousel.querySelectorAll("[data-featured-dot]"));
    if (slides.length < 2) return;
    let active = 0;
    let timer;

    const show = (next) => {
      active = (next + slides.length) % slides.length;
      slides.forEach((slide, index) => {
        const current = index === active;
        slide.classList.toggle("is-active", current);
        slide.setAttribute("aria-hidden", String(!current));
        slide.tabIndex = current ? 0 : -1;
        dots[index]?.toggleAttribute("aria-current", current);
      });
    };
    const stop = () => window.clearInterval(timer);
    const start = () => {
      stop();
      if (!window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
        timer = window.setInterval(() => show(active + 1), 6500);
      }
    };

    dots.forEach((dot, index) => dot.addEventListener("click", () => {
      show(index);
      start();
    }));
    carousel.addEventListener("pointerenter", stop);
    carousel.addEventListener("pointerleave", start);
    carousel.addEventListener("focusin", stop);
    carousel.addEventListener("focusout", start);
    start();
  });

  document.querySelectorAll("[data-tournament-theme-root]").forEach((page) => {
    const buttons = Array.from(page.querySelectorAll("[data-tournament-theme]"));
    const themes = new Set(["court", "classic", "trophy"]);
    let theme = "court";
    try {
      const saved = window.localStorage.getItem("tennisd-tournament-theme");
      if (themes.has(saved)) theme = saved;
    } catch (_) {
      // The court design remains the default when storage is unavailable.
    }
    const applyTheme = (next) => {
      theme = themes.has(next) ? next : "court";
      page.dataset.tournamentTheme = theme;
      buttons.forEach((button) => {
        button.setAttribute("aria-pressed", String(button.dataset.tournamentTheme === theme));
      });
      try {
        window.localStorage.setItem("tennisd-tournament-theme", theme);
      } catch (_) {}
    };
    buttons.forEach((button) => button.addEventListener("click", () => {
      applyTheme(button.dataset.tournamentTheme);
    }));
    applyTheme(theme);
  });

  const liveMatches = document.querySelectorAll("[data-live-match]");
  if (liveMatches.length) {
    const refreshScores = async () => {
      try {
        const response = await fetch("/api/live-matches", { cache: "no-store" });
        if (!response.ok) return;
        const payload = await response.json();
        payload.matches.forEach((match) => {
          document.querySelectorAll(`[data-live-match="${CSS.escape(match.id)}"]`).forEach((card) => {
            const score = card.querySelector("[data-live-score]");
            if (score && match.score) score.textContent = match.score;
            if (!match.odds) return;
            [1, 2].forEach((number) => {
              const price = Number(match.odds[`player${number}_price`]);
              const probability = Number(match.odds[`player${number}_probability`]);
              const side = card.querySelector(`[data-odds-side="${number}"]`);
              const priceNode = card.querySelector(`[data-odds-price="${number}"]`);
              const probabilityNode = card.querySelector(`[data-odds-probability="${number}"]`);
              if (side && Number.isFinite(price) && Number.isFinite(probability)) side.textContent = `${price.toFixed(2)} · ${probability.toFixed(0)}%`;
              if (priceNode && Number.isFinite(price)) priceNode.textContent = price.toFixed(2);
              if (probabilityNode && Number.isFinite(probability)) probabilityNode.textContent = `${probability.toFixed(1)}%`;
            });
          });
        });
      } catch (_) {
        // Keep the most recently stored score when a refresh is unavailable.
      }
    };
    window.setInterval(refreshScores, 60000);
  }

  const liveNews = document.querySelector("[data-news-live]");
  if (liveNews) {
    let firstStory = liveNews.dataset.newsFirst;
    const source = liveNews.dataset.newsSource || "all";
    const count = liveNews.querySelector("[data-news-count]");
    const checked = liveNews.querySelector("[data-news-checked]");
    const refreshNews = async () => {
      try {
        const response = await fetch(`/api/news-status?source=${encodeURIComponent(source)}`, { cache: "no-store" });
        if (!response.ok) return;
        const payload = await response.json();
        if (count) count.textContent = `${payload.total} stories`;
        if (checked) checked.textContent = "Checked just now";
        if (firstStory && payload.first_id && payload.first_id !== firstStory) {
          window.location.reload();
          return;
        }
        firstStory = payload.first_id || firstStory;
      } catch (_) {
        if (checked) checked.textContent = "Live update paused";
      }
    };
    window.setInterval(refreshNews, 60000);
  }

});
