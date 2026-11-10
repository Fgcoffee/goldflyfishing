"use strict";

/* Accounts: log in, register, reset, verify, Discord, account settings.
 *
 * Only on the web server - the desktop window has no /auth routes, and this
 * script notices (/auth/me is missing) and leaves the placeholder button as
 * it is. Every POST carries the CSRF token from the page's cookie.
 */

const Auth = {
  user: null,
  csrf: "",
  available: false,
  mode: "login",
};

function authCookie(name) {
  const match = document.cookie.match(new RegExp(`(?:^|; )${name}=([^;]*)`));
  return match ? decodeURIComponent(match[1]) : "";
}

async function authPost(path, body) {
  const response = await fetch(path, {
    method: "POST",
    credentials: "same-origin",
    headers: {
      "Content-Type": "application/json",
      "X-CSRF-Token": authCookie("mtgfish_csrf") || Auth.csrf,
    },
    body: JSON.stringify(body || {}),
  });
  let data = {};
  try { data = await response.json(); } catch (err) { /* empty body */ }
  return { ok: response.ok, status: response.status, data };
}

async function authRefresh() {
  let response;
  try {
    response = await fetch("/auth/me", { credentials: "same-origin" });
  } catch (err) {
    return;
  }
  if (response.status === 404) return; // the desktop window: no accounts
  Auth.available = true;
  const data = await response.json().catch(() => ({}));
  Auth.csrf = data.csrf || Auth.csrf;
  Auth.user = data.user || null;
  Auth.emailEnabled = !data.email || data.email.enabled !== false;
  authShowUser();
  document.dispatchEvent(new CustomEvent("authchange", { detail: Auth.user }));
}

function authShowUser() {
  const button = document.getElementById("btn-login");
  button.textContent = "";
  if (Auth.user) {
    if (Auth.user.avatar_url) {
      const img = document.createElement("img");
      img.src = Auth.user.avatar_url;
      img.alt = "";
      img.className = "avatar";
      button.appendChild(img);
    }
    button.appendChild(document.createTextNode(Auth.user.display_name));
    button.classList.remove("primary");
    button.title = "Your account";
  } else {
    button.textContent = "Log in / Register";
    button.classList.add("primary");
    button.title = "";
  }
}

/* ------------------------------------------------------------ the dialog */

const AUTH_TITLES = {
  login: ["Welcome back", "Log in"],
  register: ["Create your account", "Create account"],
  forgot: ["Reset your password", "Send the link"],
  reset: ["Choose a new password", "Set password"],
};

function authSay(text, bad) {
  const node = document.getElementById("authmessage");
  node.textContent = text || "";
  node.className = bad ? "hint bad" : "hint";
}

function authMode(mode) {
  Auth.mode = mode;
  document.querySelectorAll("#authtabs [data-auth]").forEach((b) => {
    b.classList.toggle("active", b.dataset.auth === mode);
  });
  document.getElementById("authtabs").hidden = mode === "forgot" || mode === "reset";
  document.querySelectorAll("#authform [data-auth-only]").forEach((node) => {
    node.hidden = !node.dataset.authOnly.split(" ").includes(mode);
  });
  const [title, action] = AUTH_TITLES[mode];
  document.getElementById("authtitle").textContent = title;
  document.getElementById("authsubmit").textContent = action;
  const emailOff = Auth.emailEnabled === false && (mode === "login" || mode === "register");
  if (emailOff) {
    // Discord only (no email provider configured on this server yet).
    document.querySelectorAll("#authform [data-auth-only]").forEach((node) => {
      if (node.id !== "authdiscord" && !node.classList.contains("consent")) node.hidden = true;
    });
    document.getElementById("authtabs").hidden = true;
    document.getElementById("authtitle").textContent = "Log in or create an account";
  }
  document.getElementById("authsubmit").hidden = emailOff;
  document.getElementById("authpassword").autocomplete =
    mode === "login" ? "current-password" : "new-password";
  authSay("");
}

function authOpen(mode) {
  authMode(mode);
  document.getElementById("dlg-auth").showModal();
}

async function authSubmit(event) {
  event.preventDefault();
  const email = document.getElementById("authemail").value.trim();
  const password = document.getElementById("authpassword").value;
  const name = document.getElementById("authname").value.trim();
  const submit = document.getElementById("authsubmit");
  submit.disabled = true;
  try {
    let result;
    if (Auth.mode === "login") {
      result = await authPost("/auth/login", { email, password });
    } else if (Auth.mode === "register") {
      if (!document.getElementById("authterms").checked) {
        authSay("Please confirm you're 16 or older and agree to the Terms and Privacy Policy.", true);
        return;
      }
      result = await authPost("/auth/register", {
        email, password, display_name: name, accept_terms: true,
      });
    } else if (Auth.mode === "forgot") {
      result = await authPost("/auth/password/forgot", { email });
      if (result.ok) { authSay(result.data.message); return; }
    } else {
      result = await authPost("/auth/password/reset", { token: Auth.resetToken, password });
    }
    if (!result.ok) { authSay(result.data.error || "that did not work", true); return; }
    document.getElementById("authpassword").value = "";
    await authRefresh();
    document.getElementById("dlg-auth").close();
    if (Auth.mode === "register") {
      toast("Welcome! Check your email to verify your address.");
    } else {
      toast(`Signed in as ${Auth.user ? Auth.user.display_name : ""}`);
    }
  } finally {
    submit.disabled = false;
  }
}

/* ------------------------------------------------------------ account */

function acctSay(text, bad) {
  const node = document.getElementById("acct-message");
  node.textContent = text || "";
  node.className = bad ? "hint bad" : "hint";
}

function acctFill() {
  const user = Auth.user;
  if (!user) return;
  document.getElementById("acct-name").value = user.display_name;
  document.getElementById("acct-email").textContent = user.email
    ? `${user.email}${user.email_verified ? " (verified)" : " (not verified)"}` : "none";
  document.getElementById("acct-verify").hidden = !(user.email && !user.email_verified);
  const discord = user.discord;
  document.getElementById("acct-discord").textContent = discord
    ? (discord.global_name || discord.username || "linked") : "not linked";
  document.getElementById("acct-link").hidden = !!discord;
  document.getElementById("acct-unlink").hidden = !discord;
  acctSay("");
}

function acctOpen() {
  acctFill();
  document.getElementById("dlg-account").showModal();
}

/* --------------------------------------------------------- landing hashes */

/* Email links and the Discord callback land on "/#verify=...", "/#reset=..."
 * or "/#auth_error=...". Read once, then cleared from the address bar. */
async function authHandleHash() {
  const hash = location.hash.slice(1);
  if (!hash) return;
  const params = new URLSearchParams(hash);
  let handled = false;
  if (params.has("verify")) {
    handled = true;
    const result = await authPost("/auth/verify", { token: params.get("verify") });
    toast(result.ok ? "Email verified - thanks!" : (result.data.error || "That link did not work"));
    await authRefresh();
  } else if (params.has("reset")) {
    handled = true;
    Auth.resetToken = params.get("reset");
    authOpen("reset");
  } else if (params.has("auth_error")) {
    handled = true;
    toast(params.get("auth_error"));
  }
  if (handled) history.replaceState(null, "", location.pathname + location.search);
}

/* --------------------------------------------------------------- wiring */

document.getElementById("btn-login").addEventListener("click", () => {
  if (!Auth.available) {
    // The desktop window: accounts live on the website only.
    toast("Accounts are available on the website.");
    return;
  }
  if (Auth.user) acctOpen(); else authOpen("login");
});
document.querySelectorAll("#authtabs [data-auth]").forEach((button) => {
  button.addEventListener("click", () => authMode(button.dataset.auth));
});
document.getElementById("authforgot").addEventListener("click", () => authMode("forgot"));
document.getElementById("authback").addEventListener("click", () => authMode("login"));
document.getElementById("authform").addEventListener("submit", authSubmit);

document.getElementById("acct-savename").addEventListener("click", async () => {
  const result = await authPost("/auth/account", {
    display_name: document.getElementById("acct-name").value,
  });
  if (!result.ok) { acctSay(result.data.error, true); return; }
  await authRefresh();
  acctSay("Saved.");
});
document.getElementById("acct-resend").addEventListener("click", async () => {
  const result = await authPost("/auth/verify/resend");
  acctSay(result.ok ? "Sent - check your inbox." : result.data.error, !result.ok);
});
document.getElementById("acct-unlink").addEventListener("click", async () => {
  const result = await authPost("/auth/discord/unlink");
  if (!result.ok) { acctSay(result.data.error, true); return; }
  await authRefresh();
  acctFill();
  acctSay("Discord unlinked.");
});
document.getElementById("acct-logout").addEventListener("click", async () => {
  await authPost("/auth/logout");
  document.getElementById("dlg-account").close();
  await authRefresh();
  toast("Logged out");
});
document.getElementById("acct-delete").addEventListener("click", async () => {
  const typed = prompt(
    "This deletes your account, saved decks, run history and sessions. It cannot be undone.\n"
    + "Type DELETE to confirm."
  );
  if (typed === null) return;
  const result = await authPost("/auth/account/delete", { confirm: typed });
  if (!result.ok) { acctSay(result.data.error, true); return; }
  document.getElementById("dlg-account").close();
  await authRefresh();
  toast("Your account was deleted");
});

/* ---------------------------------------------------------- cookie notice */

/* Essential cookies only, so this informs rather than asks; it is dismissed
 * once per browser (and again when the notice's wording version changes). */
const COOKIE_NOTICE = "mtgfish.cookieNotice.v1";
(function cookieNotice() {
  const box = document.getElementById("cookienotice");
  if (!box) return;
  let seen = false;
  try { seen = !!localStorage.getItem(COOKIE_NOTICE); } catch (err) { /* private mode */ }
  box.hidden = seen;
  document.getElementById("cookieok").addEventListener("click", () => {
    box.hidden = true;
    try { localStorage.setItem(COOKIE_NOTICE, new Date().toISOString()); } catch (err) { /* */ }
  });
})();

authRefresh().then(authHandleHash);
