import { ApiError } from "../api.js";
import { login } from "../auth.js";
import { logo } from "../components/icons.js";
import { h } from "../dom.js";

/** Page de connexion. onSuccess(returnTo) est appelé après une connexion réussie. */
export function renderLogin(container, { returnTo, notice, onSuccess }) {
  const error = h("div", { class: "alert alert--danger", role: "alert", hidden: true });
  const email = h("input", {
    class: "input", id: "login-email", name: "email", type: "email", required: true,
    autocomplete: "username", inputmode: "email", maxlength: "320", autofocus: true,
  });
  const password = h("input", {
    class: "input", id: "login-password", name: "password", type: "password", required: true,
    autocomplete: "current-password", maxlength: "1024",
  });
  const submit = h("button", { class: "btn btn--primary btn--block", type: "submit", text: "Se connecter" });

  const form = h("form", { class: "card", novalidate: true, "aria-labelledby": "login-title" },
    notice ? h("div", { class: "alert alert--info", role: "status", text: notice }) : null,
    error,
    h("div", { class: "field" },
      h("label", { for: "login-email", text: "Adresse e-mail" }), email),
    h("div", { class: "field" },
      h("label", { for: "login-password", text: "Mot de passe" }), password),
    submit);

  function showError(message) {
    error.textContent = message;
    error.hidden = false;
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    error.hidden = true;
    if (!email.value.trim() || !password.value) {
      showError("Veuillez saisir votre adresse e-mail et votre mot de passe.");
      (email.value.trim() ? password : email).focus();
      return;
    }
    submit.disabled = true;
    submit.setAttribute("aria-busy", "true");
    submit.textContent = "Connexion…";
    try {
      await login(email.value, password.value);
      password.value = "";
      onSuccess(returnTo);
    } catch (err) {
      password.value = "";
      showError(err instanceof ApiError ? err.message : "Connexion impossible.");
      password.focus();
    } finally {
      submit.disabled = false;
      submit.removeAttribute("aria-busy");
      submit.textContent = "Se connecter";
    }
  });

  container.replaceChildren(
    h("main", { class: "auth-layout" },
      h("div", { class: "login" },
        h("div", { class: "login__brand" },
          h("span", { class: "brand-mark" }, logo()),
          h("div", {},
            h("h1", { id: "login-title", text: "Akwaba" }),
            h("p", { text: "Administration — accès réservé" }))),
        form,
        h("p", { class: "login__footer", text: "Connexion sécurisée via le réseau Tailscale." }))));
  email.focus();
}
