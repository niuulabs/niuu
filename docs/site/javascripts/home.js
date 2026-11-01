/* Landing page interaction: copy the start commands, switch gallery views,
 * enlarge a screenshot and mark the current gallery chapter. */
const COPY_CONFIRM_MS = 1800;

function initCopy(home) {
  home.querySelectorAll("[data-copy]").forEach((button) => {
    const label = button.textContent;
    let timer;
    button.addEventListener("click", async () => {
      await navigator.clipboard.writeText(home.querySelector(button.dataset.copy).textContent);
      button.textContent = button.dataset.done;
      clearTimeout(timer);
      timer = setTimeout(() => {
        button.textContent = label;
      }, COPY_CONFIRM_MS);
    });
  });
}

function initViews(home) {
  home.querySelectorAll("[data-views]").forEach((chapter) => {
    const tabs = [...chapter.querySelectorAll('[role="tab"]')];
    const select = (selected) => {
      tabs.forEach((tab) => {
        const active = tab === selected;
        tab.setAttribute("aria-selected", String(active));
        tab.tabIndex = active ? 0 : -1;
        document.getElementById(tab.getAttribute("aria-controls")).hidden = !active;
      });
    };
    tabs.forEach((tab, index) => {
      tab.addEventListener("click", () => select(tab));
      tab.addEventListener("keydown", (event) => {
        const step = { ArrowRight: 1, ArrowLeft: -1 }[event.key];
        if (!step) return;
        event.preventDefault();
        const next = tabs[(index + step + tabs.length) % tabs.length];
        select(next);
        next.focus();
      });
    });
  });
}

function initEnlarge(home) {
  const dialog = home.querySelector("dialog.enlarge");
  const image = dialog.querySelector("img");
  home.querySelectorAll("[data-enlarge]").forEach((link) => {
    link.addEventListener("click", (event) => {
      event.preventDefault();
      image.src = link.href;
      image.alt = link.querySelector("img").alt;
      dialog.showModal();
    });
  });
  dialog.addEventListener("click", () => dialog.close());
}

function initChapterNav(home) {
  const links = [...home.querySelectorAll(".chapter-nav a")];
  const mark = (id) =>
    links.forEach((link) => link.classList.toggle("current", link.hash === `#${id}`));
  const observer = new IntersectionObserver(
    (entries) => {
      entries.filter((entry) => entry.isIntersecting).forEach((entry) => mark(entry.target.id));
    },
    { rootMargin: "-45% 0px -45% 0px" },
  );
  home.querySelectorAll(".chapter").forEach((chapter) => observer.observe(chapter));
}

function initHome() {
  const home = document.querySelector(".niuu-home");
  if (!home) return;
  initCopy(home);
  initViews(home);
  initEnlarge(home);
  initChapterNav(home);
}

document$.subscribe(initHome);
