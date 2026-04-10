import { connect } from "framer-api"
import fs from "node:fs"
import path from "node:path"

loadDotEnv(path.join(process.cwd(), ".env"))

const projectUrl = mustGetEnv("FRAMER_PROJECT_URL")
const apiKey = mustGetEnv("FRAMER_API_KEY")
const mode = process.argv[2] || "status"

if (!["status", "preview", "live"].includes(mode)) {
  console.error("Usage: node framer/publishSite.mjs [status|preview|live]")
  process.exit(1)
}

const framer = await connect(projectUrl, apiKey)
try {
  const changes = await framer.getChangedPaths()
  if (mode === "status") {
    console.log(JSON.stringify({ ok: true, mode, changes }, null, 2))
    process.exit(0)
  }
  const publishResult = await framer.publish()
  if (mode === "preview") {
    console.log(JSON.stringify({ ok: true, mode, changes, publishResult }, null, 2))
    process.exit(0)
  }
  const deploymentId = publishResult?.deployment?.id
  if (!deploymentId) throw new Error("Framer publish() did not return a deployment id")
  const deployResult = await framer.deploy(deploymentId)
  console.log(JSON.stringify({ ok: true, mode, changes, publishResult, deployResult }, null, 2))
} finally {
  await framer.disconnect()
}

function mustGetEnv(name) {
  const value = process.env[name]
  if (!value) throw new Error(`Missing environment variable: ${name}`)
  return value
}

function loadDotEnv(filePath) {
  if (!fs.existsSync(filePath)) return
  for (const line of fs.readFileSync(filePath, "utf8").split(/\r?\n/)) {
    const trimmed = line.trim()
    if (!trimmed || trimmed.startsWith("#") || !trimmed.includes("=")) continue
    const index = trimmed.indexOf("=")
    const key = trimmed.slice(0, index).trim()
    const value = trimmed.slice(index + 1).trim()
    if (!(key in process.env)) process.env[key] = value
  }
}
