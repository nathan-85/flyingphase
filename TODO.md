# TODO - Missing Data & Improvements

**Last Updated**: 2026-01-31

---

## Completed ✅

### v2.3 (2026-01-31)
- [x] **NOTAM via FAA External API** — proper `external-api.faa.gov/notamapi/v1` integration
- [x] GeoJSON response, per-ICAO GET requests (matches iOS `FAA_API` branch)
- [x] XOR-obfuscated credentials embedded in script (works out of the box)
- [x] Env vars `FAA_CLIENT_ID`/`FAA_CLIENT_SECRET` override if set
- [x] OEKF NOTAMs returned correctly (TACAN U/S, ILS 33L U/S)
- [x] NOTAMs affect alternate selection (closed AD/RWY disqualifies)
- [x] NOTAM warnings shown per alternate in output
- [x] High-impact detection: ILS/VOR/DME/TACAN/NDB/Localizer/Glideslope U/S, RWY closed, AD closed, birds
- [x] Classifies NOTAMs: RWY/NAV/AD/AIRSPACE/COM/OBST/GEN
- [x] 82 unit tests

### v2.1 (2026-01-31)
- [x] Bird-Strike Risk Levels (LOP 5-13) — `--bird low|moderate|severe`
- [x] Birds > LOW caps phase at VFR, negates all solo phases
- [x] JSON output includes full alternate details (serialisation fix)
- [x] TAF caching — 30min file cache, `--no-cache` to bypass
- [x] Fallback TAF source with timeout
- [x] Time-windowed TAF parsing — `--sortie-time HHmm`
- [x] CB detection improvements (METAR remarks + TAF periods)
- [x] METAR parse validation — clear error messages per missing field
- [x] Skill renamed to `airfieldphase` for `/airfieldphase` slash command
- [x] LOP PDF and key extracts saved to references/
- [x] Telegram custom command registered

### v2.0 (2026-01-30)
- [x] Comprehensive `airfield_data.json` v2 schema (9 airfields)
- [x] OEGS approach minimums confirmed from Saudi GACA AIP
- [x] OERK ILS minimums confirmed from Saudi GACA AIP
- [x] OEHL and OEAH ILS approaches confirmed to exist
- [x] OEDR elevation corrected (84ft MSL, not ~2000ft)
- [x] OEKK renamed to OESD with ICAO alias system
- [x] Schema v2 compatibility shim (runtime flattening)
- [x] Live TAF fetching for 7/8 alternates
- [x] All 5 phase test cases passing

---

## Awaiting Data 📋

Nathan providing IAP data (Feb 1):

### OEHL (Hail Regional Airport)
- [ ] ILS RWY 18 exact OCA(H) and visibility/RVR
- [ ] VOR RWY 18 exact OCA(H) and visibility
- [ ] RNP approach minimums

### OEAH (Al-Ahsa Airport)
- [ ] ILS RWY 34 exact OCA(H) and visibility/RVR
- [ ] VOR approach confirmation and minimums

### OEKF (Home Field — Military)
- [ ] Official ILS minimums (all 3 ILS approaches: 15L, 33R, 33L)
- [ ] Official VOR/TACAN minimums
- [ ] Confirm IFR phase uses higher of 2 approach visibility values

### OESD (King Saud Air Base — Military)
- [ ] Elevation confirmation
- [ ] Runway heading confirmation
- [ ] Approach minimums

### OEPS (Prince Sultan Air Base — Military)
- [ ] ILS minimums

### OEDR (King Abdulaziz Air Base — Military)
- [ ] ILS minimums
- [ ] VOR minimums

### OEGS (Gassim)
- [ ] VOR RWY 33 minimums (chart exists, not extracted)

---

## Medium-Priority Improvements 🔧

### Fallback TAF Sources
- [ ] Add CheckWX or AVWX as additional fallback
- [ ] Currently: aviationweather.gov primary + retry with timeout

---

## Low-Priority Enhancements 💡

### Briefing PDF Generation
- [ ] PDF output with weather graphics, METAR/TAF, phase summary
- [ ] Professional briefing format for printing

### Historical Weather Analysis
- [ ] Analyze frequency of each phase by month/season
- [ ] Training schedule optimization

### ClawdHub Publishing
- [ ] `clawdhub login` and publish as distributable skill
- [ ] Package with `package_skill.py`

---

## Data Sources 📚

### ✅ Working
- **FAA External API** — NOTAMs via `external-api.faa.gov/notamapi/v1` (GeoJSON, embedded credentials)
- **aviationweather.gov API** — METAR/TAF fetching (with caching + fallback)
- **SkyVector** — runway data, navaids, elevations
- **Saudi GACA AIP** (aimss.sans.com.sa) — partial approach data

### ⚠️ Not Available
- **OEKF METAR** — military, not on public APIs (user input only)
- **OESD METAR/TAF** — empty from aviationweather.gov (both OEKK and OESD)
- **Military approach charts** — OEKF, OESD, OEPS, OEDR (Nathan has access)

---

## Testing ✓

### Automated (82 tests)
- [x] METAR parser — standard, CAVOK, variable wind, AUTO, missing fields
- [x] Wind components — all quadrants, calm, variable, wrap-around
- [x] Phase determination — all 7 phases, boundary conditions
- [x] TAF parser — base period, BECMG, TEMPO, FM groups
- [x] Bird levels — LOW no impact, MODERATE/SEVERE cap at VFR
- [x] NOTAM classification, runway/navaid extraction
- [x] NOTAM alternate impact (AD closed, ILS/VOR outage detection)

### Real-World Validation (Pending)
- [ ] Compare output to actual SOF phase calls
- [ ] Validate divert fuel with flight planning
- [ ] Verify alternate selections match KFAA procedures

---

## Lessons Learned 📝
- **Always check all git branches**, not just main (found `FAA_API` branch with proper integration)
- FAA External API requires `client_id`/`client_secret` — embed obfuscated, don't rely on user setup
- `notams.aim.faa.gov/notamSearch/search` is website scraping, not the proper API
