import { badge } from "../components/badge.js";
import { h } from "../dom.js";

const DESCRIPTIONS = {
  "/membres": "Liste, recherche et fiche des membres, rattachement des identités Tailscale.",
  "/abonnements": "Souscriptions, renouvellements et résiliations, avec historique chaîné.",
  "/paiements": "Enregistrement et annulation des paiements, relances WhatsApp.",
  "/identites": "Identités observées sur l'Exit Node et leur rattachement aux membres.",
  "/quarantaine": "Dossiers de quarantaine et décisions simulées (mode observation).",
  "/exemptions": "Exemptions d'identité actives et historiques.",
  "/depenses": "Saisie et suivi des dépenses par catégorie.",
  "/rapports": "Revenus, dépenses et résultat par période et par formule.",
  "/audit": "Journal d'audit des actions d'administration.",
  "/agent": "État de l'agent de l'Exit Node : battements de cœur et observations.",
  "/vue-ensemble": "Indicateurs clés : membres actifs, échéances, revenus et activité.",
};

/** Section dont l'interface sera livrée par un lot ultérieur. */
export function render(container, { route }) {
  container.replaceChildren(
    h("section", { class: "card placeholder", "aria-labelledby": "placeholder-title" },
      h("h2", { id: "placeholder-title", text: route.title }),
      h("p", { class: "page-intro", text: DESCRIPTIONS[route.path] || "" }),
      h("p", { text: "Cette section est en cours de développement." }),
      h("p", { class: "placeholder__lot" }, badge(`Prévue au lot ${route.lot}`, "info"))));
}
