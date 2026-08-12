# Playbook: Build / fix the Salla↔Make product-creation scenario

Scenario **8592565**. Edit it via the Make MCP tools, never blindly. The owner's store is live; a bad blueprint stops every product.

## Golden workflow for any scenario change
1. `scenarios_get` the live blueprint first — the owner or programmer may have edited it. **Keep the returned blueprint** in context; it's your instant revert.
2. Make the smallest change to the JSON.
3. `validate_blueprint_schema` (pass `{name, flow, metadata}` — it rejects top-level `scheduling`/`interface`). Then `validate_module_configuration` for any module you changed (org 5625939, team 2934620, app `salla`, version 1).
4. `scenarios_update` (include `scheduling` + `interface` back in the blueprint object). Confirm `isinvalid:false` and `isActive:true` in the response.
5. Ask the owner to send one webhook test, then `executions_list` and read the result. **On-demand `scenarios_run` is flaky here ("Scenario is not activated") — the webhook is the reliable trigger.**

## Reading executions (the API hides bundle I/O)
`executions_get`/`-detail` return only **status**, not the input/output bundles or the Salla error `fields`. So you infer from **operation count** and status:
- The full happy path is ~7–9 ops (webhook → searches → CreateProduct → rename → image …).
- A run that stops early (e.g. **6 ops**) with `status:1` is the tell-tale of a **swallowed failure**: `CreateProduct` errored but an `onerror: Ignore` masked it ("false success"). Ops count, not status, reveals it.
- `status:1` with the **full** op count = real success. `status:3` = a real, visible error (good — not masked).

## The webhook payload (module 1 interface, Arabic field names)
`أسم المنتج, سعر المنتج, رمز المنتج sku, الوزن, سعر التكلفة, السعر المخفض, الوصف, صورة المنتج, الكمية, صور المنتج, عنوان السيو, وصف السيو, رابط السيو, الماركة, صورة شعار الماركة, اسم التصنيف`. The app sends brand in `الماركة` and category **name** in `اسم التصنيف` (not an id).

## Live-search resolution (the robust design — "v5")
Resolve brand and category by **searching Salla live**, not a stale mirror. Two `salla:makeApiCall` modules right after the feeder:
- **Category search:** `method: GET`, `path: "categories"`, `qs: [{key:"keyword", value:"{{2.\`اسم التصنيف\`}}"}]`, no body. Keyword search **reaches subcategories** (a plain list returns only ~12 top-level).
- **Brand search:** same shape, `path: "brands"`.
Then in `CreateProduct` use exact-match expressions over the search results.

### Category → id (exact match, clean array)
```
categories: {{if(get(toCollection(10.body.data; "name"; "id"); 2.`اسم التصنيف`); split(get(toCollection(10.body.data; "name"; "id"); 2.`اسم التصنيف`); ","); emptyarray())}}
```
- `toCollection(arr; "name"; "id")` then `get(map; <name>)` returns the **exactly-named** category's id, or empty (safe — no fuzzy mismatch). It correctly picks `العطور` out of results that also contain `بدائل العطور`, `إكسسوارات العطور`.

### ⚠️ The categories-422 fix (the bug that caused "false success")
Salla rejects `422 invalid_fields [Collection]` when `categories` is a **malformed array**. The classic mistake is building it with `add(emptyarray(); id)` — `add()` collides with numeric addition and yields a mangled value. **Use `split` instead:**
- `split(id; ",")` → `["123"]` (clean single-element array; the id has no comma).
- fallback `emptyarray()` → `[]` (a **truly empty** array). Do **not** use `["{{id}}"]` as the fallback because an unresolved id makes `[""]` (empty string in array), which Salla can also reject. `[]` is accepted (product created without a category — safe).

### Brand → id (bilingual exact match)
Brands are stored bilingual: `"عربي | English"` (e.g. `ديور | Dior`). The app may send either side. Match the first ~3 results, splitting each name on `" | "`, comparing to `2.\`الماركة\``. Make has **no `or()`** — use nested `if`:
```
brand_id: {{if(get(split(get(get(11.body.data;1);"name");" | ");1) = 2.`الماركة`; get(get(11.body.data;1);"id"); if(get(split(get(get(11.body.data;1);"name");" | ");2) = 2.`الماركة`; get(get(11.body.data;1);"id"); ... ; emptystring()))}}
```
Returns a valid id or `emptystring()` (no wrong-brand). Repeat the pattern for results 2 and 3.

## SEO slug: create-then-rename
Salla auto-generates the product URL from its name at **creation** and the slug is "sticky" (rename later doesn't change the URL). To get an English URL but an Arabic display name:
- `CreateProduct.name = {{ifempty(replace(2.\`رابط السيو\`; "_"; " "); 2.\`أسم المنتج\`)}}` (English slug source; `_`→space).
- then `UpdateProduct` renames to `{{2.\`أسم المنتج\`}}` (Arabic). URL stays English.
- `metadata_title`/`metadata_description` are flat fields on Create/Update; there is **no** URL field on the module (URL comes from the name).

## Images
- Main image: `salla:makeApiCall POST products/{{240.id}}/images` body `{"original":"{{2.\`صورة المنتج\`}}","default":true}`, behind a filter that `صورة المنتج` exists.
- Additional: a `BasicFeeder` over `slice(ifempty(2.\`صور المنتج\`; emptyarray()); 1; 100)` → another `makeApiCall POST .../images`.

## onerror philosophy
- **`CreateProduct` must NOT have `onerror: Ignore`** — masking its failure is exactly the "false success" trap (6-ops mystery). Let it surface (`status:3`) so failures are visible/debuggable.
- It's acceptable to keep `onerror: Ignore` on **post-creation enhancers** (rename, image POSTs): a hiccup there shouldn't roll back a successfully created product. That's a degradation, not a false success.

## Make expression gotchas (hard-won)
- No `or()` / no `and()` in expressions → nested `if`.
- `first(arr).field` returns the whole object — use `get(first(arr); "field")` or `get(get(arr; N); "field")`.
- `get(arr; idx)` is **1-based**.
- `split(text; sep)` is the clean scalar→array builder; `add(emptyarray(); x)` is not.
- Equality in `if` uses `=`.
- Filter `conditions` are OR-of-AND groups: `[[A],[B]]` = A or B.

## Optional next layers (add carefully, test each)
- **Create brand-if-missing** (sequential, before CreateProduct): Make routers don't merge, and parallel routes race (the product's brand_id resolves before the new brand exists). The correct shape is two **mutually-exclusive** routes (brand-found vs named-but-missing→create→use `{{CreateBrand.id}}`), each ending in its own CreateProduct. Ship resolve-only first; layer creation after it's validated.
