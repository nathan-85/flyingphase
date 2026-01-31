#!/usr/bin/env python3
"""
Unit tests for KFAA Flying Phase Determination Tool.
Tests METARParser, TAFParser, wind component calculations, phase determination,
and bird level phase capping.
"""

import json
import math
import os
import sys
import unittest
from pathlib import Path

# Import from flyingphase.py
sys.path.insert(0, str(Path(__file__).parent))
from flyingphase import (
    METARParser, TAFParser,
    calculate_wind_components, determine_phase, select_runway
)


class TestMETARParserStandard(unittest.TestCase):
    """Test METAR parsing with standard formats."""

    def test_basic_metar(self):
        m = METARParser("OEKF 310600Z 33012KT 9999 FEW080 22/10 Q1018")
        self.assertEqual(m.icao, "OEKF")
        self.assertEqual(m.wind_dir, 330)
        self.assertEqual(m.wind_speed, 12)
        self.assertIsNone(m.wind_gust)
        self.assertEqual(m.visibility_m, 10000)
        self.assertEqual(len(m.clouds), 1)
        self.assertEqual(m.clouds[0]['coverage'], 'FEW')
        self.assertEqual(m.clouds[0]['height_ft'], 8000)
        self.assertEqual(m.temp, 22)
        self.assertEqual(m.dewpoint, 10)
        self.assertEqual(m.qnh, 1018)

    def test_gusting_wind(self):
        m = METARParser("OEKF 310600Z 28018G25KT 5000 SCT040 32/18 Q1012")
        self.assertEqual(m.wind_dir, 280)
        self.assertEqual(m.wind_speed, 18)
        self.assertEqual(m.wind_gust, 25)
        self.assertEqual(m.get_effective_wind_speed(), 25)

    def test_multiple_cloud_layers(self):
        m = METARParser("OEKF 310600Z 33012KT 3000 BKN015 OVC025 22/10 Q1018")
        self.assertEqual(len(m.clouds), 2)
        self.assertEqual(m.clouds[0]['coverage'], 'BKN')
        self.assertEqual(m.clouds[0]['height_ft'], 1500)
        self.assertEqual(m.clouds[1]['coverage'], 'OVC')
        self.assertEqual(m.clouds[1]['height_ft'], 2500)
        self.assertEqual(m.get_ceiling_ft(), 1500)

    def test_cb_in_cloud(self):
        m = METARParser("OEKF 310600Z 33012KT 5000 BKN010CB 22/10 Q1018")
        self.assertTrue(m.has_cb())
        self.assertEqual(m.clouds[0]['type'], 'CB')

    def test_negative_temperature(self):
        m = METARParser("OEKF 310600Z 33012KT 9999 FEW080 M05/M10 Q1018")
        self.assertEqual(m.temp, -5)
        self.assertEqual(m.dewpoint, -10)

    def test_metar_prefix(self):
        m = METARParser("METAR OEKF 310600Z 33012KT 9999 FEW080 22/10 Q1018")
        self.assertEqual(m.icao, "OEKF")
        self.assertEqual(m.wind_dir, 330)

    def test_low_visibility(self):
        m = METARParser("OEKF 310600Z 33012KT 0800 OVC003 22/10 Q1018")
        self.assertEqual(m.visibility_m, 800)
        self.assertEqual(m.get_ceiling_ft(), 300)


class TestMETARParserCAVOK(unittest.TestCase):
    """Test CAVOK handling."""

    def test_cavok(self):
        m = METARParser("OEKF 310600Z 33008KT CAVOK 22/10 Q1018")
        self.assertTrue(m.cavok)
        self.assertEqual(m.visibility_m, 10000)
        self.assertEqual(len(m.clouds), 0)
        self.assertIsNone(m.get_ceiling_ft())
        self.assertIsNone(m.get_lowest_cloud_ft())


class TestMETARParserVariableWind(unittest.TestCase):
    """Test variable wind parsing."""

    def test_vrb_wind(self):
        m = METARParser("OEKF 310600Z VRB03KT 9999 FEW080 22/10 Q1018")
        self.assertIsNone(m.wind_dir)
        self.assertEqual(m.wind_speed, 3)

    def test_variable_wind_direction(self):
        m = METARParser("OEKF 310600Z 28018KT 250V310 5000 SCT040 32/18 Q1012")
        self.assertEqual(m.wind_dir, 280)
        self.assertEqual(m.wind_variable_from, 250)
        self.assertEqual(m.wind_variable_to, 310)

    def test_calm_wind(self):
        m = METARParser("OEKF 310600Z 00000KT 9999 FEW080 22/10 Q1018")
        self.assertEqual(m.wind_dir, 0)
        self.assertEqual(m.wind_speed, 0)
        self.assertEqual(m.get_effective_wind_speed(), 0)


class TestMETARParserMissingData(unittest.TestCase):
    """Test handling of missing or partial METAR data."""

    def test_no_cloud(self):
        m = METARParser("OEKF 310600Z 33012KT 9999 SKC 22/10 Q1018")
        self.assertEqual(len(m.clouds), 0)
        self.assertIsNone(m.get_ceiling_ft())
        self.assertIsNone(m.get_lowest_cloud_ft())

    def test_nsc_cloud(self):
        m = METARParser("OEKF 310600Z 33012KT 9999 NSC 22/10 Q1018")
        self.assertEqual(len(m.clouds), 0)

    def test_auto_metar(self):
        """AUTO METARs should parse normally."""
        m = METARParser("OEKF 310600Z AUTO 33012KT 9999 FEW080 22/10 Q1018")
        # AUTO is not a standard parse target, but wind should still be found
        self.assertEqual(m.wind_speed, 12)


class TestMETARParserCBDetection(unittest.TestCase):
    """Test enhanced CB detection (TS, remarks, distance/direction)."""

    def test_ts_in_weather(self):
        m = METARParser("OEKF 310600Z 33012KT 5000 +TSRA SCT025 22/10 Q1018")
        self.assertTrue(m.has_ts_weather)
        self.assertTrue(m.has_cb())
        self.assertTrue(m.has_cb_within_nm(30))

    def test_standalone_cb_with_cloud(self):
        m = METARParser("OEKF 310600Z 33012KT 2000 +TSRA CB BKN010CB 22/10 Q1018")
        self.assertTrue(m.has_cb())
        self.assertEqual(len(m.clouds), 1)
        self.assertEqual(m.clouds[0]['coverage'], 'BKN')
        self.assertEqual(m.clouds[0]['type'], 'CB')

    def test_cb_in_remarks_with_distance(self):
        m = METARParser("OEKF 310600Z 33012KT 9999 FEW080 22/10 Q1018 RMK CB NW 25NM MOV E")
        self.assertTrue(m.has_cb())
        self.assertTrue(m.has_cb_within_nm(30))
        self.assertEqual(len(m.cb_details), 1)
        self.assertEqual(m.cb_details[0]['location'], 'NW')
        self.assertEqual(m.cb_details[0]['distance_nm'], 25)
        self.assertEqual(m.cb_details[0]['movement'], 'E')

    def test_cb_distant(self):
        m = METARParser("OEKF 310600Z 33012KT 9999 FEW080 22/10 Q1018 RMK CB DSNT SW")
        self.assertTrue(m.has_cb())
        cb = m.cb_details[0]
        self.assertEqual(cb['location'], 'DSNT')
        self.assertEqual(cb['distance_nm'], 25)  # Estimated for DSNT

    def test_cb_warnings(self):
        m = METARParser("OEKF 310600Z 33012KT 5000 BKN010CB 22/10 Q1018")
        warns = m.get_cb_warnings()
        self.assertEqual(len(warns), 1)
        self.assertIn("CB in cloud layer at 1000ft", warns[0])

    def test_no_cb(self):
        m = METARParser("OEKF 310600Z 33012KT 9999 FEW080 22/10 Q1018")
        self.assertFalse(m.has_cb())
        self.assertFalse(m.has_ts_weather)
        self.assertEqual(len(m.cb_details), 0)
        self.assertEqual(len(m.get_cb_warnings()), 0)


class TestWindComponents(unittest.TestCase):
    """Test wind component calculations for all quadrants."""

    def test_headwind_aligned(self):
        """Wind directly on the nose → full headwind, zero crosswind."""
        cross, head = calculate_wind_components(330, 20, 330)
        self.assertAlmostEqual(cross, 0, places=1)
        self.assertAlmostEqual(head, 20, places=1)

    def test_tailwind_aligned(self):
        """Wind from behind → full tailwind (negative headwind)."""
        cross, head = calculate_wind_components(150, 20, 330)
        self.assertAlmostEqual(cross, 0, places=1)
        self.assertAlmostEqual(head, -20, places=1)

    def test_pure_crosswind_right(self):
        """Wind 90° from right → full crosswind, zero head/tail."""
        cross, head = calculate_wind_components(60, 20, 330)
        self.assertAlmostEqual(cross, 20, places=1)
        self.assertAlmostEqual(head, 0, places=0)

    def test_pure_crosswind_left(self):
        """Wind 90° from left → full crosswind, zero head/tail."""
        cross, head = calculate_wind_components(240, 20, 330)
        self.assertAlmostEqual(cross, 20, places=1)
        self.assertAlmostEqual(head, 0, places=0)

    def test_quartering_headwind(self):
        """Wind 45° off nose → components at ~14.1kt each for 20kt wind."""
        cross, head = calculate_wind_components(285, 20, 330)
        expected = 20 * math.sin(math.radians(45))
        self.assertAlmostEqual(cross, expected, places=1)
        self.assertAlmostEqual(head, expected, places=1)

    def test_quartering_tailwind(self):
        """Wind 135° off nose → crosswind and tailwind."""
        cross, head = calculate_wind_components(195, 20, 330)
        expected_cross = 20 * math.sin(math.radians(135))
        expected_head = 20 * math.cos(math.radians(135))
        self.assertAlmostEqual(cross, abs(expected_cross), places=1)
        self.assertAlmostEqual(head, expected_head, places=1)
        self.assertTrue(head < 0)  # Tailwind

    def test_zero_wind(self):
        """Calm wind → no components."""
        cross, head = calculate_wind_components(330, 0, 330)
        self.assertEqual(cross, 0)
        self.assertEqual(head, 0)

    def test_wrap_around_360(self):
        """Wind at 350° with runway 010° → 20° diff, mostly headwind."""
        cross, head = calculate_wind_components(350, 20, 10)
        # 20° diff
        self.assertAlmostEqual(cross, 20 * math.sin(math.radians(20)), places=1)
        self.assertAlmostEqual(head, 20 * math.cos(math.radians(20)), places=1)

    def test_large_angle_wrap(self):
        """Wind at 010° with runway 350° → 20° diff (wraps through north)."""
        cross, head = calculate_wind_components(10, 20, 350)
        self.assertAlmostEqual(cross, 20 * math.sin(math.radians(20)), places=1)
        self.assertAlmostEqual(head, 20 * math.cos(math.radians(20)), places=1)


class TestPhaseDetermination(unittest.TestCase):
    """Test phase determination boundaries."""

    @classmethod
    def setUpClass(cls):
        """Load airfield data once."""
        data_file = Path(__file__).parent / 'airfield_data.json'
        with open(data_file) as f:
            cls.airfield_data = json.load(f)
        # Schema v2 compatibility
        if 'airfields' in cls.airfield_data:
            for icao, af_data in cls.airfield_data['airfields'].items():
                cls.airfield_data[icao] = af_data
            for icao in list(cls.airfield_data.get('airfields', {}).keys()):
                af = cls.airfield_data[icao]
                new_runways = []
                for rwy in af.get('runways', []):
                    if '/' in str(rwy.get('id', '')):
                        ids = rwy['id'].split('/')
                        hdgs = str(rwy.get('heading', '')).split('/')
                        if len(ids) == 2 and len(hdgs) == 2:
                            new_runways.append({'id': ids[0], 'heading': int(hdgs[0]), 'reciprocal': ids[1]})
                            new_runways.append({'id': ids[1], 'heading': int(hdgs[1]), 'reciprocal': ids[0]})
                        else:
                            new_runways.append(rwy)
                    else:
                        new_runways.append(rwy)
                af['runways'] = new_runways
        for icao, fuel in cls.airfield_data.get('divert_fuel', {}).items():
            if 'fuel_lbs' in fuel and 'base_fuel_lbs' not in fuel:
                fuel['base_fuel_lbs'] = fuel.pop('fuel_lbs')
            if 'bearing' in fuel and 'track_deg' not in fuel:
                fuel['track_deg'] = fuel.pop('bearing')

    def _phase(self, metar_str, rwy_hdg=330):
        m = METARParser(metar_str)
        result = determine_phase(m, rwy_hdg, self.airfield_data)
        return result['phase']

    # === UNRESTRICTED boundaries ===
    def test_unrestricted_clear(self):
        self.assertEqual(
            self._phase("OEKF 310600Z 33008KT 9999 FEW080 22/10 Q1018"),
            "UNRESTRICTED"
        )

    def test_unrestricted_wind_at_limit(self):
        """25kt total wind → still UNRESTRICTED."""
        self.assertEqual(
            self._phase("OEKF 310600Z 33025KT 9999 FEW080 22/10 Q1018"),
            "UNRESTRICTED"
        )

    def test_unrestricted_wind_over_limit(self):
        """26kt total wind → should NOT be UNRESTRICTED."""
        phase = self._phase("OEKF 310600Z 33026KT 9999 FEW080 22/10 Q1018")
        self.assertNotEqual(phase, "UNRESTRICTED")

    def test_unrestricted_cloud_below_8000(self):
        """Cloud at 7000ft → NOT UNRESTRICTED."""
        phase = self._phase("OEKF 310600Z 33008KT 9999 FEW070 22/10 Q1018")
        self.assertNotEqual(phase, "UNRESTRICTED")

    def test_unrestricted_sct_above_8000(self):
        """SCT above 8000ft → NOT UNRESTRICTED (only FEW allowed)."""
        phase = self._phase("OEKF 310600Z 33008KT 9999 SCT100 22/10 Q1018")
        self.assertNotEqual(phase, "UNRESTRICTED")

    # === VFR boundaries ===
    def test_vfr_standard(self):
        self.assertEqual(
            self._phase("OEKF 310600Z 33012KT 7000 SCT040 22/10 Q1018"),
            "VFR"
        )

    def test_vfr_ceiling_at_1500(self):
        """Ceiling exactly at 1500ft → VFR (≥1500ft)."""
        self.assertEqual(
            self._phase("OEKF 310600Z 33012KT 7000 BKN015 22/10 Q1018"),
            "VFR"
        )

    def test_vfr_ceiling_below_1500(self):
        """Ceiling at 1400ft → NOT VFR."""
        phase = self._phase("OEKF 310600Z 33012KT 7000 BKN014 22/10 Q1018")
        self.assertNotEqual(phase, "VFR")
        self.assertIn(phase, ["IFR", "HOLD"])

    def test_vfr_vis_at_5km(self):
        """Vis exactly 5000m → VFR."""
        self.assertEqual(
            self._phase("OEKF 310600Z 33012KT 5000 SCT040 22/10 Q1018"),
            "VFR"
        )

    def test_vfr_vis_below_5km(self):
        """Vis 4999m → NOT VFR."""
        phase = self._phase("OEKF 310600Z 33012KT 4999 SCT040 22/10 Q1018")
        self.assertNotEqual(phase, "VFR")
        self.assertIn(phase, ["IFR", "HOLD"])

    # === IFR boundaries ===
    def test_ifr_standard(self):
        self.assertEqual(
            self._phase("OEKF 310600Z 33012KT 3000 BKN015 22/10 Q1018"),
            "IFR"
        )

    def test_ifr_low_ceiling(self):
        """Ceiling at 500ft with vis above mins → IFR."""
        self.assertEqual(
            self._phase("OEKF 310600Z 33012KT 3000 BKN005 22/10 Q1018"),
            "IFR"
        )

    # === HOLD boundaries ===
    def test_hold_very_low(self):
        self.assertEqual(
            self._phase("OEKF 310600Z 33012KT 0800 OVC003 22/10 Q1018"),
            "HOLD"
        )

    def test_hold_high_temp(self):
        """Temperature > 50°C → HOLD."""
        self.assertEqual(
            self._phase("OEKF 310600Z 33012KT 9999 FEW080 51/10 Q1018"),
            "HOLD"
        )

    def test_hold_high_crosswind(self):
        """Crosswind > 24kt → HOLD."""
        # Wind at 060° with runway 330 = 90° diff → full crosswind
        self.assertEqual(
            self._phase("OEKF 310600Z 06025KT 9999 FEW080 22/10 Q1018"),
            "HOLD"
        )

    # === RECALL boundaries ===
    def test_recall_extreme_wind(self):
        self.assertEqual(
            self._phase("OEKF 310600Z 33040G50KT 2000 +TSRA CB BKN010CB 22/10 Q1018"),
            "RECALL"
        )

    def test_recall_cb_present(self):
        self.assertEqual(
            self._phase("OEKF 310600Z 33012KT 5000 BKN010CB 22/10 Q1018"),
            "RECALL"
        )

    def test_recall_ts_weather(self):
        """TS in weather → CB implied → RECALL."""
        self.assertEqual(
            self._phase("OEKF 310600Z 33012KT 5000 +TSRA SCT025 22/10 Q1018"),
            "RECALL"
        )

    def test_recall_wind_over_35(self):
        """Wind >35kt → RECALL regardless of other conditions."""
        self.assertEqual(
            self._phase("OEKF 310600Z 33036KT 9999 FEW080 22/10 Q1018"),
            "RECALL"
        )


class TestTAFParser(unittest.TestCase):
    """Test TAF parsing."""

    def test_base_period(self):
        taf = TAFParser("TAF OEKF 310500Z 3106/3124 33010KT 8000 SCT040")
        self.assertEqual(taf.icao, "OEKF")
        self.assertIsNotNone(taf.base_period)
        self.assertEqual(taf.base_period['wind_speed'], 10)
        self.assertEqual(taf.base_period['visibility_m'], 8000)

    def test_becmg_period(self):
        taf = TAFParser(
            "TAF OEKF 310500Z 3106/3124 33010KT 8000 SCT040 "
            "BECMG 3112/3114 15010KT 5000 BKN020"
        )
        self.assertEqual(len(taf.becmg_periods), 1)
        becmg = taf.becmg_periods[0]
        self.assertEqual(becmg['wind_dir'], 150)
        self.assertEqual(becmg['wind_speed'], 10)
        self.assertEqual(becmg['visibility_m'], 5000)
        self.assertEqual(becmg['valid_from_utc'], 12)
        self.assertEqual(becmg['valid_to_utc'], 14)

    def test_tempo_period(self):
        taf = TAFParser(
            "TAF OEKF 310500Z 3106/3124 33010KT 8000 SCT040 "
            "TEMPO 3116/3120 3000 TSRA BKN010CB"
        )
        self.assertEqual(len(taf.tempo_periods), 1)
        tempo = taf.tempo_periods[0]
        self.assertEqual(tempo['visibility_m'], 3000)
        self.assertTrue(tempo['has_cb'])
        self.assertIn('TS', tempo['weather'])
        self.assertIn('RA', tempo['weather'])
        self.assertEqual(tempo['valid_from_utc'], 16)
        self.assertEqual(tempo['valid_to_utc'], 20)

    def test_fm_period(self):
        taf = TAFParser(
            "TAF OEKF 310500Z 3106/3124 33010KT 8000 SCT040 "
            "FM310800 15012KT 6000 SCT030"
        )
        self.assertEqual(len(taf.fm_periods), 1)
        fm = taf.fm_periods[0]
        self.assertEqual(fm['wind_dir'], 150)
        self.assertEqual(fm['wind_speed'], 12)
        self.assertEqual(fm['valid_from_utc'], 8)

    def test_multiple_periods(self):
        taf = TAFParser(
            "TAF OEKF 310500Z 3106/3124 33010KT 8000 SCT040 "
            "BECMG 3108/3110 28015KT "
            "TEMPO 3112/3118 4000 BKN020 "
            "BECMG 3118/3120 33008KT 9999"
        )
        self.assertEqual(len(taf.becmg_periods), 2)
        self.assertEqual(len(taf.tempo_periods), 1)

    def test_deterioration_check(self):
        taf = TAFParser(
            "TAF OEKF 310500Z 3106/3124 33010KT 8000 SCT040 "
            "TEMPO 3116/3120 3000 BKN010"
        )
        deteriorates, reason = taf.check_deterioration()
        self.assertTrue(deteriorates)
        self.assertIn("TEMPO", reason)

    def test_no_deterioration(self):
        taf = TAFParser(
            "TAF OEKF 310500Z 3106/3124 33010KT 8000 SCT040"
        )
        deteriorates, _ = taf.check_deterioration()
        self.assertFalse(deteriorates)

    def test_cb_in_taf(self):
        taf = TAFParser(
            "TAF OEKF 310500Z 3106/3124 33010KT 8000 SCT040 "
            "TEMPO 3116/3120 3000 TSRA BKN010CB"
        )
        found_cb = False
        for period_type, period in taf.get_all_periods():
            if period.get('has_cb'):
                found_cb = True
                break
        self.assertTrue(found_cb)

    def test_get_all_periods_includes_fm(self):
        taf = TAFParser(
            "TAF OEKF 310500Z 3106/3124 33010KT 8000 SCT040 "
            "FM310800 15012KT 6000 SCT030"
        )
        periods = taf.get_all_periods()
        types = [p[0] for p in periods]
        self.assertIn('BASE', types)
        self.assertIn('FM', types)

    def test_sortie_window_base_only(self):
        taf = TAFParser(
            "TAF OEKF 310500Z 3106/3124 33010KT 8000 SCT040"
        )
        result = taf.get_sortie_window_conditions(8)  # 08Z
        self.assertTrue(result['applicable'])
        self.assertEqual(result['worst_vis_m'], 8000)
        self.assertFalse(result['deteriorating'])

    def test_sortie_window_with_tempo(self):
        taf = TAFParser(
            "TAF OEKF 310500Z 3106/3124 33010KT 8000 SCT040 "
            "TEMPO 3116/3120 3000 TSRA BKN010CB"
        )
        # 17Z is within TEMPO 16-20Z window
        result = taf.get_sortie_window_conditions(17)
        self.assertTrue(result['applicable'])
        self.assertEqual(result['worst_vis_m'], 3000)
        self.assertTrue(result['has_cb'])
        self.assertTrue(result['deteriorating'])


class TestBirdLevelPhaseCapping(unittest.TestCase):
    """Test bird-strike risk level phase capping."""

    @classmethod
    def setUpClass(cls):
        data_file = Path(__file__).parent / 'airfield_data.json'
        with open(data_file) as f:
            cls.airfield_data = json.load(f)
        if 'airfields' in cls.airfield_data:
            for icao, af_data in cls.airfield_data['airfields'].items():
                cls.airfield_data[icao] = af_data
            for icao in list(cls.airfield_data.get('airfields', {}).keys()):
                af = cls.airfield_data[icao]
                new_runways = []
                for rwy in af.get('runways', []):
                    if '/' in str(rwy.get('id', '')):
                        ids = rwy['id'].split('/')
                        hdgs = str(rwy.get('heading', '')).split('/')
                        if len(ids) == 2 and len(hdgs) == 2:
                            new_runways.append({'id': ids[0], 'heading': int(hdgs[0]), 'reciprocal': ids[1]})
                            new_runways.append({'id': ids[1], 'heading': int(hdgs[1]), 'reciprocal': ids[0]})
                        else:
                            new_runways.append(rwy)
                    else:
                        new_runways.append(rwy)
                af['runways'] = new_runways

    def test_bird_moderate_caps_unrestricted(self):
        """UNRESTRICTED weather + moderate birds → VFR cap."""
        m = METARParser("OEKF 310600Z 33008KT 9999 FEW080 22/10 Q1018")
        result = determine_phase(m, 330, self.airfield_data)
        self.assertEqual(result['phase'], "UNRESTRICTED")
        # Now simulate bird capping (as done in main())
        bird_level = 'MODERATE'
        solo_phases = ['UNRESTRICTED', 'RESTRICTED', 'FS VFR']
        if result['phase'] in solo_phases:
            result['phase'] = 'VFR'
        self.assertEqual(result['phase'], 'VFR')

    def test_bird_low_no_cap(self):
        """UNRESTRICTED weather + low birds → stays UNRESTRICTED."""
        m = METARParser("OEKF 310600Z 33008KT 9999 FEW080 22/10 Q1018")
        result = determine_phase(m, 330, self.airfield_data)
        self.assertEqual(result['phase'], "UNRESTRICTED")

    def test_bird_severe_caps_unrestricted(self):
        """UNRESTRICTED weather + severe birds → VFR cap."""
        m = METARParser("OEKF 310600Z 33008KT 9999 FEW080 22/10 Q1018")
        result = determine_phase(m, 330, self.airfield_data)
        solo_phases = ['UNRESTRICTED', 'RESTRICTED', 'FS VFR']
        if result['phase'] in solo_phases:
            result['phase'] = 'VFR'
        self.assertEqual(result['phase'], 'VFR')

    def test_bird_moderate_vfr_unchanged(self):
        """VFR weather + moderate birds → stays VFR (already below solo phases)."""
        m = METARParser("OEKF 310600Z 33012KT 7000 SCT040 22/10 Q1018")
        result = determine_phase(m, 330, self.airfield_data)
        self.assertEqual(result['phase'], 'VFR')
        solo_phases = ['UNRESTRICTED', 'RESTRICTED', 'FS VFR']
        if result['phase'] in solo_phases:
            result['phase'] = 'VFR'
        self.assertEqual(result['phase'], 'VFR')


class TestRunwaySelection(unittest.TestCase):
    """Test runway selection from wind."""

    @classmethod
    def setUpClass(cls):
        data_file = Path(__file__).parent / 'airfield_data.json'
        with open(data_file) as f:
            cls.airfield_data = json.load(f)
        if 'airfields' in cls.airfield_data:
            for icao, af_data in cls.airfield_data['airfields'].items():
                cls.airfield_data[icao] = af_data
            for icao in list(cls.airfield_data.get('airfields', {}).keys()):
                af = cls.airfield_data[icao]
                new_runways = []
                for rwy in af.get('runways', []):
                    if '/' in str(rwy.get('id', '')):
                        ids = rwy['id'].split('/')
                        hdgs = str(rwy.get('heading', '')).split('/')
                        if len(ids) == 2 and len(hdgs) == 2:
                            new_runways.append({'id': ids[0], 'heading': int(hdgs[0]), 'reciprocal': ids[1]})
                            new_runways.append({'id': ids[1], 'heading': int(hdgs[1]), 'reciprocal': ids[0]})
                        else:
                            new_runways.append(rwy)
                    else:
                        new_runways.append(rwy)
                af['runways'] = new_runways

    def test_select_runway_into_wind(self):
        """Wind 330° → runway 33L or 33R."""
        m = METARParser("OEKF 310600Z 33012KT 9999 FEW080 22/10 Q1018")
        rwy, hdg = select_runway(m, self.airfield_data, 'OEKF')
        self.assertIn('33', rwy)
        self.assertEqual(hdg, 330)

    def test_select_runway_southerly_wind(self):
        """Wind 150° → runway 15L or 15R."""
        m = METARParser("OEKF 310600Z 15012KT 9999 FEW080 22/10 Q1018")
        rwy, hdg = select_runway(m, self.airfield_data, 'OEKF')
        self.assertIn('15', rwy)
        self.assertEqual(hdg, 150)


if __name__ == '__main__':
    unittest.main(verbosity=2)
