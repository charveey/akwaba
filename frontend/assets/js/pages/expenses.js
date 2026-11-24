/** Dépenses : ajout, correction atomique, annulation, catégories, filtres, export CSV, historique. */
import { api, ApiError, withQuery } from "../api.js";
import { badge } from "../components/badge.js";
import { field, readForm } from "../components/form.js";
import { dateTime, day, money } from "../components/format.js";
import { AUDIT_ACTIONS } from "../components/labels.js";
import { confirmDialog, formDialog, openDialog } from "../components/modal.js";
import { pagination } from "../components/pagination.js";
import { dataTable } from "../components/table.js";
import { toast } from "../components/toast.js";
import { errorState, h, loadingState } from "../dom.js";
import { replaceQuery } from "../router.js";

const LIMIT = 25;
const xof = (v) => money(v, "XOF");

export function render(container, { query }) {
  const controller = new AbortController();
  const signal = controller.signal;
  const state = {
    from: query.get("du") || "", to: query.get("au") || "", category: query.get("categorie") || "",
    status: query.get("etat") || "", offset: Math.max(0, Number.parseInt(query.get("debut") || "0", 10) || 0),
  };
  let categories = [];
  const catSelect = h("select", { class: "input", "aria-label": "Filtrer par catégorie" }, h("option", { value: "", text: "Toutes les catégories" }));
  catSelect.addEventListener("change", () => { state.category = catSelect.value; refresh(); });
  const statusSel = h("select", { class: "input", "aria-label": "Filtrer par état" },
    [["", "Toutes"], ["ACTIVE", "Actives"], ["VOIDED", "Annulées"]].map(([v, l]) => h("option", { value: v, selected: v === state.status }, l)));
  statusSel.addEventListener("change", () => { state.status = statusSel.value; refresh(); });
  const dateInput = (label, key) => {
    const el = h("input", { class: "input", type: "date", "aria-label": label, value: state[key] });
    el.addEventListener("change", () => { state[key] = el.value; refresh(); });
    return h("label", { class: "inline-field" }, h("span", { text: label }), el);
  };
  let exportLink = h("a", { class: "btn", href: "/api/expenses?format=csv", download: "", text: "Exporter en CSV" });
  const totals = h("div", { class: "card-grid section-gap" });
  const results = h("div");

  container.replaceChildren(
    h("div", { class: "toolbar" },
      h("div", { class: "toolbar__filters" }, dateInput("Du", "from"), dateInput("Au", "to"), catSelect, statusSel),
      h("div", { class: "actions" },
        exportLink,
        h("button", { class: "btn", type: "button", text: "Catégories", onClick: manageCategories }),
        h("button", { class: "btn btn--primary", type: "button", text: "Ajouter une dépense", onClick: () => act(expenseDialog(), "Dépense enregistrée.") }))),
    totals, results);

  function params() {
    return { date_from: state.from, date_to: state.to, category_id: state.category, status: state.status };
  }

  function refresh(reset = true) {
    if (reset) state.offset = 0;
    replaceQuery("/depenses", { du: state.from, au: state.to, categorie: state.category, etat: state.status, debut: state.offset || "" });
    load();
  }

  async function loadCategories() {
    categories = await api(withQuery("/api/expense-categories", { include_inactive: "true" }), { signal });
    const keep = state.category;
    catSelect.replaceChildren(h("option", { value: "", text: "Toutes les catégories" }),
      ...categories.map((c) => h("option", { value: c.id, selected: c.id === keep }, c.is_active ? c.name : `${c.name} (désactivée)`)));
  }

  async function act(promise, message) {
    try {
      if (await promise) { toast(message, "success"); await loadCategories(); load(); }
    } catch (err) {
      toast(err instanceof ApiError ? err.message : String(err), "danger");
    }
  }

  function expenseFields(e = {}) {
    const active = categories.filter((c) => c.is_active || c.id === e.category_id);
    return [
      field({ label: "Catégorie", name: "category_id", type: "select", required: true, value: e.category_id,
        options: [{ value: "", label: "Choisir…" }, ...active.map((c) => ({ value: c.id, label: c.name }))] }).el,
      field({ label: "Montant (francs CFA)", name: "amount", type: "number", required: true, value: e.amount,
        attrs: { min: "1", step: "1", inputmode: "numeric" } }).el,
      field({ label: "Date de la dépense", name: "expense_date", type: "date", required: true, value: e.expense_date }).el,
      field({ label: "Description", name: "description", type: "textarea", value: e.description, attrs: { maxlength: "500" } }).el,
    ];
  }

  function body(form) {
    const v = readForm(form);
    return { category_id: v.category_id, amount: Number(v.amount), expense_date: v.expense_date, description: v.description };
  }

  function expenseDialog() {
    return formDialog({ title: "Ajouter une dépense", fields: expenseFields(), submitLabel: "Enregistrer",
      onSubmit: (form) => api("/api/expenses", { method: "POST", body: body(form) }) });
  }

  function correctDialog(e) {
    return formDialog({
      title: "Corriger la dépense",
      intro: h("div", { class: "alert alert--info", role: "note",
        text: "Une dépense enregistrée ne se modifie pas. La correction annule l'ancienne (motif « Corrigée », toujours consultable) "
          + "et enregistre la nouvelle, en une seule opération journalisée." }),
      fields: expenseFields(e), submitLabel: "Enregistrer la correction",
      onSubmit: (form) => api(`/api/expenses/${e.id}/correct`, { method: "POST", body: body(form) }),
    });
  }

  function voidDialog(e) {
    const reason = field({ label: "Motif de l'annulation", name: "reason", type: "textarea", required: true, attrs: { maxlength: "500" } });
    return formDialog({
      title: "Annuler cette dépense ?", danger: true, submitLabel: "Annuler la dépense",
      intro: h("div", { class: "alert alert--warning", role: "note", text: `${xof(e.amount)} du ${day(e.expense_date)} : l'annulation est irréversible ; la dépense reste dans l'historique.` }),
      fields: [reason.el],
      onSubmit: (form) => api(`/api/expenses/${e.id}/void`, { method: "POST", body: { reason: readForm(form).reason } }),
    });
  }

  async function history(e) {
    try {
      const ids = [e.id, e.replaces_id, e.replaced_by_id].filter(Boolean);
      const pages = await Promise.all(ids.map((id) => api(withQuery("/api/audit", { entity_type: "expense", entity_id: id, limit: 50 }))));
      const items = [...new Map(pages.flatMap((p) => p.items).map((x) => [x.id, x])).values()].sort((a, b) => b.id - a.id);
      openDialog({ title: "Historique de la dépense", wide: true, body: dataTable({ rows: items, empty: "Aucun événement.", columns: [
        { label: "Date", render: (x) => dateTime(x.occurred_at) },
        { label: "Événement", render: (x) => AUDIT_ACTIONS[x.action] || x.action },
        { label: "Par", render: (x) => x.actor_display },
        { label: "Montant", render: (x) => (x.details && (x.details.new_amount ?? x.details.amount) !== undefined ? xof(x.details.new_amount ?? x.details.amount) : "—") },
      ] }) });
    } catch (err) {
      toast(err instanceof ApiError ? err.message : String(err), "danger");
    }
  }

  async function manageCategories() {
    const list = h("div");
    const draw = () => list.replaceChildren(dataTable({ rows: categories, columns: [
      { label: "Catégorie", render: (c) => c.name },
      { label: "Dépenses", render: (c) => String(c.expense_count) },
      { label: "État", render: (c) => (c.is_active ? badge("Active", "success") : badge("Désactivée", "neutral")) },
      { label: "Actions", render: (c) => h("span", { class: "actions" },
        h("button", { class: "btn btn--small", type: "button", text: "Renommer", onClick: () => editCategory(c) }),
        h("button", { class: ["btn btn--small", c.is_active && "btn--danger-outline"], type: "button", text: c.is_active ? "Désactiver" : "Réactiver", onClick: () => toggleCategory(c) })) },
    ] }));
    async function reloadList() { await loadCategories(); draw(); }
    async function editCategory(c) {
      const name = field({ label: "Nom", name: "name", required: true, value: c.name, attrs: { maxlength: "80" } });
      const ok = await formDialog({ title: "Renommer la catégorie", fields: [name.el],
        intro: c.expense_count ? h("p", { class: "field__hint", text: `${c.expense_count} dépense(s) rattachée(s) afficheront le nouveau nom.` }) : null,
        onSubmit: (form) => api(`/api/expense-categories/${c.id}`, { method: "PUT", body: { name: readForm(form).name, is_active: c.is_active } }) });
      if (ok) { toast("Catégorie renommée.", "success"); await reloadList(); load(); }
    }
    async function toggleCategory(c) {
      if (c.is_active) {
        const ok = await confirmDialog({
          title: "Désactiver cette catégorie ?", danger: true, confirmLabel: "Désactiver",
          message: c.expense_count
            ? `Attention : ${c.expense_count} dépense(s) utilisent « ${c.name} ». Elles conservent leur catégorie et leur historique ; la catégorie ne sera simplement plus proposée pour de nouvelles dépenses. Aucune suppression n'est possible.`
            : `« ${c.name} » ne sera plus proposée pour de nouvelles dépenses.`,
        });
        if (!ok) return;
      }
      try {
        await api(`/api/expense-categories/${c.id}`, { method: "PUT", body: { name: c.name, is_active: !c.is_active } });
        toast(c.is_active ? "Catégorie désactivée." : "Catégorie réactivée.", "success");
        await reloadList();
      } catch (err) {
        toast(err instanceof ApiError ? err.message : String(err), "danger");
      }
    }
    async function addCategory() {
      const name = field({ label: "Nom", name: "name", required: true, attrs: { maxlength: "80" } });
      const ok = await formDialog({ title: "Nouvelle catégorie", fields: [name.el],
        onSubmit: (form) => api("/api/expense-categories", { method: "POST", body: { name: readForm(form).name } }) });
      if (ok) { toast("Catégorie ajoutée.", "success"); await reloadList(); }
    }
    draw();
    openDialog({ title: "Catégories de dépenses", wide: true,
      body: h("div", { class: "stack" },
        h("p", { class: "field__hint", text: "Une catégorie n'est jamais supprimée : elle peut être désactivée, les dépenses existantes gardent leur historique." }),
        list),
      actions: [{ label: "Nouvelle catégorie", variant: "primary", onClick: () => addCategory() }] });
  }

  let seq = 0;
  async function load() {
    const mine = ++seq;
    results.replaceChildren(loadingState());
    const fresh = h("a", { class: "btn", href: withQuery("/api/expenses", { ...params(), format: "csv" }), download: "", text: "Exporter en CSV" });
    exportLink.replaceWith(fresh);
    exportLink = fresh;
    try {
      const page = await api(withQuery("/api/expenses", { ...params(), limit: LIMIT, offset: state.offset }), { signal });
      if (mine !== seq) return;
      totals.replaceChildren(
        h("div", { class: "card stat" }, h("span", { class: "stat__label", text: "Total des dépenses actives (filtre)" }), h("span", { class: "stat__value", text: xof(page.total_amount) })),
        h("div", { class: "card stat" }, h("span", { class: "stat__label", text: "Dépenses (toutes)" }), h("span", { class: "stat__value", text: String(page.total) })));
      results.replaceChildren(
        dataTable({ rows: page.items, caption: "Dépenses", empty: "Aucune dépense ne correspond à ces critères.", columns: [
          { label: "Date", render: (e) => day(e.expense_date) },
          { label: "Catégorie", render: (e) => e.category_name },
          { label: "Montant", render: (e) => xof(e.amount) },
          { label: "Description", render: (e) => e.description },
          { label: "État", render: (e) => h("span", { class: "stacked" },
            e.status === "ACTIVE" ? badge("Active", "success") : badge("Annulée", "neutral"),
            e.void_reason ? h("small", { text: `Motif : ${e.void_reason}` }) : null,
            e.replaces_id ? h("small", { text: "Remplace une dépense corrigée" }) : null,
            e.replaced_by_id ? h("small", { text: "Remplacée par une correction" }) : null) },
          { label: "Actions", render: (e) => h("span", { class: "actions" },
            e.status === "ACTIVE" ? h("button", { class: "btn btn--small", type: "button", text: "Corriger", onClick: () => act(correctDialog(e), "Dépense corrigée.") }) : null,
            e.status === "ACTIVE" ? h("button", { class: "btn btn--small btn--danger-outline", type: "button", text: "Annuler", onClick: () => act(voidDialog(e), "Dépense annulée.") }) : null,
            h("button", { class: "btn btn--small btn--ghost", type: "button", text: "Historique", onClick: () => history(e) })) },
        ] }),
        pagination({ total: page.total, limit: LIMIT, offset: state.offset, onChange: (o) => { state.offset = o; refresh(false); } }));
    } catch (err) {
      if (err.name === "AbortError" || mine !== seq) return;
      results.replaceChildren(errorState("Impossible de charger les dépenses.", err instanceof ApiError ? err.message : String(err), load));
    }
  }

  loadCategories().catch(() => {}).finally(load);
  return () => controller.abort();
}
