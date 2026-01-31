#!/usr/bin/env python3
"""
NOTAM checker for KFAA flyingphase — checks for operationally significant NOTAMs
at alternate airfields that could affect divert planning.

Uses Nathan's SaudiNOTAM proxy (Render.com) → FAA NOTAM API.
Stdlib only (urllib).
"""

import json
import sys
import os
from urllib.request import urlopen, Request
from urllib.error import URLError
from datetime import datetime, timezone
from typing import List, Dict, Optional, Tuple

PROXY_BASE = "https://saudi-notam-proxy.onrender.com/proxy/faa/notams"
TIMEOUT = 15  # seconds per request

# NOTAM keywords that affect alternate suitability
CRITICAL_KEYWORDS = {
    'runway': ['RWY CLSD', 'RUNWAY CLSD', 'RWY CLOSED', 'RUNWAY CLOSED',
               'RWY NOT AVBL', 'RUNWAY NOT AVBL', 'RWY UNSERVICEABLE'],
    'navaid': ['ILS UNSERVICEABLE', 'ILS U/S', 'ILS NOT AVBL', 'ILS CLSD',
               'VOR UNSERVICEABLE', 'VOR U/S', 'VOR NOT AVBL',
               'TACAN UNSERVICEABLE', 'TACAN U/S', 'TACAN NOT AVBL',
               'NDB UNSERVICEABLE', 'NDB U/S', 'DME U/S', 'DME UNSERVICEABLE',
               'LOC U/S', 'LOC UNSERVICEABLE', 'GP U/S', 'GP UNSERVICEABLE',
               'GLIDEPATH U/S', 'GLIDESLOPE U/S'],
    'lighting': ['LGT U/S', 'LGT UNSERVICEABLE', 'PAPI U/S', 'PAPI UNSERVICEABLE',
                 'ALS U/S', 'ALS UNSERVICEABLE', 'APCH LGT U/S',
                 'RWY LGT U/S', 'RWY EDGE LGT'],
    'airfield': ['AD CLSD', 'AERODROME CLSD', 'AD CLOSED', 'AERODROME CLOSED',
                 'AD NOT AVBL'],
    'comms': ['TWR CLSD', 'TOWER CLSD', 'APP CLSD', 'APPROACH CLSD',
              'FREQ NOT AVBL', 'FREQ U/S']
}

# Severity classification
SEVERITY_MAP = {
    'airfield': 'CRITICAL',   # Airfield closed = unusable alternate
    'runway': 'HIGH',         # Runway closed = may still have other runways
    'navaid': 'HIGH',         # Navaid out = approach may not be available
    'comms': 'MEDIUM',        # Comms issue = still landable
    'lighting': 'LOW'         # Lighting = day ops unaffected
}


def fetch_notams(icao: str, timeout: int = TIMEOUT) -> List[dict]:
    """Fetch NOTAMs for an ICAO code via the proxy."""
    url = f"{PROXY_BASE}?responseFormat=geoJson&icaoLocation={icao}&pageSize=100&pageNum=1"
    
    try:
        req = Request(url, headers={'Accept': 'application/json'})
        with urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode())
            return data.get('items', [])
    except (URLError, json.JSONDecodeError, TimeoutError) as e:
        print(f"  ⚠️ NOTAM fetch failed for {icao}: {e}", file=sys.stderr)
        return []


def is_active(notam_data: dict) -> bool:
    """Check if a NOTAM is currently active."""
    core = notam_data.get('properties', {}).get('coreNOTAMData', {}).get('notam', {})
    
    end_str = core.get('effectiveEnd', '')
    if not end_str:
        return True  # No end date = assume active (PERM)
    
    if end_str.upper() == 'PERM':
        return True
    
    try:
        # Handle ISO format
        end_dt = datetime.fromisoformat(end_str.replace('Z', '+00:00'))
        return datetime.now(timezone.utc) < end_dt
    except (ValueError, TypeError):
        return True  # If we can't parse, assume active


def classify_notam(text: str) -> List[Tuple[str, str, str]]:
    """
    Classify NOTAM text by category and severity.
    Returns list of (category, severity, matched_keyword).
    """
    matches = []
    text_upper = text.upper()
    
    for category, keywords in CRITICAL_KEYWORDS.items():
        for keyword in keywords:
            if keyword in text_upper:
                severity = SEVERITY_MAP[category]
                matches.append((category, severity, keyword))
                break  # One match per category is enough
    
    return matches


def check_notams_for_alternates(icao_list: List[str], 
                                  timeout: int = TIMEOUT) -> Dict[str, dict]:
    """
    Check NOTAMs for a list of alternate airfields.
    
    Returns dict keyed by ICAO with:
    {
        'notam_count': int,
        'critical': [list of critical NOTAMs],
        'warnings': [list of warning NOTAMs],
        'fetch_ok': bool,
        'summary': str
    }
    """
    results = {}
    
    for icao in icao_list:
        raw_notams = fetch_notams(icao, timeout=timeout)
        
        critical = []
        warnings = []
        
        for item in raw_notams:
            if not is_active(item):
                continue
            
            core = item.get('properties', {}).get('coreNOTAMData', {}).get('notam', {})
            text = core.get('text', '')
            number = core.get('number', '?')
            
            # Get formatted text if available
            translations = item.get('properties', {}).get('coreNOTAMData', {}).get('notamTranslation', [])
            formatted = ''
            for t in translations:
                if t.get('type') == 'ICAO':
                    formatted = t.get('formattedText', '')
                    break
            
            classifications = classify_notam(text)
            
            if classifications:
                for category, severity, keyword in classifications:
                    entry = {
                        'number': number,
                        'category': category,
                        'severity': severity,
                        'keyword': keyword,
                        'text': text[:200],
                        'effective_end': core.get('effectiveEnd', 'PERM')
                    }
                    
                    if severity in ('CRITICAL', 'HIGH'):
                        critical.append(entry)
                    else:
                        warnings.append(entry)
        
        # Build summary
        if critical:
            summary = f"⚠️ {len(critical)} critical NOTAM(s)"
        elif warnings:
            summary = f"ℹ️ {len(warnings)} advisory NOTAM(s)"
        else:
            summary = "✅ No significant NOTAMs"
        
        results[icao] = {
            'notam_count': len(raw_notams),
            'active_count': sum(1 for item in raw_notams if is_active(item)),
            'critical': critical,
            'warnings': warnings,
            'fetch_ok': True,
            'summary': summary
        }
    
    return results


def format_notam_report(results: Dict[str, dict]) -> str:
    """Format NOTAM check results for display."""
    lines = ["📋 NOTAM Check:"]
    
    has_issues = False
    
    for icao, data in results.items():
        if not data['fetch_ok']:
            lines.append(f"  {icao}: ⚠️ Fetch failed")
            continue
        
        if data['critical']:
            has_issues = True
            for n in data['critical']:
                severity_emoji = '🔴' if n['severity'] == 'CRITICAL' else '🟠'
                lines.append(f"  {icao} {severity_emoji} {n['number']}: {n['text'][:100]}")
        
        if data['warnings']:
            for n in data['warnings']:
                lines.append(f"  {icao} 🟡 {n['number']}: {n['text'][:100]}")
    
    if not has_issues:
        lines.append("  ✅ No significant NOTAMs affecting alternates")
    
    return "\n".join(lines)


# CLI interface
if __name__ == '__main__':
    import argparse
    
    parser = argparse.ArgumentParser(description='Check NOTAMs for KFAA alternate airfields')
    parser.add_argument('icaos', nargs='*', default=['OEGS', 'OERK', 'OEDM', 'OEPS', 'OEHL', 'OEAH', 'OEDR'],
                        help='ICAO codes to check (default: all KFAA alternates)')
    parser.add_argument('--json', action='store_true', help='JSON output')
    parser.add_argument('--timeout', type=int, default=TIMEOUT, help='Timeout per request (seconds)')
    
    args = parser.parse_args()
    
    print(f"Checking NOTAMs for: {', '.join(args.icaos)}", file=sys.stderr)
    results = check_notams_for_alternates(args.icaos, timeout=args.timeout)
    
    if args.json:
        print(json.dumps(results, indent=2))
    else:
        print(format_notam_report(results))
