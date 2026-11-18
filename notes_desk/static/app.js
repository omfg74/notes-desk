const statusLine = document.querySelector("#status");
const folderForm = document.querySelector("#folder-form");
const folderPath = document.querySelector("#folder-path");
const filesNode = document.querySelector("#files");
const filesNote = document.querySelector("#files-note");
const previewName = document.querySelector("#preview-name");
const previewText = document.querySelector("#preview-text");
const previewImage = document.querySelector("#preview-image");
const settingsForm = document.querySelector("#settings-form");
const baseUrl = document.querySelector("#base-url");
const modelInput = document.querySelector("#model");
const apiKey = document.querySelector("#api-key");
const transcript = document.querySelector("#transcript");
const chatForm = document.querySelector("#chat-form");
const message = document.querySelector("#message");

let selected = "";
let hasKey = false;

function setStatus(text) {
  statusLine.textContent = text;
}

async function api(path, options) {
  const response = await fetch(path, options);
  const data = await response.json();
  if (!response.ok) {
    const error = new Error(data && data.error ? data.error : "Запрос не выполнен");
    error.steps = data && data.steps ? data.steps : [];
    throw error;
  }
  return data;
}

function jsonPost(path, payload) {
  return api(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

function renderStatus(data) {
  hasKey = data.hasKey;
  const parts = [];
  parts.push(data.root ? data.root : "Папка не открыта");
  parts.push(data.model ? data.model : "модель не задана");
  parts.push(data.hasKey ? "ключ задан" : "ключ не задан");
  setStatus(parts.join(" · "));
  if (data.root) folderPath.value = data.root;
  baseUrl.value = data.baseUrl;
  modelInput.value = data.model;
  apiKey.placeholder = data.hasKey ? "ключ уже задан" : "вставьте ключ";
}

function renderFiles(data) {
  filesNode.replaceChildren();
  for (const file of data.files) {
    const item = document.createElement("li");
    const button = document.createElement("button");
    button.type = "button";
    button.dataset.path = file.path;
    if (file.path === selected) button.setAttribute("aria-current", "true");
    const kind = document.createElement("span");
    kind.className = "kind";
    kind.textContent = file.kind === "note" ? "заметка" : file.kind === "image" ? "рисунок" : "файл";
    button.append(file.path, kind);
    button.addEventListener("click", () => showFile(file));
    item.append(button);
    filesNode.append(item);
  }
  const notes = [];
  if (data.truncated) notes.push("Показаны первые 400 файлов.");
  if (data.skipped) notes.push(`Не открылись: ${data.skipped}.`);
  filesNote.textContent = notes.join(" ");
}

async function refreshFiles() {
  const data = await api("/api/files");
  renderFiles(data);
  if (selected && !data.files.some((file) => file.path === selected)) {
    clearPreview();
  }
}

function clearPreview() {
  selected = "";
  previewName.textContent = "Выберите файл слева.";
  previewText.hidden = true;
  previewText.textContent = "";
  previewImage.hidden = true;
  previewImage.removeAttribute("src");
}

async function showFile(file) {
  selected = file.path;
  previewName.textContent = file.path;
  for (const button of filesNode.querySelectorAll("button")) {
    if (button.dataset.path === file.path) button.setAttribute("aria-current", "true");
    else button.removeAttribute("aria-current");
  }
  if (file.kind === "image") {
    previewText.hidden = true;
    previewImage.hidden = false;
    previewImage.alt = file.path;
    previewImage.src = `/api/media?path=${encodeURIComponent(file.path)}`;
    return;
  }
  previewImage.hidden = true;
  previewImage.removeAttribute("src");
  if (file.kind !== "note") {
    previewText.hidden = false;
    previewText.textContent = "Этот файл агент не читает и не правит.";
    return;
  }
  const data = await api(`/api/text?path=${encodeURIComponent(file.path)}`);
  previewText.hidden = false;
  previewText.textContent = data.content;
}

function addBubble(title, text, steps) {
  const block = document.createElement("article");
  block.className = "bubble";
  const heading = document.createElement("strong");
  heading.textContent = title;
  block.append(heading);
  if (text) {
    const body = document.createElement("p");
    body.textContent = text;
    block.append(body);
  }
  for (const step of steps || []) {
    const line = document.createElement("p");
    line.className = step.ok ? "step ok" : "step fail";
    line.textContent = `${step.ok ? "сделано" : "не вышло"}: ${step.tool} — ${step.detail}`;
    block.append(line);
  }
  transcript.append(block);
  transcript.scrollTop = transcript.scrollHeight;
}

folderForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const data = await jsonPost("/api/root", { path: folderPath.value });
    if (data.reset) {
      transcript.replaceChildren();
      clearPreview();
      addBubble("Заметки", "Открыта другая папка, разговор начат заново.");
    }
    const status = await api("/api/status");
    renderStatus(status);
    await refreshFiles();
  } catch (error) {
    setStatus(error.message);
  }
});

document.querySelector("#pick").addEventListener("click", async () => {
  try {
    const data = await jsonPost("/api/pick-folder", {});
    if (data.reset) {
      transcript.replaceChildren();
      clearPreview();
    }
    const status = await api("/api/status");
    renderStatus(status);
    await refreshFiles();
  } catch (error) {
    setStatus(error.message);
  }
});

settingsForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const payload = { baseUrl: baseUrl.value, model: modelInput.value };
  if (apiKey.value.trim() !== "") payload.apiKey = apiKey.value;
  try {
    renderStatus(await jsonPost("/api/settings", payload));
    apiKey.value = "";
  } catch (error) {
    setStatus(error.message);
  }
});

document.querySelector("#clear-key").addEventListener("click", async () => {
  try {
    renderStatus(await jsonPost("/api/clear-key", {}));
    apiKey.value = "";
  } catch (error) {
    setStatus(error.message);
  }
});

document.querySelector("#reset").addEventListener("click", async () => {
  try {
    await jsonPost("/api/reset", {});
    transcript.replaceChildren();
    addBubble("Заметки", "Новый разговор. Открытая папка та же.");
  } catch (error) {
    setStatus(error.message);
  }
});

document.querySelector("#suggest").addEventListener("click", () => {
  message.value = "Какие заметки есть в папке?";
  message.focus();
});

chatForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const text = message.value.trim();
  if (text === "") return;
  message.value = "";
  addBubble("Вы", text);
  const submit = chatForm.querySelector("button[type=submit]");
  submit.disabled = true;
  try {
    const data = await jsonPost("/api/chat", { message: text });
    addBubble("Заметки", data.reply, data.steps);
    await refreshFiles();
    if (selected) {
      const current = [...filesNode.querySelectorAll("button")].find((button) => button.dataset.path === selected);
      if (current) current.click();
    }
  } catch (error) {
    addBubble("Заметки", error.message, error.steps);
    try {
      await refreshFiles();
    } catch (refreshError) {
      setStatus(refreshError.message);
    }
  } finally {
    submit.disabled = false;
  }
});

api("/api/status")
  .then(async (data) => {
    renderStatus(data);
    if (data.root) await refreshFiles();
  })
  .catch((error) => setStatus(error.message));
