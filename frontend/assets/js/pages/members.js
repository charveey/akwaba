/** Membres : recherche, filtres, tri et pagination côté serveur ; création. */
import { api, ApiError, withQuery } from "../api.js";
import { badge } from "../components/badge.js";
import { loadPlans, memberDialog } from "../components/billing-dialogs.js";
import { country, day, localDay } from "../components/format.js";
import { ACCESS, kindBadges, options, statusBadge } from "../components/labels.js";
import { pagination } from "../components/pagination.js";
import { dataTable } from "../components/table.js";
import { toast } from "../components/toast.js";
import { errorState, h, loadingState } from "../dom.js";
import { navigate, replaceQuery } from "../router.js";

const LIMIT = 25;

export function render(container, { query }) {
  const state = {
    q: query.get("q") || "",
    access: query.get("acces") || "",
    plan: query.get("formule") || "",
    archived: query.get("archives") === "1",
    sort: query.get("tri") || "name",
    offset: Math.max(0, Number.parseInt(query.get("debut") || "0", 10) || 0),
  };
  const controller = new AbortController();
  let timer = null;
  let seq = 0;

  const search = h("input", { class: "input", type: "search", placeholder: "Nom, e-mail, WhatsApp, login Tailscale…", "aria-label": "Rechercher un membre", value: state.q, maxlength: "100" });
  const access = h("select", { class: "input", "aria-label": "Filtrer par état d'accès" },
    options(ACCESS, "Tous les accès").map((o) => h("option", { value: o.value, selected: o.value === state.access }, o.label)));
  const plan = h("select", { class: "input", "aria-label": "Filtrer par formule" }, h("option", { value: "", text: "Toutes les formules" }));
  const archived = h("input", { type: "checkbox", checked: state.archived });
  const results = h("div", { class: "section-gap" });

  loadPlans().then((plans) => {
    for (const p of plans) plan.append(h("option", { value: p.code, selected: p.code === state.plan }, p.name));
  }).catch(() => {});

  const refresh = (resetOffset = true) => {
    if (resetOffset) state.offset = 0;
    replaceQuery("/membres", {
      q: state.q, acces: state.access, formule: state.plan, archives: state.archived ? "1" : "",
      tri: state.sort !== "name" ? state.sort : "", debut: state.offset || "",
    });
    load();
  };
  search.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(() => { state.q = search.value.trim(); refresh(); }, 300);
  });
  access.addEventListener("change", () => { state.access = access.value; refresh(); });
  plan.addEventListener("change", () => { state.plan = plan.value; refresh(); });
  archived.addEventListener("change", () => { state.archived = archived.checked; refresh(); });

  const create = h("button", { class: "btn btn--primary", type: "button", text: "Nouveau membre", onClick: async () => {
    const created = await memberDialog();
    if (created && created.id) {
      toast("Membre créé.", "success");
      navigate(`#/membres/${created.id}`);
    }
  } });

  container.replaceChildren(
    h("div", { class: "toolbar" },
      h("div", { class: "toolbar__filters" }, search, access, plan,
        h("label", { class: "choice" }, archived, h("span", { text: "Inclure les archivés" }))),
      create),
    results);

  async function load() {
    const mine = ++seq;
    results.replaceChildren(loadingState());
    try {
      const page = await api(withQuery("/api/members", {
        q: state.q, access_status: state.access, plan_code: state.plan,
        include_archived: state.archived ? "true" : "", sort: state.sort, limit: LIMIT, offset: state.offset,
      }), { signal: controller.signal });
      if (mine !== seq) return;
      results.replaceChildren(
        dataTable({
          rows: page.items, sort: state.sort, caption: "Liste des membres",
          empty: state.q || state.access || state.plan ? "Aucun membre ne correspond à ces critères." : "Aucun membre pour le moment.",
          onSort: (s) => { state.sort = s; refresh(false); },
          columns: [
            { label: "Nom", sortKey: "name", render: (m) => h("span", {},
              h("a", { href: `#/membres/${m.id}`, text: m.full_name }),
              m.archived_at ? h("span", { class: "cell-badge" }, badge("Archivé", "neutral")) : null) },
            { label: "Pays", render: (m) => (m.country_code ? country(m.country_code) : "—") },
            { label: "WhatsApp", className: "mono", render: (m) => m.phone_e164 },
            { label: "E-mail Tailscale", render: (m) => m.identities.filter((i) => i.is_active).map((i) => i.login_name).join(", ") },
            { label: "Formule", render: (m) => (m.current ? m.current.plan_name : "—") },
            { label: "Accès", render: (m) => statusBadge(ACCESS, m.current ? m.current.access_status : "NONE") },
            { label: "Dernière échéance", sortKey: "expiry", render: (m) => (!m.current ? "—" : m.current.is_unlimited ? "Illimité" : day(m.current.display_end)) },
            { label: "Paiement", render: (m) => (m.current ? h("span", { class: "badges" }, kindBadges(m.current.payment_kind, m.current.plan_changed)) : "—") },
            { label: "Inscrit le", sortKey: "created", descFirst: true, render: (m) => localDay(m.created_at) },
          ],
        }),
        pagination({ total: page.total, limit: LIMIT, offset: state.offset, onChange: (o) => { state.offset = o; refresh(false); } }));
    } catch (err) {
      if (err.name === "AbortError" || mine !== seq) return;
      results.replaceChildren(errorState("Impossible de charger les membres.", err instanceof ApiError ? err.message : String(err), load));
    }
  }

  load();
  return () => {
    controller.abort();
    clearTimeout(timer);
  };
}
