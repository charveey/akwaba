/** Fiche d'une identité observée : évaluation, association, appareils, observations, dossiers, décisions. */
import { api, ApiError, withQuery } from "../api.js";
import { badge } from "../components/badge.js";
import { field, readForm } from "../components/form.js";
import { dateTime } from "../components/format.js";
import {
  ACCESS, AUDIT_ACTIONS, DECISION, PROTECTION_SOURCE, QUARANTINE_STATUS, VERDICT, AMBIGUITY, ambiguityBadges,
  reason, statusBadge,
} from "../components/labels.js";
import { confirmDialog, formDialog } from "../components/modal.js";
import { dataTable } from "../components/table.js";
import { toast } from "../components/toast.js";
import { emptyState, errorState, h, loadingState } from "../dom.js";

export function render(container, { params, setTitle }) {
  const controller = new AbortController();
  const signal = controller.signal;
  let alive = true;

  async function load() {
    container.replaceChildren(loadingState("Chargement de l'identité…"));
    try {
      const d = await api(`/api/identities/${params.id}`, { signal });
      const recordIds = d.records.map((r) => r.id);
      const audits = await Promise.all([
        ...recordIds.slice(0, 20).map((id) => api(withQuery("/api/audit", { entity_type: "quarantine_record", entity_id: id, limit: 100 }), { signal })),
        d.identity.client_identity_id
          ? api(withQuery("/api/audit", { entity_type: "client_identity", entity_id: d.identity.client_identity_id, limit: 100 }), { signal })
          : Promise.resolve({ items: [] }),
      ]);
      if (!alive) return;
      setTitle(d.identity.login_name);
      const history = [...new Map(audits.flatMap((p) => p.items).map((e) => [e.id, e])).values()].sort((a, b) => b.id - a.id);
      container.replaceChildren(view(d, history));
    } catch (err) {
      if (!alive || err.name === "AbortError") return;
      const notFound = err instanceof ApiError && (err.status === 404 || err.status === 422);
      container.replaceChildren(errorState(notFound ? "Identité introuvable." : "Impossible de charger l'identité.",
        notFound ? null : err.message, notFound ? null : load));
    }
  }

  async function act(promise, message) {
    try {
      if (await promise) {
        toast(message, "success");
        await load();
      }
    } catch (err) {
      toast(err instanceof ApiError ? err.message : String(err), "danger");
    }
  }

  function view(d, history) {
    const i = d.identity;
    return h("div", { class: "stack" },
      h("section", { class: "card member-head" },
        h("div", { class: "badges member-head__badges" },
          statusBadge(VERDICT, i.verdict),
          i.protection_sources.map((s) => statusBadge(PROTECTION_SOURCE, s)),
          i.exempt ? badge("Exemptée (identité inconnue uniquement)", "info") : null,
          i.active_now ? badge("Active maintenant", "success") : null,
          ambiguityBadges(i.ambiguity)),
        h("p", { class: "member-head__plan", text: `Motif : ${reason(i.reason)}${i.access ? ` · accès du membre : ${ACCESS[i.access]?.label || i.access}` : ""}` }),
        h("div", { class: "actions" },
          i.member
            ? h("button", { class: "btn", type: "button", text: "Modifier l'association", onClick: () => act(reassignDialog(i), "Identité réaffectée.") })
            : h("button", { class: "btn btn--primary", type: "button", text: "Associer à un membre", onClick: () => act(associateDialog(i, d.contact_matches), "Identité associée.") }))),

      i.ambiguity.length ? h("div", { class: "alert alert--warning", role: "note" },
        h("p", { text: "Signaux d'ambiguïté — strictement informatifs : ils ne modifient ni le verdict, ni l'association, ni les dossiers." }),
        h("ul", {}, i.ambiguity.map((c) => h("li", { text: AMBIGUITY[c]?.help || c }))),
        d.contact_matches.length ? h("p", {}, "Membre(s) dont l'e-mail de contact correspond : ",
          d.contact_matches.map((m, n) => [n ? ", " : "", h("a", { href: `#/membres/${m.id}`, text: m.full_name })])) : null) : null,

      h("div", { class: "detail-grid" },
        card("sec-profile", "Profil Tailscale", h("dl", { class: "dl" },
          h("dt", { text: "Login Tailscale" }), h("dd", { class: "mono", text: i.login_name }),
          h("dt", { text: "Nom d'affichage" }), h("dd", { text: i.display_name || "—" }),
          h("dt", { text: "Tailscale user ID" }), h("dd", { class: "mono", text: i.tailscale_user_id ?? "—" }),
          h("dt", { text: "Partagée (sharee)" }), h("dd", { text: i.is_sharee ? "Oui" : "Non" }),
          h("dt", { text: "Première observation" }), h("dd", { text: dateTime(i.first_seen_at) }),
          h("dt", { text: "Dernière observation" }), h("dd", { text: dateTime(i.last_seen_at) }),
          h("dt", { text: "Dernière activité confirmée" }), h("dd", { text: dateTime(i.last_confirmed_activity_at) }),
          h("dt", { text: "Exit Nodes distincts" }), h("dd", { text: `${i.exit_node_count} (observations conservées, ${d.exit_node_reference_days} jours)` }))),
        card("sec-member", "Membre associé", i.member
          ? h("dl", { class: "dl" },
            h("dt", { text: "Membre" }), h("dd", {}, h("a", { href: `#/membres/${i.member.id}`, text: i.member.full_name })),
            h("dt", { text: "Accès" }), h("dd", {}, i.access ? statusBadge(ACCESS, i.access) : "—"))
          : emptyState("Aucun membre associé.", "L'association se fait uniquement par décision d'un administrateur."))),

      card("sec-devices", "Appareils", dataTable({ rows: i.devices, empty: "Aucun appareil connu.", columns: [
        { label: "Hostname", render: (x) => x.hostname },
        { label: "Stable ID", className: "mono", render: (x) => x.stable_id },
        { label: "Système", render: (x) => x.os },
        { label: "IP Tailscale", className: "mono", render: (x) => x.tailscale_ip },
        { label: "Dernière observation", render: (x) => dateTime(x.last_seen_at) },
      ] })),

      card("sec-records", "Dossiers de quarantaine", dataTable({ rows: d.records, empty: "Aucun dossier.", columns: [
        { label: "Ouvert le", render: (r) => h("a", { href: `#/quarantaine/${r.id}`, text: dateTime(r.started_at) }) },
        { label: "Statut", render: (r) => statusBadge(QUARANTINE_STATUS, r.status) },
        { label: "Résolu le", render: (r) => dateTime(r.resolved_at) },
        { label: "Note", render: (r) => r.resolution_note },
      ] })),

      card("sec-decisions", "Décisions simulées", h("div", {},
        simulationNotice(),
        dataTable({ rows: d.decisions, empty: "Aucune décision simulée.", columns: [
          { label: "Date", render: (x) => dateTime(x.decided_at) },
          { label: "Décision", render: (x) => statusBadge(DECISION, x.decision) },
          { label: "Motif", render: (x) => reason(x.reason) },
          { label: "Simulation", render: (x) => simulationState(x) },
        ] }))),

      card("sec-exemptions", "Exemptions", dataTable({ rows: d.exemptions, empty: "Aucune exemption.", columns: [
        { label: "Accordée le", render: (x) => dateTime(x.granted_at) },
        { label: "Par", render: (x) => x.granted_by_email },
        { label: "Motif", render: (x) => x.reason },
        { label: "État", render: (x) => (x.active ? badge("Active", "info") : badge(`Terminée le ${dateTime(x.ended_at)}`, "neutral")) },
      ] })),

      card("sec-obs", "Observations récentes", dataTable({ rows: d.observations, empty: "Aucune observation conservée.", columns: [
        { label: "Date", render: (o) => dateTime(o.observed_at) },
        { label: "Appareil", render: (o) => o.hostname },
        { label: "Flux actifs", render: (o) => String(o.active_flows) },
        { label: "Flux avec réponse", render: (o) => String(o.replied_flows) },
        { label: "Activité confirmée", render: (o) => (o.active_confirmed ? badge("Oui", "success") : badge("Non", "neutral")) },
      ] })),

      card("sec-history", "Historique", dataTable({ rows: history, empty: "Aucun événement enregistré.", columns: [
        { label: "Date", render: (e) => dateTime(e.occurred_at) },
        { label: "Événement", render: (e) => AUDIT_ACTIONS[e.action] || e.action },
        { label: "Par", render: (e) => e.actor_email || (e.actor_label === "system" ? "Automatique" : e.actor_label) || "—" },
      ] })));
  }

  load();
  return () => { alive = false; controller.abort(); };
}

function card(id, title, content) {
  return h("section", { class: "card", "aria-labelledby": id }, h("h2", { class: "card__title", id, text: title }), content);
}

export function simulationNotice() {
  return h("div", { class: "alert alert--info simulation-banner", role: "note",
    text: "Décisions SIMULÉES (dry-run) : aucune action réseau n'est effectuée, aucun accès Tailscale n'est modifié." });
}

export function simulationState(d) {
  return d.mode === "dry_run" && d.enforced === false ? badge("Simulée — non appliquée", "neutral") : badge("Incohérent — à signaler", "danger");
}

/** Sélection d'un membre (recherche côté serveur), avec suggestions éventuelles. */
function memberPicker(suggestions = [], excludeId = null) {
  const search = h("input", { class: "input", type: "search", placeholder: "Nom, e-mail, WhatsApp…", "aria-label": "Rechercher un membre" });
  const target = field({ label: "Membre", name: "member_id", type: "select", required: true, options: [
    { value: "", label: suggestions.length ? "Choisir (suggestions ci-dessous ou recherche)…" : "Rechercher un membre ci-dessus…" },
    ...suggestions.filter((m) => m.id !== excludeId).map((m) => ({ value: m.id, label: `${m.full_name} (e-mail de contact correspondant)` })),
  ] });
  let timer = null;
  search.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(async () => {
      try {
        const page = await api(withQuery("/api/members", { q: search.value.trim(), sort: "name", limit: 20 }));
        const keep = target.input.value;
        target.input.replaceChildren(h("option", { value: "", text: page.items.length ? "Choisir…" : "Aucun membre trouvé" }),
          ...page.items.filter((m) => m.id !== excludeId).map((m) => h("option", { value: m.id, selected: m.id === keep },
            `${m.full_name}${m.identities.some((x) => x.is_active) ? " — " + m.identities.filter((x) => x.is_active).map((x) => x.login_name).join(", ") : ""}`)));
      } catch { /* la recherche reste possible */ }
    }, 250);
  });
  return { els: [h("div", { class: "field" }, h("label", { text: "Rechercher" }), search), target.el], target };
}

function associateDialog(i, suggestions) {
  const picker = memberPicker(suggestions);
  return formDialog({
    title: "Associer l'identité à un membre",
    intro: h("p", { class: "page-intro", text: `Le login ${i.login_name} sera rattaché au membre choisi. Les signaux d'ambiguïté ne sont jamais appliqués automatiquement.` }),
    fields: picker.els, submitLabel: "Associer",
    onSubmit: (form) => api(`/api/members/${readForm(form).member_id}/identities`, { method: "POST", body: { login_name: i.login_name } }),
  });
}

function reassignDialog(i) {
  const picker = memberPicker([], i.member.id);
  return formDialog({
    title: "Modifier l'association",
    intro: h("div", { class: "stack" },
      h("dl", { class: "dl" },
        h("dt", { text: "Identité" }), h("dd", { class: "mono", text: i.login_name }),
        h("dt", { text: "Membre actuel" }), h("dd", { text: i.member.full_name })),
      h("p", { class: "field__hint", text: "Le détachement et le rattachement sont effectués en une seule opération ; l'historique des deux associations est conservé et l'opération est journalisée." })),
    fields: picker.els, submitLabel: "Confirmer la réaffectation", danger: true,
    onSubmit: async (form) => {
      const targetId = readForm(form).member_id;
      const targetName = picker.target.input.selectedOptions[0]?.textContent.split(" — ")[0] || "membre choisi";
      const ok = await confirmDialog({
        title: "Confirmer la réaffectation ?",
        message: `${i.login_name} : ${i.member.full_name} → ${targetName}. Le verdict de l'identité sera recalculé par le serveur.`,
        confirmLabel: "Réaffecter",
        danger: true,
      });
      if (!ok) throw new ApiError(0, "Réaffectation annulée.");
      return api(`/api/members/${i.member.id}/identities/${i.client_identity_id}/reassign`,
        { method: "POST", body: { target_member_id: targetId } });
    },
  });
}
