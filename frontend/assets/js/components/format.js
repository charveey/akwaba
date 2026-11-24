/**
 * Formatage pour l'affichage UNIQUEMENT (aucun calcul métier) : montants, dates, pays.
 * Les dates « jour » (AAAA-MM-JJ) viennent du backend et sont affichées sans conversion de fuseau.
 */

const currencyFormats = new Map();

function currencyFormat(currency) {
  if (!currencyFormats.has(currency)) {
    currencyFormats.set(currency, new Intl.NumberFormat("fr-FR", { style: "currency", currency }));
  }
  return currencyFormats.get(currency);
}

/** Montant en unités mineures (centimes pour EUR, francs pour XOF) → "4,99 €", "3 274 F CFA". */
export function money(minor, currency) {
  if (minor === null || minor === undefined || !currency) return "—";
  try {
    const f = currencyFormat(currency);
    const digits = f.resolvedOptions().maximumFractionDigits;
    return f.format(minor / 10 ** digits);
  } catch {
    return `${minor} ${currency}`;
  }
}

/** "2026-02-16" → "16/02/2026" (aucune conversion de fuseau). */
export function day(value) {
  const m = typeof value === "string" ? /^(\d{4})-(\d{2})-(\d{2})/.exec(value) : null;
  return m ? `${m[3]}/${m[2]}/${m[1]}` : "—";
}

/** Horodatage ISO → date et heure locales du navigateur. */
export function dateTime(value) {
  if (!value) return "—";
  const d = new Date(value);
  return Number.isNaN(d.getTime())
    ? "—"
    : d.toLocaleString("fr-FR", { dateStyle: "short", timeStyle: "short" });
}

let regionNames = null;

export function country(code) {
  if (!code) return "—";
  try {
    regionNames ||= new Intl.DisplayNames(["fr"], { type: "region" });
    return regionNames.of(code) || code;
  } catch {
    return code;
  }
}

export function duration(months, unlimited) {
  if (unlimited) return "Illimitée";
  return months ? `${months} mois` : "—";
}

/** Valeur d'un champ datetime-local (heure locale) → ISO 8601 avec fuseau, ou null. */
export function localInputToIso(value) {
  if (!value) return null;
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? null : d.toISOString();
}

/** Horodatage ISO → date locale du navigateur (jj/mm/aaaa). */
export function localDay(value) {
  if (!value) return "—";
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? "—" : d.toLocaleDateString("fr-FR");
}

/** Durée en secondes (fournie par le serveur) → "23 h 05 min". */
export function remaining(seconds) {
  if (seconds === null || seconds === undefined) return "—";
  if (seconds <= 0) return "Délai écoulé";
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  return h ? `${h} h ${String(m).padStart(2, "0")} min` : `${m} min`;
}
