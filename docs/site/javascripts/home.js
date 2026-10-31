/* Landing page interactions: the demo's view tabs and its concept notes. */
const FEEDBACK_VISIBLE_MS = 3800;

function initHome() {
  const home = document.querySelector(".niuu-home");
  if (!home) return;

  const tabs = home.querySelectorAll("[data-tab]");
  tabs.forEach((tab) => {
    tab.addEventListener("click", () => {
      tabs.forEach((other) => {
        const active = other === tab;
        other.classList.toggle("active", active);
        other.setAttribute("aria-selected", String(active));
      });
      home.querySelectorAll("[data-panels]").forEach((panels) => {
        panels.hidden = panels.dataset.panels !== tab.dataset.tab;
      });
    });
  });

  const note = home.querySelector("#feedback");
  let timer;
  home.querySelectorAll("[data-feedback]").forEach((control) => {
    control.addEventListener("click", (event) => {
      event.preventDefault();
      note.textContent = control.dataset.feedback;
      note.classList.add("show");
      clearTimeout(timer);
      timer = setTimeout(() => note.classList.remove("show"), FEEDBACK_VISIBLE_MS);
    });
  });
}

document$.subscribe(initHome);
