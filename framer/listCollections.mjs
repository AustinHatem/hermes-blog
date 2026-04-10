import { connect } from "framer-api"
import fs from "node:fs"
import path from "node:path"

loadDotEnv(path.join(process.cwd(), ".env"))

const projectUrl = process.env.FRAMER_PROJECT_URL
const apiKey = process.env.FRAMER_API_KEY

if (!projectUrl || !apiKey) {
  console.error("Missing FRAMER_PROJECT_URL or FRAMER_API_KEY. Put them in .env or export them.")
  process.exit(1)
}

const framer = await connect(projectUrl, apiKey)
try {
  const projectInfo = await framer.getProjectInfo()
  const collections = await framer.getCollections()
  const output = []
  for (const collection of collections) {
    const fields = await collection.getFields()
    output.push({
      id: collection.id,
      name: collection.name,
      slugFieldName: collection.slugFieldName,
      slugFieldBasedOn: collection.slugFieldBasedOn,
      managedBy: collection.managedBy,
      fields: fields.map((field) => ({ id: field.id, name: field.name, type: field.type })),
    })
  }
  console.log(JSON.stringify({ project: { name: projectInfo.name, id: projectInfo.id }, collections: output }, null, 2))
} finally {
  await framer.disconnect()
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
