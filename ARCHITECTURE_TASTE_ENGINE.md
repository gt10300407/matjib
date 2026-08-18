# Taste Engine v4 — Fact-based discovery

## Non-negotiable rule

Production recommendation logic must not contain city-specific or restaurant-specific exceptions.
Known restaurants may be used only as external QA examples, never as code paths that force a result.

## Pipeline

1. **Discovery inventory**
   - Kakao FD6 restaurants over the selected city's bbox grid.
   - Kakao CE7 cafes over the same bbox grid.
   - Google Nearby Search over bounded bbox cells using `POPULARITY` for restaurants/cafes.
   - Generic text/menu queries remain supplemental evidence, not the only discovery gate.

2. **Entity merge**
   - Merge same place by normalized name, coordinates and address overlap.

3. **Evidence**
   - Google `rating` and `userRatingCount`.
   - Repeated generic menu/category keyword discovery.
   - Cross-source match.
   - Public excellent-restaurant data when available.

4. **Recommendation**
   - Spatial grid overlap by itself is **not** taste evidence.
   - A place can be discovered spatially and recommended from strong/high-volume user evaluation even if no manually anticipated menu query matched it.
   - Major nationwide chains are collected but hidden from local recommendation by default.

## Why this fixes false negatives

Previous versions required a restaurant to appear in a small set of text-query top results. A popular local restaurant could therefore be absent simply because its menu keyword was not anticipated. v4 first builds a geographic candidate inventory, then attaches evidence.

## Request budget

Current Google request budget per refresh is intentionally bounded:
- 9 Text Search evidence queries.
- 2x2 Nearby grid x 2 groups (restaurant/cafe) = up to 8 Nearby calls.
- Maximum normal Google calls: about 17 per region refresh.

Kakao spatial discovery uses 3x3 cells for FD6 and CE7, up to two pages per cell, plus bounded generic keyword queries. Calls run concurrently.

Any future increase in billable Google request volume must be explicit and documented before deployment.

## Validation

Automated tests verify:
- No production region-specific query map exists.
- Spatial overlap alone cannot promote a place.
- A high-volume Google place discovered through Nearby can be recommended without a menu keyword.
- Major cafe chains remain excluded from local recommendations.
