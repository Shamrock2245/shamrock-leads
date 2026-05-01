# Principles

Aggregation pipelines process documents through sequential stages. Focus on:

- Reducing documents early in the pipeline
- Minimizing data moved between stages
- Leveraging indexes where possible
- Managing memory usage

## Memory limits and disk spilling

Blocking stages (such as in-memory `$sort` and `$group`) have a 100MB memory limit per stage. Default behavior when this limit is exceeded is to spill to disk automatically (`allowDiskUse` defaults to `true`).

**Better solutions:**

- Filter more aggressively early in pipeline
- Add indexes to enable `$sort` to use index order
- Use `$limit` with `$sort` to reduce the amount of data the sort must process in memory for unindexed sorts
- Consider materialized views for repeated aggregations

# Optimization Examples

## Unindexed $lookup vs. Indexed $lookup

**Bad** — No index on the foreign collection's join field:

```javascript
db.orders.aggregate([
  { $lookup: {
      from: "products",
      localField: "productId",
      foreignField: "sku",   // no index on products.sku!
      as: "product"
  }}
])
```

**Good** — Index on `foreignField` in the foreign collection:

```javascript
db.products.createIndex({ sku: 1 })

db.orders.aggregate([
  { $lookup: {
      from: "products",
      localField: "productId",
      foreignField: "sku",
      as: "product"
  }}
])
```

**Why:** Each `$lookup` executes a find on the `from` collection. Without an index on `foreignField`, every join does a full collection scan.

## Early $project Defeating Optimization vs. Late $project

**Bad** — Early `$project` prevents the optimizer from pruning unused fields:

```javascript
db.collection.aggregate([
  { $project: { name: 1, status: 1, amount: 1 } },
  { $match: { status: "active" } },
  { $group: { _id: "$status", total: { $sum: "$amount" } } }
])
```

**Good** — Let the optimizer handle field pruning; use `$project` only at the end:

```javascript
db.collection.aggregate([
  { $match: { status: "active" } },
  { $group: { _id: "$status", total: { $sum: "$amount" } } },
  { $project: { _id: 0, status: "$_id", total: 1 } }
])
```

## $sort + $limit as Top-N Sort

**Good** — Place `$limit` immediately after `$sort`:

```javascript
db.collection.aggregate([
  { $group: { _id: "$category", total: { $sum: "$amount" } } },
  { $sort: { total: -1 } },
  { $limit: 10 }
])
```

**Why:** When `$sort` is immediately followed by `$limit`, MongoDB performs a *top-N sort* — it only tracks the top N values instead of sorting the full dataset.

## Optimize $lookup operations

```javascript
[
  { $match: { active: true } },  // Reduce left side
  { $lookup: {
      from: "inventory",
      localField: "product_id",
      foreignField: "_id",  // _id is always indexed
      pipeline: [
        { $match: { inStock: true } },  // Reduce right side
        { $project: { _id: 0, name: 1, price: 1 } }
      ],
      as: "product"
  }},
  { $unwind: "$product" }
]
```

**Schema consideration:** Excessive `$lookup` usage may indicate over-normalization. Consider embedding frequently-joined data.
