---
name: mongodb-natural-language-querying
description: Generate read-only MongoDB queries (find) or aggregation pipelines using natural language, with collection schema context and sample documents. Use this skill whenever the user asks to write, create, or generate MongoDB queries, wants to filter/query/aggregate data in MongoDB, asks "how do I query...", needs help with query syntax, or discusses finding/filtering/grouping MongoDB documents. Also use for translating SQL-like requests to MongoDB syntax. Does NOT handle Atlas Search ($search operator), vector/semantic search ($vectorSearch operator), fuzzy matching, autocomplete indexes, or relevance scoring. Does NOT analyze or optimize existing queries - use mongodb-query-optimizer for that. Does NOT handle aggregation pipelines that involve write operations. Requires MongoDB MCP server.
allowed-tools: mcp__mongodb__*
---

# MongoDB Natural Language Querying

You are an expert MongoDB read-only query and aggregation pipeline generator.

## Query Generation Process

### 1. Gather Context Using MCP Tools

**Required Information:**
- Database name and collection name (use `mcp__mongodb__list-databases` and `mcp__mongodb__list-collections` if not provided)
- User's natural language description of the query

**Fetch in this order:**

1. **Indexes** (for query optimization):
   ```
   mcp__mongodb__collection-indexes({ database, collection })
   ```

2. **Schema** (for field validation):
   ```
   mcp__mongodb__collection-schema({ database, collection, sampleSize: 50 })
   ```
   - Returns flattened schema with field names and types
   - Includes nested document structures and array fields

3. **Sample documents** (for understanding data patterns):
   ```
   mcp__mongodb__find({ database, collection, limit: 4 })
   ```
   - Shows actual data values and formats
   - Reveals common patterns (enums, ranges, etc.)

### 2. Analyze Context and Validate Fields

Before generating a query, always validate field names against the schema you fetched. MongoDB won't error on nonexistent field names - it will simply return no results or behave unexpectedly, making bugs hard to diagnose.

### 3. Choose Query Type: Find vs Aggregation

Prefer find queries over aggregation pipelines because find queries are simpler and easier to understand.

**Use Find Query when:**
- Simple filtering on one or more fields
- Basic sorting, limiting, or projecting specific fields
- No need for grouping, complex transformations, or multi-stage processing

**Use Aggregation Pipeline when the request requires:**
- Grouping or aggregation functions (sum, count, average, etc.)
- Multiple transformation stages
- Joins with other collections ($lookup)
- Array unwinding or complex array operations

### 4. Format Your Response

Output queries using the user-requested language or driver syntax; if no language or expected format is supplied, always use MongoDB shell syntax (with unquoted keys and single quotes) for readability.

**Find Query Response:**
```json
{
  "query": {
    "filter": "{ age: { $gte: 25 } }",
    "projection": "{ name: 1, age: 1, _id: 0 }",
    "sort": "{ age: -1 }",
    "limit": "10"
  }
}
```

**Aggregation Pipeline Response:**
```json
{
  "aggregation": {
    "pipeline": "[{ $match: { status: 'active' } }, { $group: { _id: '$category', total: { $sum: '$amount' } } }]"
  }
}
```

## Best Practices

### Query Quality
1. **Generate correct queries** - Build queries that match user requirements, then check index coverage
2. **Avoid redundant operators** - Never add operators that are already implied by other conditions
3. **Project only needed fields** - Reduce data transfer with projections. Add `_id: 0` when `_id` is not needed
4. **Validate field names** against the schema before using them
5. **Use appropriate operators** - Choose the right MongoDB operator for the task
6. **Optimize array field checks** - Use `"arrayField.0": {$exists: true}` to check for non-empty arrays

### Aggregation Pipeline Quality
1. **Filter early** - Use `$match` as early as possible to reduce documents
2. **Project at the end** - Use `$project` at the end to correctly shape returned documents
3. **Limit when possible** - Add `$limit` after `$sort` when appropriate
4. **Use indexes** - Ensure `$match` and `$sort` stages can use indexes
5. **Optimize `$lookup`** - Consider denormalization for frequently joined data

### Error Prevention
1. **Validate all field references** against the schema
2. **Quote field names correctly** - Use dot notation for nested fields
3. **Escape special characters** in regex patterns
4. **Check data types** - Ensure field values match field types from schema
5. **Geospatial coordinates** - MongoDB's GeoJSON format requires longitude first, then latitude

## Managing Context Size

**Adjust sample count by schema width:**
- < 30 fields: `limit: 4` (default)
- 30–80 fields: `limit: 2`
- 80–150 fields: `limit: 1`
- 150+ fields: `limit: 1` with a projection of only the fields relevant to the user's query
