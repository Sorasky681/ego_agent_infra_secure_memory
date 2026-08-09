import { copyFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const webRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
await copyFile(resolve(webRoot, "dist/index.html"), resolve(webRoot, "dist/404.html"));
