import { mkdir, readdir, readFile, writeFile } from "node:fs/promises";
import { extname, join, relative, sep } from "node:path";
import { fileURLToPath } from "node:url";

const distDirectory = fileURLToPath(new URL("../dist/", import.meta.url));
const serverDirectory = join(distDirectory, "server");
const embeddedAssets = {};

const contentTypes = {
  ".css": "text/css; charset=utf-8",
  ".gif": "image/gif",
  ".html": "text/html; charset=utf-8",
  ".ico": "image/x-icon",
  ".jpeg": "image/jpeg",
  ".jpg": "image/jpeg",
  ".js": "text/javascript; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".map": "application/json; charset=utf-8",
  ".png": "image/png",
  ".svg": "image/svg+xml",
  ".webp": "image/webp",
  ".woff": "font/woff",
  ".woff2": "font/woff2",
};

async function collectAssets(directory) {
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    if (
      directory === distDirectory &&
      (entry.name === "server" || entry.name === ".openai")
    ) {
      continue;
    }

    const absolutePath = join(directory, entry.name);
    if (entry.isDirectory()) {
      await collectAssets(absolutePath);
      continue;
    }

    const assetPath = `/${relative(distDirectory, absolutePath)
      .split(sep)
      .join("/")}`;
    const body = await readFile(absolutePath);
    embeddedAssets[assetPath] = [
      contentTypes[extname(entry.name).toLowerCase()] ??
        "application/octet-stream",
      body.toString("base64"),
    ];
  }
}

await collectAssets(distDirectory);

if (!embeddedAssets["/index.html"]) {
  throw new Error("The production build did not emit dist/index.html");
}

const worker = `const ASSETS = ${JSON.stringify(embeddedAssets)};

function decodeBase64(value) {
  const binary = atob(value);
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) {
    bytes[index] = binary.charCodeAt(index);
  }
  return bytes;
}

function resolveAssetPath(pathname) {
  if (ASSETS[pathname]) return pathname;
  const lastSegment = pathname.slice(pathname.lastIndexOf("/") + 1);
  if (!lastSegment.includes(".")) return "/index.html";
  return null;
}

export default {
  async fetch(request) {
    if (request.method !== "GET" && request.method !== "HEAD") {
      return new Response("Method Not Allowed", {
        status: 405,
        headers: { Allow: "GET, HEAD" },
      });
    }

    const url = new URL(request.url);
    const assetPath = resolveAssetPath(decodeURIComponent(url.pathname));
    if (!assetPath) return new Response("Not Found", { status: 404 });

    const [contentType, encodedBody] = ASSETS[assetPath];
    const headers = new Headers({
      "Content-Type": contentType,
      "X-Content-Type-Options": "nosniff",
      "Cache-Control": assetPath.startsWith("/assets/")
        ? "public, max-age=31536000, immutable"
        : "no-cache",
    });

    let body = null;
    if (request.method !== "HEAD") {
      const decodedBody = decodeBase64(encodedBody);
      body = assetPath === "/index.html"
        ? new TextDecoder().decode(decodedBody).replaceAll(
            "__SITE_ORIGIN__",
            url.origin,
          )
        : decodedBody;
    }

    return new Response(body, { status: 200, headers });
  },
};
`;

await mkdir(serverDirectory, { recursive: true });
await writeFile(join(serverDirectory, "index.js"), worker, "utf8");
