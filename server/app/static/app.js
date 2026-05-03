const views = new Map();
let cleanupActiveView = null;

export function registerView(prefix, render) {
  views.set(prefix, render);
}

async function fetchJson(url, options = {}) {
  const response = await fetch(url, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(`${response.status}: ${detail}`);
  }
  return response.status === 204 ? null : response.json();
}

export const api = { fetchJson };

function showError(root, error) {
  const article = document.createElement("article");
  const heading = document.createElement("h2");
  const message = document.createElement("p");
  heading.textContent = "Error";
  message.textContent = error.message;
  article.append(heading, message);
  root.replaceChildren(article);
}

async function render() {
  const root = document.getElementById("app-root");
  const hash = window.location.hash || "#/dashboard";
  if (cleanupActiveView) {
    cleanupActiveView();
    cleanupActiveView = null;
  }
  for (const [prefix, view] of views.entries()) {
    if (hash.startsWith(prefix)) {
      root.replaceChildren();
      try {
        const cleanup = await view(root, hash);
        if (typeof cleanup === "function") cleanupActiveView = cleanup;
      } catch (error) {
        showError(root, error);
      }
      return;
    }
  }
  root.innerHTML = "<article><h2>Not found</h2></article>";
}

window.addEventListener("hashchange", render);

await Promise.all([
  import("/static/views/dashboard.js"),
  import("/static/views/create-agent.js"),
  import("/static/views/camera.js"),
]);

if (!window.location.hash) {
  window.location.hash = "#/dashboard";
}
render();
