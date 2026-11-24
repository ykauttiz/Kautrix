/* Explicit theme control; no remote scripts, tracking or storage. */
"use strict";
const button = document.getElementById("theme");
if (button) {
  const modes = ["system", "light", "dark"];
  let index = 0;
  button.hidden = false;
  button.addEventListener("click", () => {
    index = (index + 1) % modes.length;
    const mode = modes[index];
    if (mode === "system") document.documentElement.removeAttribute("data-theme");
    else document.documentElement.setAttribute("data-theme", mode);
    button.textContent = "Theme: " + mode;
  });
}
