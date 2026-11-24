/** Identités observées sur l'Exit Node : verdict, membre, protection, signaux d'ambiguïté. */
import { api, ApiError, withQuery } from "../api.js";
import { badge } from "../components/badge.js";
import { dateTime } from "../components/format.js";
import { ambiguityBadges, options, PROTECTION_SOURCE, reason, statusBadge, VERDICT, QUARANTINE_STATUS } from "../components/labels.js";
import { pagination } from "../components/pagination.js";
import { dataTable } from "../components/table.js";
import { errorState, h, loadingState } from "../dom.js";
import { replaceQuery } from "../router.js";

const LIMIT = 25;
const TRI = [{ value: "", label: "Tous" }, { value: "true", label: "Oui" }, { value: "false", label: "Non" }];

export function render(container, { query }) {
  const controller = new AbortController();
  const signal = controller.signal;
  const state = {
    q: query.get("q") || "", verdict: query.get("verdict") || "", member: query.get("membre") || "",
    protected: query.get("protegee") || "", ambiguous: query.get("ambigue") || "", open: query.get("dossier") || "",
    active: query.get("active") || "",
    offset: Math.max(0, Number.parseInt(query.get("debut") || "0", 10) || 0),
  };
  let timer = null;
  const search = h("input", { class: "input", type: "search", placeholder: "Login Tailscale…", "aria-label": "Rechercher une identité", value: state.q, maxlength: "100" });
  search.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(() => { state.q = search.value.trim(); refresh(); }, 300); });
  const select = (label, opts, key) => {
    const el = h("select", { class: "input", "aria-label": label }, opts.map((o) => h("option", { value: o.value, selected: o.value === state[key] }, o.label)));
    el.addEventListener("change", () => { state[key] = el.value; refresh(); });
    return el;
  };
  const labelled = (text, el) => h("label", { class: "inline-field" }, h("span", { text }), el);

  const unresolved = h("div");
  const counts = h("div", { class: "chips section-gap", "aria-live": "polite" });
  const results = h("div", { class: "section-gap" });
  container.replaceChildren(
    unresolved,
    h("div", { class: "toolbar" }, h("div", { class: "toolbar__filters" },
      search,
      select("Filtrer par verdict", options(VERDICT, "Tous les verdicts"), "verdict"),
      labelled("Membre", select("Filtrer : avec un membre", TRI, "member")),
      labelled("Protégée", select("Filtrer : protégée", TRI, "protected")),
      labelled("Ambiguë", select("Filtrer : signal d'ambiguïté", TRI, "ambiguous")),
      labelled("Dossier ouvert", select("Filtrer : dossier ouvert", TRI, "open")),
      labelled("Active", select("Filtrer : active maintenant", TRI, "active")))),
    counts, results);

  function refresh(reset = true) {
    if (reset) state.offset = 0;
    replaceQuery("/identites", {
      q: state.q, verdict: state.verdict, membre: state.member, protegee: state.protected,
      ambigue: state.ambiguous, dossier: state.open, active: state.active, debut: state.offset || "",
    });
    load();
  }

  async function loadUnresolved() {
    try {
      const page = await api(withQuery("/api/identities/unresolved", { hours: 24 }), { signal });
      if (!page.items.length) return unresolved.replaceChildren();
      const details = h("details", {},
        h("summary", { text: `${page.items.length} source(s) non résolue(s) ces ${page.hours} dernières heures — voir le détail` }),
        dataTable({ rows: page.items, columns: [
          { label: "IP Tailscale", className: "mono", render: (s) => s.tailscale_ip },
          { label: "Observations", render: (s) => String(s.observations) },
          { label: "Première", render: (s) => dateTime(s.first_observed_at) },
          { label: "Dernière", render: (s) => dateTime(s.last_observed_at) },
        ] }));
      unresolved.replaceChildren(h("div", { class: "alert alert--info", role: "note" },
        h("p", { text: "Identités non résolues (UNRESOLVED) : le backend n'a pas pu identifier ces sources. "
          + "Aucune identité n'est supposée et aucune action n'est jamais déclenchée pour elles." }),
        details));
    } catch {
      unresolved.replaceChildren();
    }
  }

  let seq = 0;
  async function load() {
    const mine = ++seq;
    results.replaceChildren(loadingState());
    try {
      const page = await api(withQuery("/api/identities", {
        q: state.q, verdict: state.verdict, has_member: state.member, protected: state.protected,
        ambiguous: state.ambiguous, open_record: state.open, active_now: state.active, limit: LIMIT, offset: state.offset,
      }), { signal });
      if (mine !== seq) return;
      counts.replaceChildren(...Object.keys(VERDICT).filter((v) => page.counts[v]).map((v) =>
        h("span", { class: "chip" }, statusBadge(VERDICT, v), h("strong", { text: ` ${page.counts[v]}` }))));
      results.replaceChildren(
        dataTable({
          rows: page.items, caption: "Identités observées", empty: "Aucune identité ne correspond à ces critères.",
          columns: [
            { label: "Login Tailscale", render: (i) => h("a", { href: `#/identites/${i.id}`, class: "mono", text: i.login_name }) },
            { label: "Nom d'affichage", render: (i) => i.display_name },
            { label: "Appareils", render: (i) => i.devices.map((d) => d.hostname || d.stable_id).join(", ") },
            { label: "Verdict", render: (i) => h("span", { class: "stacked" }, statusBadge(VERDICT, i.verdict), h("small", { text: reason(i.reason) })) },
            { label: "Membre", render: (i) => (i.member ? h("a", { href: `#/membres/${i.member.id}`, text: i.member.full_name }) : "—") },
            { label: "Dernière activité", render: (i) => dateTime(i.last_confirmed_activity_at) },
            { label: "Protection", render: (i) => h("span", { class: "badges" },
              i.protection_sources.map((s) => statusBadge(PROTECTION_SOURCE, s)), i.exempt ? badge("Exemptée", "info") : null) },
            { label: "Dossier", render: (i) => (i.open_record
              ? h("a", { href: `#/quarantaine/${i.open_record.id}` }, statusBadge(QUARANTINE_STATUS, i.open_record.status)) : "—") },
            { label: "Signaux", render: (i) => h("span", { class: "badges" }, ambiguityBadges(i.ambiguity)) },
          ],
        }),
        pagination({ total: page.total, limit: LIMIT, offset: state.offset, onChange: (o) => { state.offset = o; refresh(false); } }));
    } catch (err) {
      if (err.name === "AbortError" || mine !== seq) return;
      results.replaceChildren(errorState("Impossible de charger les identités.", err instanceof ApiError ? err.message : String(err), load));
    }
  }

  loadUnresolved();
  load();
  return () => { controller.abort(); clearTimeout(timer); };
}
