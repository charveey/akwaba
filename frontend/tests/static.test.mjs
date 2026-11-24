/**
 * Contrôles statiques du frontend : compatibilité CSP stricte, aucune ressource
 * externe, aucun stockage du jeton, aucune dépendance, imports résolus.
 */
import assert from "node:assert/strict";
import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");

function walk(dir) {
  return readdirSync(dir).flatMap((name) => {
    const p = join(dir, name);
    return statSync(p).isDirectory() ? walk(p) : [p];
  });
}

const served = [join(ROOT, "index.html"), ...walk(join(ROOT, "assets"))];
const js = served.filter((p) => p.endsWith(".js"));
const css = served.filter((p) => p.endsWith(".css"));
const read = (p) => readFileSync(p, "utf8");
const stripComments = (src) => src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");

test("index.html : aucun script ni style inline, aucun gestionnaire on*", () => {
  const html = read(join(ROOT, "index.html"));
  assert.match(html, /<html lang="fr">/);
  for (const m of html.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script>/g)) {
    assert.match(m[1], /\bsrc="\/assets\/js\/app\.js"/);
    assert.match(m[1], /\btype="module"/);
    assert.equal(m[2].trim(), "", "script inline interdit");
  }
  assert.doesNotMatch(html, /<style\b/i);
  assert.doesNotMatch(html, /\sstyle=/i);
  assert.doesNotMatch(html, /\son[a-z]+=/i);
});

test("aucune ressource ni URL externe (seule exception : liens wa.me validés dans dom.js)", () => {
  for (const file of served) {
    let src = stripComments(read(file)).replaceAll("http://www.w3.org/2000/svg", "");
    if (file.endsWith(join("js", "dom.js"))) src = src.replaceAll('"https://wa.me/"', "");
    assert.doesNotMatch(src, /https?:\/\//, relative(ROOT, file));
    assert.doesNotMatch(src, /url\(\s*["']?\/\//, relative(ROOT, file));
  }
});

test("aucun puits HTML ni évaluation dynamique (Trusted Types / CSP sans unsafe-eval)", () => {
  const forbidden = [
    /\.innerHTML\b/, /\.outerHTML\b/, /insertAdjacentHTML/, /document\.write/, /\beval\s*\(/,
    /new\s+Function\s*\(/, /setTimeout\s*\(\s*["'`]/, /setInterval\s*\(\s*["'`]/, /\.style\.cssText/,
    /setAttribute\(\s*["']style["']/, /createContextualFragment/, /DOMParser/,
  ];
  for (const file of js) {
    const src = stripComments(read(file));
    for (const re of forbidden) assert.doesNotMatch(src, re, `${relative(ROOT, file)} : ${re}`);
  }
});

test("jeton de session jamais stocké côté navigateur", () => {
  for (const file of js) {
    const src = stripComments(read(file));
    assert.doesNotMatch(src, /sessionStorage/, relative(ROOT, file));
    assert.doesNotMatch(src, /indexedDB/, relative(ROOT, file));
    assert.doesNotMatch(src, /document\.cookie/, relative(ROOT, file));
    if (!file.endsWith(join("js", "theme.js"))) {
      assert.doesNotMatch(src, /localStorage/, `${relative(ROOT, file)} : seul theme.js peut utiliser localStorage`);
    }
  }
});

test("imports ES : relatifs, avec extension, et résolus", () => {
  for (const file of js) {
    const src = read(file);
    for (const m of src.matchAll(/(?:import|export)\s[^"'`]*?from\s+["']([^"']+)["']|import\(\s*["']([^"']+)["']\s*\)/g)) {
      const spec = m[1] || m[2];
      assert.match(spec, /^\.{1,2}\/.+\.js$/, `${relative(ROOT, file)} : import "${spec}"`);
      assert.ok(existsSync(resolve(dirname(file), spec)), `${relative(ROOT, file)} : "${spec}" introuvable`);
    }
  }
});

test("CSS : imports locaux uniquement", () => {
  for (const file of css) {
    for (const m of read(file).matchAll(/@import\s+url\(\s*["']?([^"')]+)/g)) {
      assert.match(m[1], /^\.\/[a-z-]+\.css$/);
      assert.ok(existsSync(resolve(dirname(file), m[1])));
    }
  }
});

test("aucun fichier de dépendances ni de build", () => {
  for (const name of ["package.json", "package-lock.json", "node_modules", "vite.config.js", "webpack.config.js"]) {
    assert.equal(existsSync(join(ROOT, name)), false, name);
  }
});

test("Nginx ne sert que les extensions autorisées dans /assets", () => {
  for (const file of walk(join(ROOT, "assets"))) {
    assert.match(file, /\.(css|js|svg|png|ico|woff2)$/, relative(ROOT, file));
  }
});

test("textes visibles en français : pas de libellés anglais courants", () => {
  const english = /["'`>](Loading|Login|Logout|Sign in|Submit|Cancel|Settings|Dashboard|Error|Retry|Not found|Close|Password)["'`<.…]/;
  for (const file of [join(ROOT, "index.html"), ...js]) {
    assert.doesNotMatch(stripComments(read(file)), english, relative(ROOT, file));
  }
});

test("les liens externes ne sont créés que par whatsAppLink (dom.js)", () => {
  for (const file of js) {
    if (file.endsWith(join("js", "dom.js"))) continue;
    const src = stripComments(read(file));
    assert.doesNotMatch(src, /setAttribute\(\s*["']href["']/, relative(ROOT, file));
    assert.doesNotMatch(src, /window\.open|location\.assign|location\.href\s*=/, relative(ROOT, file));
  }
});

test("le JavaScript ne calcule aucune échéance (le backend est la source de vérité)", () => {
  // Interdit : arithmétique de dates (setDate, setMonth, getDate()+n…) dans les pages et composants.
  for (const file of js) {
    const src = stripComments(read(file));
    assert.doesNotMatch(src, /\.set(UTC)?(Date|Month|FullYear)\s*\(/, relative(ROOT, file));
    assert.doesNotMatch(src, /\.get(UTC)?Date\(\)\s*[+-]/, relative(ROOT, file));
    assert.doesNotMatch(src, /86400000|864e5/, relative(ROOT, file));
  }
});
