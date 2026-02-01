#!/usr/bin/env python3
"""
NOTAM checker for airfieldphase skill.
Fetches NOTAMs from the FAA External API (external-api.faa.gov/notamapi/v1).

Credentials stored in macOS Keychain under service "faa-notam-api".

Usage:
    from notam_checker import check_notams_for_alternates, format_notam_report
    results = check_notams_for_alternates(['OEKF', 'OEJD', 'OERK'], timeout=15)
    print(format_notam_report(results))
"""

import json
import os
import re
import urllib.request
import urllib.error
import urllib.parse
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timezone, timedelta


# FAA External API
FAA_API_BASE = "https://external-api.faa.gov/notamapi/v1"

# Obfuscated credentials (XOR with key, same scheme as iOS app)
_XOR_KEY = 0x37
_OBF_ID = [85, 15, 84, 84, 15, 85, 4, 85, 3, 6, 4, 3, 3, 1, 2, 85, 14, 6, 6, 82, 85, 2, 84, 5, 2, 86, 14, 83, 86, 15, 14, 81]
_OBF_SECRET = [86, 5, 0, 7, 7, 114, 82, 15, 6, 1, 5, 0, 3, 7, 4, 4, 117, 117, 5, 118, 117, 1, 113, 5, 115, 117, 6, 83, 86, 5, 6, 15]

# NOTAM categories by operational impact
CATEGORY_PATTERNS = {
    'RWY': [
        r'\bRWY\b', r'\bRUNWAY\b', r'\bR/W\b', r'\bTHR\b', r'\bTHRESHOLD\b',
        r'\bCLSD\b.*\bRWY\b', r'\bRWY\b.*\bCLSD\b', r'\bTDZ\b', r'\bPAPI\b',
        r'\bVASI\b', r'\bALS\b', r'\bREIL\b'
    ],
    'NAV': [
        r'\bILS\b', r'\bVOR\b', r'\bDME\b', r'\bNDB\b', r'\bTACAN\b',
        r'\bGLIDESLOPE\b', r'\bLOCALIZER\b', r'\bLOC\b', r'\bGS\b',
        r'\bRNAV\b', r'\bGPS\b', r'\bGNSS\b', r'\bWAAS\b'
    ],
    'AD': [
        r'\bAD\b', r'\bAERODROME\b', r'\bAPT\b', r'\bAIRPORT\b',
        r'\bTWY\b', r'\bTAXIWAY\b', r'\bAPRON\b', r'\bFUEL\b',
        r'\bFIRE\b', r'\bRFF\b', r'\bARFF\b', r'\bSVC\b'
    ],
    'AIRSPACE': [
        r'\bAIRSPACE\b', r'\bTFR\b', r'\bMOA\b', r'\bRESTRICTED\b',
        r'\bPROHIBITED\b', r'\bDANGER\b', r'\bWARNING\b',
        r'\bSUA\b', r'\bFIR\b', r'\bCTR\b', r'\bTMA\b'
    ],
    'COM': [
        r'\bFREQ\b', r'\bTWR\b', r'\bTOWER\b', r'\bAPP\b',
        r'\bAPPROACH\b', r'\bGND\b', r'\bGROUND\b', r'\bATIS\b',
        r'\bCTAF\b', r'\bUNICOM\b', r'\bRADAR\b'
    ],
    'OBST': [
        r'\bOBST\b', r'\bCRANE\b', r'\bTOWER\b.*\bLGT\b', r'\bCONSTRUCTION\b',
        r'\bWIND TURBINE\b', r'\bANTENNA\b', r'\bSTACK\b'
    ]
}

# High-impact patterns that affect ops
HIGH_IMPACT_PATTERNS = [
    (r'\bILS\b.*\b(U/S|UNSERVICEABLE|INOP|OUT OF SERVICE|OTS|NOT AVBL)\b', 'ILS unserviceable'),
    (r'\b(U/S|UNSERVICEABLE|INOP|OUT OF SERVICE|OTS|NOT AVBL)\b.*\bILS\b', 'ILS unserviceable'),
    (r'\bVOR\b.*\b(U/S|UNSERVICEABLE|INOP|OUT OF SERVICE|OTS|NOT AVBL)\b', 'VOR unserviceable'),
    (r'\b(U/S|UNSERVICEABLE|INOP|OUT OF SERVICE|OTS|NOT AVBL)\b.*\bVOR\b', 'VOR unserviceable'),
    (r'\bDME\b.*\b(U/S|UNSERVICEABLE|INOP|OUT OF SERVICE|OTS)\b', 'DME unserviceable'),
    (r'\bTACAN\b.*\b(U/S|UNSERVICEABLE|INOP|OUT OF SERVICE|OTS)\b', 'TACAN unserviceable'),
    (r'\bNDB\b.*\b(U/S|UNSERVICEABLE|INOP|OUT OF SERVICE|OTS)\b', 'NDB unserviceable'),
    (r'\bRWY\b.*\bCLSD\b', 'Runway closed'),
    (r'\bCLSD\b.*\bRWY\b', 'Runway closed'),
    (r'\bAD\b\s+(?!TWY|RWY|TAXI).*\bCLSD\b', 'Aerodrome closed'),
    (r'\bAD\b\s+\bCLSD\b', 'Aerodrome closed'),
    (r'\bCLSD\b.*\bAD\b', 'Aerodrome closed'),
    (r'\bAERODROME\b\s+(?:IS\s+)?(?:CLSD|CLOSED)\b', 'Aerodrome closed'),
    (r'\bAERODROME\b\s+(?!TWY|RWY|TAXI|CAUTION|CUSTOMS|WIND|ALL|C17|FIRE|SEQ).*\bCLSD\b', 'Aerodrome closed'),
    (r'\bGLIDESLOPE\b.*\b(U/S|UNSERVICEABLE|INOP|OTS|OUT OF SERVICE|NOT AVBL)\b', 'Glideslope unserviceable'),
    (r'\bLOCALIZER\b.*\b(U/S|UNSERVICEABLE|INOP|OTS|OUT OF SERVICE|NOT AVBL)\b', 'Localizer unserviceable'),
    (r'\bPAPI\b.*\b(U/S|UNSERVICEABLE|INOP|OTS|OUT OF SERVICE|NOT AVBL)\b', 'PAPI unserviceable'),
    (r'\bFUEL\b.*\b(NOT AVBL|UNAVBL|U/S)\b', 'Fuel not available'),
    (r'\bRADAR\b.*\b(U/S|UNSERVICEABLE|INOP|OTS)\b', 'Radar unserviceable'),
    (r'\bTWR\b.*\b(CLSD|CLOSED)\b', 'Tower closed'),
    (r'\bFIRE\b.*\b(CAT|DOWNGRADE)\b', 'Fire category downgraded'),
    (r'\bBIRD\b', 'Bird activity reported'),
]


def _parse_notam_time(time_str: str) -> Optional[datetime]:
    """Parse a NOTAM effective time string to datetime (UTC)."""
    if not time_str or time_str == 'PERM':
        return None
    # Try ISO format: 2026-02-01T00:00:00.000Z
    for fmt in ('%Y-%m-%dT%H:%M:%S.%fZ', '%Y-%m-%dT%H:%M:%SZ', '%Y-%m-%dT%H:%M:%S'):
        try:
            return datetime.strptime(time_str, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _parse_schedule_window(schedule: str, ref_time: datetime) -> List[Tuple[datetime, datetime]]:
    """
    Parse NOTAM schedule field into active windows for the reference day.
    Examples: '1500-2200', '0300-1500', 'DAILY 0600-1800', 'MON-FRI 0500-1700'
    Returns list of (start_utc, end_utc) tuples for the reference day.
    """
    if not schedule:
        return []

    windows = []
    # Extract HHmm-HHmm patterns
    time_ranges = re.findall(r'(\d{4})-(\d{4})', schedule)
    if not time_ranges:
        return []

    ref_date = ref_time.date()
    for start_hhmm, end_hhmm in time_ranges:
        try:
            sh, sm = int(start_hhmm[:2]), int(start_hhmm[2:])
            eh, em = int(end_hhmm[:2]), int(end_hhmm[2:])
            start = datetime(ref_date.year, ref_date.month, ref_date.day,
                           sh, sm, tzinfo=timezone.utc)
            end = datetime(ref_date.year, ref_date.month, ref_date.day,
                         eh, em, tzinfo=timezone.utc)
            # Handle overnight windows (e.g., 2200-0600)
            if end <= start:
                end += timedelta(days=1)
            windows.append((start, end))
        except (ValueError, IndexError):
            continue

    return windows


def is_notam_active_in_window(notam_data: dict, window_start: datetime,
                               window_end: datetime) -> bool:
    """
    Check if a NOTAM is active (or will be active) within the given time window.

    Logic:
    1. Parse effectiveStart/effectiveEnd
    2. NOTAM must overlap with [window_start, window_end]
    3. If schedule field exists, also check daily schedule windows
    4. PERM end = no expiry
    5. Missing start = assume already active
    """
    start_str = notam_data.get('start', '')
    end_str = notam_data.get('end', '')
    schedule = notam_data.get('schedule', '')

    # Parse effective period
    eff_start = _parse_notam_time(start_str)
    eff_end = _parse_notam_time(end_str)

    # If NOTAM hasn't started yet and won't start within window → inactive
    if eff_start and eff_start > window_end:
        return False

    # If NOTAM has already ended → inactive
    if eff_end and eff_end < window_start:
        return False

    # At this point, the NOTAM's effective period overlaps with our window.
    # If there's a daily schedule, check if any schedule window overlaps.
    if schedule:
        # Check schedule windows for each day in our planning window
        # Start from previous day to catch overnight windows (e.g., 2200-0600)
        current_day = window_start.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)
        end_day = window_end.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)

        has_schedule_overlap = False
        while current_day <= end_day:
            sched_windows = _parse_schedule_window(schedule, current_day)
            for sw_start, sw_end in sched_windows:
                # Check if schedule window overlaps with our planning window
                if sw_start < window_end and sw_end > window_start:
                    has_schedule_overlap = True
                    break
            if has_schedule_overlap:
                break
            current_day += timedelta(days=1)

        return has_schedule_overlap

    # No schedule restriction — NOTAM is active throughout its effective period
    return True


def _deobfuscate(data: list, key: int) -> str:
    """XOR deobfuscate bytes to string."""
    return ''.join(chr(b ^ key) for b in data)


def _get_credentials() -> Tuple[Optional[str], Optional[str]]:
    """Get FAA API credentials. Priority: env vars > embedded."""
    client_id = os.environ.get('FAA_CLIENT_ID') or _deobfuscate(_OBF_ID, _XOR_KEY)
    client_secret = os.environ.get('FAA_CLIENT_SECRET') or _deobfuscate(_OBF_SECRET, _XOR_KEY)
    return client_id, client_secret


def fetch_notams_for_icao(icao: str, client_id: str, client_secret: str,
                           timeout: int = 15) -> Optional[list]:
    """
    Fetch NOTAMs for a single ICAO from the FAA External API.
    Returns list of GeoJSON feature items, or None on failure.
    """
    params = urllib.parse.urlencode({
        'responseFormat': 'geoJson',
        'icaoLocation': icao,
        'pageSize': '1000',
        'pageNum': '1'
    })
    url = f"{FAA_API_BASE}/notams?{params}"

    req = urllib.request.Request(url, method='GET', headers={
        'client_id': client_id,
        'client_secret': client_secret,
        'Accept': 'application/json',
    })

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            return data.get('items', [])
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError,
            TimeoutError, OSError):
        return None


def fetch_notams(icaos: List[str], timeout: int = 15) -> dict:
    """
    Fetch NOTAMs for multiple ICAOs from the FAA External API.
    Makes one request per ICAO (matching the iOS app pattern).
    """
    client_id, client_secret = _get_credentials()
    if not client_id or not client_secret:
        return {
            'status': 'error',
            'message': 'FAA API credentials not found (check keychain service faa-notam-api)'
        }

    all_items = {}
    total = 0

    for icao in icaos:
        items = fetch_notams_for_icao(icao, client_id, client_secret, timeout=timeout)
        if items is not None:
            all_items[icao] = items
            total += len(items)
        else:
            all_items[icao] = []

    return {
        'status': 'ok',
        'items_by_icao': all_items,
        'total': total
    }


def _parse_notam_from_geojson(feature: dict, default_icao: str) -> dict:
    """Parse a single NOTAM from FAA GeoJSON feature format."""
    props = feature.get('properties', {})
    core_data = props.get('coreNOTAMData', {})
    notam = core_data.get('notam', {})

    number = notam.get('number') or notam.get('id') or f"{default_icao}-UNK"
    text = notam.get('text', '')
    icao = notam.get('icaoLocation') or notam.get('location') or default_icao
    start = notam.get('effectiveStart', '')
    end = notam.get('effectiveEnd', '')
    schedule = notam.get('schedule', '')

    category, impacts = classify_notam(text)
    affected_rwy = extract_affected_runway(text)
    affected_nav = extract_affected_navaid(text)

    return {
        'number': number,
        'text': text.strip(),
        'icao': icao,
        'category': category,
        'impacts': impacts,
        'high_impact': len(impacts) > 0,
        'affected_runway': affected_rwy,
        'affected_navaid': affected_nav,
        'start': start,
        'end': end,
        'schedule': schedule,
    }


def classify_notam(notam_text: str) -> Tuple[str, List[str]]:
    """
    Classify a NOTAM by category and identify high-impact items.
    Returns (category, list_of_high_impact_descriptions)
    """
    upper_text = notam_text.upper()

    # Determine category — NAV takes priority when navaids are the subject
    best_category = 'GEN'
    best_score = 0
    category_scores = {}

    for category, patterns in CATEGORY_PATTERNS.items():
        score = sum(1 for p in patterns if re.search(p, upper_text))
        category_scores[category] = score
        if score > best_score:
            best_score = score
            best_category = category

    # NAV priority: "ILS RWY 34 U/S" is a NAV notam, not RWY
    if (category_scores.get('NAV', 0) > 0 and best_category == 'RWY'
            and any(re.search(p, upper_text) for p in [
                r'\bILS\b', r'\bVOR\b', r'\bDME\b', r'\bNDB\b', r'\bTACAN\b',
                r'\bGLIDESLOPE\b', r'\bLOCALIZER\b'])):
        if re.search(r'\b(U/S|UNSERVICEABLE|INOP|OUT OF SERVICE|OTS|NOT AVBL)\b', upper_text):
            best_category = 'NAV'

    # Check high-impact patterns
    impacts = []
    for pattern, description in HIGH_IMPACT_PATTERNS:
        if re.search(pattern, upper_text):
            impacts.append(description)

    return best_category, impacts


def extract_affected_runway(notam_text: str) -> Optional[str]:
    """Extract affected runway from NOTAM text."""
    m = re.search(r'\bRWY\s*(\d{2}[LRC]?(?:/\d{2}[LRC]?)?)\b', notam_text.upper())
    if m:
        return m.group(1)
    return None


def extract_affected_navaid(notam_text: str) -> Optional[str]:
    """Extract affected navaid type from NOTAM text."""
    upper = notam_text.upper()
    for aid in ['ILS', 'VOR', 'DME', 'NDB', 'TACAN', 'LOCALIZER', 'GLIDESLOPE']:
        if aid in upper:
            return aid
    return None


def check_notams_for_alternates(icaos: List[str], timeout: int = 15,
                                 include_oekf: bool = True,
                                 window_hours: float = 3.0) -> dict:
    """
    Fetch and analyze NOTAMs for airfields via FAA External API.
    
    Time filtering:
    - OEKF: NOW window (active right now)
    - Alternates: NOW + window_hours (default 3hr planning window)
    - NOTAMs outside their effective period or daily schedule are excluded
    """
    all_icaos = list(icaos)
    if include_oekf and 'OEKF' not in all_icaos:
        all_icaos.insert(0, 'OEKF')

    raw = fetch_notams(all_icaos, timeout=timeout)

    if raw.get('status') == 'error':
        return {
            'status': 'error',
            'message': raw.get('message', 'Failed to fetch NOTAMs'),
            'airfields': {}
        }

    items_by_icao = raw.get('items_by_icao', {})
    total = raw.get('total', 0)

    now = datetime.now(timezone.utc)
    # OEKF window: current + 1hr (sortie planning horizon)
    oekf_window_end = now + timedelta(hours=min(window_hours, 1.0))
    # Alternate window: now + planning hours (time to divert)
    alt_window_end = now + timedelta(hours=window_hours)

    # Parse and organize by airfield
    results = {
        'status': 'ok',
        'total_fetched': total,
        'fetch_time_utc': now.strftime('%Y-%m-%dT%H:%M:%SZ'),
        'window_hours': window_hours,
        'airfields': {}
    }

    for icao in all_icaos:
        features = items_by_icao.get(icao, [])
        all_notams = [_parse_notam_from_geojson(f, icao) for f in features]

        # Time-filter: OEKF uses tight window, alternates use planning window
        if icao == 'OEKF':
            w_start, w_end = now, oekf_window_end
        else:
            w_start, w_end = now, alt_window_end

        notams = [n for n in all_notams if is_notam_active_in_window(n, w_start, w_end)]
        filtered_count = len(all_notams) - len(notams)

        high_impact = [n for n in notams if n['high_impact']]

        # Determine overall impact
        ad_closed = any('Aerodrome closed' in n['impacts'] for n in notams)
        rwy_closed = [n['affected_runway'] for n in notams
                      if 'Runway closed' in n['impacts'] and n['affected_runway']]
        nav_outages = [n for n in notams if n['category'] == 'NAV' and n['high_impact']]
        bird_notams = [n for n in notams if 'Bird activity reported' in n['impacts']]

        results['airfields'][icao] = {
            'total_notams': len(notams),
            'total_fetched': len(all_notams),
            'filtered_out': filtered_count,
            'high_impact_count': len(high_impact),
            'notams': notams,
            'summary': {
                'aerodrome_closed': ad_closed,
                'closed_runways': rwy_closed,
                'navaid_outages': [
                    f"{n.get('affected_navaid', '?')} — {'; '.join(n['impacts'])}"
                    for n in nav_outages
                ],
                'bird_activity': len(bird_notams) > 0,
                'category_counts': _count_categories(notams),
            }
        }

    return results


def _count_categories(notams: list) -> dict:
    """Count NOTAMs by category."""
    counts = {}
    for n in notams:
        cat = n.get('category', 'GEN')
        counts[cat] = counts.get(cat, 0) + 1
    return counts


def format_notam_report(results: dict) -> str:
    """Format NOTAM results for text output."""
    if results.get('status') == 'error':
        return f"⚠️  NOTAM Check: {results.get('message', 'Failed')}"

    lines = []
    lines.append("=" * 55)
    lines.append("📋 NOTAM CHECK (FAA API)")
    lines.append(f"  Fetched: {results.get('total_fetched', 0)} NOTAMs")
    lines.append(f"  Time: {results.get('fetch_time_utc', 'N/A')}")
    window_hrs = results.get('window_hours', 3.0)
    lines.append(f"  Window: NOW → +{window_hrs:.0f}hr (alternates)")
    lines.append("-" * 55)

    airfields = results.get('airfields', {})

    for icao, data in airfields.items():
        total = data.get('total_notams', 0)
        hi = data.get('high_impact_count', 0)
        filtered = data.get('filtered_out', 0)
        summary = data.get('summary', {})

        # Status indicator
        if summary.get('aerodrome_closed'):
            status = '🔴 CLOSED'
        elif hi > 0:
            status = f'🟡 {hi} HIGH-IMPACT'
        elif total > 0:
            status = f'🟢 {total} active'
        else:
            status = '⚪ No active NOTAMs'

        filter_note = f" ({filtered} outside window)" if filtered > 0 else ""
        lines.append(f"\n  {icao}: {status}{filter_note}")

        if summary.get('aerodrome_closed'):
            lines.append(f"    ‼️  AERODROME CLOSED")

        for rwy in summary.get('closed_runways', []):
            lines.append(f"    ⚠️  RWY {rwy} CLOSED")

        for outage in summary.get('navaid_outages', []):
            lines.append(f"    ⚠️  {outage}")

        if summary.get('bird_activity'):
            lines.append(f"    🐦 Bird activity reported")

        cats = summary.get('category_counts', {})
        if cats:
            cat_parts = [f"{cat}:{cnt}" for cat, cnt in sorted(cats.items())]
            lines.append(f"    [{' '.join(cat_parts)}]")

        high_impact_notams = [n for n in data.get('notams', []) if n['high_impact']]
        for n in high_impact_notams[:5]:
            impacts_str = '; '.join(n['impacts'])
            num = n.get('number', '?')
            lines.append(f"    • {num}: {impacts_str}")
            end = n.get('end', '')
            if end and end != 'PERM':
                lines.append(f"      Until: {end}")

    lines.append("")
    lines.append("=" * 55)

    return '\n'.join(lines)


def get_notam_impact_on_alternate(icao: str, results: dict) -> dict:
    """
    Get the operational impact of NOTAMs on an alternate airfield.
    Used by flyingphase.py to adjust alternate suitability.
    """
    airfield = results.get('airfields', {}).get(icao)
    if not airfield:
        return {
            'suitable': True,
            'ils_available': True,
            'vor_available': True,
            'closed_runways': [],
            'warnings': [],
            'bird_activity': False
        }

    summary = airfield.get('summary', {})

    # Check navaid availability from all high-impact NOTAMs
    # Track ILS components per-runway:
    #   - Glideslope U/S alone = LOC-only approach still flyable (higher minimums)
    #   - Localizer U/S = ILS truly unusable
    #   - ILS explicitly U/S = ILS unusable
    # Per-runway tracking prevents e.g. "ILS RWY 35R U/S" from killing 17R ILS
    global_ils_us = False
    global_loc_available = True
    global_gs_available = True
    vor_available = True
    warnings = []
    runway_status = {}  # {rwy: {'ils': True, 'loc': True, 'gs': True}}

    for n in airfield.get('notams', []):
        if n['high_impact']:
            warnings.extend(n['impacts'])
            rwy = n.get('affected_runway')  # e.g. "17R", "35R", "17R/35L"

            for impact in n['impacts']:
                upper_impact = impact.upper()

                is_loc = 'LOCALIZER' in upper_impact and 'UNSERVICEABLE' in upper_impact
                is_gs = 'GLIDESLOPE' in upper_impact and 'UNSERVICEABLE' in upper_impact
                is_ils = 'ILS' in upper_impact and 'UNSERVICEABLE' in upper_impact \
                    and 'GLIDESLOPE' not in upper_impact and 'LOCALIZER' not in upper_impact

                if rwy:
                    # Per-runway tracking — expand compound runways like "17R/35L"
                    for r in rwy.split('/'):
                        r = r.strip()
                        if r not in runway_status:
                            runway_status[r] = {'ils': True, 'loc': True, 'gs': True}
                        if is_loc:
                            runway_status[r]['loc'] = False
                            runway_status[r]['ils'] = False
                        if is_gs:
                            runway_status[r]['gs'] = False
                        if is_ils:
                            runway_status[r]['ils'] = False
                else:
                    # No runway specified — apply globally
                    if is_loc:
                        global_loc_available = False
                    if is_gs:
                        global_gs_available = False
                    if is_ils:
                        global_ils_us = True

                if 'VOR' in upper_impact and 'UNSERVICEABLE' in upper_impact:
                    vor_available = False

    # Global ILS only truly unavailable if localizer is U/S or ILS explicitly U/S
    global_ils_available = not global_ils_us and global_loc_available

    return {
        'suitable': not summary.get('aerodrome_closed', False),
        'ils_available': global_ils_available,
        'localizer_available': global_loc_available,
        'glideslope_available': global_gs_available,
        'vor_available': vor_available,
        'runway_status': runway_status,
        'closed_runways': summary.get('closed_runways', []),
        'warnings': warnings,
        'bird_activity': summary.get('bird_activity', False)
    }


# CLI for standalone testing
if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='Check NOTAMs for Saudi airfields (FAA API)')
    parser.add_argument('icaos', nargs='*',
                        default=['OEKF', 'OEJD', 'OERK', 'OEGS', 'OEHL', 'OEAH', 'OEDR', 'OEPS'],
                        help='ICAO codes to check (default: all KFAA alternates)')
    parser.add_argument('--json', action='store_true', help='JSON output')
    parser.add_argument('--timeout', type=int, default=15, help='Fetch timeout seconds')
    parser.add_argument('--window', type=float, default=3.0,
                        help='Planning window in hours for alternates (default: 3)')
    parser.add_argument('--no-filter', action='store_true',
                        help='Show all NOTAMs regardless of activation time')

    args = parser.parse_args()

    window = 8760 if args.no_filter else args.window  # 8760h = 1 year = effectively no filter
    results = check_notams_for_alternates(args.icaos, timeout=args.timeout,
                                          include_oekf=True, window_hours=window)

    if args.json:
        print(json.dumps(results, indent=2))
    else:
        print(format_notam_report(results))
