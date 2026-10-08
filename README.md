# ☀️ SunSense

**Find your roof on a 3D globe and see how much rooftop solar it can hold, what it costs after the PM Surya Ghar subsidy, and when it pays for itself. Works anywhere in India.**

🔗 **Live site: [sagnikvolt.github.io/SunSense](https://sagnikvolt.github.io/SunSense/)** · API on AWS Lambda + API Gateway (Mumbai, `ap-south-1`)

Built for the **WeMakeDevs × AWS Environmental Hacks** hackathon (Bharat Builds Tour, Event 02) · Track: **Waste and Energy** · 8–11 Oct 2026

---

## The problem

India wants 1 crore homes on rooftop solar under PM Surya Ghar, but most households stall on three questions: *How big a system do I need? What will it really cost after the subsidy? When do I get my money back?* Vendor quotes are opaque, and general-purpose calculators ignore India's subsidy rules and slab tariffs. People who can't answer those questions don't switch, and their homes stay on a coal-heavy grid (0.82 kg CO₂ per unit).

## What SunSense does

1. **Find your roof.** Type an address and pick from live suggestions, enter a pincode or latitude/longitude, or tap *Detect my location*. The camera flies down to your house in Google's photorealistic 3D, and you can drag the pin onto the exact roof.
2. **Enter two numbers.** Monthly units (or your bill in ₹) and free roof area, plus on-grid or off-grid.
3. **Get a clear answer in seconds.**
   - System size, yearly generation and **kWh per kWp**
   - Gross cost, PM Surya Ghar subsidy and net cost
   - Bill before and after, yearly savings, **payback years** and **CO₂ avoided**
   - A **PVGIS simulation for that exact roof**: monthly chart, best tilt and facing, loss breakdown, cost per unit (LCOE) and, for off-grid homes, battery performance
4. **Go deeper in the Solar lab.** Seven PVGIS tools (grid-connected, tracking, off-grid, monthly radiation, daily profile, hourly series, typical year) with charts and CSV download.

**Accuracy:** on 8 Oct 2026 every Solar lab tool was checked against the official [PVGIS site](https://re.jrc.ec.europa.eu/pvg_tools/en/tools.html) with identical inputs, and the results match exactly. For example, Kolkata (22.601, 88.404), 1 kWp at 35° gives 1408.07 kWh/yr and −25.27 % total loss on both.

---

## Architecture: where AWS fits

```
 Browser (GitHub Pages)                     AWS  ·  ap-south-1 (Mumbai)
 ┌──────────────────────────┐   POST JSON   ┌──────────────────────────────┐
 │ 3D globe (Cesium)        │ ────────────► │ Amazon API Gateway (HTTP API)│
 │ search + calculator UI   │               │  /calculate   /pvgis         │
 │ Solar lab (7 PVGIS tools)│ ◄──────────── │  throttled 10 req/s, CORS    │
 └──────────────────────────┘               └──────────────┬───────────────┘
                                                           ▼
                                            ┌──────────────────────────────┐
                                            │ AWS Lambda (Python 3.12, arm)│
                                            │ validate → calculate() →     │
                                            │ PVGIS simulation             │
                                            └───────┬──────────────┬───────┘
                                       cache hit    ▼              ▼  cache miss
                                  ┌────────────────────┐   PVGIS (EU JRC) /
                                  │ Amazon S3 (private,│   NASA POWER APIs
                                  │ encrypted cache)   │   → stored in S3
                                  └────────────────────┘
     Logs: Amazon CloudWatch (14 days) · Infrastructure as code: AWS SAM / CloudFormation (template.yaml)
```

**Why AWS is needed.** PVGIS, the EU's solar simulation service, doesn't accept calls from web browsers. The detailed simulation and the Solar lab therefore have to run on a server:

| AWS service | Job |
|---|---|
| **AWS Lambda** | Validates every request, runs `calculate()` and the PVGIS simulation on demand. No idle cost. |
| **Amazon API Gateway** | Public HTTPS endpoint, CORS allow-list so only our site can call it, throttling. |
| **Amazon S3** | Caches each location's solar data, so repeat lookups are instant and PVGIS isn't hit twice. |
| **Amazon CloudWatch** | Logs for errors and PVGIS fallbacks. |
| **AWS SAM / CloudFormation** | The whole stack (`template.yaml`) deploys as one unit. |

If PVGIS is down, the API falls back to the quick estimate instead of failing. The site also shows an instant estimate in the browser (using a bundled PVGIS yield grid for India) while the full simulation loads.

**Live API:** `https://lzgu0g309j.execute-api.ap-south-1.amazonaws.com` (`POST /calculate`, `POST /pvgis`, stack `sunsense`).

---

## Solar calculation

All the maths lives in one tested Python function, `backend/calculator.py → calculate()`, mirrored in the browser for the instant estimate.

| Step | Formula |
|---|---|
| **Size** | `kWp = monthly_units ÷ (30 × peak_sun_hours × 0.8)`, capped at `roof_area ÷ 10`, rounded to 0.05 kW |
| **Generation** | `kWp × peak_sun_hours × 365 × 0.8` (replaced by the PVGIS result when available) |
| **Gross cost** | `kWp × ₹55,000` |
| **Subsidy** (on-grid) | ₹30,000/kW for the first 2 kW + ₹18,000 for the 3rd kW, capped at ₹78,000 |
| **Savings** | bill before − bill after on your state's tariff (net metering; fixed charge stays) |
| **Payback** | `net cost ÷ yearly savings` |
| **CO₂ avoided** | `yearly kWh × 0.82 kg` (CEA grid emission factor) |

**State tariffs:** `frontend/assets/tariffs.json` holds the latest domestic tariff (energy slabs + fixed charge) for 33 of 36 states/UTs, read from each regulator's or DISCOM's tariff order (FY 2025-26 / 2026-27, source link per state). The board is picked automatically from the searched location and can be changed; for the 3 without a readable order (Mizoram, Ladakh, DNH&DD) users enter their own ₹/unit. PM Surya Ghar's 10% higher subsidy for North-East/hill states and islands is applied. The **Grid vs solar** card compares 25 years of spending with bills rising by a chosen % a year, 0.5%/yr panel ageing and 1%/yr upkeep.

**Worked example:** Kolkata 700089, 250 units/month, 30 m² roof, on-grid gives 2.3 kWp, ₹61,100 after subsidy, bill ₹1,245 → ₹0, payback about 4 years, 2.5 t CO₂ avoided a year. The PVGIS simulation gives 3,261 kWh/yr at the best tilt of 28°.

---

## API contract

`POST /calculate`

| Field | Type | Notes |
|---|---|---|
| `pincode` *or* `lat` + `lon` | string (6 digits) / numbers | India only |
| `monthly_units` *or* `monthly_bill` | number (kWh / ₹) | Bill is converted with WBSEDCL slabs |
| `roof_area` | number (m²) | Usable, shade-free area |
| `system_type` | `"on-grid"` \| `"off-grid"` | Default `"on-grid"` |
| `detail` | bool | `true` runs the PVGIS simulation |
| `tilt`, `azimuth`, `battery_kwh` | numbers, optional | Your own panel angle and battery size |

```json
{ "pincode": "700089", "monthly_units": 250, "roof_area": 30, "system_type": "on-grid", "detail": true }
```

Returns `system_kw`, `yearly_generation_kwh`, `cost_before_subsidy`, `subsidy`, `cost_after_subsidy`, `monthly_bill_before/after`, `yearly_savings`, `payback_years`, `co2_saved_kg_per_year`, `assumptions`, and a `pvgis` block (monthly kWh, tilt/azimuth, losses, LCOE; `offgrid` battery stats when relevant).

`POST /pvgis` takes `{ "tool": "grid" | "tracking" | "offgrid" | "monthly" | "daily" | "hourly" | "tmy", "lat", "lon", … }` and returns the normalised PVGIS output for the Solar lab.

---

## Security

**Frontend**
- Content Security Policy: scripts only from this site and the pinned Cesium build; inline scripts allowed only by SHA-256 hash; no plugins, frames, form posts or `<base>` changes. Cesium needs `unsafe-eval` and WebAssembly, so those are allowed.
- Subresource Integrity on Cesium JS/CSS, so a tampered CDN file is refused.
- User text is written with `textContent`, never as HTML. Inputs are capped (address 120 chars, units ≤ 100,000, roof ≤ 10,000 m²).
- Address search is cached and throttled, with India-only results. GPS coordinates go only to our own API.
- The Cesium token is public by design and restricted to this site's URLs.

**Backend**
- 2 KB request limit; strict JSON object; finite, in-range numbers only (NaN, Infinity and booleans rejected); India-only coordinates; 6-digit pincodes.
- CORS allow-list, `nosniff`, `no-store`; error messages never reveal internals.
- API throttling (10 req/s, burst 20). Outbound calls only to PVGIS / NASA POWER / OSM over HTTPS, with size limits.
- S3 cache: private, encrypted, TLS-only, expires after a year. Lambda's role can only get/put cache objects. Logs kept 14 days.
- `backend/tests/test_security.py` fuzzes the API with 3,000 junk payloads and requires that none returns a 500.

---

## Repo layout

```
SunSense/
├── src/app.html            # frontend source (edit this)
├── tools/build_index.py    # src/app.html → frontend/index.html (+ CSP hashes, SRI)
├── frontend/
│   ├── index.html          # built page served by GitHub Pages
│   └── assets/             # pincodes, India PVGIS yield grid, solar potential map
├── backend/
│   ├── calculator.py       # calculate(): pure maths, no AWS
│   ├── handler.py          # Lambda entry point: validation, /calculate, /pvgis
│   ├── pvgis.py            # PVGIS 5.3 client for the simulation and the 7 lab tools
│   ├── data_sources.py     # pincode lookup, NASA POWER, S3/local cache
│   ├── dev_server.py       # local server: site + API on http://localhost:8000
│   └── tests/              # 90 tests (calculator, handler, PVGIS, lab, security fuzz)
├── template.yaml           # AWS SAM: Lambda + HTTP API + S3 + logs
└── deploy.sh               # one-command deploy from AWS CloudShell
```

## Running locally

```bash
python backend/dev_server.py                          # site + API (incl. PVGIS) at http://localhost:8000
cd backend && python -m pytest -q                     # 90 tests
python tools/build_index.py src/app.html frontend/index.html   # after editing the frontend
```

## Deploying to AWS

From AWS CloudShell in `ap-south-1`:

```bash
git clone https://github.com/sagnikvolt/SunSense && cd SunSense && bash deploy.sh
```

`deploy.sh` validates and deploys `template.yaml` with the SAM CLI, prints the `ApiUrl` and runs a smoke test. Put that URL in `LIVE_API` in `src/app.html` and rebuild `index.html`.

*The live stack was created on 8 Oct 2026 through the CloudFormation console from the same template, with the code zip in a private S3 bucket, because CloudShell was still locked on the brand-new account.*

---

## Team

| Member | Contribution |
|---|---|
| **Sagnik Kumar Nath (Roni)** · [@sagnikvolt](https://github.com/sagnikvolt) | Idea, design, frontend, backend, AWS deployment, testing, docs |
| Shuvankar Debnath | Team member |
| Somsuddha Dasgupta | Team member |
| Soumya Tirtha Dhawa | Team member |

## AI tools used

- **Claude (Anthropic)**: coding assistance, testing, documentation and deployment help, as allowed by the hackathon rules.

## License

Copyright © 2026 Sagnik Kumar Nath. **All rights reserved.** The code is public so it can be read and judged. It may not be copied, reused or redistributed without written permission. See [LICENSE](LICENSE).

### Credits
- [CesiumJS](https://cesium.com/platform/cesiumjs/) (Apache-2.0) and Google Photorealistic 3D Tiles via Cesium ion
- [PVGIS](https://joint-research-centre.ec.europa.eu/photovoltaic-geographical-information-system-pvgis_en) API and geospatial data © European Union, 2001–2026 (CC BY 4.0)
- [NASA POWER](https://power.larc.nasa.gov/) solar data
- Search suggestions: [Photon](https://photon.komoot.io) by komoot, data © [OpenStreetMap](https://www.openstreetmap.org/copyright) contributors (ODbL)
- Pincode coordinates from [pincode-lat-long](https://www.npmjs.com/package/pincode-lat-long) (ISC)
