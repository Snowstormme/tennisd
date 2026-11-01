document.addEventListener("DOMContentLoaded", () => {
  const connection = navigator.connection || navigator.mozConnection || navigator.webkitConnection;
  const canPrefetch = !connection?.saveData
    && !/2g/.test(connection?.effectiveType || "")
    && window.matchMedia("(hover: hover) and (pointer: fine)").matches;
  const prefetched = new Set();
  if (canPrefetch) {
    document.querySelectorAll(".main-nav a, .brand").forEach((anchor) => {
      let intentTimer = null;
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
      anchor.addEventListener("pointerenter", () => {
        intentTimer = window.setTimeout(prefetch, 250);
      }, { passive: true });
      anchor.addEventListener("pointerleave", () => {
        if (intentTimer !== null) window.clearTimeout(intentTimer);
        intentTimer = null;
      }, { passive: true });
      anchor.addEventListener("focus", prefetch, { once: true, passive: true });
    });
  }

  const startVisiblePolling = (callback, intervalMs) => {
    let timer = null;
    let lastRun = Date.now();
    const run = async () => {
      if (document.hidden) return;
      lastRun = Date.now();
      await callback();
    };
    const start = () => {
      if (timer === null) timer = window.setInterval(run, intervalMs);
    };
    const stop = () => {
      if (timer !== null) window.clearInterval(timer);
      timer = null;
    };
    document.addEventListener("visibilitychange", () => {
      if (document.hidden) {
        stop();
        return;
      }
      if (Date.now() - lastRun >= intervalMs) run();
      start();
    });
    start();
  };

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
        if (dots[index]) {
          if (current) dots[index].setAttribute("aria-current", "true");
          else dots[index].removeAttribute("aria-current");
        }
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
    const renderSetScore = (scoreNode, rawScore, winnerSide) => {
      if (!scoreNode.classList.contains("featured-set-score")) {
        scoreNode.textContent = rawScore;
        return;
      }
      const sets = String(rawScore).replaceAll("–", "-").trim().split(/\s+/)
        .filter((token) => /^\d+-\d+(?:\(\d+\))?$/.test(token));
      if (!sets.length) {
        const status = document.createElement("span");
        status.className = "match-score-status";
        status.textContent = rawScore;
        scoreNode.replaceChildren(status);
        return;
      }
      const rows = sets.map((setScore, index) => {
        const separator = setScore.indexOf("-");
        const row = document.createElement("span");
        row.className = "featured-set";
        const first = document.createElement("b");
        const label = document.createElement("small");
        const second = document.createElement("b");
        first.textContent = setScore.slice(0, separator);
        second.textContent = setScore.slice(separator + 1);
        label.textContent = `SET ${index + 1}`;
        if (Number(winnerSide) === 1) first.className = "set-winner";
        if (Number(winnerSide) === 2) second.className = "set-winner";
        row.append(first, label, second);
        return row;
      });
      scoreNode.replaceChildren(...rows);
      scoreNode.setAttribute("aria-label", `Score ${rawScore}`);
    };
    const refreshScores = async () => {
      try {
        const response = await fetch("/api/live-matches");
        if (!response.ok) return;
        const payload = await response.json();
        if (!Array.isArray(payload.matches)) return;
        const currentIds = new Set(payload.matches.map((match) => String(match.id)));
        document.querySelectorAll('[data-snapshot-status="live"]').forEach((card) => {
          if (currentIds.has(card.dataset.liveMatch)) return;
          if (card.matches("a.live-feed-match-card")) {
            const grid = card.parentElement;
            card.remove();
            if (grid && !grid.querySelector("[data-live-match]") && !grid.querySelector(".snapshot-empty-note")) {
              const note = document.createElement("p");
              note.className = "snapshot-empty-note feed-note";
              note.textContent = "No current live snapshots. Reload to check the latest feed.";
              grid.append(note);
            }
          } else {
            const age = card.querySelector(".snapshot-age");
            if (age) age.textContent = "Saved snapshot · Outside the current live feed. Reload for the latest report.";
            const state = document.querySelector("[data-snapshot-state]");
            if (state) state.textContent = "SAVED SNAPSHOT";
          }
        });
        payload.matches.forEach((match) => {
          document.querySelectorAll(`[data-live-match="${CSS.escape(match.id)}"]`).forEach((card) => {
            if (match.status === "live") {
              card.dataset.snapshotStatus = "live";
              if (!card.matches("a.live-feed-match-card")) {
                const status = document.querySelector("[data-last-reported-status]");
                const state = document.querySelector("[data-snapshot-state]");
                if (status) status.textContent = "Live";
                if (state) state.textContent = "LIVE AT LAST CHECK";
              }
            }
            const score = card.querySelector("[data-live-score]");
            if (score && match.score) renderSetScore(score, match.score, match.status === "live" ? 0 : (match.winner_side || score.dataset.winnerSide));
            const age = card.querySelector(".snapshot-age");
            if (age && match.synced_at) {
              const checked = new Date(match.synced_at.endsWith("Z") || /[+-]\d\d:\d\d$/.test(match.synced_at)
                ? match.synced_at : `${match.synced_at}Z`);
              const hours = Math.floor((Date.now() - checked.getTime()) / 3600000);
              if (Number.isFinite(hours)) age.textContent = `Daily snapshot · Checked ${Math.max(0, hours)}h ago${hours > 30 ? " · Refresh overdue" : ""}`;
            }
          });
        });
      } catch (_) {
        // Keep the most recently stored score when a refresh is unavailable.
      }
    };
    startVisiblePolling(refreshScores, 180000);
  }

  const syncStatus = document.querySelector("[data-live-sync-status]");
  if (syncStatus) {
    const refreshSyncStatus = async () => {
      try {
        const response = await fetch("/api/sync-status");
        if (!response.ok) throw new Error("sync status unavailable");
        const payload = await response.json();
        const runs = Object.values(payload.feeds || {});
        const latest = runs
          .filter((run) => run.finished_at)
          .sort((left, right) => new Date(right.finished_at) - new Date(left.finished_at))[0];
        if (!latest) {
          syncStatus.textContent = "Feed has not completed a check yet";
          return;
        }
        const checked = new Date(latest.finished_at);
        const minutes = Number.isNaN(checked.getTime())
          ? ""
          : ` · ${Math.max(0, Math.floor((Date.now() - checked.getTime()) / 60000))}m ago`;
        syncStatus.textContent = payload.status === "ok"
          ? `Daily snapshot checked${minutes}`
          : `Feed needs attention${minutes}`;
      } catch (_) {
        syncStatus.textContent = "Feed status unavailable";
      }
    };
    refreshSyncStatus();
    startVisiblePolling(refreshSyncStatus, 300000);
  }

  const liveNews = document.querySelector("[data-news-live]");
  if (liveNews) {
    let firstStory = liveNews.dataset.newsFirst;
    const source = liveNews.dataset.newsSource || "all";
    const count = liveNews.querySelector("[data-news-count]");
    const checked = liveNews.querySelector("[data-news-checked]");
    const refreshNews = async () => {
      try {
        const response = await fetch(`/api/news-status?source=${encodeURIComponent(source)}`);
        if (!response.ok) return;
        const payload = await response.json();
        if (count) count.textContent = `${payload.total} stories`;
        if (checked) checked.textContent = "Checked just now";
        if (payload.first_id && payload.first_id !== firstStory) {
          window.location.reload();
          return;
        }
        firstStory = payload.first_id || firstStory;
      } catch (_) {
        if (checked) checked.textContent = "Live update paused";
      }
    };
    startVisiblePolling(refreshNews, firstStory ? 300000 : 15000);
  }

});
