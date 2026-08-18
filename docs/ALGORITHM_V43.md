# Taste Engine v4.3 algorithm

AI is deliberately excluded from matching/recommendation decisions in this phase.

## Matching pipeline
1. Build a factual city master from public licensing datasets.
2. Discover candidates with Kakao and Google.
3. Resolve one entity using deterministic signals: normalized name, exact phone, road-address tokens, city, and WGS84 distance only when coordinates are trustworthy.
4. Reject city mismatches before ranking.
5. Use Google as rating/review evidence, not as the canonical identity source when a stronger local/public entity exists.
6. Public licensing rows prove existence/registration only; they do not by themselves make a place a recommendation.

## Public master datasets
- general restaurants: `/1741000/general_restaurants/info`
- rest cafes: `/1741000/rest_cafes/info`
- bakeries: `/1741000/bakeries/info`

Each dataset is optional at runtime. Authorization failure of one dataset must not break the others.

## Cost rule
No additional Google request is added versus v4.2. Live search remains bounded to at most one Kakao request and one Google request. The public-data datasets are free public APIs and are cached per region.
