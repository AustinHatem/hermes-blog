import { spawn } from "node:child_process"
import path from "node:path"
import { fileURLToPath } from "node:url"

const postPath = process.argv[2]
if (!postPath || process.argv.includes("--help") || process.argv.includes("-h")) {
  console.log(`Add/update a Framer blog post, then publish it live.\n\nUsage:\n  node framer/addAndPublishBlogPost.mjs ./post.json\n`)
  process.exit(postPath ? 0 : 1)
}

const here = path.dirname(fileURLToPath(import.meta.url))
await run("npx", ["-y", "node@22", path.join(here, "addBlogPost.mjs"), postPath])
await run("npx", ["-y", "node@22", path.join(here, "publishSite.mjs"), "live"])

async function run(command, args) {
  await new Promise((resolve, reject) => {
    const child = spawn(command, args, { cwd: here, stdio: "inherit", env: process.env })
    child.on("error", reject)
    child.on("exit", (code) => code === 0 ? resolve() : reject(new Error(`${command} exited with code ${code}`)))
  })
}
