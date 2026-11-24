/**
 * Exemptions de quarantaine (créées en autorisant un dossier ; terminées via « Terminer ») et
 * identités protégées (manuelles modifiables ; automatiques en lecture seule). Deux notions distinctes.
 */
import { api, ApiError, withQuery } from "../api.js";
import { badge } from "../components/badge.js";
import { field, readForm } from "../components/form.js";
import { dateTime } from "../components/format.js";
import { PROTECTION_SOURCE, statusBadge } from "../components/labels.js";
import { confirmDialog, formDialog } from "../components/modal.js";
import { pagination } from "../components/pagination.js";
import { dataTable } from "../components/table.js";
import { toast } from "../components/toast.js";
import { emptyState, errorState, h, loadingState } from "../dom.js";
import { replaceQuery } from "../router.js";

const LIMIT = 25;
const STATES = [{ value: "true", label: "Actives" }, { value: "false", label: "Terminées" }, { value: "", label: "Toutes" }];

export function render(container, { query }) {
  const controller = new AbortController();
  const signal = controller.signal;
  const state = {
    active: query.has("etat") ? query.get("etat") : "true",
    offset: Math.max(0, Number.parseInt(query.get("debut") || "0", 10) || 0),
    removed: query.get("historique") === "1",
  };
  const exResults = h("div", { class: "section-gap" });
  const protResults = h("div");
  const stateSel = h("select", { class: "input", "aria-label": "Filtrer les exemptions" },
    STATES.map((o) => h("option", { value: o.value, selected: o.value === state.active }, o.label)));
  stateSel.addEventListener("change", () => { state.active = stateSel.value; state.offset = 0; sync(); loadExemptions(); });
  const removed = h("input", { type: "checkbox", checked: state.removed });
  removed.addEventListener("change", () => { state.removed = removed.checked; sync(); loadProtections(); });
  const add = h("button", { class: "btn btn--primary", type: "button", text: "Ajouter une protection manuelle", onClick: addProtection });

  container.replaceChildren(
    h("section", { class: "card", "aria-labelledby": "sec-ex" },
      h("h2", { class: "card__title", id: "sec-ex", text: "Exemptions de quarantaine" }),
      h("div", { class: "alert alert--info", role: "note" },
        h("p", { text: "Une exemption est créée uniquement en autorisant un dossier de quarantaine ; le motif affiché est la note saisie à cette occasion." }),
        h("p", { text: "Elle ne s'applique qu'à une identité inconnue (aucun membre associé). Elle ne couvre jamais un membre dont l'abonnement a expiré : celui-ci est toujours évalué normalement." }),
        h("p", { text: "Une exemption ne se modifie pas ; elle peut seulement être terminée. Son historique est conservé." })),
      h("div", { class: "toolbar" }, h("div", { class: "toolbar__filters" }, stateSel)),
      exResults),
    h("section", { class: "card", "aria-labelledby": "sec-prot" },
      h("div", { class: "card__header" }, h("h2", { class: "card__title", id: "sec-prot", text: "Identités protégées" }), add),
      h("p", { class: "field__hint", text: "Une identité protégée n'est jamais mise en quarantaine. Protections manuelles et automatiques sont gérées séparément." }),
      h("label", { class: "choice" }, removed, h("span", { text: "Afficher l'historique (protections retirées)" })),
      protResults));

  function sync() {
    replaceQuery("/exemptions", { etat: state.active === "true" ? "" : state.active || "toutes", debut: state.offset || "", historique: state.removed ? "1" : "" });
  }

  async function act(promise, message, reload) {
    try {
      if (await promise) { toast(message, "success"); reload(); }
    } catch (err) {
      toast(err instanceof ApiError ? err.message : String(err), "danger");
    }
  }

  async function loadExemptions() {
    exResults.replaceChildren(loadingState());
    try {
      const page = await api(withQuery("/api/identity-exemptions", {
        active: state.active === "toutes" ? "" : state.active, limit: LIMIT, offset: state.offset,
      }), { signal });
      exResults.replaceChildren(
        dataTable({ rows: page.items, empty: "Aucune exemption.", caption: "Exemptions de quarantaine", columns: [
          { label: "Identité", render: (x) => h("a", { href: `#/identites/${x.exit_node_identity_id}`, class: "mono", text: x.login_name }) },
          { label: "Accordée le", render: (x) => dateTime(x.granted_at) },
          { label: "Par", render: (x) => x.granted_by_email },
          { label: "Motif", render: (x) => x.reason },
          { label: "Dossier", render: (x) => (x.quarantine_record_id ? h("a", { href: `#/quarantaine/${x.quarantine_record_id}`, text: "Ouvrir" }) : "—") },
          { label: "État", render: (x) => (x.active ? badge("Active", "info") : h("span", {}, badge("Terminée", "neutral"),
            h("small", { class: "field__hint", text: ` le ${dateTime(x.ended_at)}${x.ended_by_email ? " par " + x.ended_by_email : ""}` }))) },
          { label: "Actions", render: (x) => (x.active ? h("button", { class: "btn btn--small btn--danger-outline", type: "button", text: "Terminer", onClick: () => act(
            confirmDialog({ title: "Terminer cette exemption ?", message: `${x.login_name} sera de nouveau évaluée normalement si elle reste inconnue. L'exemption reste dans l'historique.`, confirmLabel: "Terminer", danger: true })
              .then((ok) => (ok ? api(`/api/identity-exemptions/${x.id}/end`, { method: "POST" }) : null)),
            "Exemption terminée.", loadExemptions) }) : null) },
        ] }),
        pagination({ total: page.total, limit: LIMIT, offset: state.offset, onChange: (o) => { state.offset = o; sync(); loadExemptions(); } }));
    } catch (err) {
      if (err.name !== "AbortError") exResults.replaceChildren(errorState("Impossible de charger les exemptions.", err.message, loadExemptions));
    }
  }

  async function loadProtections() {
    protResults.replaceChildren(loadingState());
    try {
      const all = await api(withQuery("/api/protected-identities", { include_removed: state.removed ? "true" : "" }), { signal });
      const manual = all.filter((p) => p.source === "MANUAL");
      const auto = all.filter((p) => p.source !== "MANUAL");
      const columns = (editable) => [
        { label: "Login Tailscale", className: "mono", render: (p) => p.login_name },
        { label: "Source", render: (p) => statusBadge(PROTECTION_SOURCE, p.source) },
        { label: "Raison", render: (p) => p.reason },
        { label: "Depuis le", render: (p) => dateTime(p.created_at) },
        { label: "État", render: (p) => (p.active ? badge("Active", "success") : badge(`Retirée le ${dateTime(p.removed_at)}`, "neutral")) },
        ...(editable ? [{ label: "Actions", render: (p) => (p.active ? h("button", { class: "btn btn--small btn--danger-outline", type: "button", text: "Retirer", onClick: () => act(
          confirmDialog({ title: "Retirer cette protection ?", message: `${p.login_name} pourra de nouveau être mise en quarantaine selon les règles normales.`, confirmLabel: "Retirer", danger: true })
            .then((ok) => (ok ? api(`/api/protected-identities/${p.id}/remove`, { method: "POST" }) : null)),
          "Protection retirée.", loadProtections) }) : null) }] : []),
      ];
      protResults.replaceChildren(
        h("h3", { text: "Protections manuelles" }),
        dataTable({ rows: manual, empty: "Aucune protection manuelle.", columns: columns(true) }),
        h("h3", { text: "Protections automatiques (AUTO_MEMBER, AUTO_DEVICE)" }),
        auto.length
          ? dataTable({ rows: auto, columns: columns(false) })
          : emptyState("Aucune protection automatique.", "Ces sources sont prévues par le schéma, mais le backend actuel n'en produit pas. Lecture seule."));
    } catch (err) {
      if (err.name !== "AbortError") protResults.replaceChildren(errorState("Impossible de charger les protections.", err.message, loadProtections));
    }
  }

  async function addProtection() {
    const login = field({ label: "Login Tailscale", name: "login_name", type: "email", required: true, attrs: { maxlength: "320" } });
    const why = field({ label: "Raison", name: "reason", type: "textarea", attrs: { maxlength: "500" } });
    await act(formDialog({
      title: "Ajouter une protection manuelle",
      intro: h("div", { class: "alert alert--warning", role: "note", text: "Une identité protégée n'est jamais mise en quarantaine, quel que soit son abonnement. À réserver aux comptes d'administration ou d'infrastructure." }),
      fields: [login.el, why.el], submitLabel: "Protéger",
      onSubmit: (form) => api("/api/protected-identities", { method: "POST", body: readForm(form) }),
    }), "Protection ajoutée.", loadProtections);
  }

  loadExemptions();
  loadProtections();
  return () => controller.abort();
}
