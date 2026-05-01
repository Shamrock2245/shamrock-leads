---
name: mongodb-schema-design
description: MongoDB schema design patterns and anti-patterns. Use when designing data models, reviewing schemas, migrating from SQL, or troubleshooting performance issues caused by schema problems. Triggers on "design schema", "embed vs reference", "MongoDB data model", "schema review", "unbounded arrays", "one-to-many", "tree structure", "16MB limit", "schema validation", "JSON Schema", "time series", "schema migration", "polymorphic", "TTL", "data lifecycle", "archive", "index explosion", "unnecessary indexes", "approximation pattern", "document versioning".
license: Apache-2.0
---

# MongoDB Schema Design

Data modeling patterns and anti-patterns for MongoDB, maintained by MongoDB. Bad schema is the root cause of most MongoDB performance and cost issues—queries and indexes cannot fix a fundamentally wrong model.

## When to Apply

Reference these guidelines when:
- Designing a new MongoDB schema from scratch
- Migrating from SQL/relational databases to MongoDB
- Reviewing existing data models for performance issues
- Troubleshooting slow queries or growing document sizes
- Deciding between embedding and referencing
- Modeling relationships (one-to-one, one-to-many, many-to-many)
- Implementing tree/hierarchical structures
- Seeing Atlas Schema Suggestions or Performance Advisor warnings
- Hitting the 16MB document limit
- Adding schema validation to existing collections

## Quick Reference

### 1. Schema Anti-Patterns - 3 rules

- **Unnecessary Collections** - Splitting homogeneous data into multiple collections is often an anti-pattern.
- **Excessive Lookups** - Overly normalized collections that reference each other or frequent and possibly slow $lookup operations.
- **Unnecessary Indexes** - Indexes that overlap or are not used by queries add overhead without benefit.

### 2. Schema Fundamentals - 4 rules

- **Embed vs Reference** - Approaches to modeling different types of relationships (1:1, 1:few, 1:many, many:many, tree/hierarchical data).
- **Document Model** - Fundamentals of the document model. Important when migrating from SQL.
- **Schema Validation** - Use MongoDB's built-in `$jsonSchema` validator to catch invalid data at the database level.
- **Document Size** - MongoDB documents cannot exceed 16MB—this is a hard limit, not a guideline.

### 3. Design Patterns - 11 rules

- **Approximation** - Use approximate values for high-frequency counters
- **Archive** - Move historical data to separate/cold storage for performance
- **Attribute** - Collapse many optional fields into key-value attributes
- **Bucket** - Group time-series or IoT data into buckets
- **Computed** - Pre-calculate expensive aggregations
- **Document Versioning** - Track document changes to enable historical queries and audit trails
- **Extended Reference** - Cache frequently-accessed data from related entities
- **Outlier** - Handle collections in which a small subset of documents are much larger than the rest
- **Polymorphic** - Store different types of entities in the same collection
- **Schema Versioning** - Schema evolution, preventing drift, and safe online migrations
- **Time Series Collections** - Use native time series collections for high-frequency time series data

## Key Principle

> **"Data that is accessed together should be stored together."**

This is MongoDB's core philosophy. Embedding related data eliminates joins, reduces round trips, and enables atomic updates. Reference only when you must.

MongoDB exposes **flexible schemas**. This means you can have different fields in different documents, and even different structures. This allows you to model data in the way that best fits your access patterns, without being constrained by a rigid schema.

Another implication of the key principle is that information about the expected read and write workload becomes very relevant to schema design.

#### Schema Fundamentals Summary

- **Embed vs Reference**: Choose embedding or referencing based on access patterns: embed when data is always accessed together (1:1, 1:few, bounded arrays, atomic updates needed); reference when data is accessed independently, relationships are many-to-many, or arrays can grow without bound.
- **Data accessed together stored together**: Design schemas around queries, not entities.
- **Embrace the document model**: Don't recreate SQL tables 1:1 as MongoDB collections. Instead, denormalize joined tables into rich documents for single-query reads and atomic updates.
- **Schema validation**: Use MongoDB's built-in `$jsonSchema` validator to catch invalid data at the database level.
- **16MB document limit**: Common causes: unbounded arrays, large embedded binaries, deeply nested objects. Mitigate by moving unbounded data to separate collections and monitoring document sizes with `$bsonSize`.

## Embed/Reference Decision Framework

| Relationship | Cardinality | Access Pattern | Recommendation |
|-------------|-------------|----------------|----------------|
| One-to-One | 1:1 | Always together | Embed |
| One-to-Few | 1:N (N < 100) | Usually together | Embed array |
| One-to-Many | 1:N (N > 100) | Often separate | Reference |
| Many-to-Many | M:N | Varies | Two-way reference |

This is a **rough** guideline, and whether to embed or reference depends on your specific access patterns, data size, and read/write frequencies. Always verify with your actual workload.

## MongoDB MCP Integration

For automatic verification, connect the [MongoDB MCP Server](https://github.com/mongodb-js/mongodb-mcp-server).

When connected, I can automatically:
- Infer schema via `mcp__mongodb__collection-schema`
- Measure document/array sizes via `mcp__mongodb__aggregate`
- Check collection statistics via `mcp__mongodb__db-stats`

### Action Policy

**I will NEVER execute write operations without your explicit approval.**

| Operation Type | MCP Tools | Action |
|---------------|-----------|--------|
| **Read (Safe)** | `find`, `aggregate`, `collection-schema`, `db-stats`, `count` | I may run automatically to verify |
| **Write (Requires Approval)** | `update-many`, `insert-many`, `create-collection` | I will show the command and wait for your "yes" |
| **Destructive (Requires Approval)** | `delete-many`, `drop-collection`, `drop-database` | I will warn you and require explicit confirmation |
