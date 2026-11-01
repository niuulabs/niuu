/* Landing page interaction: copy the start commands. */
const COPY_CONFIRM_MS = 1800;

function initHome() {
  const home = document.querySelector(".niuu-home");
  if (!home) return;

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

document$.subscribe(initHome);
