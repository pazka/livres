// Visual use cases of livres (hosh-management AGENTS.md section 3), listed in TESTING.md "Visual use cases".
// Every case is shot at the 3 widths. The pages are server-rendered with no JavaScript of their own, so a
// case is a URL plus, at most, a form the harness fills and submits like a person would.
// Data: visual/seed.py (fixed books in the harvested index, live search off, one book request), the
// admin password of the harness container only (run.sh), dates in tables replaced by a fixed one.
import { setTimeout as sleep } from "node:timers/promises";

export const WIDTHS = [1400, 1024, 390];
export const ADMIN_PASSWORD = "visual-harness-only";

/** Fills the fields of the page's first form matching `selector`, submits it, waits for the next page. */
async function submit(p, selector, fields) {
  await p.evaluate(`(() => {
    const f = document.querySelector(${JSON.stringify(selector)});
    for (const [name, value] of Object.entries(${JSON.stringify(fields)})) f.elements[name].value = value;
    window.__before = true;
    f.requestSubmit();
    return true;
  })()`);
  await sleep(100);
  if (!(await p.waitFor("!window.__before && document.readyState === 'complete'", 15000))) {
    throw new Error(`submitting ${selector} did not load a new page`);
  }
}

async function login(p, base, then) {
  await p.goto(base + "/admin/login");
  await submit(p, 'form[action="/admin/login"]', { password: ADMIN_PASSWORD });
  if (then) await p.goto(base + then);
  if (!(await p.evaluate("!!document.querySelector('nav a[href=\"/admin\"]')"))) throw new Error("not logged in");
}

export const CASES = [
  // public page (Ma liseuse's owner and anyone who opens livres.hosh.it)
  { id: "H1-home", path: "/" },
  { id: "H2-search-results", path: "/recherche?q=Victor+Hugo" },
  { id: "H3-search-page-2", path: "/recherche?q=Victor+Hugo&page=2" },
  { id: "H4-search-nothing", path: "/recherche?q=Zzyzx+introuvable" },
  { id: "H5-search-too-short", path: "/recherche?q=a" },
  {
    id: "H6-request-sent", path: "/",
    steps: (p) => submit(p, 'form[action="/demande"]', { title: "Les Trois Mousquetaires", author: "Alexandre Dumas" }),
  },
  { id: "I1-robots-info", path: "/robots-info" },
  { id: "N1-not-found", path: "/nope" },
  // owner's admin
  { id: "A1-login", path: "/admin/login" },
  {
    id: "A2-login-wrong", path: "/admin/login",
    steps: (p) => submit(p, 'form[action="/admin/login"]', { password: "wrong" }),
  },
  { id: "A3-admin-sources", path: "/admin/login", steps: (p, base) => login(p, base, "/admin") },
  { id: "A4-admin-sources-flash", path: "/admin/login", steps: (p, base) => login(p, base, "/admin?msg=Source%20d%C3%A9sactiv%C3%A9e.") },
  { id: "A5-admin-requests", path: "/admin/login", steps: (p, base) => login(p, base, "/admin/requests") },
  { id: "A6-admin-events", path: "/admin/login", steps: (p, base) => login(p, base, "/admin/events") },
];
