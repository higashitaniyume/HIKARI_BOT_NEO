/**
 * Extract DATA from music_genre index.html and output as JSON.
 * Uses Node.js vm module to evaluate the JS object literal.
 */
import { readFileSync, writeFileSync } from "fs";
import vm from "vm";

const htmlPath = process.argv[2];
if (!htmlPath) {
  console.error("Usage: node extract_music_genre_data.mjs <index.html> [output.json]");
  process.exit(1);
}

const html = readFileSync(htmlPath, "utf-8");

// Find "const DATA = {" and extract the balanced brace
const marker = "const DATA = {";
const startIdx = html.indexOf(marker);
if (startIdx === -1) {
  console.error("Could not find DATA declaration");
  process.exit(1);
}

let depth = 0;
let braceStart = -1;
let dataScript = "";

for (let i = startIdx + marker.length - 1; i < html.length; i++) {
  const ch = html[i];
  if (ch === "{") {
    if (depth === 0) braceStart = i;
    depth++;
  } else if (ch === "}") {
    depth--;
    if (depth === 0 && braceStart >= 0) {
      dataScript = html.slice(braceStart, i + 1);
      break;
    }
  }
}

if (!dataScript) {
  console.error("Could not find balanced closing brace for DATA");
  process.exit(1);
}

// The genres object references `allNodes` and `GENRES` at the end (post-processing)
// stub those
const sandbox = { allNodes: [], GENRES: {}, DATA: null };

const script = new vm.Script(`DATA = ${dataScript}`);
const ctx = vm.createContext(sandbox);
script.runInContext(ctx);

const data = ctx.DATA;

if (!data || !data.chapters || !data.genres) {
  console.error("Invalid DATA structure:", JSON.stringify(data).slice(0, 200));
  process.exit(1);
}

console.error(`Chapters: ${data.chapters.length}, Genres: ${Object.keys(data.genres).length}`);

const json = JSON.stringify(data, null, 2);

if (process.argv[3]) {
  writeFileSync(process.argv[3], json, "utf-8");
  console.error(`Written ${json.length} bytes to ${process.argv[3]}`);
} else {
  console.log(json);
}
