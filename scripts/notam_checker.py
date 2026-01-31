#!/usr/bin/env python3
"""
NOTAM checker for airfieldphase skill.
Fetches NOTAMs from FAA NOTAM Search and classifies operational impact.

Based on the FAA NOTAM Search API (notams.aim.faa.gov/notamSearch/search).
No authentication required — plain form POST returns JSON.

Usage:
    from notam_checker import check_notams_for_alternates, format_notam_report
    results = check_notams_for_alternates(['OEJD', 'OERK', 'OEGS'], timeout=15)
    print(format_notam_report(results))
"""

import json
import re
import urllib.request
import urllib.error
import urllib.parse
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timezone


# FAA NOTAM Search endpoint (no auth required, form POST)
FAA_NOTAM_SEARCH_URL = "https://notams.aim.faa.gov/notamSearch/search"

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
    (r'\bRWY\b.*\bCLSD\b', 'Runway closed'),
    (r'\bCLSD\b.*\bRWY\b', 'Runway closed'),
    (r'\bAD\b.*\bCLSD\b', 'Aerodrome closed'),
    (r'\bCLSD\b.*\bAD\b', 'Aerodrome closed'),
    (r'\bAERODROME\b.*\bCLSD\b', 'Aerodrome closed'),
    (r'\bGLIDESLOPE\b.*\b(U/S|UNSERVICEABLE|INOP|OTS|OUT OF SERVICE|NOT AVBL)\b', 'Glideslope unserviceable'),
    (r'\bLOCALIZER\b.*\b(U/S|UNSERVICEABLE|INOP|OTS|OUT OF SERVICE|NOT AVBL)\b', 'Localizer unserviceable'),
    (r'\bPAPI\b.*\b(U/S|UNSERVICEABLE|INOP|OTS|OUT OF SERVICE|NOT AVBL)\b', 'PAPI unserviceable'),
    (r'\bFUEL\b.*\b(NOT AVBL|UNAVBL|U/S)\b', 'Fuel not available'),
    (r'\bRADAR\b.*\b(U/S|UNSERVICEABLE|INOP|OTS)\b', 'Radar unserviceable'),
    (r'\bTWR\b.*\b(CLSD|CLOSED)\b', 'Tower closed'),
    (r'\bFIRE\b.*\b(CAT|DOWNGRADE)\b', 'Fire category downgraded'),
    (r'\bBIRD\b', 'Bird activity reported'),
]


def fetch_notams(icaos: List[str], timeout: int = 15) -> Optional[dict]:
    """
    Fetch NOTAMs from FAA NOTAM Search API.
    
    Args:
        icaos: List of ICAO codes to query
        timeout: Request timeout in seconds
    
    Returns:
        Parsed JSON response or None on failure
    """
    # Form parameters matched exactly to the iOS SaudiNOTAM app
    form_data = {
        'searchType': '0',
        'designatorForAccountable': '',
        'designatorsForLocation': ','.join(icaos),
        'latDegrees': '',
        'latMinutes': '0',
        'latSeconds': '0',
        'longDegrees': '',
        'longMinutes': '0',
        'longSeconds': '0',
        'radius': '10',
        'sortColumns': '5 false',
        'sortDirection': 'true',
        'designatorForNotamNumberSearch': '',
        'notamNumber': '',
        'radiusSearchOnDesignator': 'false',
        'radiusSearchDesignator': '',
        'latitudeDirection': 'N',
        'longitudeDirection': 'W',
        'freeFormText': '',
        'flightPathText': '',
        'flightPathDivertAirfields': '',
        'flightPathBuffer': '4',
        'flightPathIncludeNavaids': 'true',
        'flightPathIncludeArtcc': 'false',
        'flightPathIncludeTfr': 'true',
        'flightPathIncludeRegulatory': 'false',
        'flightPathResultsType': 'All NOTAMs',
        'archiveDate': '',
        'archiveDesignator': '',
        'offset': '0',
        'notamsOnly': 'false',
        'filters': '',
        'minRunwayLength': '',
        'minRunwayWidth': '',
        'runwaySurfaceTypes': '',
        'predefinedAbraka': '',
        'predefinedDabra': '',
        'flightPathAddlBuffer': '',
    }
    
    encoded = urllib.parse.urlencode(form_data).encode('utf-8')
    
    req = urllib.request.Request(
        FAA_NOTAM_SEARCH_URL,
        data=encoded,
        method='POST',
        headers={
            'Content-Type': 'application/x-www-form-urlencoded; charset=utf-8',
            'Accept': 'application/json, text/plain, */*',
            'Referer': 'https://notams.aim.faa.gov/notamSearch/nsapp.html',
            'Origin': 'https://notams.aim.faa.gov',
            'User-Agent': 'Mozilla/5.0 (compatible; airfieldphase/1.0)',
        }
    )
    
    all_notams = []
    offset = 0
    total_count = None
    
    while True:
        form_data['offset'] = str(offset)
        encoded = urllib.parse.urlencode(form_data).encode('utf-8')
        req = urllib.request.Request(
            FAA_NOTAM_SEARCH_URL,
            data=encoded,
            method='POST',
            headers={
                'Content-Type': 'application/x-www-form-urlencoded; charset=utf-8',
                'Accept': 'application/json, text/plain, */*',
                'Referer': 'https://notams.aim.faa.gov/notamSearch/nsapp.html',
                'Origin': 'https://notams.aim.faa.gov',
                'User-Agent': 'Mozilla/5.0 (compatible; airfieldphase/1.0)',
            }
        )
        
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode('utf-8'))
        except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, 
                TimeoutError, OSError) as e:
            if offset == 0:
                return None  # First page failed
            break  # Partial results
        
        notam_list = data.get('notamList', [])
        if not notam_list:
            break
        
        all_notams.extend(notam_list)
        
        end_record = data.get('endRecordCount', 0)
        if total_count is None:
            total_count = data.get('totalNotamCount', 0)
        
        if end_record >= total_count or end_record == 0:
            break
        
        offset = end_record
    
    return {
        'notamList': all_notams,
        'totalNotamCount': total_count or len(all_notams)
    }


def classify_notam(notam_text: str) -> Tuple[str, List[str]]:
    """
    Classify a NOTAM by category and identify high-impact items.
    
    Returns:
        (category, list_of_high_impact_descriptions)
    """
    upper_text = notam_text.upper()
    
    # Determine category — NAV takes priority when navaids are the subject
    # (even if RWY appears as part of "ILS RWY 34")
    best_category = 'GEN'
    best_score = 0
    category_scores = {}
    
    for category, patterns in CATEGORY_PATTERNS.items():
        score = sum(1 for p in patterns if re.search(p, upper_text))
        category_scores[category] = score
        if score > best_score:
            best_score = score
            best_category = category
    
    # NAV priority: if NAV navaids are the subject (ILS/VOR/DME U/S), prefer NAV over RWY
    # "ILS RWY 34 U/S" is a NAV notam, not a RWY notam
    if (category_scores.get('NAV', 0) > 0 and best_category == 'RWY' 
            and any(re.search(p, upper_text) for p in [
                r'\bILS\b', r'\bVOR\b', r'\bDME\b', r'\bNDB\b', r'\bTACAN\b',
                r'\bGLIDESLOPE\b', r'\bLOCALIZER\b'])):
        # Check if it's about the navaid being unserviceable/inoperative
        if re.search(r'\b(U/S|UNSERVICEABLE|INOP|OUT OF SERVICE|OTS|NOT AVBL)\b', upper_text):
            best_category = 'NAV'
    
    # Check high-impact patterns
    impacts = []
    for pattern, description in HIGH_IMPACT_PATTERNS:
        if re.search(pattern, upper_text):
            impacts.append(description)
    
    return best_category, impacts


def is_notam_current(notam: dict) -> bool:
    """Check if NOTAM is current or upcoming (within 24h) based on dates.
    
    The FAA API already filters for relevant NOTAMs, so we only exclude
    clearly expired ones. Upcoming NOTAMs (starting within 24h) are included
    since they're operationally relevant for flight planning.
    """
    now = datetime.now(timezone.utc)
    from datetime import timedelta
    lookahead = now + timedelta(hours=24)
    
    end_str = notam.get('endDate', '')
    
    # Parse "MM/DD/YYYY HHmm" format
    def parse_faa_date(s):
        if not s or s == 'PERM':
            return None
        try:
            return datetime.strptime(s, '%m/%d/%Y %H%M').replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            return None
    
    end = parse_faa_date(end_str)
    
    # Only exclude clearly expired NOTAMs
    if end and end < now:
        return False
    
    return True


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
                                 include_oekf: bool = True) -> dict:
    """
    Fetch and analyze NOTAMs for airfields.
    
    Args:
        icaos: List of alternate ICAO codes
        timeout: Fetch timeout
        include_oekf: Also check OEKF NOTAMs
    
    Returns:
        Dict with per-airfield NOTAM analysis
    """
    all_icaos = list(icaos)
    if include_oekf and 'OEKF' not in all_icaos:
        all_icaos.insert(0, 'OEKF')
    
    raw = fetch_notams(all_icaos, timeout=timeout)
    
    if raw is None:
        return {
            'status': 'error',
            'message': 'Failed to fetch NOTAMs from FAA',
            'airfields': {}
        }
    
    notam_list = raw.get('notamList', [])
    total = raw.get('totalNotamCount', 0)
    
    # Organize by airfield
    airfield_notams: Dict[str, list] = {icao: [] for icao in all_icaos}
    
    for notam in notam_list:
        facility = notam.get('facilityDesignator', '').upper()
        notam_num = notam.get('notamNumber', 'UNKNOWN')
        icao_msg = notam.get('icaoMessage', '') or notam.get('traditionalMessage', '') or ''
        
        if not is_notam_current(notam):
            continue
        
        category, impacts = classify_notam(icao_msg)
        affected_rwy = extract_affected_runway(icao_msg)
        affected_nav = extract_affected_navaid(icao_msg)
        
        parsed = {
            'number': notam_num,
            'text': icao_msg.strip(),
            'category': category,
            'impacts': impacts,
            'high_impact': len(impacts) > 0,
            'affected_runway': affected_rwy,
            'affected_navaid': affected_nav,
            'start': notam.get('startDate', ''),
            'end': notam.get('endDate', 'PERM'),
        }
        
        # Assign to facility
        if facility in airfield_notams:
            airfield_notams[facility].append(parsed)
        else:
            # Try to match by ICAO in the message text
            for icao in all_icaos:
                if icao in icao_msg.upper():
                    airfield_notams[icao].append(parsed)
                    break
    
    # Build per-airfield summary
    results = {
        'status': 'ok',
        'total_fetched': total,
        'fetch_time_utc': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'airfields': {}
    }
    
    for icao in all_icaos:
        notams = airfield_notams.get(icao, [])
        high_impact = [n for n in notams if n['high_impact']]
        
        # Determine overall impact
        ad_closed = any('Aerodrome closed' in n['impacts'] for n in notams)
        rwy_closed = [n['affected_runway'] for n in notams 
                      if 'Runway closed' in n['impacts'] and n['affected_runway']]
        nav_outages = [n for n in notams if n['category'] == 'NAV' and n['high_impact']]
        bird_notams = [n for n in notams if 'Bird activity reported' in n['impacts']]
        
        results['airfields'][icao] = {
            'total_notams': len(notams),
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
    lines.append("📋 NOTAM CHECK")
    lines.append(f"  Fetched: {results.get('total_fetched', 0)} NOTAMs")
    lines.append(f"  Time: {results.get('fetch_time_utc', 'N/A')}")
    lines.append("-" * 55)
    
    airfields = results.get('airfields', {})
    
    for icao, data in airfields.items():
        total = data.get('total_notams', 0)
        hi = data.get('high_impact_count', 0)
        summary = data.get('summary', {})
        
        # Status indicator
        if summary.get('aerodrome_closed'):
            status = '🔴 CLOSED'
        elif hi > 0:
            status = f'🟡 {hi} HIGH-IMPACT'
        elif total > 0:
            status = f'🟢 {total} active'
        else:
            status = '⚪ No NOTAMs'
        
        lines.append(f"\n  {icao}: {status}")
        
        # Aerodrome closed
        if summary.get('aerodrome_closed'):
            lines.append(f"    ‼️  AERODROME CLOSED")
        
        # Closed runways
        for rwy in summary.get('closed_runways', []):
            lines.append(f"    ⚠️  RWY {rwy} CLOSED")
        
        # Navaid outages
        for outage in summary.get('navaid_outages', []):
            lines.append(f"    ⚠️  {outage}")
        
        # Bird activity
        if summary.get('bird_activity'):
            lines.append(f"    🐦 Bird activity reported")
        
        # Category breakdown (compact)
        cats = summary.get('category_counts', {})
        if cats:
            cat_parts = [f"{cat}:{cnt}" for cat, cnt in sorted(cats.items())]
            lines.append(f"    [{' '.join(cat_parts)}]")
        
        # List high-impact NOTAMs briefly
        high_impact_notams = [n for n in data.get('notams', []) if n['high_impact']]
        for n in high_impact_notams[:5]:  # Cap at 5 per airfield
            impacts_str = '; '.join(n['impacts'])
            num = n.get('number', '?')
            lines.append(f"    • {num}: {impacts_str}")
            # Show end date for ops awareness
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
    
    Returns:
        {
            'suitable': True/False (False if AD closed),
            'ils_available': True/False,
            'vor_available': True/False,
            'closed_runways': ['15/33', ...],
            'warnings': ['ILS RWY 15 U/S', ...],
            'bird_activity': True/False
        }
    """
    airfield = results.get('airfields', {}).get(icao)
    if not airfield:
        return {
            'suitable': True,  # No data = assume ok
            'ils_available': True,
            'vor_available': True,
            'closed_runways': [],
            'warnings': [],
            'bird_activity': False
        }
    
    summary = airfield.get('summary', {})
    
    # Check navaid availability from all high-impact NOTAMs
    ils_available = True
    vor_available = True
    warnings = []
    for n in airfield.get('notams', []):
        if n['high_impact']:
            warnings.extend(n['impacts'])
            for impact in n['impacts']:
                upper_impact = impact.upper()
                if 'ILS' in upper_impact and 'UNSERVICEABLE' in upper_impact:
                    ils_available = False
                if 'LOCALIZER' in upper_impact and 'UNSERVICEABLE' in upper_impact:
                    ils_available = False
                if 'GLIDESLOPE' in upper_impact and 'UNSERVICEABLE' in upper_impact:
                    ils_available = False  # GS out = ILS degraded
                if 'VOR' in upper_impact and 'UNSERVICEABLE' in upper_impact:
                    vor_available = False
    
    return {
        'suitable': not summary.get('aerodrome_closed', False),
        'ils_available': ils_available,
        'vor_available': vor_available,
        'closed_runways': summary.get('closed_runways', []),
        'warnings': warnings,
        'bird_activity': summary.get('bird_activity', False)
    }


# CLI for standalone testing
if __name__ == '__main__':
    import argparse
    
    parser = argparse.ArgumentParser(description='Check NOTAMs for Saudi airfields')
    parser.add_argument('icaos', nargs='*', 
                        default=['OEKF', 'OEJD', 'OERK', 'OEGS', 'OEHL', 'OEAH', 'OEDR', 'OEPS'],
                        help='ICAO codes to check (default: all KFAA alternates)')
    parser.add_argument('--json', action='store_true', help='JSON output')
    parser.add_argument('--timeout', type=int, default=15, help='Fetch timeout seconds')
    
    args = parser.parse_args()
    
    results = check_notams_for_alternates(args.icaos, timeout=args.timeout, include_oekf=True)
    
    if args.json:
        print(json.dumps(results, indent=2))
    else:
        print(format_notam_report(results))
