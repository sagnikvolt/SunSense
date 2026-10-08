# ☀️ SunSense

**Rooftop solar sizing & payback calculator for West Bengal homes.**

Enter where you live, what you pay for electricity and how much roof you have. SunSense tells you what size of rooftop solar system fits, how much it will generate, what it costs after the PM Surya Ghar subsidy, how fast it pays for itself, and how much CO₂ it saves.

Built for the **WeMakeDevs × AWS Environmental Hacks** hackathon (Bharat Builds Tour, Event 02) · Track: **Waste and Energy** · 8–11 Oct 2026

---

## The problem

Most households in West Bengal never go solar because they can't answer three simple questions: *How big a system do I need? What will it really cost me after the subsidy? When do I get my money back?* Installers give quotes, not straight answers. SunSense gives a clear, honest estimate in under a minute, using real irradiance data for the user's location.

---

## Input / output contract

This is the single source of truth between frontend, API and calculation logic. **Do not change field names without telling the team.**

### Input

| Field | Type | Required | Notes |
|---|---|---|---|
| `pincode` | string (6 digits) | one of `pincode` or `lat`+`lon` | Converted to lat/lon on the backend |
| `lat`, `lon` | number | one of `pincode` or `lat`+`lon` | Decimal degrees |
| `monthly_units` | number (kWh) | one of `monthly_units` or `monthly_bill` | From the electricity bill |
| `monthly_bill` | number (₹) | one of `monthly_units` or `monthly_bill` | Converted to units using the WBSEDCL tariff |
| `roof_area` | number (m²) | yes | Usable, shade-free roof area |
| `system_type` | `"on-grid"` \| `"off-grid"` | yes | Default `"on-grid"` |

Example request (`POST /calculate`):

```json
{
  "pincode": "700089",
  "monthly_units": 250,
  "roof_area": 30,
  "system_type": "on-grid"
}
```

### Output

| Field | Unit | Meaning |
|---|---|---|
| `system_kw` | kWp | Recommended system size (after roof cap) |
| `limited_by_roof` | bool | `true` if the roof area capped the size |
| `peak_sun_hours` | h/day | Annual average for the location |
| `yearly_generation_kwh` | kWh/yr | Expected generation |
| `cost_before_subsidy` | ₹ | Gross installed cost |
| `subsidy` | ₹ | PM Surya Ghar central subsidy |
| `cost_after_subsidy` | ₹ | What the household actually pays |
| `yearly_savings` | ₹/yr | Bill savings |
| `payback_years` | years | `cost_after_subsidy ÷ yearly_savings` |
| `co2_saved_kg_per_year` | kg/yr | CO₂ avoided |
| `assumptions` | object | Every constant used, so judges and users can see them |

Example response:

```json
{
  "system_kw": 2.5,
  "limited_by_roof": false,
  "peak_sun_hours": 4.6,
  "yearly_generation_kwh": 3358,
  "cost_before_subsidy": 0,
  "subsidy": 0,
  "cost_after_subsidy": 0,
  "yearly_savings": 0,
  "payback_years": 0,
  "co2_saved_kg_per_year": 2754,
  "assumptions": { "performance_ratio": 0.8, "grid_emission_factor": 0.82 }
}
```
*(Example values are placeholders until `calculate()` is tested.)*

---

## Calculation logic

All maths lives in **one Python function**, so it can be tested on its own and dropped into Lambda unchanged:

```python
def calculate(units: float, roof_area: float, location: dict, system_type: str = "on-grid") -> dict:
    ...
```

| Step | Formula |
|---|---|
| **Size** | `kW = monthly_units ÷ (30 × peak_sun_hours × 0.8)`, then capped at `roof_area ÷ 10` (about 10 m² per kW) |
| **Generation** | `yearly_kwh = kW × peak_sun_hours × 365 × 0.8` |
| **Gross cost** | `kW × cost_per_kw` (benchmark ₹/kW, **to confirm**) |
| **Subsidy** | ₹30,000/kW for the first 2 kW, ₹18,000 for the 3rd kW, capped at ₹78,000. On-grid only; off-grid gets ₹0 |
| **Savings** | units generated × WBSEDCL tariff slab rate |
| **Payback** | `cost_after_subsidy ÷ yearly_savings` |
| **CO₂ saved** | `yearly_kwh × 0.82 kg` |

**Constants to verify before the demo:**
- [ ] Subsidy slabs and cap on [pmsuryaghar.gov.in](https://pmsuryaghar.gov.in)
- [ ] Domestic tariff slab rates on the WBSEDCL tariff page
- [ ] Benchmark cost per kW for residential rooftop in WB
- [ ] Grid emission factor (0.82 kg CO₂/kWh, CEA baseline)

**Sanity test:** run `calculate()` against the team's existing **2.75 kWp** design numbers. We already know roughly what the answer should be, so a broken formula will show up immediately. Add it as a unit test in `backend/tests/`.

Known v1 limits: off-grid battery cost isn't modelled yet, and there's no shading or tilt correction beyond what PVGIS provides.

---

## Architecture

```
[ Frontend: HTML/React on AWS Amplify ]
              │  POST /calculate (JSON)
              ▼
      [ Amazon API Gateway ]
              │
              ▼
   [ AWS Lambda — Python: calculate() ]
              │  cache hit?  ──►  [ Amazon S3: irradiance cache by lat/lon ]
              │  cache miss  ──►  PVGIS / NASA POWER API → write to S3
```

- **Region:** Asia Pacific (Mumbai) `ap-south-1`
- **Data:** PVGIS (EU JRC) and NASA POWER for solar irradiance/peak sun hours, cached in S3 so repeat lookups are instant and free

---

## Team & role split

| Member | Role | Owns |
|---|---|---|
| **Sagnik Kumar Nath (Roni)** | Team lead · domain & calculation | `calculate()` and its tests, verifying the subsidy/tariff/cost constants, final pitch & demo |
| **Shuvankar Debnath** | Backend & AWS | Lambda + API Gateway, S3 cache, deployment (has the IAM user) |
| **Somsuddha Dasgupta** | Frontend | Input form, results page, charts, Amplify hosting |
| **Soumya Tirtha Dhawa** | Data & docs | PVGIS / NASA POWER integration, pincode → lat/lon, README, demo video & submission |

*Proposed split. Swap roles in the group chat and update this table.*

---

## Security

**Frontend (GitHub Pages / Amplify)**
- Content Security Policy: scripts only from this site and the pinned Cesium build; inline scripts allowed by SHA-256 hash only; no plugins, frames, form posts or `<base>` changes. Cesium needs `unsafe-eval` and WebAssembly, so those are allowed.
- Subresource Integrity on Cesium JS/CSS, so a tampered CDN file is refused.
- All user text is written with `textContent`, never as HTML. Inputs are capped (address 120 chars, units ≤ 100,000, roof ≤ 10,000 m²) and non-numbers are treated as 0.
- Address search is cached and throttled (20 lookups per 5 minutes per tab), India-only results.
- GPS coordinates never leave the browser except to our own API.
- Cesium token is public by design and restricted to this site's URLs in Cesium ion.

**Backend (Lambda + API Gateway + S3)**
- 2 KB request limit; strict JSON object; finite numbers in range only (NaN, Infinity, booleans rejected); India-only coordinates; 6-digit pincodes.
- CORS allow-list (`AllowedOrigins` parameter), `nosniff`, `no-store`; errors never include internals.
- API throttling (10 req/s, burst 20); upstream calls only to NASA POWER / PVGIS / OSM over HTTPS with size limits.
- S3 cache: private, encrypted, TLS-only, objects expire after a year; Lambda can only get/put cache objects. Logs kept 14 days.
- `backend/tests/test_security.py` fuzzes the API with 3,000 junk payloads; it must never return a 500.

## Repo layout (planned)

```
sunsense/
├── backend/
│   ├── calculator.py      # calculate() — pure logic, no AWS
│   ├── handler.py         # Lambda entry point
│   ├── data_sources.py    # PVGIS / NASA POWER + S3 cache
│   └── tests/
├── frontend/
└── README.md
```

---

## Running locally

```bash
cd backend
python -m pytest tests/
```

*(Frontend and deployment steps to be added.)*

---

## License

MIT
