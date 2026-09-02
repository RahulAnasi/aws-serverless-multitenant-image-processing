const config = window.MTIP_CONFIG;

const elements = {
  loginButton: document.querySelector("#login-button"),
  logoutButton: document.querySelector("#logout-button"),
  identity: document.querySelector("#identity"),
  signedOutPanel: document.querySelector("#signed-out-panel"),
  appPanel: document.querySelector("#app-panel"),
  uploadForm: document.querySelector("#upload-form"),
  imageInput: document.querySelector("#image-input"),
  fileLabel: document.querySelector("#file-label"),
  analyzeButton: document.querySelector("#analyze-button"),
  progress: document.querySelector("#progress"),
  progressText: document.querySelector("#progress-text"),
  emptyResult: document.querySelector("#empty-result"),
  resultContent: document.querySelector("#result-content"),
  labels: document.querySelector("#labels"),
  resultMeta: document.querySelector("#result-meta"),
  refreshButton: document.querySelector("#refresh-button"),
  jobsBody: document.querySelector("#jobs-body"),
  jobsEmpty: document.querySelector("#jobs-empty"),
  notice: document.querySelector("#notice")
};

const tokenKey = "mtip.oauth.tokens";
const verifierKey = "mtip.oauth.verifier";
const stateKey = "mtip.oauth.state";

function validateConfig() {
  const required = ["cognitoDomain", "clientId", "redirectUri", "logoutUri", "apiBaseUrl"];
  if (!config || required.some((key) => !config[key] || config[key].includes("YOUR_"))) {
    throw new Error("Configuration is missing. Copy config.example.js to config.js and replace every placeholder.");
  }
}

function base64Url(bytes) {
  let binary = "";
  bytes.forEach((byte) => { binary += String.fromCharCode(byte); });
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function randomValue(length = 32) {
  return base64Url(crypto.getRandomValues(new Uint8Array(length)));
}

async function sha256(value) {
  const input = new TextEncoder().encode(value);
  return new Uint8Array(await crypto.subtle.digest("SHA-256", input));
}

function decodeJwt(token) {
  const payload = token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/");
  const normalized = payload.padEnd(Math.ceil(payload.length / 4) * 4, "=");
  const bytes = Uint8Array.from(atob(normalized), (char) => char.charCodeAt(0));
  return JSON.parse(new TextDecoder().decode(bytes));
}

function getTokens() {
  const raw = sessionStorage.getItem(tokenKey);
  if (!raw) return null;
  try {
    const tokens = JSON.parse(raw);
    const claims = decodeJwt(tokens.access_token);
    if (claims.exp * 1000 <= Date.now()) {
      sessionStorage.removeItem(tokenKey);
      return null;
    }
    return tokens;
  } catch {
    sessionStorage.removeItem(tokenKey);
    return null;
  }
}

function setSignedInView(tokens) {
  const idClaims = decodeJwt(tokens.id_token);
  elements.identity.textContent = idClaims.email || idClaims.sub;
  elements.identity.hidden = false;
  elements.loginButton.hidden = true;
  elements.logoutButton.hidden = false;
  elements.signedOutPanel.hidden = true;
  elements.appPanel.hidden = false;
}

function setSignedOutView() {
  elements.identity.hidden = true;
  elements.loginButton.hidden = false;
  elements.logoutButton.hidden = true;
  elements.signedOutPanel.hidden = false;
  elements.appPanel.hidden = true;
}

function showNotice(message) {
  elements.notice.textContent = message;
  elements.notice.hidden = false;
  window.setTimeout(() => { elements.notice.hidden = true; }, 7000);
}

function setProgress(message, visible = true) {
  elements.progressText.textContent = message;
  elements.progress.hidden = !visible;
}

async function login() {
  const verifier = randomValue(48);
  const state = randomValue(24);
  const challenge = base64Url(await sha256(verifier));
  sessionStorage.setItem(verifierKey, verifier);
  sessionStorage.setItem(stateKey, state);

  const params = new URLSearchParams({
    client_id: config.clientId,
    response_type: "code",
    redirect_uri: config.redirectUri,
    scope: config.scopes.join(" "),
    state,
    code_challenge: challenge,
    code_challenge_method: "S256"
  });
  const domain = config.cognitoDomain.replace(/\/$/, "");
  window.location.assign(`${domain}/oauth2/authorize?${params}`);
}

async function handleCallback() {
  const params = new URLSearchParams(window.location.search);
  if (params.has("error")) {
    throw new Error(params.get("error_description") || params.get("error"));
  }
  const code = params.get("code");
  if (!code) return false;

  const expectedState = sessionStorage.getItem(stateKey);
  const verifier = sessionStorage.getItem(verifierKey);
  if (!expectedState || params.get("state") !== expectedState || !verifier) {
    throw new Error("The sign-in response could not be verified. Start sign-in again.");
  }

  const body = new URLSearchParams({
    grant_type: "authorization_code",
    client_id: config.clientId,
    code,
    redirect_uri: config.redirectUri,
    code_verifier: verifier
  });
  const domain = config.cognitoDomain.replace(/\/$/, "");
  const response = await fetch(`${domain}/oauth2/token`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body
  });
  if (!response.ok) throw new Error(`Token exchange failed (${response.status}).`);

  const tokens = await response.json();
  sessionStorage.setItem(tokenKey, JSON.stringify(tokens));
  sessionStorage.removeItem(verifierKey);
  sessionStorage.removeItem(stateKey);
  history.replaceState({}, document.title, config.redirectUri);
  return true;
}

function logout() {
  sessionStorage.clear();
  const params = new URLSearchParams({
    client_id: config.clientId,
    logout_uri: config.logoutUri
  });
  const domain = config.cognitoDomain.replace(/\/$/, "");
  window.location.assign(`${domain}/logout?${params}`);
}

async function api(path, options = {}) {
  const tokens = getTokens();
  if (!tokens) {
    setSignedOutView();
    throw new Error("Your session expired. Sign in again.");
  }
  const headers = new Headers(options.headers || {});
  headers.set("Authorization", `Bearer ${tokens.access_token}`);
  if (options.body) headers.set("Content-Type", "application/json");

  const baseUrl = config.apiBaseUrl.replace(/\/$/, "");
  const response = await fetch(`${baseUrl}${path}`, { ...options, headers });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.message || `API request failed (${response.status}).`);
  return body;
}

function formatDate(value) {
  if (!value) return "—";
  const options = { dateStyle: "medium", timeStyle: "short" };
  return new Intl.DateTimeFormat(undefined, options).format(new Date(value));
}

function makeCell(text) {
  const cell = document.createElement("td");
  cell.textContent = text;
  return cell;
}

function renderJobs(jobs) {
  elements.jobsBody.replaceChildren();
  elements.jobsEmpty.hidden = jobs.length !== 0;
  for (const job of jobs) {
    const row = document.createElement("tr");
    row.append(makeCell(job.originalFilename || "—"));

    const statusCell = document.createElement("td");
    const status = document.createElement("span");
    status.className = `status ${String(job.status || "").toLowerCase()}`;
    status.textContent = job.status || "UNKNOWN";
    statusCell.append(status);
    row.append(statusCell);

    row.append(makeCell(job.labelCount ?? "—"));
    row.append(makeCell(formatDate(job.createdAt)));

    const actionCell = document.createElement("td");
    if (job.status === "COMPLETED") {
      const button = document.createElement("button");
      button.className = "link-button";
      button.textContent = "View";
      button.addEventListener("click", () => {
        loadResult(job.jobId).catch((error) => showNotice(error.message));
      });
      actionCell.append(button);
    } else {
      actionCell.textContent = "—";
    }
    row.append(actionCell);
    elements.jobsBody.append(row);
  }
}

async function refreshJobs() {
  elements.refreshButton.disabled = true;
  try {
    const body = await api("/jobs");
    renderJobs(body.jobs || []);
  } finally {
    elements.refreshButton.disabled = false;
  }
}

async function loadResult(jobId) {
  const body = await api(`/jobs/${encodeURIComponent(jobId)}/result`);
  const response = await fetch(body.url);
  if (!response.ok) throw new Error(`Result download failed (${response.status}).`);
  const result = await response.json();

  elements.labels.replaceChildren();
  for (const label of result.labels || []) {
    const chip = document.createElement("span");
    chip.className = "label-chip";
    const name = document.createElement("span");
    name.textContent = label.name || "Unknown";
    const confidence = document.createElement("small");
    confidence.textContent = `${Number(label.confidence || 0).toFixed(1)}%`;
    chip.append(name, confidence);
    elements.labels.append(chip);
  }
  elements.resultMeta.textContent = `${result.labels?.length || 0} labels · completed result`;
  elements.emptyResult.hidden = true;
  elements.resultContent.hidden = false;
}

async function waitForCompletion(jobId) {
  const deadline = Date.now() + 90_000;
  while (Date.now() < deadline) {
    const body = await api(`/jobs/${encodeURIComponent(jobId)}`);
    const job = body.job;
    if (job.status === "COMPLETED") return job;
    if (job.status === "FAILED") {
      throw new Error("Image processing failed. Check the processor logs and DLQ.");
    }
    await new Promise((resolve) => window.setTimeout(resolve, 2000));
  }
  throw new Error("Processing is still running. Refresh the jobs list shortly.");
}

async function submitImage(event) {
  event.preventDefault();
  const file = elements.imageInput.files[0];
  if (!file) return;
  if (!["image/jpeg", "image/png"].includes(file.type)) {
    showNotice("Choose a JPEG or PNG image.");
    return;
  }
  if (file.size > 10 * 1024 * 1024) {
    showNotice("The image must be 10 MiB or smaller.");
    return;
  }

  elements.analyzeButton.disabled = true;
  try {
    setProgress("Creating a tenant-scoped job…");
    const created = await api("/jobs", {
      method: "POST",
      body: JSON.stringify({ filename: file.name, contentType: file.type })
    });

    setProgress("Uploading directly to private S3…");
    const upload = await fetch(created.upload.url, {
      method: created.upload.method,
      headers: created.upload.headers,
      body: file
    });
    if (!upload.ok) throw new Error(`S3 upload failed (${upload.status}).`);

    setProgress("Waiting for asynchronous processing…");
    const completed = await waitForCompletion(created.job.jobId);
    setProgress("Loading labels…");
    await loadResult(completed.jobId);
    await refreshJobs();
    setProgress("Complete.", false);
  } finally {
    elements.analyzeButton.disabled = false;
  }
}

async function start() {
  try {
    validateConfig();
    await handleCallback();
    const tokens = getTokens();
    if (!tokens) {
      setSignedOutView();
      return;
    }
    setSignedInView(tokens);
    await refreshJobs();
  } catch (error) {
    showNotice(error.message || "Unexpected application error.");
    setSignedOutView();
  }
}

elements.loginButton.addEventListener("click", () => {
  login().catch((error) => showNotice(error.message));
});
elements.logoutButton.addEventListener("click", logout);
elements.refreshButton.addEventListener("click", () => {
  refreshJobs().catch((error) => showNotice(error.message));
});
elements.uploadForm.addEventListener("submit", (event) => {
  submitImage(event).catch((error) => {
    setProgress("Stopped.", false);
    showNotice(error.message);
  });
});
elements.imageInput.addEventListener("change", () => {
  elements.fileLabel.textContent = elements.imageInput.files[0]?.name || "Choose an image";
});

start();
