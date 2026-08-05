/* ARESCO sign-in — shared by the BOQ engine and the Treasury app.
   Four screens: sign in · request code · enter code · set password. */

const $ = (id) => document.getElementById(id);

const screens = {
  login: $("form-login"),
  email: $("form-email"),
  code: $("form-code"),
  pass: $("form-pass"),
};

const state = { email: "", setupToken: "" };

/* ---------- theme: follow the app's stored preference ---------- */
(function theme() {
  const saved = localStorage.getItem("aresco-theme");
  const dark = saved ? saved === "dark"
    : window.matchMedia("(prefers-color-scheme: dark)").matches;
  if (dark) document.documentElement.setAttribute("data-theme", "dark");
})();

/* ---------- helpers ---------- */
function show(name, opts = {}) {
  Object.entries(screens).forEach(([k, el]) => el.classList.toggle("hide", k !== name));
  const copy = {
    login: ["Secure access", "Sign in", "Use the ARESCO email address you were issued."],
    email: ["New account", "Set up access", "Confirm your ARESCO address, then choose a password."],
    code: ["Step 2 of 3", "Check your email", `We sent a six-digit code to ${state.email}.`],
    pass: ["Step 3 of 3", "Choose a password", "This is what you will sign in with from now on."],
  }[name];
  $("eyebrow").textContent = copy[0];
  $("heading").textContent = copy[1];
  $("lede").textContent = copy[2];

  const stepped = name !== "login";
  $("steps").classList.toggle("hide", !stepped);
  if (stepped) {
    const idx = { email: 1, code: 2, pass: 3 }[name];
    [1, 2, 3].forEach((n) => $("s" + n).classList.toggle("on", n <= idx));
  }
  if (!opts.keepMessage) clearMsg();

  const focus = { login: "li-email", email: "su-email", code: "cd-code", pass: "pw-pass" }[name];
  const el = $(focus);
  if (el) setTimeout(() => el.focus(), 30);
}

function setMsg(text, kind = "err") {
  const box = $("msg");
  box.className = "msg " + kind;
  box.textContent = text;
}
function clearMsg() { $("msg").className = "msg hide"; $("msg").textContent = ""; }

function busy(btn, on, label) {
  btn.disabled = on;
  if (on) {
    btn.dataset.label = btn.textContent;
    btn.innerHTML = '<span class="spin"></span>' + (label || btn.dataset.label);
  } else if (btn.dataset.label) {
    btn.textContent = btn.dataset.label;
  }
}

async function post(path, body) {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  let data = {};
  try { data = await res.json(); } catch (_) { /* empty body */ }
  if (!res.ok) throw new Error(data.detail || `Request failed (${res.status})`);
  return data;
}

/* Client-side mirror of the server rule. The server is the authority; this
   just avoids a pointless round trip and gives an instant answer. */
let allowedDomain = "aresco.com.eg";
function domainOk(email) {
  const at = (email || "").trim().toLowerCase().split("@");
  return at.length === 2 && at[0].length > 0 && at[1] === allowedDomain;
}
function rejectDomain(email) {
  const typed = (email || "").trim();
  const at = typed.split("@");
  setMsg(
    at.length === 2 && at[1]
      ? `@${at[1]} addresses cannot be used. Access is limited to @${allowedDomain}.`
      : `Enter your full ARESCO address, ending in @${allowedDomain}.`
  );
}

/* ---------- boot ---------- */
(async function boot() {
  try {
    const cfg = await (await fetch("/auth/config")).json();
    allowedDomain = cfg.allowed_domain || allowedDomain;
    $("domain-label").textContent = "@" + allowedDomain;
    if (cfg.app_name) {
      document.title = "Sign in — " + cfg.app_name;
      const sub = cfg.app_name.replace(/^ARESCO\s*/i, "");
      if (sub) $("brand-sub").textContent = sub;
    }
    ["li-email", "su-email"].forEach((id) => {
      $(id).placeholder = "name@" + allowedDomain;
    });
    if (cfg.email_delivery === "server-log") {
      $("code-hint").textContent =
        "Mail is not configured on this server, so the code is written to the server log.";
    }
  } catch (_) { /* defaults are fine */ }
  show("login");
})();

/* ---------- navigation ---------- */
$("to-signup").onclick = () => { $("su-email").value = $("li-email").value; show("email"); };
$("to-login").onclick = () => { $("li-email").value = $("su-email").value; show("login"); };
$("cd-back").onclick = () => show("email");

/* ---------- sign in ---------- */
screens.login.onsubmit = async (e) => {
  e.preventDefault();
  const email = $("li-email").value.trim();
  if (!domainOk(email)) return rejectDomain(email);
  clearMsg();
  busy($("li-btn"), true, "Signing in…");
  try {
    await post("/auth/login", { email, password: $("li-pass").value });
    window.location.href = "/app/";
  } catch (err) {
    setMsg(err.message);
    busy($("li-btn"), false);
  }
};

/* ---------- step 1: request code ---------- */
screens.email.onsubmit = async (e) => {
  e.preventDefault();
  const email = $("su-email").value.trim();
  if (!domainOk(email)) return rejectDomain(email);
  clearMsg();
  busy($("su-btn"), true, "Sending…");
  try {
    const out = await post("/auth/request-code", { email });
    state.email = email.toLowerCase();
    show("code", { keepMessage: true });
    if (out.delivered_by === "email") {
      setMsg(`Code sent to ${state.email}. It expires in ${out.expires_in_minutes} minutes.`, "ok");
    } else {
      setMsg(out.message || "Mail is not configured — read the code from the server log.", "warn");
    }
  } catch (err) {
    setMsg(err.message);
  } finally {
    busy($("su-btn"), false);
  }
};

/* ---------- step 2: verify code ---------- */
$("cd-code").addEventListener("input", (e) => {
  e.target.value = e.target.value.replace(/\D/g, "").slice(0, 6);
});

screens.code.onsubmit = async (e) => {
  e.preventDefault();
  const code = $("cd-code").value.trim();
  if (code.length !== 6) return setMsg("Enter all six digits.");
  clearMsg();
  busy($("cd-btn"), true, "Verifying…");
  try {
    const out = await post("/auth/verify-code", { email: state.email, code });
    state.setupToken = out.setup_token;
    show("pass");
  } catch (err) {
    setMsg(err.message);
    $("cd-code").value = "";
    $("cd-code").focus();
  } finally {
    busy($("cd-btn"), false);
  }
};

/* ---------- step 3: set password ---------- */
screens.pass.onsubmit = async (e) => {
  e.preventDefault();
  const pw = $("pw-pass").value;
  if (pw !== $("pw-conf").value) return setMsg("The two passwords do not match.");
  clearMsg();
  busy($("pw-btn"), true, "Creating…");
  try {
    await post("/auth/set-password", {
      setup_token: state.setupToken,
      password: pw,
      full_name: $("pw-name").value.trim(),
    });
    window.location.href = "/app/";
  } catch (err) {
    setMsg(err.message);
    busy($("pw-btn"), false);
    // The setup token is short-lived; send them back to the start if it lapsed.
    if (/expired/i.test(err.message)) setTimeout(() => show("email", { keepMessage: true }), 1600);
  }
};
