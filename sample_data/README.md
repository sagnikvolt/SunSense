# Sample data

| File | Contents |
|---|---|
| `calculate_requests.json` | Three example `POST /calculate` request bodies (Kolkata pincode, a North 24 Parganas pincode with a bill in ₹ instead of units, and raw coordinates) |
| `calculate_responses.json` | Each request paired with the actual response from `backend/handler.py` (quick estimate, `detail` off) |

The bundled pincode → coordinate table used by the API is `backend/data/pincodes.csv`.

To reproduce the responses locally:

```bash
python backend/dev_server.py
curl -s -X POST http://localhost:8000/api/calculate \
  -H "Content-Type: application/json" \
  -d '{"pincode": "700089", "monthly_units": 250, "roof_area": 40}'
```

Automated test cases are in `backend/tests/` (run `cd backend && python -m pytest -q`).
