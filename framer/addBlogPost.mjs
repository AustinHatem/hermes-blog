import { connect } from "framer-api"
import fs from "node:fs"
import path from "node:path"

loadDotEnv(path.join(process.cwd(), ".env"))

if (process.argv.includes("--help") || process.argv.includes("-h")) {
  printHelp()
  process.exit(0)
}

const postPath = process.argv[2]
if (!postPath) {
  console.error("Usage: node framer/addBlogPost.mjs ./post.json")
  process.exit(1)
}

const projectUrl = mustGetEnv("FRAMER_PROJECT_URL")
const apiKey = mustGetEnv("FRAMER_API_KEY")
const collectionName = mustGetEnv("FRAMER_COLLECTION_NAME")

const post = JSON.parse(fs.readFileSync(postPath, "utf8"))
validatePost(post)

const framer = await connect(projectUrl, apiKey)
try {
  const collection = await findCollection(framer, collectionName)
  const fields = await collection.getFields()
  const fieldLookup = new Map(fields.map((field) => [normalize(field.name), field]))
  const existingItems = await collection.getItems()
  const existing = existingItems.find((item) => item.slug === post.slug)
  const fieldData = {}

  setField(fieldData, fieldLookup, envOrDefault("FRAMER_FIELD_TITLE", "Title"), { type: "string", value: post.title })
  setField(fieldData, fieldLookup, envOrDefault("FRAMER_FIELD_CONTENT", "Content"), { type: "formattedText", value: post.content, contentType: post.contentType || "html" })
  setOptionalField(fieldData, fieldLookup, envOrDefault("FRAMER_FIELD_SUBTITLE", "Subtitle"), post.subtitle, (value) => ({ type: "string", value }))
  setOptionalField(fieldData, fieldLookup, envOrDefault("FRAMER_FIELD_DATE", "Date"), post.date, (value) => ({ type: "date", value }))
  setOptionalField(fieldData, fieldLookup, envOrDefault("FRAMER_FIELD_AUTHOR_NAME", "Author | Name"), post.authorName ?? post.author, (value) => ({ type: "string", value }))
  setOptionalField(fieldData, fieldLookup, envOrDefault("FRAMER_FIELD_AUTHOR_POSITION", "Author | Position"), post.authorPosition, (value) => ({ type: "string", value }))
  setOptionalField(fieldData, fieldLookup, envOrDefault("FRAMER_FIELD_IMAGE", "Image"), post.image ?? post.coverImage, (value) => ({ type: "image", value, alt: post.imageAlt || post.coverImageAlt || post.title }))
  setOptionalField(fieldData, fieldLookup, envOrDefault("FRAMER_FIELD_AUTHOR_AVATAR", "Author | Avatar"), post.authorAvatar, (value) => ({ type: "image", value, alt: post.authorName || post.author || "Author avatar" }))

  await collection.addItems([{ ...(existing ? { id: existing.id } : {}), slug: post.slug, draft: post.draft === true, fieldData }])
  console.log(JSON.stringify({ ok: true, action: existing ? "updated" : "created", collection: collection.name, slug: post.slug, mappedFieldNames: Object.values(fieldData).length }, null, 2))
} finally {
  await framer.disconnect()
}

async function findCollection(framer, targetName) {
  const collections = await framer.getCollections()
  const collection = collections.find((item) => normalize(item.name) === normalize(targetName))
  if (!collection) throw new Error(`Collection not found: ${targetName}`)
  return collection
}

function setField(fieldData, lookup, fieldName, payload) {
  const field = lookup.get(normalize(fieldName))
  if (!field) throw new Error(`Required field not found in Framer collection: ${fieldName}`)
  fieldData[field.id] = payload
}

function setOptionalField(fieldData, lookup, fieldName, value, buildPayload) {
  if (value === undefined || value === null || value === "") return
  const field = lookup.get(normalize(fieldName))
  if (!field) return
  fieldData[field.id] = buildPayload(value)
}

function validatePost(post) {
  if (!post || typeof post !== "object") throw new Error("Post JSON must be an object")
  if (!post.title) throw new Error("Post JSON is missing title")
  if (!post.slug) throw new Error("Post JSON is missing slug")
  if (!post.content) throw new Error("Post JSON is missing content")
}

function mustGetEnv(name) {
  const value = process.env[name]
  if (!value) throw new Error(`Missing environment variable: ${name}`)
  return value
}

function envOrDefault(name, fallback) {
  return process.env[name] || fallback
}

function normalize(value) {
  return String(value || "").trim().toLowerCase()
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

function printHelp() {
  console.log(`Add or update a Framer blog post from JSON.\n\nUsage:\n  node framer/addBlogPost.mjs ./post.json\n`)
}
