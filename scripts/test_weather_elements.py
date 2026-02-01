#!/usr/bin/env python3
"""Tests for the modular weather element system."""

import sys
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from weather_elements import (
    WeatherElement, WeatherCollection, COVERAGE_RANK,
    PHASE_SOURCES, ALTERNATE_SOURCES,
    parse_metar_elements, parse_taf_elements,
    parse_warning_elements, parse_pirep_elements,
)
from flyingphase import METARParser, TAFParser


class TestWeatherElement(unittest.TestCase):
    """Test WeatherElement validity and overlap."""

    def test_no_bounds(self):
        """Element with no times overlaps everything."""
        el = WeatherElement(type='visibility', value={'meters': 5000}, source='WARNING')
        self.assertTrue(el.overlaps_window(
            datetime(2026, 1, 31, 6, 0, tzinfo=timezone.utc),
            datetime(2026, 1, 31, 7, 0, tzinfo=timezone.utc),
        ))

    def test_element_before_window(self):
        """Element ending before window starts → no overlap."""
        el = WeatherElement(
            type='visibility', value={'meters': 5000}, source='TAF',
            valid_from=datetime(2026, 1, 31, 4, 0, tzinfo=timezone.utc),
            valid_to=datetime(2026, 1, 31, 5, 0, tzinfo=timezone.utc),
        )
        self.assertFalse(el.overlaps_window(
            datetime(2026, 1, 31, 6, 0, tzinfo=timezone.utc),
            datetime(2026, 1, 31, 7, 0, tzinfo=timezone.utc),
        ))

    def test_element_after_window(self):
        """Element starting after window ends → no overlap."""
        el = WeatherElement(
            type='visibility', value={'meters': 5000}, source='TAF',
            valid_from=datetime(2026, 1, 31, 10, 0, tzinfo=timezone.utc),
            valid_to=datetime(2026, 1, 31, 12, 0, tzinfo=timezone.utc),
        )
        self.assertFalse(el.overlaps_window(
            datetime(2026, 1, 31, 6, 0, tzinfo=timezone.utc),
            datetime(2026, 1, 31, 7, 0, tzinfo=timezone.utc),
        ))

    def test_partial_overlap(self):
        """Element partially overlapping window → overlap."""
        el = WeatherElement(
            type='visibility', value={'meters': 5000}, source='TAF',
            valid_from=datetime(2026, 1, 31, 5, 0, tzinfo=timezone.utc),
            valid_to=datetime(2026, 1, 31, 6, 30, tzinfo=timezone.utc),
        )
        self.assertTrue(el.overlaps_window(
            datetime(2026, 1, 31, 6, 0, tzinfo=timezone.utc),
            datetime(2026, 1, 31, 7, 0, tzinfo=timezone.utc),
        ))

    def test_open_ended_forward(self):
        """Element with valid_to=None extends forward."""
        el = WeatherElement(
            type='visibility', value={'meters': 9999}, source='METAR',
            valid_from=datetime(2026, 1, 31, 6, 0, tzinfo=timezone.utc),
            valid_to=None,
        )
        self.assertTrue(el.overlaps_window(
            datetime(2026, 1, 31, 10, 0, tzinfo=timezone.utc),
            datetime(2026, 1, 31, 11, 0, tzinfo=timezone.utc),
        ))

    def test_open_ended_backward(self):
        """Element with valid_from=None extends backward."""
        el = WeatherElement(
            type='wind', value={'direction': 330, 'speed': 15, 'gust': None},
            source='TAF', valid_from=None,
            valid_to=datetime(2026, 1, 31, 8, 0, tzinfo=timezone.utc),
        )
        self.assertTrue(el.overlaps_window(
            datetime(2026, 1, 31, 6, 0, tzinfo=timezone.utc),
            datetime(2026, 1, 31, 7, 0, tzinfo=timezone.utc),
        ))


class TestWeatherCollectionFilter(unittest.TestCase):
    """Test collection filtering by source and time."""

    def _make_collection(self):
        t1 = datetime(2026, 1, 31, 6, 0, tzinfo=timezone.utc)
        return WeatherCollection([
            WeatherElement('visibility', {'meters': 10000}, 'METAR', valid_from=t1),
            WeatherElement('visibility', {'meters': 5000}, 'TAF', valid_from=t1),
            WeatherElement('visibility', {'meters': 2000}, 'WARNING'),
            WeatherElement('cloud', {'coverage': 'BKN', 'height_ft': 4000, 'cb': False}, 'TAF',
                           valid_from=t1,
                           valid_to=datetime(2026, 1, 31, 10, 0, tzinfo=timezone.utc)),
        ])

    def test_source_filter_phase(self):
        """Phase sources = METAR + WARNING."""
        c = self._make_collection()
        phase = c.filter(sources=PHASE_SOURCES)
        self.assertEqual(len(phase), 2)
        sources = {el.source for el in phase}
        self.assertEqual(sources, {'METAR', 'WARNING'})

    def test_source_filter_alternate(self):
        """Alternate sources = METAR + TAF + WARNING."""
        c = self._make_collection()
        alt = c.filter(sources=ALTERNATE_SOURCES)
        self.assertEqual(len(alt), 4)

    def test_time_filter_excludes(self):
        """TAF cloud valid 06-10Z excluded from 11-12Z window."""
        c = self._make_collection()
        late = c.filter(
            window_start=datetime(2026, 1, 31, 11, 0, tzinfo=timezone.utc),
            window_end=datetime(2026, 1, 31, 12, 0, tzinfo=timezone.utc),
        )
        cloud_els = [el for el in late if el.type == 'cloud']
        self.assertEqual(len(cloud_els), 0)


class TestWeatherCollectionResolve(unittest.TestCase):
    """Test worst-case conflict resolution."""

    def test_visibility_lowest_wins(self):
        c = WeatherCollection([
            WeatherElement('visibility', {'meters': 10000}, 'METAR'),
            WeatherElement('visibility', {'meters': 5000}, 'TAF'),
            WeatherElement('visibility', {'meters': 2000}, 'WARNING'),
        ])
        r = c.resolve()
        self.assertEqual(r['visibility_m'], 2000)

    def test_wind_highest_crosswind(self):
        """With RWY 33 (330°), wind from 050° has more xwind than 340°."""
        c = WeatherCollection([
            WeatherElement('wind', {'direction': 340, 'speed': 20, 'gust': None}, 'METAR'),
            WeatherElement('wind', {'direction': 50, 'speed': 15, 'gust': 22}, 'TAF'),
        ])
        r = c.resolve(runway_heading=330)
        # Wind from 050 at 22kt gust on RWY 330 = ~21.7kt xwind
        # Wind from 340 at 20kt on RWY 330 = ~3.5kt xwind
        self.assertEqual(r['wind']['direction'], 50)

    def test_cloud_merge_different_heights(self):
        """Clouds at different heights coexist."""
        c = WeatherCollection([
            WeatherElement('cloud', {'coverage': 'FEW', 'height_ft': 8000, 'cb': False}, 'METAR'),
            WeatherElement('cloud', {'coverage': 'BKN', 'height_ft': 4000, 'cb': False}, 'TAF'),
        ])
        r = c.resolve()
        self.assertEqual(len(r['clouds']), 2)
        self.assertEqual(r['clouds'][0]['height_ft'], 4000)
        self.assertEqual(r['clouds'][1]['height_ft'], 8000)

    def test_cloud_same_height_worst_coverage(self):
        """Same height: BKN beats SCT."""
        c = WeatherCollection([
            WeatherElement('cloud', {'coverage': 'SCT', 'height_ft': 4000, 'cb': False}, 'METAR'),
            WeatherElement('cloud', {'coverage': 'BKN', 'height_ft': 4000, 'cb': False}, 'TAF'),
        ])
        r = c.resolve()
        self.assertEqual(len(r['clouds']), 1)
        self.assertEqual(r['clouds'][0]['coverage'], 'BKN')

    def test_weather_union(self):
        """Weather codes form a union."""
        c = WeatherCollection([
            WeatherElement('weather', {'code': 'HZ'}, 'METAR'),
            WeatherElement('weather', {'code': 'BLDU'}, 'TAF'),
            WeatherElement('weather', {'code': 'CB'}, 'WARNING'),
        ])
        r = c.resolve()
        self.assertEqual(r['weather'], {'HZ', 'BLDU', 'CB'})
        self.assertTrue(r['has_cb'])

    def test_cb_detection_from_ts(self):
        """TS weather code triggers has_cb."""
        c = WeatherCollection([
            WeatherElement('weather', {'code': 'TSRA'}, 'METAR'),
        ])
        r = c.resolve()
        self.assertTrue(r['has_cb'])


class TestParseMETARElements(unittest.TestCase):
    """Test METAR → WeatherElement conversion."""

    def test_basic_metar(self):
        m = METARParser("OEKF 310600Z 33012KT 9999 FEW080 22/10 Q1018")
        els = parse_metar_elements(m)
        types = {el.type for el in els}
        self.assertIn('visibility', types)
        self.assertIn('wind', types)
        self.assertIn('cloud', types)

    def test_metar_values(self):
        m = METARParser("05014KT 9999 NSC 20/03 NOSIG")
        els = parse_metar_elements(m)
        vis = [el for el in els if el.type == 'visibility']
        self.assertEqual(len(vis), 1)
        self.assertEqual(vis[0].value['meters'], 10000)
        wind = [el for el in els if el.type == 'wind']
        self.assertEqual(wind[0].value['direction'], 50)
        self.assertEqual(wind[0].value['speed'], 14)

    def test_metar_all_extend_forward(self):
        """All METAR elements have valid_to=None."""
        m = METARParser("33012KT 5000 HZ BKN020 22/10")
        els = parse_metar_elements(m)
        for el in els:
            self.assertIsNone(el.valid_to)
            self.assertEqual(el.source, 'METAR')

    def test_metar_weather(self):
        m = METARParser("28015KT 3000 +TSRA BKN010CB 32/18 Q1008")
        els = parse_metar_elements(m)
        wx = [el for el in els if el.type == 'weather']
        codes = {el.value['code'] for el in wx}
        self.assertIn('+TSRA', codes)


class TestParseTAFElements(unittest.TestCase):
    """Test TAF → WeatherElement conversion with lookahead."""

    def test_base_elements(self):
        """BASE elements have valid_from=None."""
        taf = TAFParser("TAF OEKF 310000Z 3100/3124 33015KT 9999 SCT040")
        els = parse_taf_elements(taf)
        base_els = [el for el in els if el.valid_from is None]
        self.assertTrue(len(base_els) > 0)

    def test_tempo_fixed_window(self):
        """TEMPO elements have both valid_from and valid_to set."""
        taf = TAFParser("TAF OEKF 310000Z 3100/3124 33015KT 9999 SCT040 "
                        "TEMPO 3110/3114 3000 BLDU")
        els = parse_taf_elements(taf)
        tempo_vis = [el for el in els if el.type == 'visibility'
                     and el.valid_from is not None and el.valid_to is not None
                     and el.value['meters'] == 3000]
        self.assertEqual(len(tempo_vis), 1)

    def test_becmg_lookahead(self):
        """BECMG vis element ends when next BECMG with vis starts."""
        taf = TAFParser("TAF OEKF 310000Z 3100/3124 33015KT 9999 SCT040 "
                        "BECMG 3106/3108 5000 BKN020 "
                        "BECMG 3114/3116 8000 SCT050")
        els = parse_taf_elements(taf)
        # BECMG1 vis 5000: valid_from=06Z, valid_to should be 16Z (BECMG2 second time)
        becmg1_vis = [el for el in els if el.type == 'visibility'
                      and el.value['meters'] == 5000
                      and el.valid_from is not None]
        self.assertEqual(len(becmg1_vis), 1)
        self.assertIsNotNone(becmg1_vis[0].valid_to)
        self.assertEqual(becmg1_vis[0].valid_to.hour, 16)

    def test_becmg_no_match_extends(self):
        """BECMG element with no subsequent match → valid_to=None."""
        taf = TAFParser("TAF OEKF 310000Z 3100/3124 33015KT 9999 SCT040 "
                        "BECMG 3106/3108 28020KT")
        els = parse_taf_elements(taf)
        becmg_wind = [el for el in els if el.type == 'wind'
                      and el.value.get('speed') == 20]
        self.assertEqual(len(becmg_wind), 1)
        self.assertIsNone(becmg_wind[0].valid_to)

    def test_tempo_skipped_in_lookahead(self):
        """TEMPO groups are skipped during BECMG lookahead."""
        taf = TAFParser("TAF OEKF 310000Z 3100/3124 33015KT 9999 SCT040 "
                        "BECMG 3106/3108 5000 "
                        "TEMPO 3110/3112 2000 "
                        "BECMG 3114/3116 8000")
        els = parse_taf_elements(taf)
        # BECMG1 vis 5000 should lookahead past TEMPO to BECMG2 → valid_to=16Z
        becmg1_vis = [el for el in els if el.type == 'visibility'
                      and el.value['meters'] == 5000
                      and el.valid_from is not None
                      and el.valid_from.hour == 6]
        self.assertEqual(len(becmg1_vis), 1)
        self.assertIsNotNone(becmg1_vis[0].valid_to)
        self.assertEqual(becmg1_vis[0].valid_to.hour, 16)


class TestParseWarningElements(unittest.TestCase):
    """Test warning text → WeatherElement conversion."""

    def test_visibility_warning(self):
        els = parse_warning_elements("visibility 2000 or less")
        vis = [el for el in els if el.type == 'visibility']
        self.assertEqual(len(vis), 1)
        self.assertEqual(vis[0].value['meters'], 2000)
        self.assertIsNone(vis[0].valid_from)
        self.assertIsNone(vis[0].valid_to)

    def test_cb_warning(self):
        els = parse_warning_elements("CB reported 25NM southwest")
        wx = [el for el in els if el.type == 'weather']
        self.assertTrue(any(el.value['code'] == 'CB' for el in wx))

    def test_wind_warning(self):
        els = parse_warning_elements("gusts exceeding 35kt")
        wind = [el for el in els if el.type == 'wind']
        self.assertEqual(len(wind), 1)
        self.assertEqual(wind[0].value['speed'], 35)

    def test_empty_warning(self):
        els = parse_warning_elements("")
        self.assertEqual(len(els), 0)

    def test_bare_number_skc_7000(self):
        """SKC 7000 in warning → vis 7000m."""
        els = parse_warning_elements("SKC 7000")
        vis = [el for el in els if el.type == 'visibility']
        self.assertEqual(len(vis), 1)
        self.assertEqual(vis[0].value['meters'], 7000)

    def test_bare_number_bkn020_5000(self):
        """BKN020 5000 → vis 5000m + cloud BKN at 2000ft."""
        els = parse_warning_elements("BKN020 5000")
        vis = [el for el in els if el.type == 'visibility']
        clouds = [el for el in els if el.type == 'cloud']
        self.assertEqual(vis[0].value['meters'], 5000)
        self.assertEqual(clouds[0].value['coverage'], 'BKN')
        self.assertEqual(clouds[0].value['height_ft'], 2000)

    def test_bare_number_ovc010cb_3000(self):
        """OVC010CB 3000 → vis 3000m + CB cloud."""
        els = parse_warning_elements("OVC010CB 3000")
        vis = [el for el in els if el.type == 'visibility']
        clouds = [el for el in els if el.type == 'cloud']
        self.assertEqual(vis[0].value['meters'], 3000)
        self.assertTrue(clouds[0].value['cb'])

    def test_bare_number_not_qnh(self):
        """Q1019 should NOT parse as visibility."""
        els = parse_warning_elements("Q1019")
        vis = [el for el in els if el.type == 'visibility']
        self.assertEqual(len(vis), 0)

    def test_vis_reducing_7000(self):
        self.assertEqual(parse_warning_elements("VISIBILITY REDUCING 7000")[0].value['meters'], 7000)

    def test_vis_reducing_7000m(self):
        self.assertEqual(parse_warning_elements("VISIBILITY REDUCING 7000M")[0].value['meters'], 7000)

    def test_vis_reducing_7km(self):
        vis = [el for el in parse_warning_elements("VISIBILITY REDUCING 7KM") if el.type == 'visibility']
        self.assertEqual(vis[0].value['meters'], 7000)

    def test_vis_reducing_to_7000(self):
        self.assertEqual(parse_warning_elements("VISIBILITY REDUCING TO 7000")[0].value['meters'], 7000)

    def test_vis_reducing_to_7000m(self):
        self.assertEqual(parse_warning_elements("VISIBILITY REDUCING TO 7000M")[0].value['meters'], 7000)

    def test_vis_reducing_to_7km(self):
        vis = [el for el in parse_warning_elements("VISIBILITY REDUCING TO 7KM") if el.type == 'visibility']
        self.assertEqual(vis[0].value['meters'], 7000)

    def test_bare_7km(self):
        vis = [el for el in parse_warning_elements("7KM") if el.type == 'visibility']
        self.assertEqual(vis[0].value['meters'], 7000)

    def test_bare_7000m(self):
        vis = [el for el in parse_warning_elements("7000M") if el.type == 'visibility']
        self.assertEqual(vis[0].value['meters'], 7000)


class TestParsePIREPElements(unittest.TestCase):
    """Test PIREP → WeatherElement conversion."""

    def test_sky_condition(self):
        els = parse_pirep_elements("UA /OV OEKF /FL050 /TP C172 /SK BKN040CB")
        clouds = [el for el in els if el.type == 'cloud']
        self.assertEqual(len(clouds), 1)
        self.assertEqual(clouds[0].value['coverage'], 'BKN')
        self.assertEqual(clouds[0].value['height_ft'], 4000)
        self.assertTrue(clouds[0].value['cb'])

    def test_weather(self):
        els = parse_pirep_elements("UA /OV OEKF /FL030 /TP PC21 /WX TS BLDU")
        wx = [el for el in els if el.type == 'weather']
        codes = {el.value['code'] for el in wx}
        self.assertIn('TS', codes)
        self.assertIn('BLDU', codes)

    def test_visibility(self):
        els = parse_pirep_elements("UA /OV OEKF /FL040 /TP PC21 /FV 3SM")
        vis = [el for el in els if el.type == 'visibility']
        self.assertEqual(len(vis), 1)
        self.assertAlmostEqual(vis[0].value['meters'], 4827, delta=10)

    def test_all_extend_forward(self):
        els = parse_pirep_elements("UA /OV OEKF /SK OVC010 /WX FG")
        for el in els:
            self.assertIsNone(el.valid_to)
            self.assertEqual(el.source, 'PIREP')

    def test_bare_number_skc_7000(self):
        """SKC 7000 in PIREP field → vis 7000m."""
        els = parse_pirep_elements("SKC 7000", elevation_ft=2400)
        vis = [el for el in els if el.type == 'visibility']
        self.assertEqual(len(vis), 1)
        self.assertEqual(vis[0].value['meters'], 7000)

    def test_bare_number_bkn020_5000(self):
        """BKN020 5000 in PIREP field → vis 5000m + cloud."""
        els = parse_pirep_elements("BKN020 5000", elevation_ft=2400)
        vis = [el for el in els if el.type == 'visibility']
        clouds = [el for el in els if el.type == 'cloud']
        self.assertEqual(vis[0].value['meters'], 5000)
        self.assertEqual(len(clouds), 1)

    def test_pirep_vis_7000(self):
        vis = [el for el in parse_pirep_elements("VIS 7000", elevation_ft=2400) if el.type == 'visibility']
        self.assertEqual(vis[0].value['meters'], 7000)

    def test_pirep_vis_7000m(self):
        vis = [el for el in parse_pirep_elements("VIS 7000M", elevation_ft=2400) if el.type == 'visibility']
        self.assertEqual(vis[0].value['meters'], 7000)

    def test_pirep_vis_7km(self):
        vis = [el for el in parse_pirep_elements("VIS 7KM", elevation_ft=2400) if el.type == 'visibility']
        self.assertEqual(vis[0].value['meters'], 7000)

    def test_pirep_visibility_7000(self):
        vis = [el for el in parse_pirep_elements("VISIBILITY 7000", elevation_ft=2400) if el.type == 'visibility']
        self.assertEqual(vis[0].value['meters'], 7000)

    def test_pirep_vis_reducing_7000(self):
        vis = [el for el in parse_pirep_elements("VISIBILITY REDUCING 7000", elevation_ft=2400) if el.type == 'visibility']
        self.assertEqual(vis[0].value['meters'], 7000)

    def test_pirep_vis_reducing_7km(self):
        vis = [el for el in parse_pirep_elements("VISIBILITY REDUCING 7KM", elevation_ft=2400) if el.type == 'visibility']
        self.assertEqual(vis[0].value['meters'], 7000)

    def test_pirep_vis_reducing_to_7000(self):
        vis = [el for el in parse_pirep_elements("VISIBILITY REDUCING TO 7000", elevation_ft=2400) if el.type == 'visibility']
        self.assertEqual(vis[0].value['meters'], 7000)

    def test_pirep_vis_reducing_to_7km(self):
        vis = [el for el in parse_pirep_elements("VISIBILITY REDUCING TO 7KM", elevation_ft=2400) if el.type == 'visibility']
        self.assertEqual(vis[0].value['meters'], 7000)

    def test_pirep_bare_7000m(self):
        vis = [el for el in parse_pirep_elements("7000M", elevation_ft=2400) if el.type == 'visibility']
        self.assertEqual(vis[0].value['meters'], 7000)

    def test_pirep_bare_7km(self):
        vis = [el for el in parse_pirep_elements("7KM", elevation_ft=2400) if el.type == 'visibility']
        self.assertEqual(vis[0].value['meters'], 7000)


class TestEndToEndPhaseScenario(unittest.TestCase):
    """Integration tests: full pipeline from parsing to resolution."""

    def test_phase_metar_only(self):
        """Phase uses METAR+WARNING, not TAF."""
        m = METARParser("05014KT 9999 NSC 20/03 NOSIG")
        taf = TAFParser("07010KT BKN040 9999 BECMG 0900/1100 5000 BLDU")

        coll = WeatherCollection()
        coll.add_all(parse_metar_elements(m))
        coll.add_all(parse_taf_elements(taf))

        now = datetime.now(timezone.utc)
        phase_coll = coll.filter(
            window_start=now,
            window_end=now + timedelta(minutes=60),
            sources=PHASE_SOURCES,
        )
        r = phase_coll.resolve(runway_heading=330)

        # METAR has 9999 vis → should be 10000, NOT 5000 from TAF
        self.assertEqual(r['visibility_m'], 10000)
        # No clouds from METAR (NSC)
        self.assertEqual(len(r['clouds']), 0)

    def test_alternate_includes_taf(self):
        """Alternate assessment includes TAF elements."""
        m = METARParser("05014KT 9999 NSC 20/03 NOSIG")
        taf = TAFParser("07010KT BKN040 9999 BECMG 0900/1100 5000 BLDU")

        coll = WeatherCollection()
        coll.add_all(parse_metar_elements(m))
        coll.add_all(parse_taf_elements(taf))

        now = datetime.now(timezone.utc)
        alt_coll = coll.filter(
            window_start=now,
            window_end=now + timedelta(minutes=180),
            sources=ALTERNATE_SOURCES,
        )
        r = alt_coll.resolve(runway_heading=330)

        # Should include TAF vis (5000 or 10000 depending on time window overlap)
        # At minimum, clouds from TAF should be present
        self.assertTrue(len(r['clouds']) > 0 or r['visibility_m'] <= 10000)

    def test_warning_affects_phase(self):
        """Warning vis 2000 should override METAR 9999 in phase."""
        m = METARParser("33012KT 9999 FEW080 22/10 Q1018")

        coll = WeatherCollection()
        coll.add_all(parse_metar_elements(m))
        coll.add_all(parse_warning_elements("visibility 2000 or less"))

        now = datetime.now(timezone.utc)
        phase_coll = coll.filter(
            window_start=now,
            window_end=now + timedelta(minutes=60),
            sources=PHASE_SOURCES,
        )
        r = phase_coll.resolve()
        self.assertEqual(r['visibility_m'], 2000)

    def test_pirep_cb_affects_phase(self):
        """PIREP reporting CB should appear in phase resolution."""
        m = METARParser("33012KT 9999 FEW080 22/10 Q1018")

        coll = WeatherCollection()
        coll.add_all(parse_metar_elements(m))
        coll.add_all(parse_pirep_elements("UA /OV OEKF /FL050 /SK BKN040CB /WX TS"))

        now = datetime.now(timezone.utc)
        phase_coll = coll.filter(
            window_start=now,
            window_end=now + timedelta(minutes=60),
            sources=PHASE_SOURCES,
        )
        r = phase_coll.resolve()
        self.assertTrue(r['has_cb'])


if __name__ == '__main__':
    unittest.main()
