"use strict";
/*
  Photo Organizer - built-in duplicate finder API (service worker)

  This file runs inside the browser and answers  POST api/duplicates  so the page can use an
  API without any server (it works on GitHub Pages). Upload it next to index.html.

  It follows the same rules, and returns the same JSON, as app.py:
    1. group the files by size
    2. hash (SHA-256) only the sizes that appear more than once
    3. files with the same hash are duplicates
    4. in every group the first file is the one to KEEP
*/

const IMAGE_EXTENSIONS = new Set([".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp", ".bmp"]);

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (event.request.method === "POST" && url.pathname.endsWith("/api/duplicates")) {
    event.respondWith(duplicates(event.request));
  }
});

const json = (body, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

const extOf = (name) => { const i = name.lastIndexOf("."); return i < 0 ? "" : name.slice(i).toLowerCase(); };

async function sha256(file) {
  const digest = await crypto.subtle.digest("SHA-256", await file.arrayBuffer());
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

// 'photo - Copy.jpg', 'Copy of photo.jpg', 'photo (1).jpg', 'photo_1.jpg' look like copies.
function looksLikeCopy(name) {
  const base = name.replace(/\.[^.]+$/, "");
  return /(^copy of |[ _-]copy( ?\(\d+\))?$| \(\d+\)$|_\d+$)/i.test(base) ? 1 : 0;
}

// Which file to keep: not a copy-looking name, then shorter name, then older file, then path.
function keeperOrder(a, b) {
  return looksLikeCopy(a.name) - looksLikeCopy(b.name)
    || a.name.length - b.name.length
    || a.last_modified - b.last_modified
    || (a.path < b.path ? -1 : a.path > b.path ? 1 : 0);
}

async function duplicates(request) {
  let form, manifest;
  try {
    form = await request.formData();
    manifest = JSON.parse(form.get("manifest") || "[]");
  } catch (_) {
    return json({ error: "The request could not be read." }, 400);
  }
  const uploads = form.getAll("files");
  if (!Array.isArray(manifest) || manifest.length !== uploads.length) {
    return json({ error: "The manifest and the files do not match." }, 400);
  }

  // 1. group by size
  const bySize = new Map();
  let total = 0;
  uploads.forEach((file, i) => {
    const info = manifest[i] || {};
    const path = String(info.path || file.name);
    const name = path.split("/").pop();
    if (!IMAGE_EXTENSIONS.has(extOf(name))) return;
    const item = { path, name, size: file.size, last_modified: Number(info.lastModified) || 0, file };
    if (!bySize.has(item.size)) bySize.set(item.size, []);
    bySize.get(item.size).push(item);
    total++;
  });

  // 2. hash only the sizes that appear more than once
  const byHash = new Map();
  for (const items of bySize.values()) {
    if (items.length < 2) continue;
    for (const item of items) {
      const h = await sha256(item.file);
      if (!byHash.has(h)) byHash.set(h, []);
      byHash.get(h).push(item);
    }
  }

  // 3. build the groups
  const groups = [];
  for (const [digest, items] of byHash) {
    if (items.length < 2) continue;
    items.sort(keeperOrder);
    groups.push({
      hash: digest.slice(0, 12),
      size: items[0].size,
      files: items.map((it, i) => ({
        path: it.path, name: it.name, size: it.size, last_modified: it.last_modified, keep: i === 0,
      })),
    });
  }
  groups.sort((a, b) => (a.files[0].path < b.files[0].path ? -1 : a.files[0].path > b.files[0].path ? 1 : 0));

  const extras = groups.reduce((n, g) => n + g.files.length - 1, 0);
  const wasted = groups.reduce((n, g) => n + g.size * (g.files.length - 1), 0);
  return json({ total_files: total, groups, extra_copies: extras, wasted_bytes: wasted });
}