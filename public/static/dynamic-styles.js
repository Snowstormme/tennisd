(() => {
  const clampPercent = (value) => {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? Math.min(100, Math.max(0, parsed)) : null;
  };

  document.querySelectorAll("[data-style-width]").forEach((element) => {
    const value = clampPercent(element.dataset.styleWidth);
    if (value !== null) element.style.setProperty("width", `${value}%`);
  });

  document.querySelectorAll("[data-style-height]").forEach((element) => {
    const value = clampPercent(element.dataset.styleHeight);
    if (value !== null) element.style.setProperty("height", `${value}%`);
  });

  document.querySelectorAll("[data-rating-tone]").forEach((element) => {
    const value = element.dataset.ratingTone || "";
    if (/^#[0-9a-f]{6}$/i.test(value)) {
      element.style.setProperty("--rating-tone", value);
    }
  });
})();
