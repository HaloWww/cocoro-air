"""Capability and command regressions for selectively imported upstream logic."""
import importlib.util
from pathlib import Path
import unittest

spec=importlib.util.spec_from_file_location('controls',Path(__file__).parents[1]/'custom_components/cocoro_air/control.py')
controls=importlib.util.module_from_spec(spec)
spec.loader.exec_module(controls)
MODEL={'model_name':'KIPX100','hasCloudService':True,'hasHumidFunc':True,'hasSplashParticle':True}

class ControlTests(unittest.TestCase):
    def test_ki_px100_modes_exclude_internal_and_unregistered_pet_modes(self):
        self.assertEqual(controls.supported_modes(MODEL),['ai_auto','auto','pollen','sleep','feel','splash','quiet','medium','strong'])
        for mode in ('demo','settings','dog','cat','turbo'):
            with self.assertRaises(ValueError): controls.build_command('mode',mode,{'current_mode':'splash'},MODEL)

    def test_power_command_matches_vendor(self):
        self.assertEqual(controls.build_command('power',False,{'current_mode':'splash'},MODEL),
                         [{'epc':'0x80','edt':'0x31'},{'opc':'k3','odt':{'s6':'00'}}])
        with self.assertRaises(ValueError): controls.build_command('power','off',{'current_mode':'splash'},MODEL)

    def test_mode_controls_reject_cleaning_and_fault_states(self):
        for state in ('off','settings','demo','error','humidifier_cleaning','cleaning'):
            with self.assertRaises(ValueError): controls.build_command('mode','quiet',{'current_mode':state},MODEL)

    def test_exit_ai_special_mode_uses_vendor_reset(self):
        self.assertEqual(controls.build_command('mode','ai_auto',{'current_mode':'cat'},MODEL),
                         [{'opc':'k3','odt':{'s11':'0004'}}])

    def test_model_specific_modes(self):
        self.assertNotIn('splash',controls.supported_modes({'model_name':'KILS50','hasCloudService':False}))
        self.assertIn('turbo',controls.supported_modes(dict(MODEL,hasWeakTurbo=True)))
