"""ATK X1 Ultra fixtures captured read-only from the user's 1K receiver."""
from types import SimpleNamespace
from dataclasses import replace
import unittest
from unittest.mock import Mock, patch

import mouse_settings as ms


class AtkX1Tests(unittest.TestCase):
    def setUp(self):
        self.iface = SimpleNamespace(path='atk-x1-test',vid=0x373B,pid=0x101A,
            usage_page=0xFF02,usage=2,output_len=17,input_len=17,
            vid_pid='373B:101A',product='ATK Mouse 1K Dongle')
        self.mouse = ms.Mouse(self.iface.path,'ATK X1 Ultra',self.iface.vid_pid,'atk',(2,43))

    def test_real_identity_and_report_checksum(self):
        dev = Mock()
        dev.read_input.return_value = bytes.fromhex('081000000003022b2800000000000000e5')
        self.assertEqual(ms.atk_identity(dev,self.iface),(2,43))
        dev.write_output.assert_called_once_with(bytes.fromhex('081000000000000000000000000000003d'),use_hidd=False)

    def test_only_read_identity_retried_after_timeout(self):
        raw = bytes.fromhex('1000000003022b2800000000000000e5')
        with patch.object(ms,'atk_exchange',side_effect=[TimeoutError(),raw]) as exchange,patch.object(ms.time,'sleep'):
            self.assertEqual(ms.atk_identity(Mock(),self.iface),(2,43))
            self.assertEqual(exchange.call_count,2)
            self.assertTrue(all(call.args[2]==0x10 for call in exchange.call_args_list))
        with patch.object(ms,'atk_exchange',side_effect=TimeoutError()) as exchange,patch.object(ms.time,'sleep'):
            with self.assertRaises(TimeoutError):ms.atk_identity(Mock(),self.iface)
            self.assertEqual(exchange.call_count,3)

    def test_discovery_matches_receiver_and_mouse_identity(self):
        with patch.object(ms.hw,'enumerate_hid',return_value=[self.iface]),patch.object(ms.hw,'HidDevice'),patch.object(ms,'atk_identity',return_value=(2,43)):
            found=ms.discover()
        self.assertEqual(len(found),1)
        self.assertEqual((found[0].name,found[0].protocol),('ATK X1 Ultra','atk'))
        self.iface.pid=0xFFFF
        self.assertIsNone(ms.atk_model((2,43),self.iface))
        self.assertIsNone(ms.atk_model((2,255),self.iface))

    def test_real_dpi_stages_use_ultra_encoding(self):
        # System: four enabled stages, zero-based active index 3.
        replies=[bytes.fromhex(s) for s in ('015404510352','27270007','4f4f00b7','9f9f0017','3f3f4493')]
        with patch.object(ms.hw,'enumerate_hid',return_value=[self.iface]),patch.object(ms.hw,'HidDevice'),patch.object(ms,'atk_identity',return_value=(2,43)),patch.object(ms,'atk_read',side_effect=replies),patch.object(ms,'atk_profile',return_value=2) as profile:
            state=ms.read(self.mouse)
        self.assertEqual(state.stages,[(400,400),(800,800),(1600,1600),(3200,3200)])
        self.assertEqual((state.active,state.minimum,state.maximum,state.step),(3,10,42000,10))
        self.assertEqual(state.profile,2)
        self.assertEqual(profile.call_count,2)

    def test_captured_profile_reply_and_reply_matching(self):
        dev=Mock()
        captured=bytes.fromhex('080e00000001000000000000000000003e')
        # Ignore a response for another query, then decode the captured bank 0.
        dev.read_input.side_effect=[bytes.fromhex('081000000003022b2800000000000000e5'),captured]
        self.assertEqual(ms.atk_profile(dev,self.iface),1)
        dev.write_output.assert_called_once_with(ms.atk_frame(0x0E),use_hidd=False)
        with patch.object(ms,'atk_exchange',return_value=captured[1:]) as exchange:
            ms.atk_profile(dev,self.iface)
        accepts=exchange.call_args.kwargs['predicate']
        for at,value in ((0,8),(1,1),(4,0),(4,11)):
            bad=bytearray(captured[1:]);bad[at]=value
            self.assertFalse(accepts(bad))

    def test_profile_change_during_read_is_rejected(self):
        replies=[bytes.fromhex(s) for s in ('015404510352','27270007','4f4f00b7','9f9f0017','3f3f4493')]
        with patch.object(ms.hw,'enumerate_hid',return_value=[self.iface]),patch.object(ms.hw,'HidDevice'),patch.object(ms,'atk_identity',return_value=(2,43)),patch.object(ms,'atk_read',side_effect=replies),patch.object(ms,'atk_profile',side_effect=[1,2]):
            with self.assertRaisesRegex(ValueError,'配置层已变化'):
                ms.read(self.mouse)

    def test_same_dpi_in_another_profile_rejects_stale_apply(self):
        old=ms.DpiState(self.mouse,[(800,800)],0,10,42000,10,profile=1)
        with patch.object(ms,'_read',return_value=replace(old,profile=2)),patch.object(ms.hw,'HidDevice') as device:
            with self.assertRaisesRegex(ValueError,'已变化'):
                ms.apply(old,0,1000,False)
            device.assert_not_called()

    def test_profile_change_after_write_is_not_success(self):
        old=ms.DpiState(self.mouse,[(800,800)],0,10,42000,10,profile=1)
        new=replace(old,stages=[(1000,1000)],profile=2)
        with patch.object(ms,'_read',side_effect=[old,new]),patch.object(ms.hw,'enumerate_hid',return_value=[self.iface]),patch.object(ms.hw,'HidDevice'),patch.object(ms.time,'sleep'):
            with self.assertRaisesRegex(RuntimeError,'部分改动可能已生效'):
                ms.apply(old,0,1000,False)

    def test_write_and_readback_use_same_model_codec(self):
        old=ms.DpiState(self.mouse,[(400,400),(800,800),(1600,1600),(3200,3200)],3,10,42000,10)
        new=ms.DpiState(self.mouse,[(400,400),(1000,1000),(1600,1600),(3200,3200)],3,10,42000,10)
        with patch.object(ms,'_read',side_effect=[old,new]),patch.object(ms.hw,'enumerate_hid',return_value=[self.iface]),patch.object(ms.hw,'HidDevice') as device,patch.object(ms.time,'sleep'):
            result=ms.apply(old,1,1000,False)
            writes=device.return_value.__enter__.return_value.write_output.call_args_list
        self.assertIs(result,new)
        self.assertEqual(len(writes),1)
        self.assertEqual(writes[0].args[0],ms.atk_frame(7,(0,0,16,4,*bytes.fromhex('6363008f'))))


class AtkX1UltimateTests(unittest.TestCase):
    """X1 Ultimate 大师版: identity (2,46) captured from a 373B:101B 8K dongle."""

    def setUp(self):
        self.iface = SimpleNamespace(path='atk-x1u-test',vid=0x373B,pid=0x101B,
            usage_page=0xFF02,usage=2,output_len=17,input_len=17,
            vid_pid='373B:101B',product='ATK Mouse 8K Dongle')

    def test_identity_spec_and_range(self):
        self.assertEqual(ms.ATK_MODELS[(2,46)],('ATK X1 Ultimate 大师版','ultra',10,42000))
        self.assertEqual(ms.atk_model((2,46),self.iface),ms.ATK_MODELS[(2,46)])

    def test_unknown_receivers_stay_rejected(self):
        for pid in (0x1002,0x1019,0x101C,0xFFFF):
            self.iface.pid=pid
            self.assertIsNone(ms.atk_model((2,46),self.iface))
        self.iface.pid=0x101B;self.iface.vid=0x3554
        self.assertIsNone(ms.atk_model((2,46),self.iface))

    def test_read_uses_captured_reports(self):
        # Live capture: one enabled stage, active index 0, rate code 1, DPI 1600.
        replies=[bytes.fromhex('015401540055'),bytes.fromhex('9f9f0017')]
        mouse=ms.Mouse(self.iface.path,'ATK X1 Ultimate 大师版','373B:101B','atk',(2,46),interface=self.iface)
        with patch.object(ms.hw,'enumerate_hid',return_value=[self.iface]),patch.object(ms.hw,'HidDevice'),patch.object(ms,'atk_identity',return_value=(2,46)),patch.object(ms,'atk_read',side_effect=replies),patch.object(ms,'atk_profile',return_value=1):
            state=ms.read(mouse)
        self.assertEqual(state.stages,[(1600,1600)])
        self.assertEqual((state.active,state.minimum,state.maximum,state.step),(0,10,42000,10))
        self.assertEqual(state.profile,1)

    def test_interface_gate_accepts_8k_and_rejects_bad_channels(self):
        import mouse_controls as mc
        mouse=ms.Mouse(self.iface.path,'ATK X1 Ultimate 大师版','373B:101B','atk',(2,46),interface=self.iface)
        with patch.object(ms.hw,'enumerate_hid',return_value=[self.iface]):
            self.assertIs(mc._interface(mouse),self.iface)
        # The gate must reject anything that is not the 17-byte 0xFF02 channel on a shared receiver.
        for bad in ({'pid':0x1002},{'pid':0xFFFF},{'output_len':64},{'usage_page':0xFF01}):
            iface=SimpleNamespace(**vars(self.iface))
            for key,value in bad.items():setattr(iface,key,value)
            probe=ms.Mouse(iface.path,'ATK X1 Ultimate 大师版','373B:101B','atk',(2,46),interface=iface)
            with patch.object(ms.hw,'enumerate_hid',return_value=[iface]):
                with self.assertRaisesRegex(ValueError,'配置接口已变化'):mc._interface(probe)


if __name__=='__main__':unittest.main()
