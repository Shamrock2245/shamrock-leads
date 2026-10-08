# Collier / Glades stored-bond cleanup plan

**STATUS: NOT RUN. Awaiting Brendan's OK.** Nothing in this doc has been run against prod. The agent box has no `MONGODB_URI`. Leads Ops runs it only after Brendan approves, and only after the #139 parser fix is deployed.

## Why
#139 stops `collier.py` and `glades.py` from inventing bonds. Rows still on the roster get the corrected value the next time they are scraped. Rows for people who already left the roster keep the old value. That old value is:

- **Collier:** every non-empty stored bond. The daily report publishes no bond amount, so the stored figure came from charge text (for example "$750-$5K" became 750) or is the old `"0"` default for unknown.
- **Glades:** stored `"0"` (the old default for no match) and `"0.00"` (the card-level "Bond Amount:", which prints `$0.00` even when the charges publish $15,000 to $245,000, read from this card or the next one).
- **Not identifiable:** a stored **positive** Glades bond. No per-charge bonds or card text are stored, so we can't tell whether it came from this card or the next card. Those rows are counted (`positive_unattributable`) but not changed. The same goes for Glades text values.

## Rules
- Back up before changing anything. The backup holds only `_id`, county, state, the bond fields and `scraped_at`. No names, and it never goes into git.
- Rows with any staff provenance are skipped: `staff_edits` present, `bond_override: true`, or `last_checked_mode: "MANUAL_CHARGE_BONDS"`. The filter excludes them with `$nor`.
- Rows last scraped after the fix deploy (`--before`) are skipped, because the fixed parser wrote them.
- Unknown bond becomes `bond_amount_raw: ""` with `bond_amount: 0.0`. That is exactly what the fixed scrapers write for an unknown bond (`ArrestRecord.to_mongo_doc`). The old values are kept on the row in `bond_cleanup_2026_10`.
- Lead scores are not recomputed here. A row's score may still reflect the old bond until it is rescored.

## Steps (Leads Ops, on the VPS, with `MONGODB_URI` set)
Set the deploy time of #139 in UTC and a run stamp:
```bash
export DEPLOY_UTC=2026-10-09T00:00:00        # replace with the real #139 deploy time (UTC)
export RUN=$(date -u +%Y%m%dT%H%M%SZ)
export DB=${MONGODB_DB_NAME:-ShamrockBailDB}
mkdir -p ~/backups/bond_cleanup             # ops backup dir, not git
```

### 1. Counts before (read-only)
```bash
python scripts/collier_glades_bad_bond_count.py --before "$DEPLOY_UTC" | tee ~/backups/bond_cleanup/counts_before_$RUN.json
```
Expected: `collier.would_blank` = `collier.bad_zero` + `collier.bad_positive_from_charge_text` + `collier.bad_text`, and `glades.would_blank` = `glades.bad_zero`. For each county, `filter_matches` must equal `would_blank`. If they differ, stop and send the JSON to Scraper Watch.

### 2. Backup (mongoexport of the affected rows)
```bash
for C in Collier Glades; do
  python scripts/collier_glades_bad_bond_count.py --before "$DEPLOY_UTC" --print-filter "$C" > ~/backups/bond_cleanup/filter_${C}_$RUN.json
  mongoexport --uri "$MONGODB_URI" --db "$DB" --collection arrests \
    --query "$(cat ~/backups/bond_cleanup/filter_${C}_$RUN.json)" \
    --fields _id,county,state,bond_amount,bond_amount_raw,total_bond_amount,bond_type,scraped_at \
    --out ~/backups/bond_cleanup/bond_cleanup_${C}_$RUN.json
  wc -l ~/backups/bond_cleanup/bond_cleanup_${C}_$RUN.json   # must equal that county's filter_matches
done
```

### 3. Blank (one county at a time, Collier first)
```bash
export C=Collier   # then repeat with: export C=Glades
mongosh "$MONGODB_URI/$DB" --quiet --eval '
  const f = EJSON.parse(require("fs").readFileSync(process.env.HOME + "/backups/bond_cleanup/filter_" + process.env.C + "_" + process.env.RUN + ".json", "utf8"));
  const filter = {$and: [f, {bond_cleanup_2026_10: {$exists: false}}]};
  const before = db.arrests.countDocuments(filter);
  const res = db.arrests.updateMany(filter, [{$set: {
    bond_cleanup_2026_10: {
      old_bond_amount: "$bond_amount",
      old_bond_amount_raw: "$bond_amount_raw",
      old_total_bond_amount: "$total_bond_amount",
      reason: "source publishes no such bond; parser fixed in #139",
      run: process.env.RUN, at: "$$NOW", by: "leads_ops"
    },
    bond_amount_raw: "",
    bond_amount: 0.0,
    total_bond_amount: {$cond: [{$eq: [{$type: "$total_bond_amount"}, "missing"]}, "$$REMOVE", 0.0]}
  }}]);
  printjson({county: process.env.C, matched_before: before, matched: res.matchedCount, modified: res.modifiedCount});
'
```
`matched` must equal that county's backup line count.

### 4. Verification
```bash
python scripts/collier_glades_bad_bond_count.py --before "$DEPLOY_UTC" | tee ~/backups/bond_cleanup/counts_after_$RUN.json
mongosh "$MONGODB_URI/$DB" --quiet --eval '
  printjson({
    collier_marked: db.arrests.countDocuments({county: /^Collier(\s+County)?$/i, "bond_cleanup_2026_10.run": process.env.RUN}),
    glades_marked:  db.arrests.countDocuments({county: /^Glades(\s+County)?$/i,  "bond_cleanup_2026_10.run": process.env.RUN}),
    marked_with_staff_provenance: db.arrests.countDocuments({"bond_cleanup_2026_10.run": process.env.RUN,
      $or: [{staff_edits: {$exists: true}}, {bond_override: true}, {last_checked_mode: "MANUAL_CHARGE_BONDS"}]})
  });
'
```
Expected:
- `filter_matches` is 0 for both counties, and `would_blank` is 0.
- `collier_marked` and `glades_marked` equal the backup line counts.
- `marked_with_staff_provenance` is 0.
- `skipped_staff_provenance` and `glades.positive_unattributable` are unchanged from the before counts.

### Rollback
This restores the backed-up values. It only touches rows this run marked whose bond has not been rescraped since:
```bash
for C in Collier Glades; do
  export C
  mongoimport --uri "$MONGODB_URI" --db "$DB" --collection "bond_cleanup_restore_${C}_$RUN" \
    --file ~/backups/bond_cleanup/bond_cleanup_${C}_$RUN.json
  mongosh "$MONGODB_URI/$DB" --quiet --eval '
    const coll = db.getCollection("bond_cleanup_restore_" + process.env.C + "_" + process.env.RUN);
    let n = 0;
    coll.find().forEach(b => {
      const set = {bond_amount: b.bond_amount, bond_amount_raw: b.bond_amount_raw};
      const unset = {bond_cleanup_2026_10: ""};
      if (b.total_bond_amount === undefined) unset.total_bond_amount = ""; else set.total_bond_amount = b.total_bond_amount;
      n += db.arrests.updateOne(
        {_id: b._id, "bond_cleanup_2026_10.run": process.env.RUN, bond_amount_raw: ""},
        {$set: set, $unset: unset}).modifiedCount;
    });
    printjson({county: process.env.C, restored: n});
  '
done
```
Afterwards, drop the `bond_cleanup_restore_*` collections once the counts match the before counts.

## Sign-off
- [ ] Brendan OK (date, ET):
- [ ] #139 deployed at (UTC):
- [ ] Before counts attached
- [ ] Backup line counts equal `filter_matches`
- [ ] After counts attached
