/** Diagnostic de l'agent / des Exit Nodes (lecture seule, aucune commande). */
import { api, ApiError } from "../api.js";
import { badge } from "../components/badge.js";
import { columnChart } from "../components/charts.js";
import { dateTime } from "../components/format.js";
import { dataTable } from "../components/table.js";
import { emptyState, errorState, h, loadingState } from "../dom.js";

const yes = (v, ok = "OK", ko = "En échec") => (v === null || v === undefined ? badge("Inconnu", "neutral") : v ? badge(ok, "success") : badge(ko, "danger"));
const pct = (v) => (v === null || v === undefined ? "—" : `${Math.round(v * 100)} %`);
const secs = (v) => (v === null || v === undefined ? "—" : `${v > 0 ? "+" : ""}${Math.round(v * 10) / 10} s`);

export function render(container) {
  const controller = new AbortController();
  let alive = true;

  async function load() {
    container.replaceChildren(loadingState("Lecture du diagnostic…"));
    try {
      const page = await api("/api/exit-nodes", { signal: controller.signal });
      const details = await Promise.all(page.items.map((n) => api(`/api/exit-nodes/${n.id}`, { signal: controller.signal })));
      if (!alive) return;
      container.replaceChildren(
        h("div", { class: "alert alert--info", role: "note",
          text: `Diagnostic en lecture seule : aucune commande n'est envoyée à l'agent. « Sain » suit la règle du réconciliateur `
            + `(battement de cœur de moins de ${Math.round(page.health_max_age_seconds / 60)} min, conntrack et whois en état). Les secrets et identifiants de clé ne sont jamais affichés.` }),
        page.items.length ? h("div", { class: "stack" }, details.map(nodeView)) : emptyState("Aucun Exit Node enregistré."),
        h("p", { class: "field__hint", text: `Lu le ${dateTime(page.evaluated_at)}.` }));
    } catch (err) {
      if (!alive || err.name === "AbortError") return;
      container.replaceChildren(errorState("Impossible de lire le diagnostic.", err instanceof ApiError ? err.message : String(err), load));
    }
  }

  function nodeView(d) {
    const n = d.node;
    const hb = n.last_heartbeat;
    const history = [...d.heartbeats].reverse();
    return h("section", { class: "card", "aria-labelledby": `node-${n.id}` },
      h("div", { class: "card__header" },
        h("h2", { class: "card__title", id: `node-${n.id}`, text: `${n.name} — ${n.tailscale_ip}` }),
        h("span", { class: "badges" }, n.healthy ? badge("Sain", "success") : badge("Non sain", "danger"), n.is_active ? null : badge("Inactif", "neutral"))),
      hb ? h("dl", { class: "dl" },
        h("dt", { text: "Dernier heartbeat" }), h("dd", { text: dateTime(hb.received_at) }),
        h("dt", { text: "conntrack_ok" }), h("dd", {}, yes(hb.conntrack_ok)),
        h("dt", { text: "whois_ok" }), h("dd", {}, yes(hb.whois_ok)),
        h("dt", { text: "status_ok" }), h("dd", {}, yes(hb.status_ok)),
        h("dt", { text: "Saturation conntrack" }), h("dd", {}, `${pct(hb.conntrack_saturation)} (${hb.conntrack_count ?? "—"} / ${hb.conntrack_max ?? "—"}) `, hb.conntrack_saturated ? badge("Saturé", "danger") : null),
        h("dt", { text: "Spool (file locale)" }), h("dd", { text: `${hb.spool_depth} lot(s) en attente${hb.spool_oldest_age_seconds ? `, plus ancien : ${Math.round(hb.spool_oldest_age_seconds)} s` : ""}` }),
        h("dt", { text: "Décalage d'horloge (agent)" }), h("dd", { text: secs(hb.clock_skew_seconds) }),
        h("dt", { text: "Décalage émission → réception (serveur)" }), h("dd", { text: secs(hb.server_offset_seconds) }),
        h("dt", { text: "Backend joignable" }), h("dd", {}, yes(hb.backend_reachable, "Oui", "Non")),
        h("dt", { text: "Envois échoués / rejetés / perdus" }), h("dd", { text: `${hb.send_failures ?? "—"} / ${hb.rejected_batches ?? "—"} / ${hb.dropped_batches ?? "—"}` }),
        h("dt", { text: "Versions" }), h("dd", { text: `agent ${hb.agent_version}${hb.tailscaled_version ? ` · tailscaled ${hb.tailscaled_version}` : ""}` }),
        h("dt", { text: "Dernière erreur" }), h("dd", { text: n.last_error ? `${n.last_error.error || n.last_error.send_error} (${dateTime(n.last_error.at)})` : "Aucune" }),
        h("dt", { text: "Identifiants" }), h("dd", { text: `${n.credentials.active} actif(s), ${n.credentials.revoked} révoqué(s)${n.credentials.last_used_at ? ` · dernière utilisation ${dateTime(n.credentials.last_used_at)}` : ""}` }))
        : emptyState("Aucun battement de cœur reçu."),
      history.length ? h("div", { class: "section-gap" }, columnChart({
        title: "Dernières 24 h : file locale (spool) et saturation", labels: history.map((x) => dateTime(x.received_at).split(" ")[1] || dateTime(x.received_at)),
        bars: [{ name: "Lots en attente (spool)", values: history.map((x) => x.spool_depth) }],
        line: { name: "Saturation conntrack (%)", values: history.map((x) => Math.round((x.conntrack_saturation || 0) * 100)), className: "is-line" },
      })) : null,
      h("h3", { text: "Derniers lots reçus" }),
      dataTable({ rows: d.batches, empty: "Aucun lot reçu.", columns: [
        { label: "Reçu le", render: (b) => dateTime(b.received_at) },
        { label: "Fenêtre", render: (b) => `${dateTime(b.window_start)} → ${dateTime(b.window_end)}` },
        { label: "Échantillons", render: (b) => String(b.sample_count) },
        { label: "Observations", render: (b) => String(b.observations) },
      ] }),
      h("h3", { text: "Heartbeats (24 h)" }),
      dataTable({ rows: d.heartbeats.slice(0, 50), empty: "Aucun heartbeat sur 24 h.", columns: [
        { label: "Reçu le", render: (x) => dateTime(x.received_at) },
        { label: "conntrack", render: (x) => yes(x.conntrack_ok) },
        { label: "whois", render: (x) => yes(x.whois_ok) },
        { label: "status", render: (x) => yes(x.status_ok) },
        { label: "Saturation", render: (x) => pct(x.conntrack_saturation) },
        { label: "Spool", render: (x) => String(x.spool_depth) },
        { label: "Décalage", render: (x) => secs(x.clock_skew_seconds) },
        { label: "Erreur", render: (x) => x.last_error || x.last_send_error },
      ] }));
  }

  load();
  return () => { alive = false; controller.abort(); };
}
